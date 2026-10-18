from __future__ import annotations

import sqlite3
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.db import migrate
from backend.app.gateway import MockModelProvider, ModelGateway
from backend.app.governor import AdvisorDescriptor, GovernorBlocked, GovernorController, GovernorRequest


class Events:
    def __init__(self) -> None:
        self.items = []

    def audit(self, **kwargs):
        self.items.append(kwargs)


def make_db():
    db = sqlite3.connect(":memory:", isolation_level=None)
    db.row_factory = sqlite3.Row
    migrate(db)
    return db


def advisors() -> list[AdvisorDescriptor]:
    return [
        AdvisorDescriptor("a1", "Advisor One", "analysis", ("text_generation",), 0.95, 0.10, 100),
        AdvisorDescriptor("a2", "Advisor Two", "analysis", ("text_generation",), 0.90, 0.10, 100),
        AdvisorDescriptor("a3", "Advisor Three", "analysis", ("text_generation",), 0.80, 0.10, 100),
    ]


def test_confident_parallel_and_idempotent() -> None:
    db = make_db()
    events = Events()
    controller = GovernorController(db, events, ModelGateway(providers=[MockModelProvider()]))
    calls: list[str] = []
    active = 0
    max_active = 0
    lock = threading.Lock()

    def consult(descriptor, request):
        nonlocal active, max_active
        calls.append(descriptor.advisor_id)
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return {"answer": "choose the evidence-backed plan", "confidence": 0.9, "evidence": ["source-a", "source-b"], "agreement_key": "plan"}

    request = GovernorRequest("plan campaign and validate evidence", budget=1.0, max_advisors=3, idempotency_key="decision-key")
    started = time.perf_counter()
    decision = controller.decide(request, consultation=consult, advisors=advisors())
    elapsed = time.perf_counter() - started
    assert decision.status == "CONFIDENT"
    assert decision.fused_answer == "choose the evidence-backed plan"
    assert decision.questions["should_stop"] is False
    assert len(calls) == 3
    assert decision.model_selection["status"] == "SELECTED"
    assert max_active >= 2, "consultations should overlap in bounded parallel execution"
    assert elapsed < 0.12, "consultations should remain bounded rather than hanging"
    repeated = controller.decide(request, consultation=lambda *_: (_ for _ in ()).throw(AssertionError("idempotency reconsulted")), advisors=advisors())
    assert repeated.decision_id == decision.decision_id
    assert db.execute("SELECT COUNT(*) AS count FROM governor_decisions").fetchone()["count"] == 1
    assert db.execute("SELECT COUNT(*) AS count FROM governor_consultations").fetchone()["count"] == 3


def test_conflict_unresolved_and_outcome_reputation() -> None:
    db = make_db()
    controller = GovernorController(db, Events())
    opinions = {"a1": "route A", "a2": "route B", "a3": "route B"}

    def consult(descriptor, request):
        return {"answer": opinions[descriptor.advisor_id], "confidence": 0.85, "evidence": [descriptor.advisor_id], "agreement_key": opinions[descriptor.advisor_id]}

    decision = controller.decide(GovernorRequest("resolve conflicting options", max_advisors=3), consultation=consult, advisors=advisors())
    assert decision.status == "UNRESOLVED"
    assert decision.fused_answer is None
    assert decision.conflict["detected"] is True
    assert decision.questions["should_escalate"] is True
    outcome = controller.record_outcome(decision_id=decision.decision_id, success=False, accuracy=0.4, task_affinity=0.8, agreement=0.5, note="Human review rejected the unresolved plan.")
    assert outcome["success"] is False
    assert controller.advisors()[0]["reputation"]["consultations"] >= 1
    restored = GovernorController(db, Events()).get_decision(decision.decision_id)
    assert restored["status"] == "UNRESOLVED"


def test_retry_budget_and_budget_block() -> None:
    db = make_db()
    controller = GovernorController(db, Events())
    attempts = {"a1": 0}

    def retrying(descriptor, request):
        attempts[descriptor.advisor_id] += 1
        if attempts[descriptor.advisor_id] == 1:
            raise GovernorConsultationError("TEMPORARY", "temporary advisor outage", retryable=True)
        return {"answer": "recovered answer", "confidence": 0.8, "evidence": ["recovered-source"], "agreement_key": "recovered"}

    # Exercise the public controller with the documented callback exception.
    from backend.app.governor.orchestrator import GovernorConsultationError
    decision = controller.decide(GovernorRequest("recover consultation", budget=0.2, max_advisors=1, retry_budget=1), consultation=retrying, advisors=[advisors()[0]])
    assert decision.status == "UNCERTAIN"
    assert attempts["a1"] == 2
    expensive = controller.decide(GovernorRequest("too expensive", budget=0.05, max_advisors=2), consultation=lambda descriptor, request: {"answer": "answer", "confidence": 0.9, "evidence": ["e1", "e2"], "agreement_key": "same"}, advisors=advisors()[:2])
    assert expensive.status == "BLOCKED"
    assert expensive.execution_plan["status"] == "STOP"


def test_blocked_risk_tool_and_missing_advisor_states() -> None:
    db = make_db()
    controller = GovernorController(db, Events(), ModelGateway(providers=[MockModelProvider()]))
    critical = controller.decide(GovernorRequest("approve a critical operation", risk_level="critical"), consultation=lambda *_: {}, advisors=advisors())
    assert critical.status == "BLOCKED"
    assert critical.questions["should_stop"] is True
    missing = controller.decide(GovernorRequest("inspect database", required_capability="database", needs_tool=True), advisors=advisors())
    assert missing.status == "BLOCKED"
    assert missing.tool_selection["status"] == "BLOCKED"
    assert controller.decide(GovernorRequest("do text work", required_capability="unregistered"), advisors=advisors()).status == "BLOCKED"


def main() -> None:
    test_confident_parallel_and_idempotent()
    test_conflict_unresolved_and_outcome_reputation()
    test_retry_budget_and_budget_block()
    test_blocked_risk_tool_and_missing_advisor_states()
    print("governor: passed; interpretation, decomposition, parallel consultation, fusion, conflict, retry, budget, tool, risk, idempotency, reputation, and persistence verified")


if __name__ == "__main__":
    main()
