export type JsonPrimitive = string | number | boolean | null
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue }

export type DataMode = 'REAL' | 'CONTRACT_STUB' | 'DEMO_ONLY' | 'MISSING_BACKEND'
export type RequestPhase = 'idle' | 'loading' | 'success' | 'empty' | 'blocked' | 'error' | 'unauthorized' | 'offline' | 'retry' | 'running' | 'cancelling' | 'cancelled'

export type ApiErrorCode =
  | 'UNAUTHENTICATED_LOCAL_REQUEST'
  | 'LOCAL_SESSION_TOKEN_UNCONFIGURED'
  | 'MEDIA_DEPENDENCY_MISSING'
  | 'MEDIA_DEPENDENCY_CORRUPT'
  | 'MEDIA_PATH_FORBIDDEN'
  | 'MEDIA_OPERATION_FORBIDDEN'
  | 'MEDIA_OUTPUT_INVALID'
  | 'MONITOR_HALTED'
  | 'WORKFLOW_RECOVERY_REQUIRED'
  | 'PROVIDER_DISABLED'
  | 'PROVIDER_CREDENTIAL_UNAVAILABLE'
  | 'UNKNOWN'

export type ApiError = {
  code: ApiErrorCode | string
  message: string
  retryable?: boolean
  trace_id?: string
  error_id?: string
}

export type ApiResponse<T> = T | { error: ApiError } | { detail: ApiError }

export type RequestState<T> = {
  phase: RequestPhase
  data: T | null
  error: ApiError | null
  updatedAt?: string
}

export type Project = { id: string; name: string; slug: string; created_at: string; updated_at: string; metadata_json?: string }
export type Campaign = { id: string; project_id: string; name: string; created_at: string; updated_at: string; metadata_json?: string }
export type Conversation = { id: string; project_id: string; campaign_id?: string | null; title: string; pinned?: boolean | number; archived?: boolean | number; created_at: string; updated_at: string; messages: Message[] }
export type Message = { id: string; conversation_id: string; role: 'user' | 'assistant' | 'system' | string; content: string; created_at: string; metadata_json?: string }
export type Attachment = { label: string; kind: 'file' | 'image' | 'video' | 'audio' | 'text' | string; role?: string | null; filename?: string | null; content_hash?: string | null; local_path?: string | null }
export type Reference = { id: string; project_id: string; conversation_id?: string | null; label: string; kind: string; role?: string | null; path?: string | null; sha256?: string | null; status: string; metadata_json?: string; created_at: string }
export type Entity = { id: string; project_id: string; name: string; kind: string; status: string; metadata_json?: string }
export type Evidence = { id: string; project_id: string; reference_id?: string | null; claim: string; source_type: string; source_locator?: string | null; model_id?: string | null; model_version?: string | null; confidence: number; created_at: string; metadata_json?: string }
export type Genome = { id: string; project_id: string; name: string; version: string; patterns_json: string; evidence_ids_json: string; created_at: string }
export type Blueprint = { id: string; project_id: string; genome_id?: string | null; name: string; scenes_json: string; constraints_json: string; created_at: string }
export type GenerationJob = { id: string; project_id?: string; workflow_name: string; status: string; idempotency_key?: string; checkpoint: string; progress: number; error_code?: string | null; error_message?: string | null; estimated_cost?: number; estimated_duration_seconds?: number; interruption_mode?: string; checkpoint_data_json?: string; created_at?: string; updated_at: string }
export type Workflow = { id: string; name: string; status: string; checkpoint?: string; progress?: number; job_id?: string }
export type Advisor = { id: string; name: string; role: string; status: string; confidence?: number; evidence_ids?: string[] }
export type Model = { provider: string; model_id: string; version: string; capabilities: string[]; modalities: string[]; fallback_group: string; priority: number; status: string; objectives?: Record<string, number> }
export type Provider = { id: string; name: string; masked_key?: string | null; status: string; last_tested_at?: string | null; created_at?: string; updated_at?: string }
export type CredentialMetadata = { credential_id: string; provider: string; display_name: string; masked_value: string; scope: string; status: string; created_at?: string; updated_at?: string }
export type Tool = { id: string; project_id: string; name: string; version: string; state: string; scope: string; security_status: string; last_used_at?: string | null; last_audited_at?: string | null; created_at: string; updated_at: string }
export type Skill = { id: string; name: string; version: string; scope: string; status: string; confidence?: number; regression_status?: string }
export type Recipe = { id: string; name: string; version: string; status: string; constraints?: string[]; evidence_ids?: string[] }
export type Memory = { id: string; project_id: string; source: string; scope: string; title: string; content: string; confidence: number; created_at: string }
export type GuardResult = { phase: string; status: 'PASS' | 'BLOCK' | 'REVIEW' | string; allowed: boolean; reason: string; checks: Record<string, JsonValue> }
export type MonitorState = { state: { status: string; halt_reason?: string | null; halted_at?: string | null; resumed_at?: string | null }; limits: Record<string, number | string>; active_jobs: number; alerts: MonitorAlert[]; reconciled_at: string; independent: boolean; human_resume_required: boolean }
export type MonitorAlert = { code: string; severity: 'warning' | 'critical' | string; message?: string; scope?: string; consumed?: number; limit?: number; active_jobs?: number }
export type DoctorIncident = { id: string; severity: string; title: string; detail: string; status: string; recovery_action?: string; created_at?: string }
export type Artifact = { id: string; project_id: string; kind: string; name: string; path?: string | null; status: string; created_at: string; metadata_json?: string }
export type LineageEdge = { id: string; artifact_id: string; parent_type: string; parent_id: string; relationship: string; created_at: string }
export type Usage = { model_id?: string | null; task?: string | null; requests: number; input_tokens: number; output_tokens: number; duration_ms: number; estimated_cost: number; created_at?: string }
export type Review = { id: string; project_id: string; candidate_id: string; candidate_type: string; evaluation_outcome: string; status: string; reviewer_id?: string | null; decision_reason?: string | null; created_at: string; decided_at?: string | null }
export type PublishingJob = { id: string; artifact_id: string; destination: string; status: 'blocked' | 'prepared' | 'submitted' | 'published' | 'failed' | string; human_approved: boolean; qa_passed: boolean; created_at?: string }
export type PublishAdapterStatus = { platform_id: string; display_name: string; adapter_version: string; configured: boolean; credential_reference?: string | null; notes?: string }
export type PublishPrepStep = { name: string; status: 'passed' | 'skipped' | 'blocked' | 'failed' | string; detail: string; artifacts: string[] }
export type PublishPrepRun = { run_id: string; project_id: string; artifact_id: string; platform: string; status: string; steps: PublishPrepStep[]; bundle_path?: string | null; guard_decision_id?: string | null; error_code?: string | null; error_message?: string | null; receipt: Record<string, JsonValue>; created_at?: string; updated_at?: string }

export type AuditEvent = { id: string; timestamp: string; severity?: string; category: string; message: string; operation?: string | null; status?: string | null; trace_id?: string | null; job_id?: string | null; artifact_id?: string | null; route?: string | null; input_summary?: string | null; output_summary?: string | null; metadata_json?: string | null }
export type BackendHealth = { status: string; version: string; database: boolean; ffmpeg: boolean; ffprobe: boolean; media?: { status: string; ffmpeg: { status: string; code?: string | null }; ffprobe: { status: string; code?: string | null } }; gateway?: { status: string; model_count: number; simulator_only: boolean }; worker_capacity?: number; constitution_version?: string }
export type BackendJob = { job_id: string; workflow: string; status: string; trace_id: string; cached?: boolean }
export type ArtifactLineage = { artifact: Artifact; lineage: LineageEdge[] }

export type FrontendRoute = 'command' | 'chat' | 'projects' | 'campaigns' | 'library' | 'intake' | 'analysis' | 'evidence' | 'entities' | 'genome' | 'genomeCompare' | 'blueprint' | 'compiler' | 'production' | 'qa' | 'reviewQueue' | 'governor' | 'council' | 'modelRegistry' | 'toolLab' | 'mediaEngine' | 'mediaTools' | 'composition' | 'terminal' | 'replay' | 'creativeMode' | 'experiments' | 'performance' | 'analytics' | 'drift' | 'adaptation' | 'adaptationCenter' | 'skills' | 'recipes' | 'memory' | 'evolution' | 'automation' | 'calendar' | 'sourceMonitor' | 'socialAccounts' | 'publishing' | 'workflow' | 'doctor' | 'incidents' | 'sandbox' | 'resources' | 'observability' | 'credentials' | 'security' | 'policies' | 'controlPlane' | 'memorySources' | 'permissions' | 'setup' | 'copilot' | 'skillDiff' | 'lineage' | 'guard' | 'monitor' | 'settings'

export type FrontendSurfaceContract = {
  route: FrontendRoute
  data_mode: DataMode
  backend_path?: string
  states: Partial<Record<RequestPhase, boolean>>
  notes: string
}
