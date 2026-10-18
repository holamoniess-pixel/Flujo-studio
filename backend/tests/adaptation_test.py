from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.adaptation import AdaptationBlocked, AdaptationController, AdaptationRequest
from backend.app.db import migrate


class Events:
    def __init__(self) -> None:
        self.items = []

    def audit(self, **kwargs):
        self.items.append(kwargs)


def make_controller():
    db = sqlite3.connect(":memory:", isolation_level=None)
    db.row_factory = sqlite3.Row
    migrate(db)
    db.execute("INSERT INTO projects(id,name,slug,created_at,updated_at) VALUES('p1','P1','p1','now','now')")
    events = Events()
    return db, AdaptationController(db, events), events


def full_cases(passed: bool = True):
    return [{"kind": kind, "case_id": kind, "passed": passed, "reason": "fixture"} for kind in ("golden", "regression", "adversarial", "fresh_session")]


def base_request(**overrides):
    values = dict(project_id="p1", task="improve intro", feedback="The revised intro retained viewers longer.", candidate_type="prompt", scope="PROJECT", proposal={"template": "evidence-backed intro v2"}, evidence_ids=("e1", "e2"))
    values.update(overrides)
    return AdaptationRequest(**values)


def test_experience_candidate_compile_evaluate_promote_rollback_drift_retire():
    db, controller, events = make_controller()
    experience = controller.record_experience(base_request())
    assert experience["status"] == "RECORDED" and experience["scope"] == "PROJECT"
    attribution = controller.attribute_feedback(experience["experience_id"], outcome="accepted", quality=0.9, attribution="task:intro-review", evidence_ids=["e3"])
    assert attribution["status"] == "ATTRIBUTED"
    candidate = controller.extract_candidate(base_request(), experience_id=experience["experience_id"])
    assert candidate["status"] == "PROPOSED" and candidate["current_version"] == 0
    compiled = controller.compile_candidate(candidate["id"])
    assert compiled["status"] == "COMPILED" and compiled["compiled"]["weights_modified"] is False
    evaluation = controller.evaluate_candidate(candidate["id"], full_cases())
    assert evaluation.status == "PASS" and evaluation.score == 1.0 and evaluation.missing_kinds == []
    promoted = controller.promote(candidate["id"], reviewer_id="human-1", reason="All required local gates passed.")
    assert promoted["status"] == "ACTIVE" and promoted["current_version"] == 1
    controller.evaluate_candidate(candidate["id"], full_cases())
    promoted_v2 = controller.promote(candidate["id"], reviewer_id="human-2", reason="Second evidence-backed version approved.")
    assert promoted_v2["current_version"] == 2
    drift = controller.record_drift(candidate["id"], observed_score=0.3, threshold=0.7, evidence=["drift-case-1"])
    assert drift["state"] == "DRIFTED" and drift["automatic_rollback"] is False
    rolled = controller.rollback(candidate["id"], reviewer_id="human-3", reason="Rollback after measured drift.", target_version=1)
    assert rolled["status"] == "ROLLED_BACK" and rolled["current_version"] == 1
    retired = controller.retire(candidate["id"], reviewer_id="human-4", reason="Recipe superseded.")
    assert retired["status"] == "RETIRED"
    assert len(events.items) >= 7 and all(item["category"] == "adaptation" for item in events.items)
    assert db.execute("SELECT COUNT(*) FROM adaptation_versions").fetchone()[0] == 2


def test_scopes_and_global_consent_are_fail_closed():
    _, controller, _ = make_controller()
    for scope in ("TASK", "PROJECT", "USER"):
        assert controller.record_experience(base_request(scope=scope))["scope"] == scope
    try:
        controller.record_experience(base_request(scope="GLOBAL"))
    except AdaptationBlocked as exc:
        assert exc.code == "GLOBAL_CONSENT_REQUIRED"
    else:
        raise AssertionError("global learning without consent must block")
    granted = base_request(scope="GLOBAL", consent={"consent_state": "GRANTED", "project_scope": "GLOBAL_ALLOWED"})
    assert controller.record_experience(granted)["scope"] == "GLOBAL"


def test_evaluation_near_miss_fail_promotion_gate_and_weight_block():
    _, controller, _ = make_controller()
    candidate = controller.extract_candidate(base_request())
    near = controller.evaluate_candidate(candidate["id"], [{"kind": "golden", "passed": True}])
    assert near.status == "NEAR_MISS" and set(near.missing_kinds) == {"adversarial", "fresh_session", "regression"}
    try:
        controller.promote(candidate["id"], reviewer_id="human", reason="not enough")
    except AdaptationBlocked as exc:
        assert exc.code == "PROMOTION_EVALUATION_REQUIRED"
    else:
        raise AssertionError("near-miss candidate must not promote")
    failed = controller.evaluate_candidate(candidate["id"], full_cases(passed=False))
    assert failed.status == "FAIL" and failed.regressions
    try:
        controller.extract_candidate(base_request(proposal={"model_weights": "mutate"}))
    except AdaptationBlocked as exc:
        assert exc.code == "MODEL_WEIGHTS_FORBIDDEN"
    else:
        raise AssertionError("model-weight mutation must never be accepted")


def test_recipe_tool_learning_and_model_affinity():
    _, controller, _ = make_controller()
    recipe = controller.extract_candidate(base_request(candidate_type="recipe", recipe_name="scene_recipe", proposal={"steps": ["probe", "render"]}))
    tool = controller.extract_candidate(base_request(candidate_type="tool", tool_name="safe-tool", proposal={"arguments": {"mode": "bounded"}}))
    assert recipe["candidate_type"] == "recipe" and tool["candidate_type"] == "tool"
    first = controller.record_model_outcome(model_id="model-a", task_key="intro", success=True, quality=0.9, latency_ms=120, cost=0.02)
    second = controller.record_model_outcome(model_id="model-a", task_key="intro", success=False, quality=0.4, latency_ms=200, cost=0.03)
    assert first[0]["trials"] == 1 and second[0]["trials"] == 2 and second[0]["success_rate"] == 0.5


def main() -> None:
    test_experience_candidate_compile_evaluate_promote_rollback_drift_retire()
    test_scopes_and_global_consent_are_fail_closed()
    test_evaluation_near_miss_fail_promotion_gate_and_weight_block()
    test_recipe_tool_learning_and_model_affinity()
    print("adaptation: passed; evidence, attribution, scopes, candidate compiler, golden/regression/adversarial/fresh-session evaluation, human promotion, rollback, drift, retirement, recipe/tool learning, model affinity, and no-weight mutation verified")


if __name__ == "__main__":
    main()
