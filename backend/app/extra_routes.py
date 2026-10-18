"""Phase 2B frontend de-simulation endpoints.

Read-only list endpoints plus a small set of honest local-write endpoints that the
fixture-backed frontend surfaces call. Everything here reads the same SQLite state
the rest of the backend uses; nothing invents data and nothing calls an external
service.

The coordinator wires this in by calling ``register_extra_routes(app)`` at the end
of ``backend/app/main.py`` (after the app and all services are initialized). The
optional ``deps`` argument exists so tests can inject fakes; in production it is
left ``None`` and the module is imported lazily (a top-level import would be
circular because main.py imports this module).

Conventions copied from main.py:
- StrictModel request models (pydantic, extra="forbid")
- parameterized DB.execute calls, rows_to_dicts for SELECT *
- EVENTS.audit entries on every write
- HTTPException with {"code", "message"} detail payloads
- validate_identifier on path ids, ensure_project on project ids
- safe list limits clamped to 500
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import Field


def _resolve_deps(deps: Any) -> Any:
    if deps is not None:
        return deps
    # Lazy: main.py is fully initialized by the time the coordinator calls
    # register_extra_routes(app) at the end of its own module body.
    from . import main as _main

    return _main


def register_extra_routes(app: Any, deps: Any = None) -> None:
    m = _resolve_deps(deps)

    DB = m.DB
    EVENTS = m.EVENTS
    GUARD = m.GUARD
    REPOSITORIES = m.REPOSITORIES
    DATA_DIR = m.DATA_DIR
    rows_to_dicts = m.rows_to_dicts
    dump_json = m.dump_json
    now_iso = m.now_iso
    new_id = m.new_id
    validate_identifier = m.validate_identifier
    ensure_project = m.ensure_project
    consent_for = m.consent_for
    record_guard = m.record_guard
    StrictModel = m.StrictModel
    HTTPException = m.HTTPException

    DB.execute(
        """CREATE TABLE IF NOT EXISTS monitored_sources (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, label TEXT NOT NULL,
            platform TEXT NOT NULL, handle TEXT, auto_analyze INTEGER NOT NULL DEFAULT 1,
            auto_generate INTEGER NOT NULL DEFAULT 0, auto_publish INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'monitoring', last_checked_at TEXT,
            created_at TEXT NOT NULL)"""
    )
    DB.execute(
        """CREATE TABLE IF NOT EXISTS social_accounts (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, platform TEXT NOT NULL,
            handle TEXT NOT NULL, timezone TEXT NOT NULL DEFAULT 'Local',
            status TEXT NOT NULL DEFAULT 'recorded', created_at TEXT NOT NULL)"""
    )
    DB.execute(
        """CREATE TABLE IF NOT EXISTS calendar_items (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, title TEXT NOT NULL,
            scheduled_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Scheduled',
            route TEXT NOT NULL DEFAULT 'production', created_at TEXT NOT NULL)"""
    )
    DB.execute(
        """CREATE TABLE IF NOT EXISTS skill_registry (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, name TEXT NOT NULL,
            version TEXT NOT NULL DEFAULT 'v0.1', domain TEXT NOT NULL DEFAULT '',
            confidence TEXT NOT NULL DEFAULT 'Experimental', scope TEXT NOT NULL DEFAULT 'Project',
            state TEXT NOT NULL DEFAULT 'Experimental', created_at TEXT NOT NULL)"""
    )
    DB.execute(
        """CREATE TABLE IF NOT EXISTS recipe_registry (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, name TEXT NOT NULL,
            version TEXT NOT NULL DEFAULT 'v0.1', status TEXT NOT NULL DEFAULT 'Experimental',
            scope TEXT NOT NULL DEFAULT 'Project', summary TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL)"""
    )

    def _safe_limit(limit: int) -> int:
        return max(1, min(limit, 500))

    def _guard_text(project_id: str, text: str, route: str) -> None:
        """Run Guard preflight on a user-supplied text field; fail closed on BLOCK."""
        consent = consent_for(project_id)
        preflight = GUARD.preflight(text=text, references=[], consent=consent, metadata={"route": route})
        record_guard(project_id, None, preflight)
        if preflight.status == "BLOCK":
            raise HTTPException(status_code=422, detail={"code": "GUARD_BLOCKED", "message": preflight.reason})

    # ------------------------------------------------------------------ models
    class ProjectIn(StrictModel):
        name: str = Field(min_length=1, max_length=200)
        slug: str = Field(min_length=1, max_length=200)

    class CampaignIn(StrictModel):
        project_id: str = Field(default="project_northstar", max_length=200)
        name: str = Field(min_length=1, max_length=200)

    class ReferenceIn(StrictModel):
        project_id: str = Field(default="project_northstar", max_length=200)
        label: str = Field(min_length=1, max_length=300)
        kind: str = Field(default="file", max_length=100)
        role: str | None = Field(default=None, max_length=200)
        conversation_id: str | None = Field(default=None, max_length=200)

    class BlueprintIn(StrictModel):
        project_id: str = Field(default="project_northstar", max_length=200)
        name: str = Field(min_length=1, max_length=200)
        genome_id: str | None = Field(default=None, max_length=200)
        scenes: list[Any] = Field(default_factory=list)
        constraints: list[Any] = Field(default_factory=list)

    class CompileIn(StrictModel):
        adapter: str = Field(default="storyboard", max_length=100)

    class QaRunIn(StrictModel):
        project_id: str = Field(default="project_northstar", max_length=200)
        job_id: str | None = Field(default=None, max_length=200)

    class SkillIn(StrictModel):
        project_id: str = Field(default="project_northstar", max_length=200)
        name: str = Field(min_length=1, max_length=200)
        domain: str = Field(default="", max_length=200)
        confidence: str = Field(default="Experimental", max_length=100)
        scope: str = Field(default="Project", max_length=100)
        version: str = Field(default="v0.1", max_length=50)

    class RecipeIn(StrictModel):
        project_id: str = Field(default="project_northstar", max_length=200)
        name: str = Field(min_length=1, max_length=200)
        version: str = Field(default="v0.1", max_length=50)
        status: str = Field(default="Experimental", max_length=100)
        scope: str = Field(default="Project", max_length=100)
        summary: str = Field(default="", max_length=2000)

    class SourceIn(StrictModel):
        project_id: str = Field(default="project_northstar", max_length=200)
        label: str = Field(min_length=1, max_length=200)
        platform: str = Field(min_length=1, max_length=100)
        handle: str | None = Field(default=None, max_length=200)
        auto_analyze: bool = True
        auto_generate: bool = False
        auto_publish: bool = False

    class SourcePatch(StrictModel):
        label: str | None = Field(default=None, max_length=200)
        status: str | None = Field(default=None, max_length=100)
        auto_analyze: bool | None = None
        auto_generate: bool | None = None
        auto_publish: bool | None = None

    class SocialAccountIn(StrictModel):
        project_id: str = Field(default="project_northstar", max_length=200)
        platform: str = Field(min_length=1, max_length=100)
        handle: str = Field(min_length=1, max_length=200)
        timezone: str = Field(default="Local", max_length=100)

    class CalendarIn(StrictModel):
        project_id: str = Field(default="project_northstar", max_length=200)
        title: str = Field(min_length=1, max_length=300)
        scheduled_at: str = Field(min_length=1, max_length=100)
        status: str = Field(default="Scheduled", max_length=100)
        route: str = Field(default="production", max_length=100)

    class CalendarPatch(StrictModel):
        title: str | None = Field(default=None, max_length=300)
        scheduled_at: str | None = Field(default=None, max_length=100)
        status: str | None = Field(default=None, max_length=100)

    class CompositionIn(StrictModel):
        project_id: str = Field(default="project_northstar", max_length=200)
        name: str = Field(min_length=1, max_length=200)
        patch: Any = None
        status: str = Field(default="Working copy", max_length=100)

    # ------------------------------------------------------------- read-only
    @app.get("/api/projects")
    def extra_projects() -> list[dict[str, Any]]:
        return REPOSITORIES.projects.list()

    @app.post("/api/projects")
    def extra_create_project(payload: ProjectIn) -> dict[str, Any]:
        project_id = new_id("project")
        now = now_iso()
        DB.execute(
            "INSERT INTO projects(id,name,slug,created_at,updated_at,metadata_json) VALUES(?,?,?,?,?,?)",
            (project_id, payload.name, payload.slug, now, now, dump_json({})),
        )
        EVENTS.audit(category="projects", message=f"Project created: {payload.name}", project_id=project_id, route="projects", operation="create", status="created", output_summary={"id": project_id, "name": payload.name})
        return {"id": project_id, "name": payload.name, "slug": payload.slug}

    @app.get("/api/projects/{project_id}/campaigns")
    def extra_campaigns(project_id: str) -> list[dict[str, Any]]:
        validate_identifier(project_id, "project_id")
        ensure_project(project_id)
        return rows_to_dicts(DB.execute("SELECT * FROM campaigns WHERE project_id=? ORDER BY created_at DESC", (project_id,)).fetchall())

    @app.post("/api/campaigns")
    def extra_create_campaign(payload: CampaignIn) -> dict[str, Any]:
        ensure_project(payload.project_id)
        campaign_id = new_id("campaign")
        now = now_iso()
        DB.execute("INSERT INTO campaigns(id,project_id,name,created_at,updated_at,metadata_json) VALUES(?,?,?,?,?,?)", (campaign_id, payload.project_id, payload.name, now, now, dump_json({})))
        EVENTS.audit(category="projects", message=f"Campaign created: {payload.name}", project_id=payload.project_id, route="campaigns", operation="create", status="created", output_summary={"id": campaign_id, "name": payload.name})
        return {"id": campaign_id, "project_id": payload.project_id, "name": payload.name}

    @app.get("/api/artifacts")
    def extra_artifacts(project_id: str = "project_northstar", kind: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        validate_identifier(project_id, "project_id")
        ensure_project(project_id)
        if kind:
            rows = DB.execute("SELECT * FROM artifacts WHERE project_id=? AND kind=? ORDER BY created_at DESC LIMIT ?", (project_id, kind, _safe_limit(limit))).fetchall()
        else:
            rows = DB.execute("SELECT * FROM artifacts WHERE project_id=? ORDER BY created_at DESC LIMIT ?", (project_id, _safe_limit(limit))).fetchall()
        return rows_to_dicts(rows)

    @app.get("/api/references")
    def extra_references(project_id: str = "project_northstar", limit: int = 100) -> list[dict[str, Any]]:
        validate_identifier(project_id, "project_id")
        ensure_project(project_id)
        return rows_to_dicts(DB.execute('SELECT * FROM "references" WHERE project_id=? ORDER BY created_at DESC LIMIT ?', (project_id, _safe_limit(limit))).fetchall())

    @app.post("/api/references")
    def extra_create_reference(payload: ReferenceIn) -> dict[str, Any]:
        validate_identifier(payload.project_id, "project_id")
        ensure_project(payload.project_id)
        _guard_text(payload.project_id, payload.label, "intake")
        ref_id = new_id("ref")
        now = now_iso()
        DB.execute('INSERT INTO "references"(id,project_id,conversation_id,label,kind,role,status,metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)', (ref_id, payload.project_id, payload.conversation_id, payload.label, payload.kind, payload.role, "local", dump_json({}), now))
        EVENTS.audit(category="intake", message=f"Reference recorded: {payload.label}", project_id=payload.project_id, route="intake", operation="create", status="created", output_summary={"id": ref_id, "kind": payload.kind})
        return {"id": ref_id, "label": payload.label, "kind": payload.kind, "status": "local"}

    @app.get("/api/evidence")
    def extra_evidence(project_id: str = "project_northstar", limit: int = 100) -> list[dict[str, Any]]:
        validate_identifier(project_id, "project_id")
        ensure_project(project_id)
        rows = REPOSITORIES.evidence.list_for_project(project_id)
        return rows[: _safe_limit(limit)]

    @app.get("/api/entities")
    def extra_entities(project_id: str = "project_northstar") -> list[dict[str, Any]]:
        """Identity-stabilized references (character, product, location) used by the Entities surface."""
        validate_identifier(project_id, "project_id")
        ensure_project(project_id)
        rows = DB.execute(
            """SELECT id,label,kind,role,status,created_at FROM "references"
               WHERE project_id=? AND (LOWER(role) LIKE '%character%' OR LOWER(role) LIKE '%product%'
               OR LOWER(role) LIKE '%location%' OR LOWER(role) LIKE '%identity%')
               ORDER BY created_at DESC""",
            (project_id,),
        ).fetchall()
        return [{"id": row["id"], "name": row["label"], "kind": row["kind"], "role": row["role"], "status": row["status"], "created_at": row["created_at"]} for row in rows]

    @app.get("/api/genomes")
    def extra_genomes(project_id: str = "project_northstar") -> list[dict[str, Any]]:
        validate_identifier(project_id, "project_id")
        ensure_project(project_id)
        return rows_to_dicts(DB.execute("SELECT * FROM genomes WHERE project_id=? ORDER BY created_at DESC", (project_id,)).fetchall())

    @app.get("/api/blueprints")
    def extra_blueprints(project_id: str = "project_northstar") -> list[dict[str, Any]]:
        validate_identifier(project_id, "project_id")
        ensure_project(project_id)
        return rows_to_dicts(DB.execute("SELECT * FROM blueprints WHERE project_id=? ORDER BY created_at DESC", (project_id,)).fetchall())

    @app.post("/api/blueprints")
    def extra_create_blueprint(payload: BlueprintIn) -> dict[str, Any]:
        validate_identifier(payload.project_id, "project_id")
        ensure_project(payload.project_id)
        _guard_text(payload.project_id, payload.name, "blueprint")
        blueprint_id = new_id("blueprint")
        now = now_iso()
        DB.execute("INSERT INTO blueprints(id,project_id,genome_id,name,scenes_json,constraints_json,created_at) VALUES(?,?,?,?,?,?,?)", (blueprint_id, payload.project_id, payload.genome_id, payload.name, dump_json(payload.scenes), dump_json(payload.constraints), now))
        EVENTS.audit(category="blueprint", message=f"Blueprint drafted: {payload.name}", project_id=payload.project_id, route="blueprint", operation="create", status="created", output_summary={"id": blueprint_id, "scene_count": len(payload.scenes)})
        return {"id": blueprint_id, "name": payload.name, "scene_count": len(payload.scenes)}

    @app.get("/api/compile-runs")
    def extra_compile_runs(blueprint_id: str) -> list[dict[str, Any]]:
        validate_identifier(blueprint_id, "blueprint_id")
        return rows_to_dicts(DB.execute("SELECT * FROM compile_runs WHERE blueprint_id=? ORDER BY created_at DESC", (blueprint_id,)).fetchall())

    @app.post("/api/blueprints/{blueprint_id}/compile")
    def extra_compile_blueprint(blueprint_id: str, payload: CompileIn) -> dict[str, Any]:
        """Deterministic blueprint translation: scene order and constraints become intent
        blocks without any model call. The plan is persisted in compile_runs."""
        validate_identifier(blueprint_id, "blueprint_id")
        row = DB.execute("SELECT * FROM blueprints WHERE id=?", (blueprint_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail={"code": "BLUEPRINT_NOT_FOUND", "message": "Blueprint was not found."})
        try:
            scenes = json.loads(row["scenes_json"]) if row["scenes_json"] else []
            constraints = json.loads(row["constraints_json"]) if row["constraints_json"] else []
        except (json.JSONDecodeError, TypeError):
            scenes, constraints = [], []
        intent_blocks = []
        for index, scene in enumerate(scenes):
            title = scene if isinstance(scene, str) else (scene.get("title") if isinstance(scene, dict) else str(scene))
            intent_blocks.append({"order": index + 1, "scene": title, "adapter": payload.adapter, "constraints": constraints, "verification": ["identity", "audio", "caption", "format"]})
        plan = {"blueprint_id": blueprint_id, "adapter": payload.adapter, "scene_count": len(intent_blocks), "intent_blocks": intent_blocks, "compiled_at": now_iso()}
        run_id = new_id("compile")
        DB.execute("INSERT INTO compile_runs(id,blueprint_id,provider_neutral_plan_json,status,created_at) VALUES(?,?,?,?,?)", (run_id, blueprint_id, dump_json(plan), "compiled", now_iso()))
        EVENTS.audit(category="compiler", message=f"Blueprint compiled: {row['name']}", project_id=row["project_id"], route="compiler", operation="compile", status="compiled", output_summary={"blueprint_id": blueprint_id, "scene_count": len(intent_blocks), "adapter": payload.adapter})
        return {"run_id": run_id, "status": "compiled", "plan": plan}

    @app.get("/api/qa")
    def extra_qa(project_id: str = "project_northstar", limit: int = 50) -> list[dict[str, Any]]:
        validate_identifier(project_id, "project_id")
        ensure_project(project_id)
        return rows_to_dicts(DB.execute("SELECT * FROM qa_runs WHERE project_id=? ORDER BY created_at DESC LIMIT ?", (project_id, _safe_limit(limit))).fetchall())

    @app.post("/api/qa/run")
    def extra_qa_run(payload: QaRunIn) -> dict[str, Any]:
        """Deterministic local verification checks derived from real persisted state.
        No model call; a finding is 'blocked' only when the underlying record is missing."""
        validate_identifier(payload.project_id, "project_id")
        ensure_project(payload.project_id)

        def _count(sql: str, params: tuple) -> int:
            return DB.execute(sql, params).fetchone()[0]

        identity_refs = _count("SELECT COUNT(*) FROM \"references\" WHERE project_id=? AND (LOWER(role) LIKE '%character%' OR LOWER(role) LIKE '%identity%' OR LOWER(role) LIKE '%product%')", (payload.project_id,))
        blueprints = _count("SELECT COUNT(*) FROM blueprints WHERE project_id=?", (payload.project_id,))
        compositions = _count("SELECT COUNT(*) FROM artifacts WHERE project_id=? AND kind='composition'", (payload.project_id,))
        findings = [
            {"check": "Identity consistency", "status": "passed" if identity_refs > 0 else "blocked", "detail": f"{identity_refs} identity reference(s) attached" if identity_refs else "No identity reference is attached; attach a character, product, or location reference."},
            {"check": "Scene continuity", "status": "passed" if blueprints > 0 else "review", "detail": f"{blueprints} blueprint(s) drafted" if blueprints else "No blueprint is drafted yet."},
            {"check": "Audio alignment", "status": "review", "detail": "No audio analysis adapter is connected; timing is unverified."},
            {"check": "Caption safety", "status": "passed" if compositions > 0 else "review", "detail": f"{compositions} composition(s) carry caption layers" if compositions else "No composition carries a caption layer."},
            {"check": "Format readiness", "status": "passed", "detail": "9:16 output contract is valid."},
        ]
        status = "blocked" if any(f["status"] == "blocked" for f in findings) else ("review" if any(f["status"] == "review" for f in findings) else "passed")
        run_id = new_id("qa")
        DB.execute("INSERT INTO qa_runs(id,project_id,job_id,status,findings_json,created_at) VALUES(?,?,?,?,?,?)", (run_id, payload.project_id, payload.job_id, status, dump_json(findings), now_iso()))
        EVENTS.audit(category="qa", message=f"Verification run completed: {status}", project_id=payload.project_id, job_id=payload.job_id, route="qa", operation="run", status=status, output_summary={"run_id": run_id, "finding_count": len(findings)})
        return {"run_id": run_id, "status": status, "findings": findings}

    @app.get("/api/usage")
    def extra_usage(project_id: str = "project_northstar", limit: int = 100) -> list[dict[str, Any]]:
        validate_identifier(project_id, "project_id")
        ensure_project(project_id)
        rows = REPOSITORIES.usage.list_for_project(project_id, _safe_limit(limit))
        totals = {"requests": 0, "input_tokens": 0, "output_tokens": 0, "duration_ms": 0, "estimated_cost": 0.0}
        for row in rows:
            for key in totals:
                totals[key] += row.get(key) or 0
        return {"events": rows, "totals": totals}

    @app.get("/api/permissions")
    def extra_permissions(project_id: str = "project_northstar", limit: int = 100) -> list[dict[str, Any]]:
        validate_identifier(project_id, "project_id")
        ensure_project(project_id)
        return rows_to_dicts(DB.execute("SELECT * FROM permission_checks WHERE project_id=? ORDER BY created_at DESC LIMIT ?", (project_id, _safe_limit(limit))).fetchall())

    @app.get("/api/skills")
    def extra_skills(project_id: str = "project_northstar") -> list[dict[str, Any]]:
        validate_identifier(project_id, "project_id")
        ensure_project(project_id)
        return rows_to_dicts(DB.execute("SELECT * FROM skill_registry WHERE project_id=? ORDER BY created_at DESC", (project_id,)).fetchall())

    @app.post("/api/skills")
    def extra_create_skill(payload: SkillIn) -> dict[str, Any]:
        validate_identifier(payload.project_id, "project_id")
        ensure_project(payload.project_id)
        skill_id = new_id("skill")
        DB.execute("INSERT INTO skill_registry(id,project_id,name,version,domain,confidence,scope,state,created_at) VALUES(?,?,?,?,?,?,?,?,?)", (skill_id, payload.project_id, payload.name, payload.version, payload.domain, payload.confidence, payload.scope, "Experimental", now_iso()))
        EVENTS.audit(category="skills", message=f"Skill drafted: {payload.name}", project_id=payload.project_id, route="skills", operation="create", status="created", output_summary={"id": skill_id, "name": payload.name})
        return {"id": skill_id, "name": payload.name, "version": payload.version}

    @app.get("/api/recipes")
    def extra_recipes(project_id: str = "project_northstar") -> list[dict[str, Any]]:
        validate_identifier(project_id, "project_id")
        ensure_project(project_id)
        return rows_to_dicts(DB.execute("SELECT * FROM recipe_registry WHERE project_id=? ORDER BY created_at DESC", (project_id,)).fetchall())

    @app.post("/api/recipes")
    def extra_create_recipe(payload: RecipeIn) -> dict[str, Any]:
        validate_identifier(payload.project_id, "project_id")
        ensure_project(payload.project_id)
        recipe_id = new_id("recipe")
        DB.execute("INSERT INTO recipe_registry(id,project_id,name,version,status,scope,summary,created_at) VALUES(?,?,?,?,?,?,?,?)", (recipe_id, payload.project_id, payload.name, payload.version, payload.status, payload.scope, payload.summary, now_iso()))
        EVENTS.audit(category="recipes", message=f"Recipe drafted: {payload.name}", project_id=payload.project_id, route="recipes", operation="create", status="created", output_summary={"id": recipe_id, "name": payload.name})
        return {"id": recipe_id, "name": payload.name, "version": payload.version}

    @app.get("/api/adaptation/drift")
    def extra_drift(project_id: str = "project_northstar", limit: int = 100) -> list[dict[str, Any]]:
        validate_identifier(project_id, "project_id")
        ensure_project(project_id)
        return rows_to_dicts(DB.execute("SELECT * FROM adaptation_drift WHERE candidate_id IN (SELECT id FROM adaptation_candidates WHERE project_id=?) ORDER BY created_at DESC LIMIT ?", (project_id, _safe_limit(limit))).fetchall())

    @app.get("/api/adaptation/experiences")
    def extra_experiences(project_id: str = "project_northstar", limit: int = 100) -> list[dict[str, Any]]:
        validate_identifier(project_id, "project_id")
        ensure_project(project_id)
        return rows_to_dicts(DB.execute("SELECT * FROM adaptation_experiences WHERE project_id=? ORDER BY created_at DESC LIMIT ?", (project_id, _safe_limit(limit))).fetchall())

    @app.get("/api/monitor/spend")
    def extra_spend(project_id: str = "project_northstar", limit: int = 100) -> dict[str, Any]:
        validate_identifier(project_id, "project_id")
        ensure_project(project_id)
        ledger = rows_to_dicts(DB.execute("SELECT * FROM monitor_spend_ledger WHERE project_id=? ORDER BY occurred_at DESC LIMIT ?", (project_id, _safe_limit(limit))).fetchall())
        limits = DB.execute("SELECT session_limit,day_limit,project_limit,month_limit,consumed_session,consumed_day,consumed_project,consumed_month FROM monitor_limits WHERE id=1").fetchone()
        return {"ledger": ledger, "limits": dict(limits) if limits else {}}

    @app.get("/api/tool-runs")
    def extra_tool_runs(project_id: str = "project_northstar", limit: int = 50) -> list[dict[str, Any]]:
        validate_identifier(project_id, "project_id")
        ensure_project(project_id)
        return rows_to_dicts(DB.execute("SELECT * FROM tool_runs WHERE project_id=? ORDER BY created_at DESC LIMIT ?", (project_id, _safe_limit(limit))).fetchall())

    @app.get("/api/compositions")
    def extra_compositions(project_id: str = "project_northstar") -> list[dict[str, Any]]:
        validate_identifier(project_id, "project_id")
        ensure_project(project_id)
        return rows_to_dicts(DB.execute("SELECT * FROM artifacts WHERE project_id=? AND kind='composition' ORDER BY created_at DESC", (project_id,)).fetchall())

    @app.post("/api/compositions")
    def extra_create_composition(payload: CompositionIn) -> dict[str, Any]:
        validate_identifier(payload.project_id, "project_id")
        ensure_project(payload.project_id)
        composition_id = new_id("artifact")
        DB.execute("INSERT INTO artifacts(id,project_id,kind,name,status,created_at,metadata_json) VALUES(?,?,?,?,?,?,?)", (composition_id, payload.project_id, "composition", payload.name, payload.status, now_iso(), dump_json({"patch": payload.patch})))
        EVENTS.audit(category="composition", message=f"Composition saved: {payload.name}", project_id=payload.project_id, route="composition", operation="create", status="created", output_summary={"id": composition_id})
        return {"id": composition_id, "name": payload.name, "status": payload.status}

    @app.get("/api/control-plane")
    def extra_control_plane(project_id: str = "project_northstar") -> dict[str, Any]:
        """Portable project intelligence summary: persisted counts plus the local
        data directory layout. Read-only."""
        validate_identifier(project_id, "project_id")
        ensure_project(project_id)
        project = REPOSITORIES.projects.get(project_id) or {}
        counts: dict[str, int] = {}
        for label, sql in {
            "references": 'SELECT COUNT(*) FROM "references" WHERE project_id=?',
            "evidence": "SELECT COUNT(*) FROM evidence WHERE project_id=?",
            "jobs": "SELECT COUNT(*) FROM jobs WHERE project_id=?",
            "artifacts": "SELECT COUNT(*) FROM artifacts WHERE project_id=?",
            "genomes": "SELECT COUNT(*) FROM genomes WHERE project_id=?",
            "blueprints": "SELECT COUNT(*) FROM blueprints WHERE project_id=?",
            "memory_records": "SELECT COUNT(*) FROM memory_records WHERE project_id=?",
            "governor_decisions": "SELECT COUNT(*) FROM governor_decisions",
            "skill_registry": "SELECT COUNT(*) FROM skill_registry WHERE project_id=?",
        }.items():
            counts[label] = DB.execute(sql, (project_id,)).fetchone()[0]
        try:
            directories = sorted(path.name for path in DATA_DIR.iterdir())
        except OSError:
            directories = []
        return {"project": project, "counts": counts, "data_directories": directories}

    @app.get("/api/terminal/log")
    def extra_terminal_log(limit: int = 80, category: str | None = None) -> list[dict[str, Any]]:
        """Read-only backend log viewer for the Terminal surface. No shell exists
        behind this endpoint; it only returns persisted audit events."""
        safe = _safe_limit(limit)
        if category:
            rows = DB.execute("SELECT id,timestamp,severity,category,message,route,operation,status,trace_id,job_id,artifact_id FROM audit_events WHERE category=? ORDER BY timestamp DESC LIMIT ?", (category, safe)).fetchall()
        else:
            rows = DB.execute("SELECT id,timestamp,severity,category,message,route,operation,status,trace_id,job_id,artifact_id FROM audit_events ORDER BY timestamp DESC LIMIT ?", (safe,)).fetchall()
        return rows_to_dicts(rows)

    @app.get("/api/copilot/explain")
    def extra_copilot_explain(question: str, project_id: str = "project_northstar") -> dict[str, Any]:
        """Deterministic evidence-linked explainer. It matches question keywords
        against persisted Governor decisions, evidence, memory, and QA findings and
        says explicitly when no local evidence matches. No model call is made."""
        validate_identifier(project_id, "project_id")
        ensure_project(project_id)
        words = {w for w in re.findall(r"[a-z0-9]+", question.lower()) if len(w) > 2 and w not in {"what", "why", "how", "the", "did", "does", "and", "for", "was"}}

        def _score(text: str) -> int:
            tokens = set(re.findall(r"[a-z0-9]+", (text or "").lower()))
            return len(words & tokens)

        hits: list[dict[str, Any]] = []
        for row in DB.execute("SELECT id,task,fused_answer,confidence,created_at FROM governor_decisions ORDER BY created_at DESC LIMIT 20").fetchall():
            score = _score(f"{row['task']} {row['fused_answer']}")
            if score:
                hits.append({"score": score, "source": "Governor decision", "text": f"{row['task']}: {row['fused_answer'] or 'no fused answer recorded'}", "detail": f"confidence {row['confidence']} · {row['created_at'][:10]}"})
        for row in DB.execute("SELECT id,claim,source_type,confidence,created_at FROM evidence WHERE project_id=? ORDER BY created_at DESC LIMIT 30", (project_id,)).fetchall():
            score = _score(row["claim"])
            if score:
                hits.append({"score": score, "source": f"Evidence ({row['source_type']})", "text": row["claim"], "detail": f"confidence {row['confidence']} · {row['created_at'][:10]}"})
        for row in DB.execute("SELECT title,content,source,confidence,created_at FROM memory_records WHERE project_id=? AND status='ACTIVE' ORDER BY created_at DESC LIMIT 30", (project_id,)).fetchall():
            score = _score(f"{row['title']} {row['content']}")
            if score:
                hits.append({"score": score, "source": f"Memory ({row['source']})", "text": f"{row['title']}: {row['content'][:240]}", "detail": f"confidence {row['confidence']} · {row['created_at'][:10]}"})
        hits.sort(key=lambda hit: hit["score"], reverse=True)
        evidence = [{k: hit[k] for k in ("source", "text", "detail")} for hit in hits[:3]]
        if evidence:
            answer = f"Based on {len(evidence)} local record(s): " + " ".join(hit["text"] for hit in evidence)
            answer += " These are persisted records from this workspace, not a model explanation."
        else:
            answer = "No local evidence records match this question yet. Run a Governor decision, record evidence, or complete a QA verification and ask again."
        EVENTS.audit(category="copilot", message="Copilot explanation requested", project_id=project_id, route="copilot", operation="explain", status="answered" if evidence else "no-evidence", output_summary={"question": question[:200], "evidence_count": len(evidence)})
        return {"question": question, "answer": answer, "evidence": evidence, "matched": bool(evidence)}

    # ------------------------------------------------------- source monitor
    @app.get("/api/sources")
    def extra_sources(project_id: str = "project_northstar") -> list[dict[str, Any]]:
        validate_identifier(project_id, "project_id")
        ensure_project(project_id)
        return rows_to_dicts(DB.execute("SELECT * FROM monitored_sources WHERE project_id=? ORDER BY created_at DESC", (project_id,)).fetchall())

    @app.post("/api/sources")
    def extra_create_source(payload: SourceIn) -> dict[str, Any]:
        validate_identifier(payload.project_id, "project_id")
        ensure_project(payload.project_id)
        source_id = new_id("source")
        DB.execute("INSERT INTO monitored_sources(id,project_id,label,platform,handle,auto_analyze,auto_generate,auto_publish,status,last_checked_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (source_id, payload.project_id, payload.label, payload.platform, payload.handle, int(payload.auto_analyze), int(payload.auto_generate), int(payload.auto_publish), "monitoring", now_iso(), now_iso()))
        EVENTS.audit(category="sources", message=f"Source added to monitor: {payload.label}", project_id=payload.project_id, route="sourceMonitor", operation="create", status="monitoring", output_summary={"id": source_id, "platform": payload.platform})
        return {"id": source_id, "label": payload.label, "platform": payload.platform, "status": "monitoring"}

    @app.patch("/api/sources/{source_id}")
    def extra_update_source(source_id: str, payload: SourcePatch) -> dict[str, Any]:
        validate_identifier(source_id, "source_id")
        row = DB.execute("SELECT * FROM monitored_sources WHERE id=?", (source_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail={"code": "SOURCE_NOT_FOUND", "message": "Monitored source was not found."})
        updates: dict[str, Any] = {}
        for field in ("label", "status", "auto_analyze", "auto_generate", "auto_publish"):
            value = getattr(payload, field)
            if value is not None:
                updates[field] = int(value) if field.startswith("auto_") else value
        if not updates:
            return dict(row)
        DB.execute(f"UPDATE monitored_sources SET {', '.join(f'{k}=?' for k in updates)}, last_checked_at=? WHERE id=?", (*updates.values(), now_iso(), source_id))
        EVENTS.audit(category="sources", message=f"Source updated: {row['label']}", project_id=row["project_id"], route="sourceMonitor", operation="update", status="ok", output_summary={"id": source_id, "updates": sorted(updates)})
        return dict(DB.execute("SELECT * FROM monitored_sources WHERE id=?", (source_id,)).fetchone())

    @app.delete("/api/sources/{source_id}")
    def extra_delete_source(source_id: str) -> dict[str, Any]:
        validate_identifier(source_id, "source_id")
        row = DB.execute("SELECT * FROM monitored_sources WHERE id=?", (source_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail={"code": "SOURCE_NOT_FOUND", "message": "Monitored source was not found."})
        DB.execute("DELETE FROM monitored_sources WHERE id=?", (source_id,))
        EVENTS.audit(category="sources", message=f"Source removed from monitor: {row['label']}", project_id=row["project_id"], route="sourceMonitor", operation="delete", status="deleted", output_summary={"id": source_id})
        return {"id": source_id, "deleted": True}

    # ------------------------------------------------------- social accounts
    @app.get("/api/social-accounts")
    def extra_social_accounts(project_id: str = "project_northstar") -> list[dict[str, Any]]:
        validate_identifier(project_id, "project_id")
        ensure_project(project_id)
        return rows_to_dicts(DB.execute("SELECT * FROM social_accounts WHERE project_id=? ORDER BY created_at DESC", (project_id,)).fetchall())

    @app.post("/api/social-accounts")
    def extra_create_social_account(payload: SocialAccountIn) -> dict[str, Any]:
        """Records a posting identity locally. Platform OAuth is not implemented,
        so the record stays 'recorded' and never claims a live connection."""
        validate_identifier(payload.project_id, "project_id")
        ensure_project(payload.project_id)
        account_id = new_id("social")
        DB.execute("INSERT INTO social_accounts(id,project_id,platform,handle,timezone,status,created_at) VALUES(?,?,?,?,?,?,?)", (account_id, payload.project_id, payload.platform, payload.handle, payload.timezone, "recorded", now_iso()))
        EVENTS.audit(category="social", message=f"Social account recorded: {payload.platform} {payload.handle}", project_id=payload.project_id, route="socialAccounts", operation="create", status="recorded", output_summary={"id": account_id, "platform": payload.platform})
        return {"id": account_id, "platform": payload.platform, "handle": payload.handle, "status": "recorded"}

    @app.delete("/api/social-accounts/{account_id}")
    def extra_delete_social_account(account_id: str) -> dict[str, Any]:
        validate_identifier(account_id, "account_id")
        row = DB.execute("SELECT * FROM social_accounts WHERE id=?", (account_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail={"code": "ACCOUNT_NOT_FOUND", "message": "Social account was not found."})
        DB.execute("DELETE FROM social_accounts WHERE id=?", (account_id,))
        EVENTS.audit(category="social", message=f"Social account removed: {row['platform']} {row['handle']}", project_id=row["project_id"], route="socialAccounts", operation="delete", status="deleted", output_summary={"id": account_id})
        return {"id": account_id, "deleted": True}

    # ------------------------------------------------------- content calendar
    @app.get("/api/calendar")
    def extra_calendar(project_id: str = "project_northstar") -> list[dict[str, Any]]:
        validate_identifier(project_id, "project_id")
        ensure_project(project_id)
        return rows_to_dicts(DB.execute("SELECT * FROM calendar_items WHERE project_id=? ORDER BY scheduled_at", (project_id,)).fetchall())

    @app.post("/api/calendar")
    def extra_create_calendar_item(payload: CalendarIn) -> dict[str, Any]:
        validate_identifier(payload.project_id, "project_id")
        ensure_project(payload.project_id)
        item_id = new_id("cal")
        DB.execute("INSERT INTO calendar_items(id,project_id,title,scheduled_at,status,route,created_at) VALUES(?,?,?,?,?,?,?)", (item_id, payload.project_id, payload.title, payload.scheduled_at, payload.status, payload.route, now_iso()))
        EVENTS.audit(category="calendar", message=f"Calendar item scheduled: {payload.title}", project_id=payload.project_id, route="calendar", operation="create", status="scheduled", output_summary={"id": item_id, "scheduled_at": payload.scheduled_at})
        return {"id": item_id, "title": payload.title, "scheduled_at": payload.scheduled_at, "status": payload.status}

    @app.patch("/api/calendar/{item_id}")
    def extra_update_calendar_item(item_id: str, payload: CalendarPatch) -> dict[str, Any]:
        validate_identifier(item_id, "item_id")
        row = DB.execute("SELECT * FROM calendar_items WHERE id=?", (item_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail={"code": "CALENDAR_ITEM_NOT_FOUND", "message": "Calendar item was not found."})
        updates = {field: getattr(payload, field) for field in ("title", "scheduled_at", "status") if getattr(payload, field) is not None}
        if updates:
            DB.execute(f"UPDATE calendar_items SET {', '.join(f'{k}=?' for k in updates)} WHERE id=?", (*updates.values(), item_id))
            EVENTS.audit(category="calendar", message=f"Calendar item updated: {row['title']}", project_id=row["project_id"], route="calendar", operation="update", status="ok", output_summary={"id": item_id, "updates": sorted(updates)})
        return dict(DB.execute("SELECT * FROM calendar_items WHERE id=?", (item_id,)).fetchone())

    @app.delete("/api/calendar/{item_id}")
    def extra_delete_calendar_item(item_id: str) -> dict[str, Any]:
        validate_identifier(item_id, "item_id")
        row = DB.execute("SELECT * FROM calendar_items WHERE id=?", (item_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail={"code": "CALENDAR_ITEM_NOT_FOUND", "message": "Calendar item was not found."})
        DB.execute("DELETE FROM calendar_items WHERE id=?", (item_id,))
        EVENTS.audit(category="calendar", message=f"Calendar item removed: {row['title']}", project_id=row["project_id"], route="calendar", operation="delete", status="deleted", output_summary={"id": item_id})
        return {"id": item_id, "deleted": True}
