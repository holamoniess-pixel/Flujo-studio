from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


ADAPTATION_CONTRACT_VERSION = "adaptation.lifecycle.v1"
SCOPES = {"TASK", "PROJECT", "USER", "GLOBAL"}
CANDIDATE_TYPES = {"prompt", "skill", "routing", "tool", "recipe", "parameter"}
EVAL_KINDS = {"golden", "regression", "adversarial", "fresh_session"}
FORBIDDEN_WEIGHT_KEYS = {"weights", "model_weights", "fine_tune", "finetune", "checkpoint", "gradient"}


class AdaptationBlocked(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class AdaptationRequest:
    project_id: str
    task: str
    feedback: str
    candidate_type: str = "prompt"
    scope: str = "PROJECT"
    user_id: str | None = None
    model_id: str | None = None
    provider_id: str | None = None
    tool_name: str | None = None
    recipe_name: str | None = None
    proposal: dict[str, Any] = field(default_factory=dict)
    evidence_ids: tuple[str, ...] = ()
    consent: dict[str, Any] = field(default_factory=dict)
    outcome: str = ""


@dataclass(frozen=True)
class AdaptationEvaluation:
    evaluation_id: str
    candidate_id: str
    status: str
    score: float
    cases: list[dict[str, Any]]
    missing_kinds: list[str]
    regressions: list[str]
    reason: str
    contract_version: str = ADAPTATION_CONTRACT_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "evaluation_id": self.evaluation_id,
            "candidate_id": self.candidate_id,
            "status": self.status,
            "score": self.score,
            "cases": self.cases,
            "missing_kinds": self.missing_kinds,
            "regressions": self.regressions,
            "reason": self.reason,
            "contract_version": self.contract_version,
        }


class AdaptationController:
    def __init__(self, db: Any, events: Any | None = None) -> None:
        self.db = db
        self.events = events

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _audit(self, operation: str, status: str, message: str, metadata: dict[str, Any] | None = None, project_id: str | None = None) -> None:
        if self.events is not None:
            self.events.audit(category="adaptation", message=message, project_id=project_id, route="adaptation", operation=operation, status=status, metadata=metadata or {})

    def _json(self, value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    def _ensure_safe_payload(self, payload: dict[str, Any]) -> None:
        keys = {str(key).casefold() for key in payload.keys()}
        if keys & FORBIDDEN_WEIGHT_KEYS:
            raise AdaptationBlocked("MODEL_WEIGHTS_FORBIDDEN", "Adaptation cannot modify model weights or fine-tuning checkpoints.")
        encoded = self._json(payload).casefold()
        if any(token in encoded for token in ("model_weights", "fine_tune", "checkpoint", "gradient")):
            raise AdaptationBlocked("MODEL_WEIGHTS_FORBIDDEN", "Adaptation proposal contains a forbidden model-weight mutation marker.")

    def record_experience(self, request: AdaptationRequest) -> dict[str, Any]:
        scope = request.scope.upper()
        candidate_type = request.candidate_type.lower()
        if scope not in SCOPES:
            raise AdaptationBlocked("INVALID_SCOPE", "Adaptation scope must be TASK, PROJECT, USER, or GLOBAL.")
        if candidate_type not in CANDIDATE_TYPES:
            raise AdaptationBlocked("INVALID_CANDIDATE_TYPE", "Adaptation candidate type is not supported by the safe learning scope.")
        if not request.task.strip() or not request.feedback.strip():
            raise AdaptationBlocked("EXPERIENCE_INCOMPLETE", "An experience requires a task and non-empty feedback.")
        if scope == "GLOBAL" and not self._global_consent(request.consent):
            raise AdaptationBlocked("GLOBAL_CONSENT_REQUIRED", "Global learning requires explicit granted consent.")
        experience_id = f"experience-{uuid.uuid4().hex}"
        now = self._now()
        self.db.execute("INSERT INTO adaptation_experiences(id,project_id,user_id,scope,task,feedback,outcome,model_id,provider_id,tool_name,recipe_name,evidence_ids_json,metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (experience_id, request.project_id, request.user_id, scope, request.task.strip(), request.feedback.strip()[:10000], request.outcome[:2000], request.model_id, request.provider_id, request.tool_name, request.recipe_name, self._json(list(request.evidence_ids)), self._json({"candidate_type": candidate_type}), now))
        self._audit("experience", "RECORDED", "Adaptation experience recorded as evidence input.", {"experience_id": experience_id, "scope": scope, "candidate_type": candidate_type}, request.project_id)
        return {"experience_id": experience_id, "scope": scope, "status": "RECORDED", "evidence_backed": bool(request.evidence_ids)}

    def attribute_feedback(self, experience_id: str, *, outcome: str, quality: float, attribution: str, evidence_ids: list[str] | None = None) -> dict[str, Any]:
        row = self.db.execute("SELECT project_id FROM adaptation_experiences WHERE id=?", (experience_id,)).fetchone()
        if row is None:
            raise AdaptationBlocked("EXPERIENCE_NOT_FOUND", "Adaptation experience was not found.")
        quality = max(0.0, min(float(quality), 1.0))
        if not attribution.strip():
            raise AdaptationBlocked("ATTRIBUTION_REQUIRED", "Feedback attribution must identify the responsible task, model, tool, or recipe.")
        self.db.execute("INSERT INTO adaptation_feedback(id,experience_id,outcome,quality,attribution,evidence_ids_json,created_at) VALUES(?,?,?,?,?,?,?)", (f"feedback-{uuid.uuid4().hex}", experience_id, outcome[:2000], quality, attribution[:500], self._json(evidence_ids or []), self._now()))
        self.db.execute("UPDATE adaptation_experiences SET outcome=? WHERE id=?", (outcome[:2000], experience_id))
        self._audit("attribute", "RECORDED", "Feedback attribution recorded for adaptation evidence.", {"experience_id": experience_id, "quality": quality, "attribution": attribution[:120]}, row["project_id"])
        return {"experience_id": experience_id, "outcome": outcome, "quality": quality, "attribution": attribution, "status": "ATTRIBUTED"}

    def _global_consent(self, consent: dict[str, Any]) -> bool:
        return str(consent.get("consent_state", "")).upper() == "GRANTED" and str(consent.get("project_scope", consent.get("learning_scope", ""))).upper() == "GLOBAL_ALLOWED"

    def extract_candidate(self, request: AdaptationRequest, *, experience_id: str | None = None) -> dict[str, Any]:
        if not request.evidence_ids:
            raise AdaptationBlocked("EVIDENCE_REQUIRED", "A learned candidate requires at least one evidence identifier.")
        scope = request.scope.upper()
        if scope == "GLOBAL" and not self._global_consent(request.consent):
            raise AdaptationBlocked("GLOBAL_CONSENT_REQUIRED", "Global candidate extraction requires explicit granted consent.")
        self._ensure_safe_payload(request.proposal)
        if not request.proposal:
            raise AdaptationBlocked("PROPOSAL_REQUIRED", "Candidate extraction requires a concrete prompt, skill, routing, tool, recipe, or parameter proposal.")
        candidate_id = f"candidate-{uuid.uuid4().hex}"
        now = self._now()
        key = f"{request.project_id}:{scope}:{request.task.strip().casefold()}:{request.candidate_type.lower()}"
        self.db.execute("INSERT INTO adaptation_candidates(id,project_id,user_id,scope,candidate_type,task_key,proposal_json,evidence_ids_json,status,current_version,source_experience_id,consent_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (candidate_id, request.project_id, request.user_id, scope, request.candidate_type.lower(), key[:500], self._json(request.proposal), self._json(list(request.evidence_ids)), "PROPOSED", 0, experience_id, self._json(request.consent), now, now))
        self._audit("extract", "PROPOSED", "Evidence-backed adaptation candidate extracted.", {"candidate_id": candidate_id, "scope": scope, "candidate_type": request.candidate_type.lower(), "evidence_count": len(request.evidence_ids)}, request.project_id)
        return self.get_candidate(candidate_id)

    def compile_candidate(self, candidate_id: str) -> dict[str, Any]:
        candidate = self.get_candidate(candidate_id)
        self._ensure_safe_payload(candidate["proposal"])
        compiled = {"candidate_id": candidate_id, "candidate_type": candidate["candidate_type"], "scope": candidate["scope"], "payload": candidate["proposal"], "compiled_version": f"{candidate_id}:compile-1", "weights_modified": False}
        digest = hashlib.sha256(self._json(compiled).encode()).hexdigest()
        self.db.execute("UPDATE adaptation_candidates SET compiled_json=?,status=?,updated_at=? WHERE id=?", (self._json(compiled | {"sha256": digest}), "COMPILED", self._now(), candidate_id))
        self._audit("compile", "COMPILED", "Adaptation candidate compiled without model-weight mutation.", {"candidate_id": candidate_id, "sha256": digest, "weights_modified": False}, candidate["project_id"])
        return self.get_candidate(candidate_id)

    def evaluate_candidate(self, candidate_id: str, cases: list[dict[str, Any]]) -> AdaptationEvaluation:
        candidate = self.get_candidate(candidate_id)
        if candidate["status"] not in {"PROPOSED", "COMPILED", "EVALUATED", "NEAR_MISS", "DRIFTED", "ACTIVE"}:
            raise AdaptationBlocked("CANDIDATE_NOT_EVALUABLE", "Candidate status is not eligible for evaluation.")
        normalized: list[dict[str, Any]] = []
        for case in cases[:200]:
            kind = str(case.get("kind", "")).lower()
            if kind not in EVAL_KINDS:
                normalized.append({"kind": kind or "unknown", "passed": False, "reason": "Unsupported evaluation kind."})
                continue
            if "passed" in case:
                passed = bool(case["passed"])
                reason = str(case.get("reason", "Explicit test result."))
            elif "expected" in case and "observed" in case:
                passed = case["expected"] == case["observed"]
                reason = "Expected and observed values matched." if passed else "Expected and observed values differed."
            else:
                passed = False
                reason = "Evaluation case requires a passed flag or expected/observed pair."
            normalized.append({"case_id": str(case.get("case_id", f"case-{len(normalized)+1}")), "kind": kind, "passed": passed, "reason": reason[:1000], "fresh_session": kind == "fresh_session"})
        present = {case["kind"] for case in normalized}
        missing = sorted(EVAL_KINDS - present)
        regressions = [case["case_id"] for case in normalized if case["kind"] in {"regression", "adversarial"} and not case["passed"]]
        pass_count = sum(1 for case in normalized if case["passed"])
        score = pass_count / max(len(normalized), 1)
        failed = [case for case in normalized if not case["passed"]]
        if failed:
            status = "FAIL" if any(case["kind"] in {"golden", "regression", "adversarial"} for case in failed) else "NEAR_MISS"
            reason = "Required evaluation cases failed; promotion is blocked." if status == "FAIL" else "Only non-critical cases failed; human review is required."
        elif missing:
            status = "NEAR_MISS"
            reason = "Evaluation is incomplete; golden, regression, adversarial, and fresh-session coverage is required before promotion."
        else:
            status = "PASS"
            reason = "All required golden, regression, adversarial, and fresh-session cases passed."
        evaluation_id = f"evaluation-{uuid.uuid4().hex}"
        self.db.execute("INSERT INTO adaptation_evaluations(id,candidate_id,status,score,cases_json,missing_kinds_json,regressions_json,reason,created_at) VALUES(?,?,?,?,?,?,?,?,?)", (evaluation_id, candidate_id, status, score, self._json(normalized), self._json(missing), self._json(regressions), reason, self._now()))
        self.db.execute("UPDATE adaptation_candidates SET status=?,updated_at=? WHERE id=?", ("EVALUATED" if status == "PASS" else "NEAR_MISS" if status == "NEAR_MISS" else "FAILED", self._now(), candidate_id))
        self._audit("evaluate", status, "Adaptation candidate evaluation completed.", {"candidate_id": candidate_id, "evaluation_id": evaluation_id, "score": score, "missing_kinds": missing, "regressions": regressions}, candidate["project_id"])
        return AdaptationEvaluation(evaluation_id, candidate_id, status, score, normalized, missing, regressions, reason)

    def promote(self, candidate_id: str, *, reviewer_id: str, reason: str, consent: dict[str, Any] | None = None) -> dict[str, Any]:
        if not reviewer_id.strip() or not reason.strip():
            raise AdaptationBlocked("HUMAN_REVIEW_REQUIRED", "Promotion requires a named human reviewer and reason.")
        candidate = self.get_candidate(candidate_id)
        latest = self.db.execute("SELECT * FROM adaptation_evaluations WHERE candidate_id=? ORDER BY created_at DESC LIMIT 1", (candidate_id,)).fetchone()
        if latest is None or latest["status"] != "PASS":
            raise AdaptationBlocked("PROMOTION_EVALUATION_REQUIRED", "Only a candidate with a passing complete evaluation may be promoted.")
        if candidate["scope"] == "GLOBAL" and not self._global_consent(consent or candidate["consent"]):
            raise AdaptationBlocked("GLOBAL_CONSENT_REQUIRED", "Global promotion requires explicit granted consent.")
        next_version = int(candidate["current_version"]) + 1
        now = self._now()
        self.db.execute("INSERT INTO adaptation_versions(id,candidate_id,version,payload_json,status,reviewer_id,review_reason,created_at) VALUES(?,?,?,?,?,?,?,?)", (f"version-{uuid.uuid4().hex}", candidate_id, next_version, self._json(candidate["proposal"]), "ACTIVE", reviewer_id[:120], reason[:2000], now))
        self.db.execute("UPDATE adaptation_versions SET status='SUPERSEDED' WHERE candidate_id=? AND version<? AND status='ACTIVE'", (candidate_id, next_version))
        self.db.execute("UPDATE adaptation_candidates SET status='ACTIVE',current_version=?,updated_at=? WHERE id=?", (next_version, now, candidate_id))
        self._audit("promote", "ACTIVE", "Human-approved adaptation candidate promoted.", {"candidate_id": candidate_id, "version": next_version, "reviewer_id": reviewer_id, "reason": reason[:200]}, candidate["project_id"])
        return self.get_candidate(candidate_id) | {"promotion": {"reviewer_id": reviewer_id, "reason": reason, "version": next_version}}

    def rollback(self, candidate_id: str, *, reviewer_id: str, reason: str, target_version: int | None = None) -> dict[str, Any]:
        if not reviewer_id.strip() or not reason.strip():
            raise AdaptationBlocked("HUMAN_REVIEW_REQUIRED", "Rollback requires a named human reviewer and reason.")
        candidate = self.get_candidate(candidate_id)
        versions = self.db.execute("SELECT * FROM adaptation_versions WHERE candidate_id=? ORDER BY version DESC", (candidate_id,)).fetchall()
        if not versions:
            raise AdaptationBlocked("NO_PROMOTED_VERSION", "No promoted adaptation version exists to roll back.")
        target = target_version if target_version is not None else max(1, int(candidate["current_version"]) - 1)
        target_row = next((row for row in versions if int(row["version"]) == target), None)
        if target_row is None:
            raise AdaptationBlocked("ROLLBACK_TARGET_NOT_FOUND", "Requested rollback target version was not found.")
        now = self._now()
        self.db.execute("UPDATE adaptation_versions SET status='ROLLED_BACK' WHERE candidate_id=? AND status='ACTIVE'", (candidate_id,))
        self.db.execute("UPDATE adaptation_versions SET status='ACTIVE' WHERE id=?", (target_row["id"],))
        self.db.execute("UPDATE adaptation_candidates SET status='ROLLED_BACK',current_version=?,updated_at=? WHERE id=?", (target, now, candidate_id))
        self._audit("rollback", "ROLLED_BACK", "Human-approved adaptation rollback completed.", {"candidate_id": candidate_id, "target_version": target, "reviewer_id": reviewer_id, "reason": reason[:200]}, candidate["project_id"])
        return self.get_candidate(candidate_id) | {"rollback": {"target_version": target, "reviewer_id": reviewer_id, "reason": reason}}

    def record_drift(self, candidate_id: str, *, observed_score: float, threshold: float, evidence: list[str], reviewer_id: str | None = None) -> dict[str, Any]:
        candidate = self.get_candidate(candidate_id)
        observed_score = max(0.0, min(float(observed_score), 1.0))
        threshold = max(0.0, min(float(threshold), 1.0))
        state = "DRIFTED" if observed_score < threshold else "STABLE"
        drift_id = f"drift-{uuid.uuid4().hex}"
        self.db.execute("INSERT INTO adaptation_drift(id,candidate_id,observed_score,threshold,state,evidence_json,reviewer_id,created_at) VALUES(?,?,?,?,?,?,?,?)", (drift_id, candidate_id, observed_score, threshold, state, self._json(evidence), reviewer_id, self._now()))
        if state == "DRIFTED":
            self.db.execute("UPDATE adaptation_candidates SET status='DRIFTED',updated_at=? WHERE id=?", (self._now(), candidate_id))
        self._audit("drift", state, "Adaptation drift measurement recorded; no automatic rollback was performed.", {"candidate_id": candidate_id, "observed_score": observed_score, "threshold": threshold, "evidence_count": len(evidence), "automatic_rollback": False}, candidate["project_id"])
        return {"drift_id": drift_id, "candidate_id": candidate_id, "state": state, "observed_score": observed_score, "threshold": threshold, "automatic_rollback": False}

    def retire(self, candidate_id: str, *, reviewer_id: str, reason: str) -> dict[str, Any]:
        if not reviewer_id.strip() or not reason.strip():
            raise AdaptationBlocked("HUMAN_REVIEW_REQUIRED", "Retirement requires a named human reviewer and reason.")
        candidate = self.get_candidate(candidate_id)
        self.db.execute("UPDATE adaptation_candidates SET status='RETIRED',updated_at=? WHERE id=?", (self._now(), candidate_id))
        self._audit("retire", "RETIRED", "Human-approved adaptation candidate retired.", {"candidate_id": candidate_id, "reviewer_id": reviewer_id, "reason": reason[:200]}, candidate["project_id"])
        return self.get_candidate(candidate_id) | {"retirement": {"reviewer_id": reviewer_id, "reason": reason}}

    def record_model_outcome(self, *, model_id: str, task_key: str, success: bool, quality: float, latency_ms: int, cost: float) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM adaptation_model_affinity WHERE model_id=? AND task_key=?", (model_id, task_key)).fetchone()
        now = self._now()
        if row is None:
            self.db.execute("INSERT INTO adaptation_model_affinity(model_id,task_key,trials,successes,total_quality,total_latency_ms,total_cost,updated_at) VALUES(?,?,?,?,?,?,?,?)", (model_id, task_key, 1, int(success), max(0.0, min(float(quality), 1.0)), max(0, int(latency_ms)), max(0.0, float(cost)), now))
        else:
            self.db.execute("UPDATE adaptation_model_affinity SET trials=trials+1,successes=successes+?,total_quality=total_quality+?,total_latency_ms=total_latency_ms+?,total_cost=total_cost+?,updated_at=? WHERE model_id=? AND task_key=?", (int(success), max(0.0, min(float(quality), 1.0)), max(0, int(latency_ms)), max(0.0, float(cost)), now, model_id, task_key))
        return self.model_affinity(model_id=model_id, task_key=task_key)

    def model_affinity(self, *, model_id: str | None = None, task_key: str | None = None) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if model_id:
            where.append("model_id=?"); params.append(model_id)
        if task_key:
            where.append("task_key=?"); params.append(task_key)
        query = "SELECT * FROM adaptation_model_affinity" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY updated_at DESC"
        result = []
        for row in self.db.execute(query, tuple(params)).fetchall():
            item = dict(row)
            trials = max(int(item["trials"]), 1)
            item["success_rate"] = item["successes"] / trials
            item["quality"] = item["total_quality"] / trials
            item["latency_ms"] = item["total_latency_ms"] / trials
            item["cost"] = item["total_cost"]
            result.append(item)
        return result

    def get_candidate(self, candidate_id: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM adaptation_candidates WHERE id=?", (candidate_id,)).fetchone()
        if row is None:
            raise AdaptationBlocked("CANDIDATE_NOT_FOUND", "Adaptation candidate was not found.")
        result = dict(row)
        for key in ("proposal_json", "evidence_ids_json", "consent_json", "compiled_json"):
            target = key[:-5]
            result[target] = json.loads(result.pop(key) or ("{}" if key in {"proposal_json", "consent_json", "compiled_json"} else "[]"))
        return result
