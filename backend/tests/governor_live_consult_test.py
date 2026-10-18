from __future__ import annotations

import hashlib
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.db import migrate
from backend.app.gateway import GatewayError
from backend.app.governor import AdvisorDescriptor, GovernorBlocked, GovernorController, GovernorRequest
from backend.app.governor.live_consult import gateway_consult, simulator_consult


def make_db():
    db = sqlite3.connect(":memory:", isolation_level=None)
    db.row_factory = sqlite3.Row
    migrate(db)
    return db


class Events:
    def __init__(self) -> None:
        self.items = []

    def audit(self, **kwargs):
        self.items.append(kwargs)


def advisors() -> list[AdvisorDescriptor]:
    return [
        AdvisorDescriptor("a1", "Advisor One", "analysis", ("text_generation",), 0.95, 0.10, 100),
        AdvisorDescriptor("a2", "Advisor Two", "analysis", ("text_generation",), 0.90, 0.10, 100),
    ]


def test_simulator_mode_delegates() -> None:
    gateway = MagicMock()
    gateway.health.return_value = {"simulator_only": True, "status": "healthy"}
    advisor = advisors()[0]
    request = GovernorRequest("plan a launch", idempotency_key="k1")
    opinion = gateway_consult(gateway, advisor, request)
    assert opinion["simulator_only"] is True
    assert opinion["confidence"] == 0.76
    assert opinion["agreement_key"] == "simulator-consensus"
    assert opinion["cost"] == advisor.cost_per_consult
    digest = hashlib.sha256(f"{advisor.advisor_id}|{request.task}".encode()).hexdigest()[:16]
    assert opinion["evidence"] == [f"simulator-evidence:{digest}"]
    assert opinion["answer"] == f"simulated {advisor.role} recommendation for the requested task"
    gateway.invoke.assert_not_called()
    # Shape identical to direct simulator_consult.
    assert opinion == simulator_consult(advisor, request)


def test_live_mode_normalizes() -> None:
    gateway = MagicMock()
    gateway.health.return_value = {"simulator_only": False, "status": "healthy"}
    result = MagicMock()
    result.output = {
        "answer": "Choose PLAN X for the launch",
        "confidence": 2.5,
        "evidence": ["e1", "e2", "e3", "e4", "e5", "e6", "  "],
    }
    gateway.invoke.return_value = result
    advisor = advisors()[0]
    request = GovernorRequest("plan a launch", idempotency_key="k-live")
    opinion = gateway_consult(gateway, advisor, request)
    assert opinion["simulator_only"] is False
    assert opinion["answer"] == "Choose PLAN X for the launch"
    assert opinion["confidence"] == 1.0  # clamped
    assert opinion["evidence"] == ["e1", "e2", "e3", "e4", "e5"]  # truncated to 5
    assert opinion["cost"] == advisor.cost_per_consult
    assert opinion["agreement_key"] == "choose plan x for the launch"[:160]
    assert opinion["success"] is True
    # Verify invoke wiring: capability mapped, prompt compact, idempotency key namespaced.
    _, kwargs = gateway.invoke.call_args
    assert kwargs["capability"] == "text_generation"
    payload = kwargs.get("payload", kwargs.get("input"))
    assert payload["advisor_id"] == "a1"
    assert payload["role"] == "analysis"
    assert len(payload["prompt"]) < 1500
    assert "analysis" in payload["prompt"]
    assert kwargs["idempotency_key"] == "governor:k-live:a1"


def test_live_mode_gateway_error() -> None:
    gateway = MagicMock()
    gateway.health.return_value = {"simulator_only": False}
    gateway.invoke.side_effect = GatewayError("GATEWAY_AUTH_FAILED", "rejected", retryable=False)
    opinion = gateway_consult(gateway, advisors()[0], GovernorRequest("do work", idempotency_key="k-err"))
    assert opinion["success"] is False
    assert opinion["error_code"] == "GATEWAY_AUTH_FAILED"
    assert opinion["retryable"] is False
    assert opinion["answer"] == ""
    assert opinion["confidence"] == 0.0
    assert opinion["simulator_only"] is False


def test_unsupported_capability_blocked() -> None:
    gateway = MagicMock()
    gateway.health.return_value = {"simulator_only": False}
    try:
        gateway_consult(gateway, advisors()[0], GovernorRequest("weird", required_capability="nope"))
    except GovernorBlocked as exc:
        assert exc.code == "CONSULTATION_CAPABILITY_UNAVAILABLE"
    else:
        raise AssertionError("expected GovernorBlocked")


def test_end_to_end_confident_live() -> None:
    db = make_db()
    gateway = MagicMock()
    gateway.health.return_value = {"simulator_only": False}

    def fake_invoke(capability=None, payload=None, input=None, idempotency_key=""):
        result = MagicMock()
        result.output = {
            "answer": "agree on the evidence-backed plan",
            "confidence": 0.9,
            "evidence": ["live-source-a", "live-source-b"],
        }
        return result

    gateway.invoke.side_effect = fake_invoke
    controller = GovernorController(db, Events(), gateway)
    request = GovernorRequest("plan campaign and validate evidence", budget=1.0, max_advisors=2, idempotency_key="live-e2e")
    decision = controller.decide(
        request,
        consultation=lambda advisor, req: gateway_consult(gateway, advisor, req),
        advisors=advisors(),
    )
    assert decision.status == "CONFIDENT"
    assert decision.fused_answer == "agree on the evidence-backed plan"
    assert len(decision.opinions) == 2
    assert all(op["simulator_only"] is False for op in decision.opinions)


def test_uncredentialed_gateway_uses_labeled_simulator_fallback() -> None:
    # Models advertised without a credential (keyless free tier) must NOT
    # route consults to the live path: the labeled simulator fallback applies.
    gateway = MagicMock()
    gateway.health.return_value = {"simulator_only": False, "credentialed": False}
    advisor = advisors()[0]
    request = GovernorRequest("plan a launch", idempotency_key="k-uncred")
    opinion = gateway_consult(gateway, advisor, request)
    assert opinion["simulator_only"] is True
    assert opinion["reason"] == "no provider key configured"
    gateway.invoke.assert_not_called()


def test_simulator_fallback_carries_explicit_reason() -> None:
    advisor = advisors()[0]
    opinion = simulator_consult(advisor, GovernorRequest("plan a launch"))
    assert opinion["simulator_only"] is True
    assert opinion["reason"] == "no provider key configured"


def main() -> None:
    test_simulator_mode_delegates()
    test_uncredentialed_gateway_uses_labeled_simulator_fallback()
    test_simulator_fallback_carries_explicit_reason()
    test_live_mode_normalizes()
    test_live_mode_gateway_error()
    test_unsupported_capability_blocked()
    test_end_to_end_confident_live()
    print("governor_live_consult: passed")


if __name__ == "__main__":
    main()
