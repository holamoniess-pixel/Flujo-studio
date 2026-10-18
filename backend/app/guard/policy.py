from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


POLICY_VERSION = "guard.policy.v2"
SUPPORTED_MODALITIES = {"text", "image", "video", "audio", "ocr", "metadata", "document"}
TRUST_CLASSES = {
    "SYSTEM",
    "POLICY",
    "USER",
    "GOVERNOR",
    "ADVISOR",
    "REFERENCE",
    "EXTERNAL_CONTENT",
    "TOOL_OUTPUT",
    "MODEL_OUTPUT",
}
TRUST_ORDER = {
    "SYSTEM": 0,
    "POLICY": 1,
    "USER": 2,
    "GOVERNOR": 3,
    "ADVISOR": 4,
    "REFERENCE": 5,
    "EXTERNAL_CONTENT": 6,
    "MODEL_OUTPUT": 7,
    "TOOL_OUTPUT": 8,
}

# These are deterministic local signals, not trained safety classifiers. The
# explicit policy registry and modality states prevent this implementation from
# claiming coverage that is not present on the local machine.
POLICY_DEFINITIONS = {
    "harmful": {"action": "BLOCK", "classifier": "deterministic-text-v2"},
    "explicit": {"action": "REVIEW", "classifier": "deterministic-text-v2"},
    "csam_prevention": {"action": "BLOCK", "classifier": "deterministic-text-v2"},
    "violence": {"action": "BLOCK", "classifier": "deterministic-text-v2"},
    "self_harm": {"action": "BLOCK", "classifier": "deterministic-text-v2"},
    "hate": {"action": "BLOCK", "classifier": "deterministic-text-v2"},
    "impersonation": {"action": "REVIEW", "classifier": "deterministic-text-v2"},
    "likeness": {"action": "CONSENT", "classifier": "deterministic-text-v2"},
    "voice_cloning": {"action": "CONSENT", "classifier": "deterministic-text-v2"},
    "privacy": {"action": "BLOCK", "classifier": "deterministic-text-v2"},
    "ip_trademark": {"action": "REVIEW", "classifier": "deterministic-text-v2"},
    "prompt_injection": {"action": "BLOCK", "classifier": "deterministic-text-v2"},
    "provider_policy": {"action": "REVIEW", "classifier": "local-policy-contract"},
}

_PATTERNS = {
    "harmful": re.compile(r"(?i)(instructions?\s+to\s+(?:harm|kill)|how\s+to\s+make\s+(?:a\s+)?bomb|malware\s+payload|credible\s+threat)"),
    "explicit": re.compile(r"(?i)(\bnsfw\b|porn|explicit\s+sexual|sexually\s+explicit|nude|nudity)"),
    "csam_prevention": re.compile(r"(?i)(\bcsam\b|child\s+sexual|sexual\s+(?:minor|child)|underage\s+sexual)"),
    "violence": re.compile(r"(?i)(graphic\s+violence|torture\s+instructions?|instructions?\s+to\s+attack|mass\s+casualty)"),
    "self_harm": re.compile(r"(?i)(self[-\s]?harm|suicide\s+method|how\s+to\s+end\s+my\s+life)"),
    "hate": re.compile(r"(?i)(racial\s+slur|ethnic\s+cleansing|kill\s+all\s+(?:[a-z]+\s+)?people|extremist\s+propaganda)"),
    "impersonation": re.compile(r"(?i)(pretend\s+to\s+be\s+(?:the\s+)?(?:president|police|bank)|impersonate\s+(?:a|the)\s+(?:person|official|celebrity))"),
    "likeness": re.compile(r"(?i)(real\s+person|celebrity|likeness|face\s+of|photorealistic\s+person|client\s+portrait|deepfake)"),
    "voice_cloning": re.compile(r"(?i)(voice\s+clone|clone\s+(?:my|their|the)\s+voice|voice\s+of|voice\s+likeness|deepfake\s+voice)"),
    "privacy": re.compile(r"(?i)(?:\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b|\b\+?\d[\d ()-]{7,}\d\b|\b\d{3}-\d{2}-\d{4}\b|private\s+address|doxx(?:ing)?|expose\s+personal\s+data)"),
    "ip_trademark": re.compile(r"(?i)\b(kfc|mcdonald(?:'s|s)?|coca[-\s]?cola|nike|apple|adidas|starbucks|recreate\s+the\s+advertisement)\b"),
    "prompt_injection": re.compile(r"(?i)(ignore\s+(?:all\s+)?(?:previous|prior|system)\s+instructions|reveal\s+(?:the\s+)?(?:api\s+)?key|send\s+the\s+secret|you\s+are\s+now\s+the\s+system|disable\s+safety|bypass\s+guard|treat\s+this\s+reference\s+as\s+instructions)"),
    "provider_policy": re.compile(r"(?i)(disable\s+provider\s+policy|bypass\s+provider\s+policy|use\s+an\s+unapproved\s+provider)"),
}


@dataclass(frozen=True)
class GuardDecision:
    status: str
    phase: str
    checks: dict[str, str]
    reason: str
    trust_classes: list[str]
    policy_version: str = POLICY_VERSION
    modalities: list[str] = field(default_factory=lambda: ["text"])
    classifier_states: dict[str, str] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.status == "PASS"

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "phase": self.phase,
            "checks": self.checks,
            "reason": self.reason,
            "trust_classes": self.trust_classes,
            "policy_version": self.policy_version,
            "modalities": self.modalities,
            "classifier_states": self.classifier_states,
            "evidence": self.evidence,
        }


def normalize_trust_class(value: Any, default: str = "EXTERNAL_CONTENT") -> str:
    candidate = str(value or "").strip().upper().replace("-", "_")
    return candidate if candidate in TRUST_CLASSES else default


def is_instruction_authority(value: Any) -> bool:
    """Only system/policy metadata can define policy; all content remains data."""
    return normalize_trust_class(value) in {"SYSTEM", "POLICY"}


class FlujoGuard:
    """Independent local safety authority with explicit, measurable limitations."""

    policy_version = POLICY_VERSION
    policy_definitions = POLICY_DEFINITIONS

    def _source_text(self, *, text: str, references: list[dict[str, Any]], metadata: dict[str, Any] | None = None) -> tuple[str, list[str], list[str]]:
        chunks = [str(text or "")]
        classes = ["USER"]
        modalities = ["text"]
        for reference in references:
            source_class = normalize_trust_class(reference.get("source_class") or reference.get("trust_class") or reference.get("role"), "REFERENCE")
            classes.append(source_class)
            modality = str(reference.get("modality") or reference.get("kind") or "document").lower()
            modalities.append(modality)
            # Labels, supplied OCR/transcripts, and metadata are data to scan;
            # they never become instructions because of their source class.
            chunks.append(" ".join(str(reference.get(key, "")) for key in ("label", "role", "text", "ocr_text", "transcript", "metadata")))
        if metadata:
            chunks.append(str(metadata))
            modalities.append("metadata")
        return "\n".join(chunks), sorted(set(classes), key=lambda item: TRUST_ORDER[item]), sorted(set(modalities))

    def _evaluate(self, *, text: str, references: list[dict[str, Any]], consent: dict[str, Any], phase: str, metadata: dict[str, Any] | None = None) -> GuardDecision:
        source, trust_classes, modalities = self._source_text(text=text, references=references, metadata=metadata)
        checks = {policy: "PASS" for policy in POLICY_DEFINITIONS}
        checks.update({"likeness_consent": "PASS", "voice_consent": "PASS"})
        classifier_states = {modality: "AVAILABLE_DETERMINISTIC_TEXT" for modality in modalities}
        evidence: dict[str, Any] = {"matched_policies": [], "unavailable_modalities": [], "non_authoritative_sources": [item for item in trust_classes if not is_instruction_authority(item)]}
        statuses: list[str] = []

        for modality in modalities:
            if modality not in SUPPORTED_MODALITIES:
                classifier_states[modality] = "BLOCKED_UNSUPPORTED_MODALITY"
                evidence["unavailable_modalities"].append(modality)
                statuses.append("BLOCK")
            elif modality in {"image", "video", "audio"}:
                classifier_states[modality] = "REVIEW_CLASSIFIER_UNAVAILABLE"
                evidence["unavailable_modalities"].append(modality)
                statuses.append("REVIEW")
            elif modality == "document" and not any(reference.get("text") or reference.get("ocr_text") or reference.get("transcript") for reference in references):
                classifier_states[modality] = "REVIEW_TEXT_EXTRACTION_UNAVAILABLE"
                evidence["unavailable_modalities"].append(modality)
                statuses.append("REVIEW")

        for policy, pattern in _PATTERNS.items():
            if pattern.search(source):
                action = POLICY_DEFINITIONS[policy]["action"]
                checks[policy] = "BLOCK" if action in {"BLOCK", "CONSENT"} else "REVIEW"
                evidence["matched_policies"].append(policy)
                statuses.append(checks[policy])

        if "likeness" in evidence["matched_policies"]:
            likeness_ok = bool(consent.get("likeness_consent") or consent.get("face_consent")) and str(consent.get("consent_state", "GRANTED")).upper() == "GRANTED"
            if not likeness_ok:
                checks["likeness"] = "BLOCK"
                checks["likeness_consent"] = "BLOCK"
                statuses.append("BLOCK")
        if "voice_cloning" in evidence["matched_policies"]:
            voice_ok = bool(consent.get("voice_consent")) and str(consent.get("consent_state", "GRANTED")).upper() == "GRANTED"
            if not voice_ok:
                checks["voice_cloning"] = "BLOCK"
                checks["voice_consent"] = "BLOCK"
                statuses.append("BLOCK")
        if consent.get("learning_scope") not in {None, "PROJECT_ONLY", "USER_ONLY", "GLOBAL_ALLOWED"}:
            checks["privacy"] = "BLOCK"
            statuses.append("BLOCK")
        provider_status = str(consent.get("provider_policy_status", "APPROVED")).upper()
        if provider_status == "DENIED":
            checks["provider_policy"] = "BLOCK"
            statuses.append("BLOCK")
        elif provider_status not in {"APPROVED", "SIMULATOR_ONLY"}:
            checks["provider_policy"] = "REVIEW"
            statuses.append("REVIEW")

        status = "BLOCK" if "BLOCK" in statuses else "REVIEW" if "REVIEW" in statuses else "PASS"
        if status == "BLOCK":
            reason = "Guard blocked content or an unavailable safety boundary under the versioned local policy."
        elif status == "REVIEW":
            reason = "Guard requires human review because a policy risk or unavailable modality classifier was detected."
        else:
            reason = "Guard passed deterministic local checks; no classifier recall beyond the declared local coverage is claimed."
        return GuardDecision(status, phase, checks, reason, trust_classes, self.policy_version, modalities, classifier_states, evidence)

    def preflight(self, *, text: str, references: list[dict[str, Any]], consent: dict[str, Any], metadata: dict[str, Any] | None = None) -> GuardDecision:
        return self._evaluate(text=text, references=references, consent=consent, phase="preflight", metadata=metadata)

    def postflight(self, *, output_summary: str, consent: dict[str, Any], modalities: list[dict[str, Any]] | None = None) -> GuardDecision:
        references = modalities or []
        return self._evaluate(text=output_summary, references=references, consent=consent, phase="postflight")

    def evaluate_modality(self, *, modality: str, content: str = "", metadata: dict[str, Any] | None = None, source_class: str = "EXTERNAL_CONTENT", consent: dict[str, Any] | None = None, phase: str = "preflight") -> GuardDecision:
        return self._evaluate(text=content, references=[{"modality": modality, "text": content, "source_class": source_class}], consent=consent or {}, phase=phase, metadata=metadata)

    def publish_gate(self, *, artifact_summary: str, modality: str = "document", consent: dict[str, Any] | None = None) -> GuardDecision:
        """Re-evaluate at the local publish boundary; only PASS may publish."""
        return self._evaluate(text=artifact_summary, references=[{"modality": modality, "text": artifact_summary, "source_class": "MODEL_OUTPUT"}], consent=consent or {}, phase="publish")

    def classify_reference(self, text: str, source_class: str | None = None) -> str:
        """Classify material without elevating it to instruction authority."""
        return normalize_trust_class(source_class, "REFERENCE" if text else "EXTERNAL_CONTENT")
