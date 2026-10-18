from __future__ import annotations

import hashlib
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterable


GOVERNOR_CONTRACT_VERSION = "governor.decision.v1"
GOVERNOR_STATES = {"CONFIDENT", "UNCERTAIN", "UNRESOLVED", "BLOCKED"}


class GovernorBlocked(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class GovernorConsultationError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class GovernorRequest:
    task: str
    required_capability: str = "text_generation"
    context: dict[str, Any] = field(default_factory=dict)
    budget: float = 1.0
    risk_level: str = "medium"
    max_advisors: int = 3
    retry_budget: int = 1
    needs_tool: bool = False
    idempotency_key: str | None = None


@dataclass(frozen=True)
class AdvisorDescriptor:
    advisor_id: str
    name: str
    role: str
    capabilities: tuple[str, ...]
    task_affinity: float = 0.5
    cost_per_consult: float = 0.1
    expected_latency_ms: int = 100
    enabled: bool = True


@dataclass(frozen=True)
class AdvisorOpinion:
    advisor_id: str
    answer: str
    confidence: float
    evidence: tuple[str, ...] = ()
    cost: float = 0.0
    latency_ms: int = 0
    success: bool = True
    retryable: bool = False
    error_code: str | None = None
    agreement_key: str = ""
    simulator_only: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GovernorDecision:
    decision_id: str
    status: str
    interpretation: dict[str, Any]
    subtasks: list[dict[str, Any]]
    selected_advisors: list[dict[str, Any]]
    opinions: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    fused_answer: str | None
    conflict: dict[str, Any]
    model_selection: dict[str, Any]
    tool_selection: dict[str, Any]
    execution_plan: dict[str, Any]
    questions: dict[str, bool]
    cost: float
    latency_ms: int
    contract_version: str = GOVERNOR_CONTRACT_VERSION

    @property
    def state(self) -> str:
        return self.status

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


Consultation = Callable[[AdvisorDescriptor, GovernorRequest], AdvisorOpinion | dict[str, Any]]


class GovernorController:
    """Local orchestration authority; provider adapters remain outside this class."""

    def __init__(self, db: Any, events: Any | None = None, gateway: Any | None = None) -> None:
        self.db = db
        self.events = events
        self.gateway = gateway

    def _audit(self, operation: str, status: str, message: str, metadata: dict[str, Any] | None = None, decision_id: str | None = None) -> None:
        if self.events is not None:
            self.events.audit(category="governor", message=message, route="governor", operation=operation, status=status, decision_id=decision_id, metadata=metadata or {})

    def _now(self) -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()

    def register_advisor(self, descriptor: AdvisorDescriptor) -> dict[str, Any]:
        now = self._now()
        self.db.execute("INSERT INTO governor_advisors(id,name,role,capabilities_json,task_affinity,cost_per_consult,expected_latency_ms,enabled,updated_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,role=excluded.role,capabilities_json=excluded.capabilities_json,task_affinity=excluded.task_affinity,cost_per_consult=excluded.cost_per_consult,expected_latency_ms=excluded.expected_latency_ms,enabled=excluded.enabled,updated_at=excluded.updated_at", (descriptor.advisor_id, descriptor.name, descriptor.role, json.dumps(descriptor.capabilities), descriptor.task_affinity, descriptor.cost_per_consult, descriptor.expected_latency_ms, int(descriptor.enabled), now))
        return self._advisor_record(descriptor.advisor_id)

    def _advisor_record(self, advisor_id: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM governor_advisors WHERE id=?", (advisor_id,)).fetchone()
        if row is None:
            raise GovernorBlocked("ADVISOR_NOT_FOUND", "The selected advisor is not registered.")
        result = dict(row)
        result["capabilities"] = json.loads(result.pop("capabilities_json") or "[]")
        consultations = int(result.pop("total_consultations"))
        result["reputation"] = {
            "consultations": consultations,
            "successes": result.pop("successes"),
            "accuracy": (result.pop("total_accuracy") / max(consultations, 1)),
            "cost": result.pop("total_cost"),
            "latency_ms": result.pop("total_latency_ms") / max(consultations, 1),
            "task_affinity": (result.pop("total_affinity") / max(consultations, 1)),
            "agreement": (result.pop("total_agreement") / max(consultations, 1)),
        }
        return result

    def advisors(self) -> list[dict[str, Any]]:
        return [self._advisor_record(row["id"]) for row in self.db.execute("SELECT id FROM governor_advisors WHERE enabled=1 ORDER BY task_affinity DESC, id").fetchall()]

    def _descriptors(self, request: GovernorRequest, supplied: Iterable[AdvisorDescriptor] | None) -> list[AdvisorDescriptor]:
        if supplied is not None:
            descriptors = list(supplied)
        else:
            descriptors = []
            for row in self.db.execute("SELECT * FROM governor_advisors WHERE enabled=1").fetchall():
                descriptors.append(AdvisorDescriptor(row["id"], row["name"], row["role"], tuple(json.loads(row["capabilities_json"] or "[]")), float(row["task_affinity"]), float(row["cost_per_consult"]), int(row["expected_latency_ms"]), bool(row["enabled"])))
        eligible = [item for item in descriptors if item.enabled and request.required_capability in item.capabilities]
        def selection_score(item: AdvisorDescriptor) -> float:
            row = self.db.execute("SELECT total_consultations,total_accuracy,total_agreement,total_affinity FROM governor_advisors WHERE id=?", (item.advisor_id,)).fetchone()
            if row is None or int(row["total_consultations"] or 0) == 0:
                reputation = 0.0
            else:
                count = max(int(row["total_consultations"]), 1)
                reputation = (float(row["total_accuracy"] or 0) / count) * 0.35 + (float(row["total_agreement"] or 0) / count) * 0.25 + (float(row["total_affinity"] or 0) / count) * 0.20 + (float(row["total_consultations"] or 0) / (count + 5)) * 0.20
            return item.task_affinity * 0.55 + reputation * 0.35 + (1.0 / (1.0 + item.cost_per_consult)) * 0.05 + (1.0 / (1.0 + item.expected_latency_ms)) * 0.05
        eligible.sort(key=selection_score, reverse=True)
        max_advisors = max(1, min(request.max_advisors, 8))
        budget = max(0.0, request.budget)
        selected: list[AdvisorDescriptor] = []
        total = 0.0
        for item in eligible:
            if len(selected) >= max_advisors:
                break
            if total + item.cost_per_consult <= budget or not selected:
                selected.append(item)
                total += item.cost_per_consult
        return selected

    def interpret(self, request: GovernorRequest) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        task = " ".join(request.task.strip().split())
        if not task:
            raise GovernorBlocked("TASK_EMPTY", "Governor cannot interpret an empty task.")
        chunks = [chunk.strip() for chunk in task.replace(";", " and ").split(" and ") if chunk.strip()]
        subtasks = [{"subtask_id": f"subtask-{index + 1}", "description": chunk, "status": "planned"} for index, chunk in enumerate(chunks[:12])]
        risk = request.risk_level.lower() if request.risk_level.lower() in {"low", "medium", "high", "critical"} else "medium"
        interpretation = {"task": task, "required_capability": request.required_capability, "risk_level": risk, "ambiguity": len(chunks) == 0, "context_keys": sorted(request.context.keys())}
        return interpretation, subtasks or [{"subtask_id": "subtask-1", "description": task, "status": "planned"}]

    def _normalize_opinion(self, descriptor: AdvisorDescriptor, value: AdvisorOpinion | dict[str, Any], elapsed_ms: int) -> AdvisorOpinion:
        if isinstance(value, AdvisorOpinion):
            return value
        answer = str(value.get("answer", "")).strip()
        confidence = max(0.0, min(float(value.get("confidence", 0.0)), 1.0))
        evidence = tuple(str(item)[:1000] for item in value.get("evidence", []) if str(item).strip())
        return AdvisorOpinion(descriptor.advisor_id, answer, confidence, evidence, float(value.get("cost", descriptor.cost_per_consult)), int(value.get("latency_ms", elapsed_ms)), bool(value.get("success", True)), bool(value.get("retryable", False)), value.get("error_code"), str(value.get("agreement_key", answer.casefold()[:160])), bool(value.get("simulator_only", False)))

    def _consult_one(self, descriptor: AdvisorDescriptor, request: GovernorRequest, consultation: Consultation) -> AdvisorOpinion:
        started = time.perf_counter()
        try:
            return self._normalize_opinion(descriptor, consultation(descriptor, request), int((time.perf_counter() - started) * 1000))
        except GovernorConsultationError as exc:
            return AdvisorOpinion(descriptor.advisor_id, "", 0.0, (), descriptor.cost_per_consult, int((time.perf_counter() - started) * 1000), False, exc.retryable, exc.code, "")
        except Exception:
            return AdvisorOpinion(descriptor.advisor_id, "", 0.0, (), descriptor.cost_per_consult, int((time.perf_counter() - started) * 1000), False, False, "ADVISOR_FAILED", "")

    def _consult_parallel(self, descriptors: list[AdvisorDescriptor], request: GovernorRequest, consultation: Consultation) -> list[AdvisorOpinion]:
        opinions: list[AdvisorOpinion] = []
        with ThreadPoolExecutor(max_workers=max(1, min(len(descriptors), 8))) as pool:
            futures = {pool.submit(self._consult_one, descriptor, request, consultation): descriptor for descriptor in descriptors}
            for future in as_completed(futures):
                opinions.append(future.result())
        return opinions

    def _fuse(self, opinions: list[AdvisorOpinion]) -> tuple[str, str | None, list[dict[str, Any]], dict[str, Any], dict[str, bool]]:
        successful = [item for item in opinions if item.success and item.answer]
        evidence = [{"advisor_id": item.advisor_id, "items": list(item.evidence), "confidence": item.confidence} for item in successful]
        groups: dict[str, list[AdvisorOpinion]] = {}
        for item in successful:
            key = item.agreement_key or item.answer.casefold().strip()
            groups.setdefault(key, []).append(item)
        ranked = sorted(groups.values(), key=lambda group: (len(group), sum(item.confidence for item in group)), reverse=True)
        conflict = {"detected": len(groups) > 1, "group_count": len(groups), "groups": [{"agreement_key": key, "advisor_ids": [item.advisor_id for item in group], "confidence": sum(item.confidence for item in group) / len(group)} for key, group in groups.items()]}
        if not successful:
            status = "BLOCKED" if opinions and all(not item.retryable for item in opinions) else "UNRESOLVED"
            questions = {"need_another_advisor": True, "need_deeper_evidence": True, "should_retry": any(item.retryable for item in opinions), "should_escalate": True, "should_stop": status == "BLOCKED"}
            return status, None, evidence, conflict, questions
        winner = ranked[0]
        average_confidence = sum(item.confidence for item in winner) / len(winner)
        total_evidence = sum(len(item.evidence) for item in winner)
        majority_ratio = len(winner) / max(len(successful), 1)
        if len(ranked) > 1 and majority_ratio < 0.75:
            status = "UNRESOLVED"
        elif len(ranked) == 1 and len(winner) >= 2 and average_confidence >= 0.75 and total_evidence >= 2:
            status = "CONFIDENT"
        elif len(ranked) > 1 and len(winner) == 1:
            status = "UNRESOLVED"
        else:
            status = "UNCERTAIN"
        questions = {"need_another_advisor": status in {"UNCERTAIN", "UNRESOLVED"}, "need_deeper_evidence": total_evidence < 2 or average_confidence < 0.75, "should_retry": any(not item.success and item.retryable for item in opinions), "should_escalate": status in {"UNRESOLVED", "BLOCKED"}, "should_stop": status == "BLOCKED"}
        answer = winner[0].answer if status != "UNRESOLVED" else None
        return status, answer, evidence, conflict, questions

    def _select_model(self, request: GovernorRequest) -> dict[str, Any]:
        if self.gateway is None:
            return {"status": "UNAVAILABLE", "reason": "No gateway catalog was supplied to Governor."}
        candidates = [model for model in self.gateway.discover() if request.required_capability in model.capabilities and model.status == "healthy"]
        if not candidates:
            return {"status": "UNRESOLVED", "reason": "No healthy gateway model satisfies the requested capability."}
        selected = max(candidates, key=lambda model: (model.reliability_score * 0.30) + (model.quality_score * 0.30) + (model.cost_score * 0.15) + (model.latency_score * 0.15) + (model.priority / 1000))
        return {"status": "SELECTED", "provider": selected.provider, "model_id": selected.model_id, "version": selected.version, "capabilities": list(selected.capabilities), "selection_reason": "Capability match weighted by reliability, quality, cost, latency, and priority."}

    def _select_tool(self, request: GovernorRequest, tools: list[dict[str, Any]] | None) -> dict[str, Any]:
        available = [tool for tool in (tools or []) if tool.get("enabled", True) and tool.get("allowed", False)]
        wanted = request.needs_tool or any(token in request.task.casefold() for token in ("search", "inspect file", "database", "calculate"))
        if not wanted:
            return {"needed": False, "status": "NOT_REQUIRED", "tool": None}
        if not available:
            return {"needed": True, "status": "BLOCKED", "reason": "Governor identified a tool need but no explicitly allowlisted tool is available.", "tool": None}
        selected = max(available, key=lambda item: float(item.get("task_affinity", 0.5)))
        return {"needed": True, "status": "SELECTED", "tool": {"name": selected.get("name"), "version": selected.get("version"), "capabilities": selected.get("capabilities", [])}, "selection_reason": "Selected only from explicitly allowlisted tools."}

    def _from_row(self, row: Any) -> GovernorDecision:
        def decode(name: str, default: Any) -> Any:
            return json.loads(row[name] or json.dumps(default))
        return GovernorDecision(row["id"], row["status"], decode("interpretation_json", {}), decode("subtasks_json", []), decode("selected_advisors_json", []), decode("opinions_json", []), decode("evidence_json", []), row["fused_answer"], decode("conflict_json", {}), decode("model_selection_json", {}), decode("tool_selection_json", {}), decode("execution_plan_json", {}), decode("questions_json", {}), float(row["cost"]), int(row["latency_ms"]))

    def _persist(self, decision: GovernorDecision, request: GovernorRequest) -> None:
        now = self._now()
        key = request.idempotency_key or decision.decision_id
        self.db.execute("INSERT OR REPLACE INTO governor_decisions(id,idempotency_key,task,status,interpretation_json,subtasks_json,selected_advisors_json,opinions_json,evidence_json,fused_answer,conflict_json,model_selection_json,tool_selection_json,execution_plan_json,questions_json,cost,latency_ms,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (decision.decision_id, key, request.task, decision.status, json.dumps(decision.interpretation, ensure_ascii=False), json.dumps(decision.subtasks, ensure_ascii=False), json.dumps(decision.selected_advisors, ensure_ascii=False), json.dumps(decision.opinions, ensure_ascii=False), json.dumps(decision.evidence, ensure_ascii=False), decision.fused_answer, json.dumps(decision.conflict, ensure_ascii=False), json.dumps(decision.model_selection, ensure_ascii=False), json.dumps(decision.tool_selection, ensure_ascii=False), json.dumps(decision.execution_plan, ensure_ascii=False), json.dumps(decision.questions, ensure_ascii=False), decision.cost, decision.latency_ms, now))
        for opinion in decision.opinions:
            self.db.execute("INSERT INTO governor_consultations(id,decision_id,advisor_id,status,answer,confidence,evidence_json,cost,latency_ms,retry_count,error_code,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (f"consult-{uuid.uuid4().hex}", decision.decision_id, opinion["advisor_id"], "SUCCESS" if opinion["success"] else "FAILED", opinion["answer"], opinion["confidence"], json.dumps(opinion["evidence"], ensure_ascii=False), opinion["cost"], opinion["latency_ms"], opinion.get("retry_count", 0), opinion.get("error_code"), now))
            self.db.execute("UPDATE governor_advisors SET total_consultations=total_consultations+1,successes=successes+?,total_cost=total_cost+?,total_latency_ms=total_latency_ms+?,total_affinity=total_affinity+?,total_agreement=total_agreement+?,updated_at=? WHERE id=?", (int(opinion["success"]), opinion["cost"], opinion["latency_ms"], next((item["task_affinity"] for item in decision.selected_advisors if item["advisor_id"] == opinion["advisor_id"]), 0.0), 1.0 if opinion["agreement_key"] and decision.status == "CONFIDENT" else 0.0, now, opinion["advisor_id"]))

    def decide(self, request: GovernorRequest, *, consultation: Consultation | None = None, advisors: Iterable[AdvisorDescriptor] | None = None, tools: list[dict[str, Any]] | None = None) -> GovernorDecision:
        if request.idempotency_key:
            existing = self.db.execute("SELECT * FROM governor_decisions WHERE idempotency_key=?", (request.idempotency_key,)).fetchone()
            if existing is not None:
                if existing["task"] != request.task:
                    raise GovernorBlocked("IDEMPOTENCY_CONFLICT", "The Governor idempotency key is already bound to a different task.")
                return self._from_row(existing)
        started = time.perf_counter()
        interpretation, subtasks = self.interpret(request)
        descriptors = self._descriptors(request, advisors)
        for descriptor in descriptors:
            self.register_advisor(descriptor)
        if not descriptors:
            decision = GovernorDecision(f"decision-{uuid.uuid4().hex}", "BLOCKED", interpretation, subtasks, [], [], [], None, {"detected": False, "reason": "No eligible advisor satisfies the requested capability within the bounded budget."}, self._select_model(request), self._select_tool(request, tools), {"status": "STOP", "reason": "No eligible advisor."}, {"need_another_advisor": True, "need_deeper_evidence": True, "should_retry": False, "should_escalate": True, "should_stop": True}, 0.0, int((time.perf_counter() - started) * 1000))
            self._persist(decision, request)
            self._audit("decide", decision.status, "Governor stopped because no eligible advisor was available.", {"task": request.task}, decision.decision_id)
            return decision
        if request.risk_level.lower() == "critical" and not request.context.get("human_approved"):
            decision = GovernorDecision(f"decision-{uuid.uuid4().hex}", "BLOCKED", interpretation, subtasks, [], [], [], None, {"detected": False}, self._select_model(request), self._select_tool(request, tools), {"status": "STOP", "reason": "Critical-risk task requires explicit human approval before orchestration."}, {"need_another_advisor": False, "need_deeper_evidence": False, "should_retry": False, "should_escalate": True, "should_stop": True}, 0.0, int((time.perf_counter() - started) * 1000))
            self._persist(decision, request)
            self._audit("decide", decision.status, "Governor blocked a critical-risk task without human approval.", {"task": request.task}, decision.decision_id)
            return decision
        callback = consultation or (lambda descriptor, req: {"answer": f"No local consultation adapter is configured for {descriptor.name}.", "confidence": 0.0, "evidence": [], "success": False, "retryable": False, "error_code": "CONSULTATION_ADAPTER_UNAVAILABLE"})
        opinions = self._consult_parallel(descriptors, request, callback)
        retry_count = 0
        if request.retry_budget > 0:
            opinions_by_advisor = {opinion.advisor_id: opinion for opinion in opinions}
            retry_descriptors = [descriptor for descriptor in descriptors if not opinions_by_advisor.get(descriptor.advisor_id, AdvisorOpinion(descriptor.advisor_id, "", 0.0)).success and opinions_by_advisor.get(descriptor.advisor_id, AdvisorOpinion(descriptor.advisor_id, "", 0.0)).retryable]
            if retry_descriptors:
                retry_count = 1
                opinions.extend(self._consult_parallel(retry_descriptors, request, callback))
        status, answer, evidence, conflict, questions = self._fuse(opinions)
        total_cost = sum(item.cost for item in opinions)
        if total_cost > request.budget:
            status = "BLOCKED"
            answer = None
            questions.update({"should_stop": True, "should_escalate": True})
            execution_status = "STOP"
            execution_reason = "Consultation cost exceeded the declared Governor budget."
        elif status == "CONFIDENT":
            execution_status = "READY"
            execution_reason = "Independent advisors agreed with sufficient evidence and confidence."
        elif status == "BLOCKED":
            execution_status = "STOP"
            execution_reason = "Governor cannot produce a safe executable plan from the available consultations."
        else:
            execution_status = "REVIEW"
            execution_reason = "Human review or deeper evidence is required before execution."
        model_selection = self._select_model(request)
        tool_selection = self._select_tool(request, tools)
        if tool_selection.get("status") == "BLOCKED":
            status = "BLOCKED"
            answer = None
            execution_status = "STOP"
            execution_reason = tool_selection["reason"]
        model_selection["fallback"] = "Use the next eligible descriptor/model only after the selected path reports a retryable failure; never retry indefinitely."
        execution_plan = {"status": execution_status, "reason": execution_reason, "subtasks": subtasks, "retry_count": retry_count, "risk_level": interpretation["risk_level"], "requires_human_review": status in {"UNCERTAIN", "UNRESOLVED", "BLOCKED"}, "fallback_available": len(descriptors) > 1 or model_selection.get("status") == "SELECTED"}
        selected_records = [{"advisor_id": item.advisor_id, "name": item.name, "role": item.role, "task_affinity": item.task_affinity, "cost_per_consult": item.cost_per_consult, "expected_latency_ms": item.expected_latency_ms, "selection_reason": "Capability match ranked by task affinity, cost, latency, and recorded reputation."} for item in descriptors]
        decision = GovernorDecision(f"decision-{uuid.uuid4().hex}", status, interpretation, subtasks, selected_records, [item.as_dict() | {"retry_count": retry_count if not item.success else 0} for item in opinions], evidence, answer, conflict, model_selection, tool_selection, execution_plan, questions, total_cost, int((time.perf_counter() - started) * 1000))
        self._persist(decision, request)
        self._audit("decide", decision.status, "Governor created a durable decision ledger entry.", {"task": request.task, "advisor_count": len(descriptors), "cost": total_cost, "questions": questions}, decision.decision_id)
        return decision

    def record_outcome(self, *, decision_id: str, success: bool, accuracy: float, task_affinity: float, agreement: float, note: str = "") -> dict[str, Any]:
        accuracy = max(0.0, min(float(accuracy), 1.0))
        task_affinity = max(0.0, min(float(task_affinity), 1.0))
        agreement = max(0.0, min(float(agreement), 1.0))
        row = self.db.execute("SELECT id FROM governor_decisions WHERE id=?", (decision_id,)).fetchone()
        if row is None:
            raise GovernorBlocked("DECISION_NOT_FOUND", "Governor outcome cannot be recorded for an unknown decision.")
        outcome_id = f"outcome-{uuid.uuid4().hex}"
        self.db.execute("INSERT INTO governor_outcomes(id,decision_id,success,accuracy,task_affinity,agreement,note,created_at) VALUES(?,?,?,?,?,?,?,?)", (outcome_id, decision_id, int(success), accuracy, task_affinity, agreement, note[:2000], self._now()))
        self.db.execute("UPDATE governor_advisors SET total_accuracy=total_accuracy+?,total_affinity=total_affinity+?,total_agreement=total_agreement+?,updated_at=? WHERE id IN (SELECT advisor_id FROM governor_consultations WHERE decision_id=?)", (accuracy, task_affinity, agreement, self._now(), decision_id))
        self._audit("outcome", "RECORDED", "Governor decision outcome and advisor reputation metrics were recorded.", {"decision_id": decision_id, "success": success, "accuracy": accuracy, "task_affinity": task_affinity, "agreement": agreement}, decision_id)
        return {"outcome_id": outcome_id, "decision_id": decision_id, "success": success, "accuracy": accuracy, "task_affinity": task_affinity, "agreement": agreement}

    def get_decision(self, decision_id: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM governor_decisions WHERE id=?", (decision_id,)).fetchone()
        if row is None:
            raise GovernorBlocked("DECISION_NOT_FOUND", "Governor decision was not found.")
        result = dict(row)
        for field_name in ("interpretation_json", "subtasks_json", "selected_advisors_json", "opinions_json", "evidence_json", "conflict_json", "model_selection_json", "tool_selection_json", "execution_plan_json", "questions_json"):
            result[field_name[:-5]] = json.loads(result.pop(field_name) or "{}")
        return result
