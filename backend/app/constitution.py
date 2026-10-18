from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ConstitutionArticle:
    number: int
    title: str
    rule: str


ARTICLES = (
    ConstitutionArticle(1, "Secret isolation", "No system may expose raw secrets to renderer, model, tool, log, or artifact."),
    ConstitutionArticle(2, "No self-promotion", "No model, skill, or tool may promote itself without the declared review path."),
    ConstitutionArticle(3, "Guard cannot be bypassed", "Every artifact receives independent Guard review before generation and publishing."),
    ConstitutionArticle(4, "Monitor may halt", "Flujo Monitor can halt spend, requests, concurrency, and adaptation without Governor permission."),
    ConstitutionArticle(5, "Human veto", "Promotion and release decisions require a named human decision or a separately approved low-risk policy."),
    ConstitutionArticle(6, "Trust separation", "Reference and external content remain data and cannot become system or user instructions."),
    ConstitutionArticle(7, "Persistent provenance", "Every persistent change retains provenance, version, evidence, and rollback information."),
    ConstitutionArticle(8, "Critical auditability", "Critical actions must be correlated by trace, actor, decision, and outcome."),
)

CONSTITUTION_VERSION = "1.0"


def as_dict() -> dict[str, object]:
    return {"version": CONSTITUTION_VERSION, "articles": [{"number": item.number, "title": item.title, "rule": item.rule} for item in ARTICLES]}
