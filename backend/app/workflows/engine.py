from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from threading import Event, Lock
from typing import Any, Callable, Protocol


class WorkflowCancelled(Exception):
    pass


class WorkflowEngine(Protocol):
    def submit(self, job_id: str, handler: Callable[..., Any], *args: Any) -> Future[Any]: ...
    def cancel(self, job_id: str) -> bool: ...
    def is_cancelled(self, job_id: str) -> bool: ...
    def shutdown(self) -> None: ...


@dataclass(frozen=True)
class WorkflowAttempt:
    job_id: str
    attempt: int
    status: str


class LocalDurableEngine:
    """Process-local engine with stable job ownership and cooperative cancellation.

    SQLite remains the source of truth for job checkpoints. This class deliberately
    does not claim crash-resume durability; that is the contract boundary for a
    later TemporalEngine implementation.
    """

    def __init__(self, max_workers: int = 2) -> None:
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="flujo-worker")
        self.lock = Lock()
        self.futures: dict[str, Future[Any]] = {}
        self.cancel_flags: dict[str, Event] = {}
        self.attempts: dict[str, int] = {}

    def submit(self, job_id: str, handler: Callable[..., Any], *args: Any) -> Future[Any]:
        with self.lock:
            existing = self.futures.get(job_id)
            if existing and not existing.done():
                return existing
            self.cancel_flags[job_id] = Event()
            self.attempts[job_id] = self.attempts.get(job_id, 0) + 1
            future = self.executor.submit(self._run, job_id, handler, *args)
            self.futures[job_id] = future
            return future

    def _run(self, job_id: str, handler: Callable[..., Any], *args: Any) -> Any:
        if self.is_cancelled(job_id):
            raise WorkflowCancelled(f"Workflow {job_id} was cancelled before start.")
        return handler(*args)

    def cancel(self, job_id: str) -> bool:
        with self.lock:
            flag = self.cancel_flags.get(job_id)
            future = self.futures.get(job_id)
            if flag is None:
                return False
            flag.set()
            return bool(future and future.cancel())

    def is_cancelled(self, job_id: str) -> bool:
        with self.lock:
            flag = self.cancel_flags.get(job_id)
            return bool(flag and flag.is_set())

    def attempt(self, job_id: str) -> WorkflowAttempt:
        with self.lock:
            future = self.futures.get(job_id)
            status = "cancelled" if self.is_cancelled(job_id) else "running" if future and not future.done() else "completed"
            return WorkflowAttempt(job_id, self.attempts.get(job_id, 0), status)

    def shutdown(self) -> None:
        self.executor.shutdown(wait=True, cancel_futures=True)


class TemporalEngine:
    """Deployment adapter placeholder; it fails closed until configured."""

    def submit(self, job_id: str, handler: Callable[..., Any], *args: Any) -> Future[Any]:
        raise RuntimeError("TEMPORAL_ENGINE_NOT_CONFIGURED")

    def cancel(self, job_id: str) -> bool:
        return False

    def is_cancelled(self, job_id: str) -> bool:
        return False

    def shutdown(self) -> None:
        return None
