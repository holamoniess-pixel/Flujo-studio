import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { Activity, AlertTriangle, Archive, ArrowRight, BarChart3, Bot, Boxes, Check, CheckCircle2, ChevronRight, CircleAlert, ClipboardCheck, Clock3, Cloud, Code2, FileStack, FileText, FlaskConical, FolderTree, Gauge, GitCompare, Globe2, History, KeyRound, LockKeyhole, MessageCircle, Network, Play, Plus, RefreshCw, Search, Send, ShieldCheck, Sparkles, TestTube2, Terminal, Trash2, UserRound, Users, WandSparkles, Workflow, X } from 'lucide-react'
import { backendFetch } from './backendClient'
import { DataStates, formatDate, formatTime, useBackendList, useBackendObject } from './surfaceBits'
import type { SurfaceProps } from './surfaceBits'
import type { AuditEvent, CredentialMetadata, FrontendRoute, GenerationJob } from './domain'

type P = SurfaceProps

function Card({ children, className = '' }: { children: ReactNode; className?: string }) { return <section className={`panel ${className}`}>{children}</section> }
function Head({ title, sub, action, click }: { title: string; sub?: string; action?: string; click?: () => void }) { return <div className="section-head"><div><h3>{title}</h3>{sub && <span>{sub}</span>}</div>{action && <button className="text-button" onClick={click}>{action}<ArrowRight size={14} /></button>}</div> }
function Pill({ children, tone = 'neutral' }: { children: ReactNode; tone?: string }) { return <span className={`status-pill ${tone}`}>{children}</span> }
function Stat({ value, label, tone = '' }: { value: string; label: string; tone?: string }) { return <div className="metric"><strong className={tone}>{value}</strong><span>{label}</span></div> }
function Row({ icon: I, title, detail, status = 'Ready', tone = 'neutral', click }: { icon?: any; title: string; detail: string; status?: string; tone?: string; click?: () => void }) { return <button className="control-row" onClick={click}><span className={`control-icon ${tone}`}>{I ? <I size={16} /> : <CircleAlert size={16} />}</span><span><b>{title}</b><small>{detail}</small></span><Pill tone={tone}>{status}</Pill><ChevronRight size={15} /></button> }
function Banner({ icon: I, title, detail, tone = 'coral' }: { icon?: any; title: string; detail: string; tone?: string }) { return <div className={`surface-banner ${tone}`}><span className="banner-icon">{I ? <I size={22} /> : <Sparkles size={22} />}</span><div><span className="panel-kicker">Flujo Studio / local backend</span><h2>{title}</h2><p>{detail}</p></div></div> }
function Toggle({ label, enabled, onClick }: { label: string; enabled: boolean; onClick: () => void }) { return <button className={`toggle-row ${enabled ? 'enabled' : ''}`} onClick={onClick} aria-pressed={enabled}><span className="toggle-track"><i /></span><span><b>{label}</b><small>{enabled ? 'On' : 'Off'}</small></span></button> }

type Project = { id: string; name: string; slug: string; created_at: string; updated_at: string }
type Campaign = { id: string; project_id: string; name: string; created_at: string; updated_at: string }

export function Projects({ go, announce }: P) {
  const { items: projects, loading, error, reload } = useBackendList<Project>('/api/projects')
  const [selected, setSelected] = useState('')
  const [name, setName] = useState('')
  const [forming, setForming] = useState(false)
  const { items: campaigns } = useBackendList<Campaign>(selected ? `/api/projects/${selected}/campaigns` : null)
  const create = () => {
    if (!name.trim()) return
    backendFetch<Project>('/api/projects', { method: 'POST', body: JSON.stringify({ name: name.trim(), slug: name.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').slice(0, 60) || 'project' }) })
      .then(created => { setName(''); setForming(false); setSelected(created.id); announce(`Project ${created.name} created in the local backend.`); reload() })
      .catch(() => announce('The project could not be created.'))
  }
  return <><div className="toolbar-row"><div className="search-field"><Search size={15} /><input placeholder="Search projects" /></div>{forming
    ? <div className="search-field"><input placeholder="Project name" value={name} onChange={e => setName(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') create() }} /><button className="primary-button compact-button" onClick={create}><Plus size={15} /> Create</button><button className="secondary-button compact-button" onClick={() => setForming(false)}><X size={14} /></button></div>
    : <button className="primary-button compact-button" onClick={() => setForming(true)}><Plus size={15} /> New project</button>}</div>
    <DataStates loading={loading} error={error} retry={reload} empty={projects.length === 0} emptyTitle="No projects recorded yet." emptyDetail="Create a project to give the local backend its first workspace.">
      <div className="registry-grid project-grid">{projects.map((project, i) => <Card className="project-card featured" key={project.id}><div className="project-card-top"><Pill tone={i === 0 ? 'positive' : 'neutral'}>{i === 0 ? 'Active' : 'Workspace'}</Pill></div><span className={`project-orb ${['coral', 'sage', 'amber'][i % 3]}`}><Boxes size={22} /></span><h3>{project.name}</h3><p>Slug {project.slug} · created {formatDate(project.created_at)}</p><div className="project-card-foot"><span>{selected === project.id ? `${campaigns.length} campaign(s)` : ''}</span><button className="text-button" onClick={() => setSelected(selected === project.id ? '' : project.id)}>{selected === project.id ? 'Hide campaigns' : 'Open project'} <ArrowRight size={14} /></button></div></Card>)}</div>
      {selected && <Card><Head title="Campaigns" sub={projects.find(p => p.id === selected)?.name || ''} />{campaigns.length === 0 ? <div className="empty-state"><Archive size={22} /><p>No campaigns in this project yet.</p></div> : <div className="campaign-table table-wrap"><table className="data-table"><thead><tr><th>Campaign</th><th>Created</th><th /></tr></thead><tbody>{campaigns.map(c => <tr key={c.id}><td><b>{c.name}</b></td><td>{formatDate(c.created_at)}</td><td><button className="icon-button subtle" onClick={() => go('library')} aria-label={`Open ${c.name}`}><ChevronRight size={15} /></button></td></tr>)}</tbody></table></div>}</Card>}
    </DataStates></>
}

export function Campaigns({ go, announce }: P) {
  const { items: projects } = useBackendList<Project>('/api/projects')
  const [projectId, setProjectId] = useState('project_northstar')
  const { items: campaigns, loading, error, reload } = useBackendList<Campaign>(`/api/projects/${projectId}/campaigns`)
  const [name, setName] = useState('')
  const create = () => {
    if (!name.trim()) return
    backendFetch<Campaign>('/api/campaigns', { method: 'POST', body: JSON.stringify({ project_id: projectId, name: name.trim() }) })
      .then(() => { setName(''); announce('Campaign created in the local backend.'); reload() })
      .catch(() => announce('The campaign could not be created.'))
  }
  return <><div className="toolbar-row"><select className="filter-button" value={projectId} onChange={e => setProjectId(e.target.value)} aria-label="Project">{projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}{projects.length === 0 && <option value="project_northstar">Default project</option>}</select><div className="search-field"><input placeholder="Campaign name" value={name} onChange={e => setName(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') create() }} /><button className="primary-button compact-button" onClick={create}><Plus size={15} /> New campaign</button></div></div>
    <Card><Head title="Campaign workspace" sub="Each campaign carries its own references, genome, blueprint, and outcomes" />
      <DataStates loading={loading} error={error} retry={reload} empty={campaigns.length === 0} emptyTitle="No campaigns recorded yet." emptyDetail="Create a campaign to scope references, genomes, and blueprints.">
        <div className="campaign-table table-wrap"><table className="data-table"><thead><tr><th>Campaign</th><th>Created</th><th>Updated</th><th /></tr></thead><tbody>{campaigns.map(c => <tr key={c.id}><td><b>{c.name}</b><small>{c.id}</small></td><td>{formatDate(c.created_at)}</td><td>{formatDate(c.updated_at)}</td><td><button className="icon-button subtle" onClick={() => go('library')} aria-label={`Open ${c.name}`}><ChevronRight size={15} /></button></td></tr>)}</tbody></table></div>
      </DataStates></Card></>
}

type Artifact = { id: string; project_id: string; kind: string; name: string; path?: string | null; status: string; created_at: string }

export function Library({ inspect }: P) {
  const [tab, setTab] = useState('All')
  const { items: artifacts, loading, error, reload } = useBackendList<Artifact>('/api/artifacts?project_id=project_northstar&limit=200')
  const tabs = ['All', 'reference', 'generated', 'composition', 'export']
  const filtered = artifacts.filter(a => tab === 'All' || a.kind === tab || (tab === 'reference' && a.kind === 'reference'))
  const countBy = (kind: string) => artifacts.filter(a => a.kind === kind).length
  return <><div className="toolbar-row"><div className="filter-row">{tabs.map(x => <button key={x} className={`filter-button ${tab === x ? 'active' : ''}`} onClick={() => setTab(x)}>{x === 'All' ? 'All' : x[0].toUpperCase() + x.slice(1)}</button>)}</div><div className="search-field compact-search"><Search size={15} /><input placeholder="Search artifacts" /></div></div>
    <DataStates loading={loading} error={error} retry={reload} empty={artifacts.length === 0} emptyTitle="No artifacts recorded yet." emptyDetail="Artifacts appear here when the local engine produces or imports them.">
      <div className="library-kpis"><Stat value={String(artifacts.length)} label="Total artifacts" /><Stat value={String(countBy('generated'))} label="Generated assets" /><Stat value={String(countBy('export'))} label="Exports" tone="warning" /><Stat value={String(countBy('composition'))} label="Compositions" /></div>
      <div className="artifact-grid">{filtered.map(a => <button className="artifact-card" key={a.id} onClick={() => inspect('artifact', a.name, `${a.kind} artifact · status ${a.status}. Provenance is available in Artifact Lineage.`)}><span className="artifact-preview preview-slate"><FileStack size={21} /></span><span><b>{a.name}</b><small>{a.kind} · {a.status} · {formatDate(a.created_at)}</small></span><ChevronRight size={15} /></button>)}</div>
    </DataStates></>
}

type EntityRow = { id: string; name: string; kind: string; role: string | null; status: string; created_at: string }

export function Entities({ go, inspect }: P) {
  const { items: entities, loading, error, reload } = useBackendList<EntityRow>('/api/entities?project_id=project_northstar')
  return <><Banner icon={Users} title="Reference Identity" detail="Turn source images into stable characters, products, locations, and generation constraints." tone="amber" />
    <DataStates loading={loading} error={error} retry={reload} empty={entities.length === 0} emptyTitle="No identity entities recorded yet." emptyDetail="Add references with a character, product, location, or identity role in Intake and they will appear here.">
      <div className="entity-kpis"><Stat value={String(entities.length).padStart(2, '0')} label="Entities" /></div>
      <div className="entity-grid">{entities.map((x, i) => <Card className="entity-card" key={x.id}><div className={`entity-avatar ${['coral', 'sage', 'amber', 'slate'][i % 4]}`}><UserRound size={21} /></div><div className="entity-card-head"><div><h3>{x.name}</h3><small>{x.role || x.kind} · {x.status}</small></div><Pill tone="positive">{x.status}</Pill></div><button className="soft-action" onClick={() => inspect('entity', x.name, `${x.name} identity record (${x.role || x.kind}) recorded ${formatDate(x.created_at)}.`)}>Inspect identity <ArrowRight size={14} /></button></Card>)}</div>
      <Card><Head title="Identity handoffs" sub="Where entities are used across the current flow" /><Row icon={GitCompare} title="Review Queue" detail="Identity verification is blocking the next batch" status="Review" tone="warning" click={() => go('qa')} /></Card>
    </DataStates></>
}

type Genome = { id: string; project_id: string; name: string; version: string; patterns_json: string; evidence_ids_json: string; created_at: string }

function parseJsonArray(value: string): any[] {
  try { const parsed = JSON.parse(value || '[]'); return Array.isArray(parsed) ? parsed : [] } catch { return [] }
}

export function GenomeCompare({ }: P) {
  const { items: genomes, loading, error, reload } = useBackendList<Genome>('/api/genomes?project_id=project_northstar')
  const [aId, setAId] = useState('')
  const [bId, setBId] = useState('')
  const a = genomes.find(g => g.id === (aId || genomes[0]?.id))
  const b = genomes.find(g => g.id === (bId || genomes[1]?.id || genomes[0]?.id))
  const patternsOf = (g?: Genome) => parseJsonArray(g?.patterns_json || '').map(p => typeof p === 'string' ? p : JSON.stringify(p))
  const aPatterns = patternsOf(a)
  const bPatterns = patternsOf(b)
  const rows = Array.from(new Set([...aPatterns, ...bPatterns])).slice(0, 12)
  return <><Banner icon={GitCompare} title="Creative Genome Comparison" detail="Compare reusable creative grammar across persisted genomes." tone="coral" />
    <DataStates loading={loading} error={error} retry={reload} empty={genomes.length === 0} emptyTitle="No genomes recorded yet." emptyDetail="Genomes are synthesized from evidence and stored in the local backend.">
      <div className="compare-selectors"><label>Genome A<select value={a?.id || ''} onChange={e => setAId(e.target.value)}>{genomes.map(g => <option key={g.id} value={g.id}>{g.name} {g.version}</option>)}</select></label><GitCompare size={20} /><label>Genome B<select value={b?.id || ''} onChange={e => setBId(e.target.value)}>{genomes.map(g => <option key={g.id} value={g.id}>{g.name} {g.version}</option>)}</select></label></div>
      <Card><Head title="Grammar comparison" sub={`${a?.name || '--'} vs ${b?.name || '--'}`} />{rows.length === 0 ? <div className="empty-state"><GitCompare size={22} /><p>These genomes carry no parsed patterns to compare.</p></div> : <div className="comparison-table"><div className="comparison-head"><span>Pattern</span><b>{a?.name}</b><b>{b?.name}</b></div>{rows.map(pattern => <div className="comparison-row" key={pattern}><span>{pattern.slice(0, 60)}</span><b>{aPatterns.includes(pattern) ? 'Present' : '--'}</b><b>{bPatterns.includes(pattern) ? 'Present' : '--'}</b></div>)}</div>}</Card>
    </DataStates></>
}

type Advisor = { id: string; name: string; role: string; status: string; confidence?: number; capabilities?: string[] }
type GovernorDecision = { id: string; status: string; task: string; fused_answer?: string; confidence?: number; selected_advisors?: any[]; opinions?: any[]; conflict?: any; created_at?: string }

export function Council({ announce, inspect }: P) {
  const { items: advisors, loading: advisorsLoading, error: advisorsError, reload: reloadAdvisors } = useBackendList<Advisor>('/api/governor/advisors')
  const [gateway, setGateway] = useState<{ simulator_only?: boolean } | null>(null)
  const [question, setQuestion] = useState('Is this camera movement or character movement?')
  const [asking, setAsking] = useState(false)
  const [decision, setDecision] = useState<GovernorDecision | null>(null)
  const [askError, setAskError] = useState<unknown>(null)
  useEffect(() => { backendFetch<{ gateway?: { simulator_only?: boolean } }>('/health').then(h => setGateway(h.gateway || null)).catch(() => setGateway(null)) }, [])
  const ask = () => {
    if (!question.trim() || asking) return
    setAsking(true); setAskError(null)
    backendFetch<GovernorDecision>('/api/governor/decide', { method: 'POST', body: JSON.stringify({ task: question.trim(), max_advisors: Math.min(3, advisors.length || 3) }) })
      .then(result => { setDecision(result); announce('Council decision recorded from the local Governor.') })
      .catch(cause => { setAskError(cause); announce('The council request could not be completed.') })
      .finally(() => setAsking(false))
  }
  const simulatorOnly = gateway?.simulator_only
  return <><Banner icon={Users} title="Council Lab" detail="Manually test the advisor society against the local Governor." tone="coral" />
    {simulatorOnly !== false && <div className="notice-strip"><AlertTriangle size={16} /><span><strong>Simulator state.</strong> The Model Gateway reports no live provider credential, so advisor consultations run against the local simulator path.</span></div>}
    <div className="section-grid two-one"><Card><Head title="Question" sub="Ask a cross-domain creative decision" /><textarea className="lab-input" value={question} onChange={e => setQuestion(e.target.value)} rows={5} />
      <div className="advisor-picks">{advisors.map(a => <label key={a.id}><input type="checkbox" defaultChecked /><span>{a.name}</span></label>)}{advisors.length === 0 && !advisorsLoading && <span>No advisors registered.</span>}</div>
      <button className="primary-button full-button" onClick={ask} disabled={asking}><MessageCircle size={15} /> {asking ? 'Consulting…' : 'Ask Council'}</button>
      {askError ? <div className="chat-inline-error" role="alert"><b>Request failed</b><span>The council request could not be completed.</span></div> : null}</Card>
      <Card><Head title="Governor response" sub="Decision with evidence behind it" />{decision ? <><div className="council-response"><Pill tone={decision.status === 'decided' ? 'positive' : 'warning'}><CheckCircle2 size={13} /> {decision.status}</Pill><h3>{decision.fused_answer || 'Decision recorded without a fused answer.'}</h3><p>{decision.task}</p></div><Row icon={Network} title="Advisors consulted" detail={`${(decision.selected_advisors || []).length} advisor(s) · confidence ${decision.confidence ?? '--'}`} status="Recorded" tone="sage" click={() => inspect('council', 'Council decision', `Decision ${decision.id} recorded ${decision.created_at || ''}.`)} /></> : <div className="empty-state"><Bot size={22} /><p>Ask the council to see advisor reasoning and evidence.</p></div>}</Card></div>
    <Card><Head title="Advisor selection" sub="Registered advisors in the local Governor" />
      <DataStates loading={advisorsLoading} error={advisorsError} retry={reloadAdvisors} empty={advisors.length === 0} emptyTitle="No advisors registered." emptyDetail="Advisors are registered by the Governor controller in the local backend.">
        <div className="advisor-grid compact-advisors">{advisors.map((x, i) => <div className="advisor-card" key={x.id}><span className={`advisor-icon ${['coral', 'amber', 'sage', 'slate'][i % 4]}`}><Bot size={16} /></span><span>{x.name}</span><strong>{x.role}</strong><small>{x.status}</small></div>)}</div>
      </DataStates></Card></>
}

type Tool = { id: string; project_id: string; name: string; version: string; state: string; scope: string; security_status: string; last_used_at?: string | null; updated_at: string }

export function ToolLab({ go, inspect, announce }: P) {
  const { items: tools, loading, error, reload } = useBackendList<Tool>('/api/tools?project_id=project_northstar')
  const [name, setName] = useState('')
  const transition = (tool: Tool, state: string) => {
    backendFetch(`/api/tools/${tool.id}/transition`, { method: 'POST', body: JSON.stringify({ state, actor: 'human-operator', reason: `Manual transition from Tool Lab` }) })
      .then(() => { announce(`${tool.name} moved to ${state}.`); reload() })
      .catch(() => announce('The tool transition could not be recorded.'))
  }
  const register = () => {
    if (!name.trim()) return
    backendFetch('/api/tools', { method: 'POST', body: JSON.stringify({ name: name.trim(), version: 'v0.1', scope: 'project' }) })
      .then(() => { setName(''); announce('Tool registered in the local lifecycle.'); reload() })
      .catch(() => announce('The tool could not be registered.'))
  }
  const byState = (state: string) => tools.filter(t => t.state === state).length
  return <><Banner icon={Code2} title="Tool Lab" detail="Plan, secure, sandbox, evaluate, and either destroy or promote temporary tools." tone="amber" />
    <div className="tool-flow"><span>Tool Planner</span><ArrowRight size={15} /><span>Security validation</span><ArrowRight size={15} /><span>Sandbox</span><ArrowRight size={15} /><span>Use</span><ArrowRight size={15} /><span>Evaluate</span></div>
    <div className="toolbar-row"><div className="search-field"><input placeholder="Tool name" value={name} onChange={e => setName(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') register() }} /><button className="primary-button compact-button" onClick={register}><Plus size={15} /> Register tool</button></div><button className="secondary-button compact-button" onClick={reload}><RefreshCw size={14} /> Refresh</button></div>
    <DataStates loading={loading} error={error} retry={reload} empty={tools.length === 0} emptyTitle="No tools in the lifecycle yet." emptyDetail="Register a tool to start its governed lifecycle in the local backend.">
      <div className="section-grid two-one"><Card><Head title="Tool inventory" sub="Tools separated by lifecycle" />
        <Row icon={Code2} title="Created" detail={`${byState('CREATED')} tool(s) awaiting validation`} status={String(byState('CREATED'))} tone="neutral" />
        <Row icon={Clock3} title="Testing" detail={`${byState('TESTING')} tool(s) under evaluation`} status={String(byState('TESTING'))} tone="warning" />
        <Row icon={CheckCircle2} title="Active" detail={`${byState('ACTIVE')} tool(s) in use`} status={String(byState('ACTIVE'))} tone="positive" />
        <Row icon={Archive} title="Retired" detail={`${byState('RETIRED') + byState('DEPRECATED')} tool(s) retired or deprecated`} status={String(byState('RETIRED') + byState('DEPRECATED'))} tone="slate" /></Card>
        <Card><Head title="Lifecycle detail" sub="Transition states explicitly" />{tools.slice(0, 6).map(t => <div className="review-item" key={t.id}><span><b>{t.name}</b><small>{t.version} · {t.scope} · security {t.security_status}</small></span><Pill tone={t.state === 'ACTIVE' ? 'positive' : t.state === 'RETIRED' ? 'neutral' : 'warning'}>{t.state}</Pill><div className="review-actions">{t.state !== 'ACTIVE' && <button onClick={() => transition(t, 'ACTIVE')}><Check size={13} /> Activate</button>}{t.state !== 'RETIRED' && <button onClick={() => transition(t, 'RETIRED')}><Trash2 size={13} /> Retire</button>}<button onClick={() => inspect('tool', t.name, `Lifecycle state ${t.state}; security ${t.security_status}.`)}><ChevronRight size={14} /></button></div></div>)}</Card></div>
    </DataStates>
    <Card><Head title="Tool security" sub="Lifecycle transitions are audited" /><div className="security-strip"><Pill tone="positive"><Check size={13} /> Sandbox policy loaded</Pill><Pill tone="positive"><Check size={13} /> Permission boundary loaded</Pill><Pill tone="warning"><AlertTriangle size={13} /> Execution disconnected</Pill></div><button className="soft-action" onClick={() => go('security')}><ShieldCheck size={15} /> Security detail</button></Card></>
}

type ToolRun = { id: string; project_id: string; operation: string; status: string; exit_code?: number | null; duration_ms?: number | null; created_at: string }

export function MediaEngine({ go, announce }: P) {
  const { items: runs, loading: runsLoading, error: runsError, reload: reloadRuns } = useBackendList<ToolRun>('/api/tool-runs?project_id=project_northstar&limit=20')
  const { data: health, loading: healthLoading } = useBackendObject<{ ffmpeg?: { status: string }; ffprobe?: { status: string } }>('/api/media/health')
  return <><Banner icon={Boxes} title="Flujo Media & Tool Engine" detail="A governed media layer for FFmpeg, FFprobe, Remotion, visual analytics, image tools, audio tools, and sandboxed execution." tone="coral" />
    <div className="media-engine-kpis"><Stat value={String(runs.filter(r => r.status === 'running').length).padStart(2, '0')} label="Active runs" /><Stat value={String(runs.length).padStart(2, '0')} label="Recorded runs" /><Stat value={healthLoading ? '…' : (health?.ffmpeg?.status || 'unknown')} label="FFmpeg" tone={health?.ffmpeg?.status === 'healthy' ? 'positive' : 'warning'} /><Stat value={healthLoading ? '…' : (health?.ffprobe?.status || 'unknown')} label="FFprobe" tone={health?.ffprobe?.status === 'healthy' ? 'positive' : 'warning'} /></div>
    <div className="media-pipeline"><span><Bot size={14} /> Governor</span><ArrowRight size={14} /><span><WandSparkles size={14} /> Media Planner</span><ArrowRight size={14} /><span><Workflow size={14} /> Tool Router</span><ArrowRight size={14} /><span className="current"><Boxes size={14} /> Media Engine</span></div>
    <div className="section-grid two-one"><Card><Head title="Engine workers" sub="Dependency health from the local backend" />
      <Row icon={Terminal} title="FFmpeg" detail={health?.ffmpeg?.status || 'health unknown'} status={health?.ffmpeg?.status === 'healthy' ? 'Ready' : 'Blocked'} tone={health?.ffmpeg?.status === 'healthy' ? 'positive' : 'critical'} click={() => go('mediaTools')} />
      <Row icon={Search} title="FFprobe" detail={health?.ffprobe?.status || 'health unknown'} status={health?.ffprobe?.status === 'healthy' ? 'Ready' : 'Blocked'} tone={health?.ffprobe?.status === 'healthy' ? 'positive' : 'critical'} click={() => go('terminal')} />
      <Row icon={Sparkles} title="Remotion" detail="Compositions render in the Composition Lab" status="Lab" tone="coral" click={() => go('composition')} /></Card>
      <Card><Head title="Recorded tool runs" sub="Persisted media operations" action="Refresh" click={reloadRuns} />
        <DataStates loading={runsLoading} error={runsError} retry={reloadRuns} empty={runs.length === 0} emptyTitle="No tool runs recorded yet." emptyDetail="Media operations executed through the governed tool API will appear here.">
          {runs.slice(0, 6).map(r => <div className="review-item" key={r.id}><span><b>{r.operation}</b><small>{formatTime(r.created_at)} · {r.duration_ms ?? '--'} ms · exit {r.exit_code ?? '--'}</small></span><Pill tone={r.status === 'completed' ? 'positive' : r.status === 'failed' ? 'critical' : 'warning'}>{r.status}</Pill></div>)}
        </DataStates></Card></div>
    <Card><Head title="Media engine policy" sub="The Governor never executes raw shell commands" /><div className="security-strip"><Pill tone="positive"><Check size={13} /> Tool API only</Pill><Pill tone="positive"><Check size={13} /> Sandbox boundary</Pill><Pill tone="warning"><CircleAlert size={13} /> Human gate for host access</Pill></div><button className="soft-action" onClick={() => announce('Media engine policy is enforced by the local backend.')}>Explain policy <ArrowRight size={14} /></button></Card></>
}

export function MediaTools({ go, inspect }: P) {
  const { data: health } = useBackendObject<{ ffmpeg?: { status: string }; ffprobe?: { status: string } }>('/api/media/health')
  const categories: Array<[string, string[], string]> = [
    ['Analysis', ['ffprobe metadata', 'frame extractor', 'scene detector', 'OCR', 'stream probe'], health?.ffprobe?.status === 'healthy' ? 'positive' : 'warning'],
    ['Editing', ['trim', 'concat', 'crop', 'normalize-audio', 'subtitle', 'transcode'], health?.ffmpeg?.status === 'healthy' ? 'positive' : 'warning'],
    ['Composition', ['Remotion composition', 'caption compositor', 'transition compositor'], 'positive'],
    ['Validation', ['integrity checker', 'sync checker', 'duration checker', 'black-frame detector'], health?.ffmpeg?.status === 'healthy' ? 'positive' : 'warning'],
  ]
  return <><Banner icon={Code2} title="Media Tool Registry" detail="The operations the local media engine exposes through its governed tool API. Statuses reflect real dependency health." tone="amber" />
    <div className="tool-registry-grid">{categories.map(([category, items, tone]) => <Card className="media-tool-category" key={category}><div className="category-head"><span className="panel-kicker">{category}</span><Pill tone={tone}>{items.length} operations</Pill></div>{items.map(item => <button className="tool-registry-row" key={item} onClick={() => inspect('media-tool', item, `${item} is exposed as a governed Media Tool API operation. It requires a policy decision before execution.`)}><span className="tool-status-dot" /><b>{item}</b><ChevronRight size={14} /></button>)}</Card>)}</div>
    <Card><Head title="Tool API contract" sub="Human-readable intent becomes a controlled operation" /><div className="api-example-grid"><div><span className="panel-kicker">Intent</span><p>Trim Scene 4 from 00:12.3 to 00:16.7.</p></div><ArrowRight size={15} /><div><span className="panel-kicker">Operation</span><code>POST /api/media/operation</code></div><ArrowRight size={15} /><div><span className="panel-kicker">Policy</span><Pill tone="positive">MEDIA_WRITE / Allowed</Pill></div></div><button className="primary-button" onClick={() => go('terminal')}><Terminal size={15} /> Open Flujo Terminal</button></Card></>
}

export function Composition({ go, inspect, announce }: P) {
  const { items: compositions, loading, error, reload } = useBackendList<Artifact>('/api/compositions?project_id=project_northstar')
  const [name, setName] = useState('AdTemplate.tsx')
  const [patch, setPatch] = useState("+ caption\ny: 920, kinetic: 'rise', emphasis: 'citrus'")
  const save = () => {
    if (!name.trim()) return
    backendFetch('/api/compositions', { method: 'POST', body: JSON.stringify({ project_id: 'project_northstar', name: name.trim(), patch, status: 'Working copy' }) })
      .then(() => { announce('Composition working copy saved in the local backend.'); reload() })
      .catch(() => announce('The composition could not be saved.'))
  }
  const latest = compositions[0]
  const latestPatch = (() => { try { return (latest as any)?.metadata_json ? JSON.parse((latest as any).metadata_json).patch : '' } catch { return '' } })()
  return <><Banner icon={Sparkles} title="Remotion Composition Lab" detail="Working composition copies are persisted in the local backend. Render stays sandboxed; promotion requires QA." tone="coral" />
    <div className="composition-header"><div><span className="panel-kicker">Production composition</span><h2>{latest?.name || 'No composition yet'}</h2><p>Video + code + assets + parameters remain reproducible.</p></div><Pill tone={latest ? 'warning' : 'neutral'}>{latest?.status || 'Empty'}</Pill></div>
    <div className="section-grid two-one"><Card><Head title="Composition patch" sub="Persisted working copy" />
      <label className="wizard-field"><span>Composition name</span><input value={name} onChange={e => setName(e.target.value)} /></label>
      <label className="wizard-field"><span>Patch</span><textarea value={patch} onChange={e => setPatch(e.target.value)} rows={5} className="lab-input" /></label>
      <div className="hero-actions"><button className="primary-button" onClick={save}><Play size={15} /> Save working copy</button><button className="secondary-button" onClick={() => inspect('composition', 'Composition history', `${compositions.length} persisted working cop(ies) in the local backend.`)}>Inspect history</button></div>
      {latestPatch && <div className="code-block composition-code"><div className="code-line"><span>01</span><b>latest patch</b><p>{String(latestPatch).slice(0, 200)}</p></div></div>}</Card>
      <Card><Head title="Render gates" sub="Promotion path" />
        <Row icon={FlaskConical} title="Sandbox render" detail="Isolated composition copy" status={compositions.length > 0 ? 'Saved' : 'Pending'} tone={compositions.length > 0 ? 'positive' : 'warning'} />
        <Row icon={ClipboardCheck} title="QA" detail="Black frames, sync, duration, quality" status="Pending" tone="neutral" click={() => go('qa')} />
        <Row icon={GitCompare} title="Diff review" detail="Human approval before promote" status="Required" tone="warning" />
        <Row icon={Archive} title="Promote" detail="Write to production composition" status="Gated" tone="neutral" /></Card></div>
    <Card><Head title="Persisted compositions" sub="Every saved working copy" />
      <DataStates loading={loading} error={error} retry={reload} empty={compositions.length === 0} emptyTitle="No compositions saved yet." emptyDetail="Save a working copy to start the persisted composition history.">
        {compositions.map(c => <div className="review-item" key={c.id}><span><b>{c.name}</b><small>{formatDate(c.created_at)} · {c.status}</small></span><Pill tone="warning">{c.status}</Pill></div>)}
      </DataStates></Card></>
}

type Candidate = { id: string; project_id: string; candidate_type: string; task_key: string; proposal_json: string; status: string; created_at: string; updated_at: string }

function candidateTitle(c: Candidate): string {
  try { const proposal = JSON.parse(c.proposal_json || '{}'); return String(proposal.title || proposal.name || c.task_key) } catch { return c.task_key }
}

export function Experiments({ go, announce }: P) {
  const { items: candidates, loading, error, reload } = useBackendList<Candidate>('/api/adaptation/candidates?project_id=project_northstar&limit=50')
  const promote = (c: Candidate) => {
    backendFetch(`/api/adaptation/candidates/${c.id}/promote`, { method: 'POST', body: JSON.stringify({}) })
      .then(() => { announce(`Candidate ${candidateTitle(c)} sent for promotion review.`); go('adaptationCenter') })
      .catch(() => announce('The promotion request could not be recorded.'))
  }
  return <><Banner icon={FlaskConical} title="Experiment Lab" detail="Adaptation candidates from the local backend: compare, evaluate, and promote with evidence." tone="coral" />
    <DataStates loading={loading} error={error} retry={reload} empty={candidates.length === 0} emptyTitle="No experiment candidates yet." emptyDetail="Adaptation candidates appear here when the local Adaptation engine proposes them.">
      <div className="experiment-grid">{candidates.slice(0, 6).map((c, i) => <div className={`experiment-card ${i === 0 ? 'selected' : ''}`} key={c.id}><span className="experiment-label">{String.fromCharCode(65 + i)}</span><h3>{candidateTitle(c)}</h3><p>{c.candidate_type} · {c.task_key}</p><span className="bar-track"><i style={{ width: `${Math.min(100, 40 + i * 12)}%` }} /></span><Pill tone={c.status === 'promoted' ? 'positive' : i === 0 ? 'positive' : 'neutral'}>{i === 0 ? 'Leading' : c.status}</Pill><div className="hero-actions"><button className="secondary-button compact-button" onClick={() => promote(c)}>Send to Adaptation <ArrowRight size={13} /></button></div></div>)}</div>
      <Card><Head title="Candidate states" sub="Persisted adaptation state" />{candidates.map(c => <div className="review-item" key={c.id}><span><b>{candidateTitle(c)}</b><small>{c.candidate_type} · updated {formatDate(c.updated_at)}</small></span><Pill tone={c.status === 'promoted' ? 'positive' : 'warning'}>{c.status}</Pill></div>)}</Card>
    </DataStates></>
}

type UsagePayload = { events: Array<{ id: string; model_id?: string | null; task?: string | null; requests: number; input_tokens: number; output_tokens: number; estimated_cost: number; created_at: string }>; totals: { requests: number; input_tokens: number; output_tokens: number; duration_ms: number; estimated_cost: number } }

export function Analytics({ go }: P) {
  const { data, loading, error, reload } = useBackendObject<UsagePayload>('/api/usage?project_id=project_northstar&limit=200')
  const totals = data?.totals
  return <><Banner icon={BarChart3} title="Analytics / Outcomes" detail="Local usage and learning signals from the persisted usage ledger. No platform analytics are connected." tone="sage" />
    <DataStates loading={loading} error={error} retry={reload} empty={!totals || totals.requests === 0} emptyTitle="No usage events recorded yet." emptyDetail="Usage events are recorded when the local engine performs model or media work.">
      <div className="analytics-kpis"><Stat value={String(totals?.requests || 0)} label="Requests" /><Stat value={String(((totals?.input_tokens || 0) + (totals?.output_tokens || 0)) / 1000) + 'k'} label="Tokens" /><Stat value={`$${(totals?.estimated_cost || 0).toFixed(2)}`} label="Estimated cost" /><Stat value={`${Math.round((totals?.duration_ms || 0) / 1000)}s`} label="Engine time" /></div>
      <div className="section-grid two-one"><Card><Head title="Usage events" sub="Persisted local usage ledger" />{(data?.events || []).slice(0, 8).map(e => <div className="outcome-row" key={e.id}><span><b>{e.model_id || 'local engine'}</b><small>{e.task || 'task'} · {formatDate(e.created_at)}</small></span><strong>{e.requests}<small>req</small></strong><Pill tone="neutral">${Number(e.estimated_cost || 0).toFixed(2)}</Pill></div>)}</Card>
        <Card><Head title="Evolution signal" sub="Outcome feedback can update recipes" /><div className="evolution-signal"><Sparkles size={22} /><h3>Local signals only.</h3><p>Connect outcomes to decisions once the engine records them.</p><button className="primary-button compact-button" onClick={() => go('evolution')}>Open evolution</button></div></Card></div>
    </DataStates></>
}

type DriftRow = { id: string; candidate_id: string; observed_score: number; threshold: number; state: string; created_at: string }

export function Drift({ go }: P) {
  const { items: drift, loading, error, reload } = useBackendList<DriftRow>('/api/adaptation/drift?project_id=project_northstar&limit=50')
  return <><Banner icon={Activity} title="Model and Skill Drift" detail="Persisted drift observations from the local Adaptation engine." tone="warning" />
    <DataStates loading={loading} error={error} retry={reload} empty={drift.length === 0} emptyTitle="No drift recorded." emptyDetail="Drift observations are recorded when the Adaptation engine detects stale confidence.">
      <Card><Head title="Drift signals" sub="Observed vs threshold" />{drift.map(d => <div className="drift-row" key={d.id}><span><b>{d.candidate_id}</b><small>observed {d.observed_score}</small></span><strong>{d.state}</strong><Pill tone={d.state === 'stable' ? 'positive' : 'warning'}>{d.state}</Pill></div>)}
        <button className="soft-action" onClick={() => go('adaptationCenter')}>Open Adaptation Engine <ArrowRight size={14} /></button></Card>
    </DataStates></>
}

type GuardReview = { id: string; project_id: string; job_id?: string | null; artifact_id?: string | null; reason: string; status: string; decision: string; created_at: string }

export function ReviewQueue({ go, inspect, announce }: P) {
  const { items: reviews, loading, error, reload } = useBackendList<GuardReview>('/api/projects/project_northstar/guard/reviews?limit=50')
  const decide = (review: GuardReview, decision: 'APPROVE' | 'REJECT') => {
    backendFetch(`/api/guard/reviews/${review.id}/decision`, { method: 'POST', body: JSON.stringify({ reviewer_id: 'human-operator', decision, note: `Manual ${decision.toLowerCase()} from Review Queue` }) })
      .then(() => { announce(`${review.id} ${decision === 'APPROVE' ? 'approved' : 'rejected'}.`); reload() })
      .catch(() => announce('The review decision could not be recorded.'))
  }
  const pending = reviews.filter(r => (r.decision || 'PENDING') === 'PENDING')
  return <><Banner icon={ClipboardCheck} title="Review Queue" detail="Guard review records from the local backend. Decisions are persisted, not staged." tone="amber" />
    <div className="review-queue-head"><Stat value={String(pending.length).padStart(2, '0')} label="Needs review" /><div><Pill tone="warning"><CircleAlert size={13} /> Human checkpoint</Pill><small>Approve or reject; the decision is written to the local backend.</small></div></div>
    <Card><Head title="Needs review" sub="The safety valve of the creative automation system" />
      <DataStates loading={loading} error={error} retry={reload} empty={reviews.length === 0} emptyTitle="No review records yet." emptyDetail="Guard reviews appear here when the safety pipeline flags work for a human.">
        {pending.map((x, i) => <div className="review-item" key={x.id}><span className="review-item-index">0{i + 1}</span><span><b>{x.id}</b><small>{x.reason}</small></span><Pill tone="warning">Review</Pill><div className="review-actions"><button onClick={() => decide(x, 'APPROVE')}><Check size={13} /> Approve</button><button onClick={() => decide(x, 'REJECT')}><X size={13} /> Reject</button><button onClick={() => inspect('review', x.id, x.reason)}><Bot size={13} /> Ask Governor</button><button onClick={() => go('guard')}><ChevronRight size={14} /></button></div></div>)}
        {reviews.filter(r => (r.decision || 'PENDING') !== 'PENDING').length > 0 && <><Head title="Decided" sub="Persisted human decisions" />{reviews.filter(r => (r.decision || 'PENDING') !== 'PENDING').slice(0, 5).map(x => <div className="review-item" key={x.id}><span><b>{x.id}</b><small>{x.reason}</small></span><Pill tone={x.decision === 'APPROVE' ? 'positive' : 'critical'}>{x.decision}</Pill></div>)}</>}
      </DataStates></Card></>
}

export function Credentials({ go, announce }: P) {
  const { items: credentials, loading, error, reload } = useBackendList<CredentialMetadata>('/api/credentials')
  const { items: providers } = useBackendList<{ id: string; name: string; masked_key?: string | null; status: string }>('/api/providers')
  return <><Banner icon={KeyRound} title="Credentials" detail="Provider access stored in the secure credential layer. Secrets are redacted and never shown in full." tone="slate" />
    <DataStates loading={loading} error={error} retry={reload} empty={credentials.length === 0 && providers.length === 0} emptyTitle="No credentials stored." emptyDetail="Add provider credentials in Settings → Models. Values are masked after saving.">
      <div className="credential-grid">{credentials.map(c => <Card className="credential-card" key={c.credential_id}><div className="credential-top"><span className="account-logo"><KeyRound size={19} /></span><Pill tone={c.status === 'active' ? 'positive' : 'neutral'}>{c.status}</Pill></div><h3>{c.provider}</h3><p>{c.display_name} · scope {c.scope}</p><code>{c.masked_value}</code><div className="credential-foot"><span>Updated <b>{formatDate(c.updated_at)}</b></span><button className="secondary-button compact-button" onClick={() => go('settings')}>Manage</button></div></Card>)}
        {providers.map(p => <Card className="credential-card" key={p.id}><div className="credential-top"><span className="account-logo"><Cloud size={19} /></span><Pill tone={p.status === 'configured' ? 'positive' : 'neutral'}>{p.status}</Pill></div><h3>{p.name}</h3><p>Provider record</p><code>{p.masked_key || 'no key stored'}</code><div className="credential-foot"><span>Local provider registry</span><button className="secondary-button compact-button" onClick={() => go('settings')}>Manage</button></div></Card>)}</div>
    </DataStates>
    <Card><Head title="Credential safety" sub="Secrets are redacted and never shown in full" /><div className="security-strip"><Pill tone="positive"><Check size={13} /> Redaction enabled</Pill><Pill tone="positive"><Check size={13} /> Provider scopes visible</Pill><Pill tone="warning"><RefreshCw size={13} /> Rotation via credential API</Pill></div><button className="soft-action" onClick={() => announce('Credential operations are audited in the local backend.')}>Explain audit <ArrowRight size={14} /></button></Card></>
}

export function Security({ go, inspect }: P) {
  const { items: credentials } = useBackendList<CredentialMetadata>('/api/credentials')
  const { data: policies } = useBackendObject<{ policies?: any[] }>('/api/guard/policies')
  const { data: consent } = useBackendObject<{ face_consent?: number | boolean; voice_consent?: number | boolean }>('/api/projects/project_northstar/consent')
  const { items: errors } = useBackendList<{ id: string; message: string; source: string; created_at: string }>('/api/diagnostics/errors?limit=10')
  const cards: Array<[string, string, 'positive' | 'warning', any]> = [
    ['Secrets', `${credentials.length} credential(s) in the secure store; values are masked`, 'positive', KeyRound],
    ['Prompt Injection Guard', `${(policies as any)?.policies?.length ?? '--'} guard polic(ies) loaded`, 'positive', ShieldCheck],
    ['Publishing Permissions', `Face consent ${consent?.face_consent ? 'granted' : 'not granted'} · voice consent ${consent?.voice_consent ? 'granted' : 'not granted'}`, consent?.face_consent || consent?.voice_consent ? 'warning' : 'positive', Globe2],
    ['Security incidents', `${errors.length} recent error event(s) in the local log`, errors.length ? 'warning' : 'positive', CircleAlert],
  ]
  return <><Banner icon={ShieldCheck} title="Security Center" detail="Secret isolation, sandboxing, untrusted content, and permission boundaries from the local backend." tone="slate" />
    <div className="security-grid">{cards.map(([name, detail, tone, I]) => <button className="security-card" key={name} onClick={() => inspect('security', name, detail)}><span className={`security-icon ${tone}`}><I size={18} /></span><span><b>{name}</b><small>{detail}</small></span><Pill tone={tone}>{tone === 'positive' ? 'Healthy' : 'Review'}</Pill></button>)}</div>
    <Card><Head title="Security incidents" sub="Recent error events route to Doctor and Review Queue" />
      {errors.length === 0 ? <div className="empty-state"><ShieldCheck size={22} /><p>No security-relevant error events recorded.</p></div> :
        errors.slice(0, 5).map(e => <Row key={e.id} icon={CircleAlert} title={e.source} detail={e.message.slice(0, 120)} status={formatDate(e.created_at)} tone="warning" click={() => go('incidents')} />)}
      <button className="soft-action" onClick={() => go('credentials')}><KeyRound size={15} /> Open credentials <ArrowRight size={14} /></button></Card></>
}

export function Policies({ inspect }: P) {
  const { data: constitution } = useBackendObject<{ articles?: Array<{ title: string; body: string }> } & Record<string, any>>('/api/constitution')
  const { data: guardPolicies, loading, error, reload } = useBackendObject<{ policies?: Array<{ name: string; description?: string }> } & Record<string, any>>('/api/guard/policies')
  const articles = constitution?.articles || []
  const policies = guardPolicies?.policies || []
  return <><Banner icon={LockKeyhole} title="Policy Center" detail="The policy hierarchy from the local backend: constitution articles and guard policies." tone="amber" />
    <div className="policy-chain"><span>System</span><ArrowRight size={15} /><span>Security</span><ArrowRight size={15} /><span>Platform</span><ArrowRight size={15} /><span>User</span><ArrowRight size={15} /><span>Project</span><ArrowRight size={15} /><span>Governor</span><ArrowRight size={15} /><span>Advisor</span></div>
    <DataStates loading={loading} error={error} retry={reload} empty={articles.length === 0 && policies.length === 0} emptyTitle="No policy records returned." emptyDetail="The constitution and guard policy endpoints reported no articles.">
      <div className="policy-grid">{articles.map((a, i) => <Card className={`policy-card ${['coral', 'slate', 'amber', 'sage'][i % 4]}`} key={a.title || i}><span className="panel-kicker">Constitution article</span><h3>{a.title}</h3><p>{String(a.body || '').slice(0, 220)}</p><button className="text-button" onClick={() => inspect('policy', a.title, String(a.body || '').slice(0, 400))}>Inspect rules <ArrowRight size={14} /></button></Card>)}
        {policies.map((p: any, i: number) => <Card className={`policy-card ${['coral', 'slate', 'amber', 'sage'][i % 4]}`} key={p.name || i}><span className="panel-kicker">Guard policy</span><h3>{p.name}</h3><p>{String(p.description || p.status || '').slice(0, 220)}</p><button className="text-button" onClick={() => inspect('policy', p.name, String(p.description || '').slice(0, 400))}>Inspect rules <ArrowRight size={14} /></button></Card>)}</div>
    </DataStates></>
}

type CopilotAnswer = { question: string; answer: string; evidence: Array<{ source: string; text: string; detail: string }>; matched: boolean }

export function Copilot({ announce }: P) {
  const [question, setQuestion] = useState('Why did the Governor choose Cosmos?')
  const [answered, setAnswered] = useState(false)
  const [asking, setAsking] = useState(false)
  const [result, setResult] = useState<CopilotAnswer | null>(null)
  const explain = () => {
    if (!question.trim() || asking) return
    setAsking(true)
    backendFetch<CopilotAnswer>(`/api/copilot/explain?project_id=project_northstar&question=${encodeURIComponent(question.trim())}`)
      .then(answer => { setResult(answer); setAnswered(true); announce(answer.matched ? 'Copilot explanation built from local evidence.' : 'No local evidence matched; the answer says so explicitly.') })
      .catch(() => announce('The explanation request could not be completed.'))
      .finally(() => setAsking(false))
  }
  return <><Banner icon={Bot} title="Flujo Copilot / Explain" detail="Evidence-linked explanations assembled from persisted local records. No model call is made." tone="coral" />
    <div className="copilot-layout"><Card><Head title="Ask for an explanation" sub="Use plain language, not hidden reasoning" />
      <div className="copilot-prompts">{['Why did the Governor choose Cosmos?', 'Why is Scene 7 blocked?', 'What does Adaptive Sampling mean?', 'Why is this skill being promoted?'].map(x => <button key={x} onClick={() => { setQuestion(x); setAnswered(false) }}><MessageCircle size={14} />{x}<ArrowRight size={13} /></button>)}</div>
      <div className="copilot-input"><textarea value={question} onChange={e => setQuestion(e.target.value)} rows={3} /><button className="primary-button" onClick={explain} disabled={asking}><Send size={15} /> {asking ? 'Explaining…' : 'Explain'}</button></div></Card>
      <Card className="copilot-answer"><Head title="Explanation" sub="Evidence-linked answer" />{answered && result ? <><Pill tone={result.matched ? 'positive' : 'warning'}><CheckCircle2 size={13} /> {result.matched ? 'Evidence attached' : 'No matching evidence'}</Pill><h3>{result.answer}</h3>{result.evidence.length > 0 && <div className="explain-chain">{result.evidence.map((e, i) => <span key={i}>{e.source}: {e.text.slice(0, 90)}</span>)}</div>}<small>{result.matched ? 'Cited records are persisted in the local backend.' : 'This is an explicit no-evidence answer, not a guess.'}</small></> : <div className="empty-state"><Bot size={24} /><p>Ask a question and Flujo will explain from the attached evidence.</p></div>}</Card></div></>
}

export function Sandbox({ go, announce }: P) {
  const [running, setRunning] = useState(false)
  return <><Banner icon={TestTube2} title="Flujo Sandbox" detail="Intentional simulator: run plans without touching real APIs, social accounts, production data, publishing, or permanent skills." tone="sage" />
    <div className="notice-strip"><AlertTriangle size={16} /><span><strong>Simulation by design.</strong> This surface estimates cost, runtime, and risk deterministically; it never calls providers or publishes.</span></div>
    <div className="sandbox-hero"><div><span className="panel-kicker">Simulation request</span><h2>Simulate generating three food ads using the current model council.</h2><p>Sandbox mode will estimate cost, runtime, advisors, models, quality, and potential failures.</p></div><button className="primary-button" onClick={() => { setRunning(true); announce('Sandbox simulation started locally.'); window.setTimeout(() => setRunning(false), 2400) }}>{running ? <Activity className="spin" size={16} /> : <Play size={16} />} {running ? 'Running' : 'Run simulation'}</button></div>
    <div className="sandbox-grid"><Card><Head title="Projected run" sub="Deterministic estimate, not a provider quote" /><Stat value="$2.52" label="Estimated cost" /><Stat value="04m 18s" label="Estimated runtime" /><Stat value="03" label="Advisors used" /><Stat value="06" label="Potential failures" tone="warning" /></Card>
      <Card><Head title="Sandbox boundaries" sub="What cannot be touched" /><Row icon={Cloud} title="Real APIs" detail="No provider requests" status="Blocked" tone="positive" /><Row icon={Globe2} title="Social accounts" detail="No account access" status="Blocked" tone="positive" /><Row icon={Archive} title="Permanent skills" detail="No promotion from sandbox" status="Blocked" tone="positive" /><button className="soft-action" onClick={() => go('workflow')}>Inspect sandbox workflow <ArrowRight size={14} /></button></Card></div></>
}

type SpendPayload = { ledger: Array<{ event_id: string; operation: string; amount: number; occurred_at: string; model_id?: string | null }>; limits: { consumed_session?: number; consumed_day?: number; consumed_month?: number; session_limit?: number; day_limit?: number; month_limit?: number } }

export function Resources({ go }: P) {
  const { data, loading, error, reload } = useBackendObject<SpendPayload>('/api/monitor/spend?project_id=project_northstar&limit=100')
  const limits = data?.limits || {}
  const used = limits.consumed_month || 0
  const budget = limits.month_limit || 500
  const pct = budget > 0 ? Math.min(100, Math.round((used / budget) * 100)) : 0
  const byOperation: Record<string, number> = {}
  for (const entry of data?.ledger || []) byOperation[entry.operation] = (byOperation[entry.operation] || 0) + (entry.amount || 0)
  return <><Banner icon={Gauge} title="Resource Center" detail="Spend and limits from the local Monitor ledger. Hard limits stay armed." tone="amber" />
    <DataStates loading={loading} error={error} retry={reload} empty={(data?.ledger || []).length === 0} emptyTitle="No spend recorded yet." emptyDetail="Spend ledger entries appear when the local engine authorizes metered operations.">
      <div className="resource-kpis"><Stat value={`$${(limits.consumed_session || 0).toFixed(2)}`} label="Session" /><Stat value={`$${(limits.consumed_day || 0).toFixed(2)}`} label="Today" /><Stat value={`$${used.toFixed(2)}`} label="This month" tone={pct > 80 ? 'warning' : ''} /><Stat value={`$${budget.toFixed(0)}`} label="Monthly limit" /></div>
      <div className="section-grid two-one"><Card><Head title="Spend by operation" sub="Persisted Monitor ledger" />{Object.entries(byOperation).slice(0, 8).map(([op, amount]) => <div className="cost-row" key={op}><span className="cost-mark coral" /><b>{op}</b><span>${amount.toFixed(2)}</span></div>)}<div className="cost-total"><b>Total</b><strong>${(data?.ledger || []).reduce((sum, e) => sum + (e.amount || 0), 0).toFixed(2)}</strong></div></Card>
        <Card><Head title="Recent ledger entries" sub="Latest authorized spend" />{(data?.ledger || []).slice(0, 6).map(e => <div className="review-item" key={e.event_id}><span><b>{e.operation}</b><small>{formatTime(e.occurred_at)} · {e.model_id || 'local engine'}</small></span><Pill tone="neutral">${Number(e.amount || 0).toFixed(2)}</Pill></div>)}
          <button className="soft-action" onClick={() => go('monitor')}>Open Monitor <ArrowRight size={14} /></button></Card></div>
      <Card><Head title="Budget policy" sub="Prevent runaway advisor and generation calls" /><div className="budget-bar"><span style={{ width: `${pct}%` }} /></div><div className="meter-foot"><span>${used.toFixed(2)} used</span><b>${budget.toFixed(0)} monthly</b></div></Card>
    </DataStates></>
}

type ControlPlanePayload = { project: Project; counts: Record<string, number>; data_directories: string[] }

export function ControlPlane({ go, inspect }: P) {
  const { data, loading, error, reload } = useBackendObject<ControlPlanePayload>('/api/control-plane?project_id=project_northstar')
  return <><Banner icon={FolderTree} title=".flujo Project Control Plane" detail="Persisted project intelligence from the local backend: live record counts and the local data layout." tone="amber" />
    <DataStates loading={loading} error={error} retry={reload} empty={!data} emptyTitle="Control plane unavailable." emptyDetail="The local backend did not return a control-plane summary.">
      <div className="control-plane-layout"><Card className="file-tree-card"><Head title={data?.project?.name || 'Project'} sub="Local data directories" /><div className="file-tree">{(data?.data_directories || []).map(dir => <div className="tree-line" key={dir}><span className="tree-dot coral" /><b>{dir}/</b></div>)}</div></Card>
        <Card><Head title="Persisted records" sub="Live counts from SQLite" /><div className="detail-list">{Object.entries(data?.counts || {}).map(([label, count]) => <span key={label}>{label} <b>{count}</b></span>)}</div>
          <button className="primary-button full-button" onClick={() => inspect('control-plane', 'Project intelligence', `${Object.values(data?.counts || {}).reduce((a, b) => a + b, 0)} persisted records across ${Object.keys(data?.counts || {}).length} tables.`)}>Inspect counts</button></Card></div>
      <Card><Head title="Runtime workspace" sub="Ephemeral work is destroyed after completion" /><div className="runtime-grid"><span><b>workflow runtime</b><small>Jobs, checkpoints, and tool runs stay queryable</small></span><span><b>logs</b><small>Structured audit events with trace IDs</small></span></div><button className="soft-action" onClick={() => go('terminal')}><Terminal size={14} /> Inspect logs in Flujo Terminal <ArrowRight size={14} /></button></Card>
    </DataStates></>
}

type MemoryRecord = { id: string; project_id: string; source: string; scope: string; title: string; content: string; confidence: number; created_at: string }

export function MemorySources({ go, inspect, announce }: P) {
  const { items: records, loading, error, reload } = useBackendList<MemoryRecord>('/api/memory/records?project_id=project_northstar&limit=100')
  const sources = ['Flujo Memory', 'Obsidian', 'Project Files', 'Conversation']
  return <><Banner icon={History} title="Memory Adapter" detail="Memory sources with live record counts from the local backend." tone="sage" />
    <DataStates loading={loading} error={error} retry={reload} empty={false} emptyTitle="" emptyDetail="">
      <div className="memory-adapter-grid"><Card><Head title="Connected sources" sub="Record counts from the local memory store" />
        <Row icon={History} title="Flujo Memory" detail={`${records.length} active record(s) in the local store`} status={records.length > 0 ? 'Connected' : 'Empty'} tone={records.length > 0 ? 'positive' : 'neutral'} click={() => go('memory')} />
        <Row icon={Archive} title="Obsidian Vault" detail="Optional vault adapter; import through the memory API" status="Optional" tone="neutral" click={() => inspect('memory', 'Obsidian adapter', 'The Obsidian adapter imports selected folders through POST /api/memory/obsidian/import. It is not crawling anything now.')} />
        <Row icon={FileText} title="Project files" detail="Permitted README and brand notes" status="Scoped" tone="amber" />
        <Row icon={MessageCircle} title="Conversation memory" detail="Current Creative Chat only" status="Scoped" tone="slate" /></Card>
        <Card><Head title="Memory by source" sub="Where remembered preferences come from" />{sources.map(s => { const count = records.filter(r => r.source.toUpperCase() === s.toUpperCase().replace(' ', '_') || r.source === s).length; return <div className="review-item" key={s}><span><b>{s}</b><small>{count} record(s)</small></span><Pill tone={count > 0 ? 'positive' : 'neutral'}>{count > 0 ? 'Active' : 'Empty'}</Pill></div> })}
          <button className="soft-action" onClick={() => { announce('Memory source detail opened.'); }}><History size={14} /> Review sources <ArrowRight size={14} /></button></Card></div>
      <Card><Head title="Memory provenance" sub="Every remembered preference stays traceable" />{records.length === 0 ? <div className="empty-state"><History size={22} /><p>No memory records yet. Recorded preferences will appear with their source and confidence.</p></div> :
        records.slice(0, 4).map(r => <div className="memory-provenance" key={r.id}><span className="provenance-source"><History size={16} /><b>{r.title || r.content.slice(0, 40)}</b></span><span>Source <b>{r.source}</b></span><span>Confidence <b>{r.confidence}</b></span><span>Scope <b>{r.scope}</b></span><button className="text-button" onClick={() => go('evidence')}>Add to Evidence Graph <ArrowRight size={14} /></button></div>)}</Card>
    </DataStates></>
}

type PermissionCheck = { id: string; project_id: string; operation: string; requested_permissions_json: string; decision: string; reason: string; created_at: string }

const PERMISSION_CONTRACT: Array<[string, string, boolean]> = [
  ['FILE_READ', 'Read permitted project files', true],
  ['FILE_WRITE', 'Create and modify project files', true],
  ['MEDIA_READ', 'Read media inside the project boundary', true],
  ['MEDIA_WRITE', 'Modify media and write generated artifacts', true],
  ['PROCESS_EXECUTE', 'Run approved tools in sandbox', true],
  ['NETWORK_ACCESS', 'Reach external providers', false],
  ['PUBLISH', 'Publish to social accounts', false],
  ['SYSTEM_ACCESS', 'Modify host system', false],
]

export function PermissionCenter({ announce, inspect }: P) {
  const { items: checks, loading, error, reload } = useBackendList<PermissionCheck>('/api/permissions?project_id=project_northstar&limit=50')
  return <><Banner icon={ShieldCheck} title="Permission Center" detail="The permission contract plus the persisted audit trail of real permission checks." tone="slate" />
    <div className="permission-grid">{PERMISSION_CONTRACT.map(([name, detail, enabled]) => <div className={`permission-row ${enabled ? 'enabled' : ''}`} key={name}><span className="permission-lock"><LockKeyhole size={15} /></span><span><b>{name}</b><small>{detail}</small></span><Pill tone={enabled ? 'positive' : 'critical'}>{enabled ? 'Allowed' : 'Denied'}</Pill><button className="icon-button subtle" onClick={() => inspect('permission', name, `${detail}. The model cannot grant itself this permission.`)} aria-label={`Inspect ${name}`}><ChevronRight size={14} /></button></div>)}</div>
    <Card><Head title="Permission audit trail" sub="Persisted permission checks from real engine runs" />
      <DataStates loading={loading} error={error} retry={reload} empty={checks.length === 0} emptyTitle="No permission checks recorded yet." emptyDetail="Checks are recorded when the engine evaluates an operation against the permission contract.">
        {checks.slice(0, 8).map(c => { let requested: string[] = []; try { requested = JSON.parse(c.requested_permissions_json || '[]') } catch { requested = [] } return <div className="review-item" key={c.id}><span><b>{c.operation}</b><small>{requested.join(', ')} · {c.reason}</small></span><Pill tone={c.decision === 'allow' || c.decision === 'ALLOWED' ? 'positive' : 'critical'}>{c.decision}</Pill><span><small>{formatTime(c.created_at)}</small></span></div> })}
      </DataStates></Card>
    <Card><Head title="Safe defaults" sub="Sandbox only is the default local mode" /><div className="security-strip"><Pill tone="positive"><Check size={13} /> No root access</Pill><Pill tone="positive"><Check size={13} /> No secret logging</Pill><Pill tone="positive"><Check size={13} /> No arbitrary network shell</Pill><Pill tone="warning"><CircleAlert size={13} /> Host mode opt-in</Pill></div><button className="soft-action" onClick={() => announce('Permission policy explanation opened locally.')}>Explain permission hierarchy <ArrowRight size={14} /></button></Card></>
}

type CalendarItem = { id: string; project_id: string; title: string; scheduled_at: string; status: string; route: string; created_at: string }

export function Calendar({ go, announce }: P) {
  const { items, loading, error, reload } = useBackendList<CalendarItem>('/api/calendar?project_id=project_northstar')
  const [title, setTitle] = useState('')
  const [when, setWhen] = useState('')
  const create = () => {
    if (!title.trim() || !when.trim()) { announce('Give the item a title and a scheduled time.'); return }
    backendFetch('/api/calendar', { method: 'POST', body: JSON.stringify({ project_id: 'project_northstar', title: title.trim(), scheduled_at: when.trim(), status: 'Scheduled', route: 'production' }) })
      .then(() => { setTitle(''); setWhen(''); announce('Schedule item recorded in the local backend.'); reload() })
      .catch(() => announce('The schedule item could not be recorded.'))
  }
  const remove = (item: CalendarItem) => {
    backendFetch(`/api/calendar/${item.id}`, { method: 'DELETE' }).then(() => { announce('Schedule item removed.'); reload() }).catch(() => announce('The item could not be removed.'))
  }
  return <><div className="calendar-header"><div><span className="panel-kicker">Content Calendar</span><h2>The creative schedule</h2><p>{items.length} persisted schedule item(s) in the local backend.</p></div><div className="calendar-actions"><div className="search-field"><input placeholder="Item title" value={title} onChange={e => setTitle(e.target.value)} /><input placeholder="Scheduled at (e.g. 2026-10-20 13:00)" value={when} onChange={e => setWhen(e.target.value)} /><button className="primary-button compact-button" onClick={create}><Plus size={15} /> Schedule content</button></div></div></div>
    <Card className="calendar-card"><Head title="Scheduled items" sub="Persisted locally; nothing is published automatically" />
      <DataStates loading={loading} error={error} retry={reload} empty={items.length === 0} emptyTitle="Nothing scheduled yet." emptyDetail="Schedule content to plan releases with visible review gates.">
        <div className="calendar-grid">{items.map(item => <div className="calendar-slot" key={item.id}><span>{item.scheduled_at}</span><div className={`calendar-event ${item.status === 'Published' ? 'positive' : item.status === 'Scheduled' ? 'warning' : 'neutral'}`}><b>{item.title}</b><small>{item.status}</small></div><div className="hero-actions"><button className="secondary-button compact-button" onClick={() => go(item.route as FrontendRoute)}>Open</button><button className="icon-button subtle" onClick={() => remove(item)} aria-label={`Remove ${item.title}`}><Trash2 size={14} /></button></div></div>)}</div>
      </DataStates></Card>
    <Card><Head title="Schedule rules" sub="Constraints the calendar enforces" /><Row icon={ShieldCheck} title="Human review required" detail="Every content item must pass QA before publishing" status="On" tone="positive" /><Row icon={Clock3} title="Quiet hours" detail="No publishing after 21:00 local time" status="On" tone="amber" /><Row icon={Users} title="Platform identity" detail="Northstar Food Stories / default account" status="Scoped" tone="slate" /></Card></>
}

type MonitoredSource = { id: string; project_id: string; label: string; platform: string; handle?: string | null; auto_analyze: number; auto_generate: number; auto_publish: number; status: string; last_checked_at?: string | null; created_at: string }

export function SourceMonitor({ announce }: P) {
  const { items: sources, loading, error, reload } = useBackendList<MonitoredSource>('/api/sources?project_id=project_northstar')
  const [label, setLabel] = useState('')
  const [platform, setPlatform] = useState('Instagram')
  const add = () => {
    if (!label.trim()) { announce('Name the source first.'); return }
    backendFetch('/api/sources', { method: 'POST', body: JSON.stringify({ project_id: 'project_northstar', label: label.trim(), platform }) })
      .then(() => { setLabel(''); announce('Source added to the local monitor.'); reload() })
      .catch(() => announce('The source could not be added.'))
  }
  const toggle = (source: MonitoredSource, field: 'auto_analyze' | 'auto_generate' | 'auto_publish') => {
    backendFetch(`/api/sources/${source.id}`, { method: 'PATCH', body: JSON.stringify({ [field]: !source[field] }) }).then(() => reload()).catch(() => announce('The toggle could not be saved.'))
  }
  const checkNow = (source: MonitoredSource) => {
    backendFetch(`/api/sources/${source.id}`, { method: 'PATCH', body: JSON.stringify({ status: 'monitoring' }) }).then(() => { announce(`Checked ${source.label}; schedule remains local.`); reload() }).catch(() => announce('The check could not be recorded.'))
  }
  const remove = (source: MonitoredSource) => {
    backendFetch(`/api/sources/${source.id}`, { method: 'DELETE' }).then(() => { announce('Source removed from the monitor.'); reload() }).catch(() => announce('The source could not be removed.'))
  }
  return <><Banner icon={Globe2} title="Source Monitor" detail="Monitored inspiration sources persisted locally. Checks run on a local schedule; no external fetching is performed." tone="slate" />
    <div className="toolbar-row"><div className="search-field"><input placeholder="Source label (e.g. @creator)" value={label} onChange={e => setLabel(e.target.value)} /><select value={platform} onChange={e => setPlatform(e.target.value)} aria-label="Platform"><option>Instagram</option><option>YouTube</option><option>TikTok</option><option>Blog</option><option>Newsletter</option></select><button className="primary-button compact-button" onClick={add}><Plus size={15} /> Add source</button></div></div>
    <DataStates loading={loading} error={error} retry={reload} empty={sources.length === 0} emptyTitle="No sources monitored." emptyDetail="Add an inspiration source to watch it on a local schedule.">
      <div className="monitor-source-grid">{sources.map(source => <Card className="source-card" key={source.id}><div className="source-head"><div className="source-avatar"><Globe2 size={18} /></div><div><b>{source.label}</b><small>{source.platform}{source.handle ? ` · ${source.handle}` : ''}</small></div><Pill tone="positive"><span /> {source.status}</Pill></div>
        <div className="source-stats"><span>Last checked <b>{source.last_checked_at ? formatTime(source.last_checked_at) : '--'}</b></span><span>Added <b>{formatDate(source.created_at)}</b></span></div>
        <div className="source-toggles"><Toggle label="Auto-analyze" enabled={Boolean(source.auto_analyze)} onClick={() => toggle(source, 'auto_analyze')} /><Toggle label="Auto-generate" enabled={Boolean(source.auto_generate)} onClick={() => toggle(source, 'auto_generate')} /><Toggle label="Auto-publish" enabled={Boolean(source.auto_publish)} onClick={() => toggle(source, 'auto_publish')} /></div>
        <div className="hero-actions"><button className="secondary-button compact-button" onClick={() => checkNow(source)}><RefreshCw size={14} /> Check now</button><button className="icon-button subtle" onClick={() => remove(source)} aria-label={`Remove ${source.label}`}><Trash2 size={14} /></button></div></Card>)}
      </div>
    </DataStates></>
}

type SocialAccount = { id: string; project_id: string; platform: string; handle: string; timezone: string; status: string; created_at: string }

export function SocialAccounts({ go, announce }: P) {
  const { items: accounts, loading, error, reload } = useBackendList<SocialAccount>('/api/social-accounts?project_id=project_northstar')
  const [platform, setPlatform] = useState('Instagram')
  const [handle, setHandle] = useState('')
  const add = () => {
    if (!handle.trim()) { announce('Enter the account handle first.'); return }
    backendFetch('/api/social-accounts', { method: 'POST', body: JSON.stringify({ project_id: 'project_northstar', platform, handle: handle.trim() }) })
      .then(() => { setHandle(''); announce('Account recorded locally. Platform OAuth is not configured, so it stays recorded, not connected.'); reload() })
      .catch(() => announce('The account could not be recorded.'))
  }
  const remove = (account: SocialAccount) => {
    backendFetch(`/api/social-accounts/${account.id}`, { method: 'DELETE' }).then(() => { announce('Account record removed.'); reload() }).catch(() => announce('The account could not be removed.'))
  }
  return <><Banner icon={Globe2} title="Social Accounts" detail="Posting identities recorded locally. Platform OAuth is not implemented, so records stay local and never claim a live connection." tone="coral" />
    <div className="toolbar-row"><div className="search-field"><select value={platform} onChange={e => setPlatform(e.target.value)} aria-label="Platform"><option>Instagram</option><option>YouTube</option><option>TikTok</option><option>Facebook</option><option>X</option></select><input placeholder="Account handle" value={handle} onChange={e => setHandle(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') add() }} /><button className="primary-button compact-button" onClick={add}><Plus size={15} /> Record account</button></div></div>
    <DataStates loading={loading} error={error} retry={reload} empty={accounts.length === 0} emptyTitle="No accounts recorded." emptyDetail="Record a posting identity to manage its permissions, platform rules, and limits locally.">
      <div className="account-grid">{accounts.map(a => <Card className="account-card" key={a.id}><div className="account-top"><span className="account-logo"><Globe2 size={20} /></span><Pill tone="neutral">{a.status === 'recorded' ? 'Recorded' : a.status}</Pill></div><h3>{a.platform}</h3><p>{a.handle}</p><div className="account-meta"><span>Timezone <b>{a.timezone}</b></span><span>Posting <b>Blocked</b></span></div><div className="hero-actions"><button className="secondary-button full-button" onClick={() => go('publishing')}>Manage account <ArrowRight size={14} /></button><button className="icon-button subtle" onClick={() => remove(a)} aria-label={`Remove ${a.handle}`}><Trash2 size={14} /></button></div></Card>)}</div>
    </DataStates>
    <Card><Head title="Platform defaults" sub="Applied before a publishing job is created" /><div className="section-grid two-one"><Row icon={MessageCircle} title="Default captions" detail="Direct language with conversational CTA" tone="coral" /><Row icon={Clock3} title="Publishing windows" detail="Human review before scheduled release" tone="amber" /><Row icon={ShieldCheck} title="Permission boundary" detail="No auto-publish without approval" tone="sage" /></div></Card></>
}

type SkillRow = { id: string; project_id: string; name: string; version: string; domain: string; confidence: string; scope: string; state: string; created_at: string }

export function SkillDiff({ inspect }: P) {
  const { items: skills, loading, error, reload } = useBackendList<SkillRow>('/api/skills?project_id=project_northstar')
  const names = Array.from(new Set(skills.map(s => s.name)))
  const [name, setName] = useState('')
  const selected = name || names[0] || ''
  const versions = skills.filter(s => s.name === selected)
  return <><Banner icon={GitCompare} title="Skill Version Diff" detail="Version history of persisted skills. Compare what changed before promoting." tone="slate" />
    <DataStates loading={loading} error={error} retry={reload} empty={skills.length === 0} emptyTitle="No skills in the registry." emptyDetail="Draft skills in the Skills surface to start version history.">
      <div className="compare-selectors"><label>Skill<select value={selected} onChange={e => setName(e.target.value)}>{names.map(n => <option key={n} value={n}>{n}</option>)}</select></label></div>
      {versions.length < 2 ? <Card><Head title={selected} sub="Version history" /><p className="panel-copy">Only {versions.length} version(s) recorded. Draft a new version in Skills to compare.</p></Card> :
        <><div className="diff-header"><div><span className="panel-kicker">Comparing</span><h2>{selected} <span>{versions[versions.length - 1].version} → {versions[0].version}</span></h2></div><Pill tone="positive">Registry diff</Pill></div>
          <div className="diff-grid">{[versions[versions.length - 1], versions[0]].map((v, i) => <Card key={v.id}><Head title={`${selected} ${v.version}`} sub={i === 0 ? 'Previous version' : 'Candidate version'} /><div className={`diff-line ${i === 0 ? 'removed' : 'added'}`}><span>{i === 0 ? '-' : '+'} Domain</span><b>{v.domain || '--'}</b></div><div className={`diff-line ${i === 0 ? 'removed' : 'added'}`}><span>{i === 0 ? '-' : '+'} Confidence</span><b>{v.confidence}</b></div><div className={`diff-line ${i === 0 ? 'removed' : 'added'}`}><span>{i === 0 ? '-' : '+'} Scope</span><b>{v.scope}</b></div><div className={`diff-line ${i === 0 ? 'removed' : 'added'}`}><span>{i === 0 ? '-' : '+'} State</span><b>{v.state}</b></div></Card>)}
          </div>
          <Card><Head title="Promotion" sub="A promotion requires evidence" /><button className="primary-button" onClick={() => inspect('skill', selected, `Candidate ${versions[0].version} is ready for evidence-backed promotion.`)}>Inspect promotion record <ArrowRight size={14} /></button></Card></>}
    </DataStates></>
}

export function Memory({ announce }: P) {
  const { items: records, loading, error, reload } = useBackendList<MemoryRecord>('/api/memory/records?project_id=project_northstar&limit=100')
  const [content, setContent] = useState('')
  const [title, setTitle] = useState('')
  const [scope, setScope] = useState('PROJECT')
  const add = () => {
    if (!content.trim()) { announce('Write the preference first.'); return }
    backendFetch('/api/memory/records', { method: 'POST', body: JSON.stringify({ project_id: 'project_northstar', domain: 'PROJECT', scope, content: content.trim(), title: title.trim(), source: 'USER', confidence: 0.8 }) })
      .then(() => { setContent(''); setTitle(''); announce('Preference recorded in local memory.'); reload() })
      .catch(() => announce('The preference could not be recorded.'))
  }
  return <><Banner icon={History} title="Creative Memory" detail="Scoped preferences persisted in the local memory store." tone="sage" />
    <div className="memory-scope"><span>Global</span><ArrowRight size={14} /><span>User</span><ArrowRight size={14} /><span>Project</span><ArrowRight size={14} /><span>Campaign</span><ArrowRight size={14} /><span>Task</span></div>
    <Card><Head title="Record a preference" sub="Scoped so one project never contaminates another" />
      <div className="toolbar-row"><div className="search-field"><input placeholder="Title (optional)" value={title} onChange={e => setTitle(e.target.value)} /><input placeholder="Preference, e.g. Direct language" value={content} onChange={e => setContent(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') add() }} /><select value={scope} onChange={e => setScope(e.target.value)} aria-label="Scope">{['TASK', 'PROJECT', 'CONVERSATION', 'USER', 'GLOBAL'].map(s => <option key={s}>{s}</option>)}</select><button className="primary-button compact-button" onClick={add}><Plus size={15} /> Record</button></div></div></Card>
    <Card><Head title="Scoped preferences" sub="Persisted local memory records" />
      <DataStates loading={loading} error={error} retry={reload} empty={records.length === 0} emptyTitle="No memory records yet." emptyDetail="Record a preference above to scope it from global context down to a task.">
        {records.map(r => <div className="memory-entry" key={r.id}><span><b>{r.title || r.scope}</b><small>{r.source} · {r.scope} · confidence {r.confidence}</small></span><strong>{r.content.slice(0, 120)}</strong><span><small>{formatDate(r.created_at)}</small></span></div>)}
      </DataStates></Card></>
}

type Experience = { id: string; project_id: string; scope: string; task: string; feedback: string; outcome: string; created_at: string }

export function Evolution({ go }: P) {
  const { items: experiences, loading, error, reload } = useBackendList<Experience>('/api/adaptation/experiences?project_id=project_northstar&limit=50')
  const { items: candidates } = useBackendList<Candidate>('/api/adaptation/candidates?project_id=project_northstar&limit=50')
  return <><Banner icon={Sparkles} title="Creative Evolution" detail="Observe, measure, learn, mutate, test, promote, and create again, from persisted adaptation records." tone="coral" />
    <div className="evolution-loop"><span>Reference</span><ArrowRight size={14} /><span>Create</span><ArrowRight size={14} /><span>Publish</span><ArrowRight size={14} /><span>Observe</span><ArrowRight size={14} /><span>Learn</span><ArrowRight size={14} /><span>Mutate</span><ArrowRight size={14} /><span>Promote</span></div>
    <div className="evolution-grid"><Card><Head title="Recent evolution" sub="Persisted experiences and candidates" />
      <DataStates loading={loading} error={error} retry={reload} empty={experiences.length === 0 && candidates.length === 0} emptyTitle="No evolution records yet." emptyDetail="Experiences are recorded when adaptation feedback arrives.">
        {experiences.slice(0, 4).map(e => <Row key={e.id} icon={Sparkles} title={e.task.slice(0, 60)} detail={`${e.scope} · ${formatDate(e.created_at)}`} status={e.outcome || 'Recorded'} tone="positive" />)}
        {candidates.slice(0, 3).map(c => <Row key={c.id} icon={RefreshCw} title={candidateTitle(c)} detail="Mutation candidate" status={c.status} tone="warning" click={() => go('adaptationCenter')} />)}
      </DataStates></Card>
      <Card><Head title="Next action" sub="The loop is ready to continue" /><div className="evolution-next"><Sparkles size={23} /><h3>Test the winning mutation against a fresh scene.</h3><p>Keep the same genome and compare the outcome before promotion.</p><button className="primary-button" onClick={() => go('experiments')}>Open Experiment Lab <ArrowRight size={14} /></button></div></Card></div></>
}

type RecipeRow = { id: string; project_id: string; name: string; version: string; status: string; scope: string; summary: string; created_at: string }

export function Recipes({ go, inspect, announce }: P) {
  const { items: recipes, loading, error, reload } = useBackendList<RecipeRow>('/api/recipes?project_id=project_northstar')
  const [name, setName] = useState('')
  const [summary, setSummary] = useState('')
  const draft = () => {
    if (!name.trim()) { announce('Name the recipe first.'); return }
    backendFetch('/api/recipes', { method: 'POST', body: JSON.stringify({ project_id: 'project_northstar', name: name.trim(), summary: summary.trim(), status: 'Experimental', scope: 'Project', version: 'v0.1' }) })
      .then(() => { setName(''); setSummary(''); announce('Recipe drafted in the local registry.'); reload() })
      .catch(() => announce('The recipe could not be drafted.'))
  }
  return <><Banner icon={Archive} title="Recipe Registry" detail="Recipes package reliable creative workflows with inputs, constraints, model affinity, and evidence." tone="amber" />
    <Card><Head title="Draft a recipe" sub="Versioned from the first draft" /><div className="toolbar-row"><div className="search-field"><input placeholder="Recipe name" value={name} onChange={e => setName(e.target.value)} /><input placeholder="Summary" value={summary} onChange={e => setSummary(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') draft() }} /><button className="primary-button compact-button" onClick={draft}><Plus size={15} /> Draft recipe</button></div></div></Card>
    <Card><Head title="Reusable recipes" sub="Versioned and scoped by evidence" />
      <DataStates loading={loading} error={error} retry={reload} empty={recipes.length === 0} emptyTitle="No recipes in the registry." emptyDetail="Draft a recipe to package a reliable creative workflow.">
        <div className="recipe-grid">{recipes.map(x => <button className="recipe-card" key={x.id} onClick={() => inspect('recipe', x.name, `${x.name} ${x.version} · ${x.status} · ${x.scope} scope. ${x.summary}`)}><div><span className="panel-kicker">{x.scope}</span><Pill tone={x.status === 'Validated' ? 'positive' : x.status === 'Experimental' ? 'accent' : 'warning'}>{x.status}</Pill></div><h3>{x.name}</h3><strong>{x.version}</strong><p>{x.summary || 'No summary recorded.'}</p><span className="text-button">Inspect recipe <ArrowRight size={13} /></span></button>)}</div>
      </DataStates></Card>
    <Card><Head title="Recipe builder" sub="Tool Builder and evidence are required before promotion" action="Open Tool Lab" click={() => go('toolLab')} /><div className="recipe-builder-note"><Code2 size={17} /><span>Define inputs, outputs, constraints, tests, and scope. No code execution is available in the current front end.</span></div></Card></>
}

type IncidentRow = { id: string; component?: string; severity?: string; title?: string; detail?: string; status: string; recommendation?: string; created_at: string; message?: string; source?: string }

export function Incidents({ go, inspect }: P) {
  const { items: incidents, loading, error, reload } = useBackendList<IncidentRow>('/api/doctor/incidents?limit=50')
  const { items: errors } = useBackendList<IncidentRow>('/api/diagnostics/errors?limit=50')
  return <><Banner icon={CircleAlert} title="Incident / Recovery Center" detail="Persisted doctor incidents and error events with root causes and recovery paths." tone="brick" />
    <div className="incident-kpis"><Stat value={String(incidents.filter(i => i.status === 'open').length).padStart(2, '0')} label="Open incidents" tone="warning" /><Stat value={String(incidents.length)} label="Doctor incidents" /><Stat value={String(errors.length)} label="Error events" /><Stat value={incidents.length + errors.length > 0 ? `${Math.round((incidents.filter(i => i.status !== 'open').length / Math.max(1, incidents.length)) * 100)}%` : '--'} label="Recovery rate" /></div>
    <Card><Head title="Incident stream" sub="Every incident has a root cause and recovery path" />
      <DataStates loading={loading} error={error} retry={reload} empty={incidents.length === 0 && errors.length === 0} emptyTitle="No incidents recorded." emptyDetail="Doctor incidents and error events will appear here when the backend records them.">
        {incidents.map(x => <button className="incident-card-row" key={x.id} onClick={() => inspect('incident', x.title || x.component || x.id, `${x.detail || ''} ${x.recommendation || ''}`)}><span className={`incident-number ${x.status === 'open' ? 'critical' : 'positive'}`}><CircleAlert size={16} /></span><span><b>{x.title || x.component}</b><small>{x.detail}</small></span><span><b>{x.status}</b><small>{x.recommendation}</small></span><Pill tone={x.status === 'open' ? 'critical' : 'positive'}>{x.status}</Pill><ChevronRight size={15} /></button>)}
        {errors.slice(0, 5).map(x => <button className="incident-card-row" key={x.id} onClick={() => inspect('incident', x.source || 'Error event', x.message || '')}><span className="incident-number warning"><CircleAlert size={16} /></span><span><b>{x.source}</b><small>{(x.message || '').slice(0, 100)}</small></span><span><b>Logged</b><small>{formatTime(x.created_at)}</small></span><Pill tone="warning">Error</Pill><ChevronRight size={15} /></button>)}
      </DataStates></Card>
    <Card><Head title="Recovery policy" sub="Explain before repair" /><Row icon={RefreshCw} title="Resume from checkpoint" detail="Preserve verified work and isolate the failure" status="Enabled" tone="positive" /><Row icon={ShieldCheck} title="Require human review" detail="Do not auto-promote quality regressions" status="Enabled" tone="positive" /><button className="soft-action" onClick={() => go('doctor')}>Open System Doctor <ArrowRight size={14} /></button></Card></>
}

export function TerminalSurface({ announce }: P) {
  const [category, setCategory] = useState('')
  const [limit, setLimit] = useState(40)
  const path = `/api/terminal/log?limit=${limit}${category ? `&category=${encodeURIComponent(category)}` : ''}`
  const { items: events, loading, error, reload } = useBackendList<AuditEvent>(path)
  const categories = ['http', 'workflow', 'media', 'credentials', 'monitor', 'guard', 'database', 'security', 'frontend-diagnostic']
  return <><Banner icon={Terminal} title="Flujo Terminal" detail="Read-only backend log and event viewer. There is no shell here: this surface only reads persisted audit events." tone="slate" />
    <div className="notice-strip"><AlertTriangle size={16} /><span><strong>Read-only by design.</strong> A real command shell is intentionally not shipped; this viewer reads the structured audit log so every action stays inspectable.</span></div>
    <div className="terminal-layout"><Card className="terminal-card"><div className="terminal-head"><span><span className="terminal-led" /> audit log · read-only</span><Pill tone="positive">No shell</Pill></div>
      <div className="toolbar-row"><select className="filter-button" value={category} onChange={e => setCategory(e.target.value)} aria-label="Event category"><option value="">All categories</option>{categories.map(c => <option key={c} value={c}>{c}</option>)}</select><select className="filter-button" value={limit} onChange={e => setLimit(Number(e.target.value))} aria-label="Event limit">{[20, 40, 80, 150].map(n => <option key={n} value={n}>{n} events</option>)}</select><button className="secondary-button compact-button" onClick={reload}><RefreshCw size={14} /> Refresh</button></div>
      <div className="terminal-output"><DataStates loading={loading} error={error} retry={reload} empty={events.length === 0} emptyTitle="No audit events yet." emptyDetail="Events are recorded as the local engine works.">{events.map(e => <div key={e.id}><span className="terminal-prompt">[{formatTime(e.timestamp)}]</span> <b>{e.category}</b> · {e.message} <small>({e.route || '--'} · {e.status || '--'})</small></div>)}</DataStates></div>
      <div className="terminal-foot"><span>Source: audit_events · secrets are redacted at write time</span><button onClick={() => announce('Terminal view is a read-only audit projection.')}>Why read-only?</button></div></Card>
      <Card><Head title="Permission gate" sub="Why there is no shell" /><div className="reason-card"><span className="panel-kicker">DECISION</span><b>Read-only log viewer</b><span className="panel-kicker">REASON</span><b>A command shell would bypass the Governor, Guard, and permission contract</b><span className="panel-kicker">SCOPE</span><b>audit_events · structured fields only</b><span className="panel-kicker">RISK</span><Pill tone="positive">LOW</Pill></div>
        <div className="allow-deny-grid"><div><Pill tone="positive">ALLOW</Pill><p>Read persisted audit events, filter by category, inspect trace IDs.</p></div><div><Pill tone="critical">DENY</Pill><p>Command execution, host access, credential extraction, arbitrary network calls.</p></div></div></Card></div></>
}

type Promotion = { id: string; project_id: string; candidate_id: string; candidate_type: string; evaluation_outcome: string; status: string; created_at: string; decided_at?: string | null }

export function AdaptationCenter({ go, announce }: P) {
  const { items: candidates, loading: cLoading, error: cError, reload: cReload } = useBackendList<Candidate>('/api/adaptation/candidates?project_id=project_northstar&limit=50')
  const { items: promotions, loading: pLoading, error: pError, reload: pReload } = useBackendList<Promotion>('/api/promotions?project_id=project_northstar&limit=50')
  const pending = promotions.filter(p => p.status === 'PENDING' || p.status === 'PENDING_HUMAN')
  const decide = (promotion: Promotion, decision: 'APPROVE' | 'REJECT') => {
    backendFetch(`/api/promotions/${promotion.id}/decision`, { method: 'POST', body: JSON.stringify({ reviewer_id: 'human-operator', decision, reason: `Manual ${decision.toLowerCase()} from Adaptation Center` }) })
      .then(() => { announce(`Promotion ${decision === 'APPROVE' ? 'approved' : 'rejected'}.`); pReload() })
      .catch(() => announce('The promotion decision could not be recorded.'))
  }
  return <><Banner icon={RefreshCw} title="Adaptation Engine Control Center" detail="Persisted adaptation candidates and promotion reviews from the local backend." tone="amber" />
    <div className="adaptation-kpis"><Stat value={String(candidates.length)} label="Candidates" /><Stat value={String(pending.length)} label="Pending promotions" tone={pending.length ? 'warning' : ''} /><Stat value={String(promotions.filter(p => p.status === 'APPROVED').length)} label="Approved" /><Stat value={String(promotions.length)} label="Total reviews" /></div>
    <div className="section-grid two-one"><Card><Head title="Promotion queue" sub="Evidence-backed candidates" />
      <DataStates loading={pLoading} error={pError} retry={pReload} empty={promotions.length === 0} emptyTitle="No promotion reviews yet." emptyDetail="Promotions are created from evaluated adaptation candidates.">
        {promotions.slice(0, 6).map(p => <div className="candidate-card" key={p.id}><div className="candidate-heading"><Pill tone={p.status === 'APPROVED' ? 'positive' : 'warning'}>{p.status}</Pill><span className="confidence">{p.candidate_type}</span></div><h3>{p.candidate_id}</h3><p>Evaluation outcome: {p.evaluation_outcome}</p>{(p.status === 'PENDING' || p.status === 'PENDING_HUMAN') && <div className="hero-actions"><button className="primary-button compact-button" onClick={() => decide(p, 'APPROVE')}><Check size={13} /> Approve</button><button className="secondary-button compact-button" onClick={() => decide(p, 'REJECT')}><X size={13} /> Reject</button></div>}</div>)}
      </DataStates></Card>
      <Card><Head title="Engine learning signals" sub="Persisted candidates" />
        <DataStates loading={cLoading} error={cError} retry={cReload} empty={candidates.length === 0} emptyTitle="No candidates yet." emptyDetail="Adaptation candidates appear here when proposed.">
          {candidates.slice(0, 6).map(c => <Row key={c.id} icon={GitCompare} title={candidateTitle(c)} detail={`${c.candidate_type} · ${c.status}`} status={c.status} tone={c.status === 'promoted' ? 'positive' : 'sage'} />)}
        </DataStates>
        <button className="soft-action" onClick={() => go('evolution')}>Open evolution history <ArrowRight size={14} /></button></Card></div></>
}

type Reference = { id: string; project_id: string; conversation_id?: string | null; label: string; kind: string; role?: string | null; status: string; created_at: string }
type EvidenceRow = { id: string; project_id: string; reference_id?: string | null; claim: string; source_type: string; confidence: number; created_at: string }
type Blueprint = { id: string; project_id: string; genome_id?: string | null; name: string; scenes_json: string; constraints_json: string; created_at: string }
type QaRun = { id: string; project_id: string; job_id?: string | null; status: string; findings_json: string; created_at: string }
type CompileRun = { id: string; blueprint_id: string; provider_neutral_plan_json: string; status: string; created_at: string }

function Metric({ value, label, tone = '' }: { value: string; label: string; tone?: string }) { return <div className="metric"><strong className={tone}>{value}</strong><span>{label}</span></div> }

export function Command({ go, inspect, announce }: P) {
  const { items: jobs, loading: jobsLoading, error: jobsError, reload: reloadJobs } = useBackendList<GenerationJob>('/api/jobs?project_id=project_northstar&limit=50')
  const { items: events } = useBackendList<AuditEvent>('/api/diagnostics/events?limit=8')
  const { data: health } = useBackendObject<{ gateway?: { simulator_only?: boolean; model_count?: number } }>('/health')
  const { items: reviews } = useBackendList<GuardReview>('/api/projects/project_northstar/guard/reviews?limit=5')
  const [briefing, setBriefing] = useState(false)
  const queueBrief = () => {
    if (briefing) { announce('A brief is already being queued.'); return }
    setBriefing(true)
    backendFetch('/api/conversations/conversation_citrus/messages', { method: 'POST', body: JSON.stringify({ content: 'Continue the flow: run the next local vertical slice.', attachments: [], idempotency_key: `command-${Date.now()}`, project_id: 'project_northstar', campaign_id: 'campaign_citrus' }) })
      .then(() => { announce('Brief queued in the local backend.'); reloadJobs() })
      .catch(() => announce('The brief could not be queued.'))
      .finally(() => setBriefing(false))
  }
  const simulatorOnly = health?.gateway?.simulator_only
  const active = jobs.filter(j => ['queued', 'running', 'cancelling'].includes(j.status))
  return <><div className="hero-grid"><Card className="hero-panel"><div className="hero-kicker"><span className="signal-ring"><i /></span> Flow is in motion <span className="demo-label">{simulatorOnly === false ? 'LIVE GATEWAY' : 'LOCAL ENGINE'}</span></div>
    <h2>Make the next creative decision <em>inspectable.</em></h2><p>Flujo is holding the thread from reference to result. {active.length} job(s) are active, {reviews.filter(r => (r.decision || 'PENDING') === 'PENDING').length} review(s) are waiting.</p>
    <div className="hero-actions"><button className="primary-button" onClick={queueBrief} disabled={briefing}>{briefing ? 'Queueing…' : 'Continue the flow'}</button><button className="secondary-button" onClick={() => go('intake')}><Plus size={16} /> Add reference</button></div>
    <div className="hero-footnote"><ShieldCheck size={14} /> {simulatorOnly === false ? 'A live model gateway is configured.' : 'Model Gateway is simulator-only: no provider credential is configured.'} No publishing actions are connected.</div></Card>
    <div className="hero-side"><Card><div className="panel-top"><span className="panel-kicker">Current flow</span><span className="status-pill positive"><span /> Healthy</span></div><div className="signal-value">{String(active.length).padStart(2, '0')}<span> active</span></div><div className="signal-caption">jobs in the local queue</div><div className="panel-bottom"><span>Understand → Create</span><button className="text-button" onClick={() => go('monitor')}>View run <ArrowRight size={14} /></button></div></Card>
      <Card className="risk-panel"><div className="panel-top"><span className="panel-kicker">Review queue</span><AlertTriangle size={16} className="warning-icon" /></div><strong>{reviews.filter(r => (r.decision || 'PENDING') === 'PENDING').length} item(s) need review</strong><p>Guard-flagged work waits for a human decision.</p><button className="inline-link" onClick={() => go('reviewQueue')}>Open review queue <ArrowRight size={14} /></button></Card></div></div>
    <div className="section-grid two-one"><Card><Head title="Active work" sub="Persisted local jobs" action="Open production" click={() => go('production')} />
      <DataStates loading={jobsLoading} error={jobsError} retry={reloadJobs} empty={jobs.length === 0} emptyTitle="No jobs queued yet." emptyDetail="Queue a brief from Creative Chat or press Continue the flow.">
        {jobs.slice(0, 3).map(j => <button className="job-row" key={j.id} onClick={() => inspect('job', j.id, `${j.workflow_name} · ${j.status} · checkpoint ${j.checkpoint}`)}><span className="job-icon coral"><Boxes size={15} /></span><span className="job-copy"><strong>{j.workflow_name}</strong><small>{j.checkpoint}</small></span><span className={`status-pill ${j.status === 'completed' ? 'positive' : j.status === 'blocked' ? 'critical' : 'warning'}`}>{j.status}</span><span className="job-progress"><i style={{ width: `${Math.round((j.progress || 0) * 100)}%` }} /></span><ChevronRight size={15} /></button>)}
      </DataStates></Card>
      <Card><Head title="Governor queue" sub="Persisted guard reviews" action="Review all" click={() => go('reviewQueue')} />{reviews.length === 0 ? <div className="empty-state"><CheckCircle2 size={22} /><p>No guard reviews recorded.</p></div> : reviews.slice(0, 3).map(r => <button className="decision-row" key={r.id} onClick={() => inspect('review', r.id, r.reason)}><span className="decision-icon warning"><AlertTriangle size={16} /></span><span><strong>{r.id}</strong><small>{r.reason.slice(0, 80)}</small></span><ChevronRight size={15} /></button>)}</Card></div>
    <Card><Head title="Recent activity" sub="Persisted audit events" action="Open monitor" click={() => go('monitor')} />{events.length === 0 ? <div className="empty-state"><Activity size={22} /><p>No events recorded yet.</p></div> : events.slice(0, 4).map(e => <div className="activity-item" key={e.id}><span className="activity-icon slate"><Activity size={14} /></span><span><strong>{e.message}</strong><small>{e.category} · {e.route || '--'}</small></span><time>{formatTime(e.timestamp)}</time></div>)}</Card></>
}

export function Intake({ go, inspect, announce }: P) {
  const { items: references, loading, error, reload } = useBackendList<Reference>('/api/references?project_id=project_northstar&limit=200')
  const [label, setLabel] = useState('')
  const [kind, setKind] = useState('image')
  const [role, setRole] = useState('Reference')
  const add = () => {
    if (!label.trim()) { announce('Name the reference first.'); return }
    backendFetch('/api/references', { method: 'POST', body: JSON.stringify({ project_id: 'project_northstar', label: label.trim(), kind, role }) })
      .then(() => { setLabel(''); announce('Reference recorded in the local backend.'); reload() })
      .catch(() => announce('The reference could not be recorded.'))
  }
  return <><div className="notice-strip"><AlertTriangle size={16} /><span><strong>Local intake.</strong> References are metadata records in the local backend. No files leave this computer.</span></div>
    <div className="toolbar-row"><div className="search-field"><input placeholder="Reference label" value={label} onChange={e => setLabel(e.target.value)} /><select value={kind} onChange={e => setKind(e.target.value)} aria-label="Kind">{['image', 'video', 'audio', 'text', 'file'].map(k => <option key={k}>{k}</option>)}</select><select value={role} onChange={e => setRole(e.target.value)} aria-label="Role">{['Reference', 'Character reference', 'Location reference', 'Product reference', 'Style reference'].map(r => <option key={r}>{r}</option>)}</select><button className="primary-button compact-button" onClick={add} onKeyDown={e => { if (e.key === 'Enter') add() }}><Plus size={15} /> Add reference</button></div></div>
    <Card><Head title="Reference library" sub={`${references.length} persisted record(s)`} />
      <DataStates loading={loading} error={error} retry={reload} empty={references.length === 0} emptyTitle="No references recorded." emptyDetail="Add a reference above; it becomes a persisted metadata record.">
        <div className="reference-grid">{references.map(r => <article className="reference-card" key={r.id}><div className="reference-preview preview-slate"><FileText size={25} /><button onClick={() => inspect('reference', r.label, `${r.kind} · ${r.role || 'unlabeled'} · ${r.status}`)} aria-label={`Inspect ${r.label}`}><ChevronRight size={15} /></button></div><div className="reference-body"><div className="reference-top"><span>{r.kind}</span><span className="status-pill neutral">{r.status}</span></div><strong>{r.label}</strong><small>{r.role || 'Unlabeled'} · {formatDate(r.created_at)}</small><div className="reference-foot"><button className="text-button" onClick={() => go('analysis')}>Open analysis <ArrowRight size={14} /></button></div></div></article>)}</div>
      </DataStates></Card></>
}

export function Analysis({ go, inspect, announce }: P) {
  const { items: evidence, loading, error, reload } = useBackendList<EvidenceRow>('/api/evidence?project_id=project_northstar&limit=100')
  return <><div className="analysis-header"><div className="analysis-reference"><div className="reference-thumb preview-amber"><FileText size={21} /></div><div><span className="panel-kicker">Persisted evidence</span><strong>{evidence.length} claim(s)</strong><small>Recorded by local analysis runs</small></div></div><span className="status-pill positive"><CheckCircle2 size={13} /> {evidence.length > 0 ? 'Evidence present' : 'No claims yet'}</span></div>
    <div className="section-grid two-one"><Card><Head title="Creative findings" sub="Persisted claims with confidence" action="Open evidence" click={() => go('evidence')} />
      <DataStates loading={loading} error={error} retry={reload} empty={evidence.length === 0} emptyTitle="No findings recorded." emptyDetail="Findings are persisted as evidence claims by local analysis runs.">
        <div className="finding-grid">{evidence.slice(0, 8).map(e => <div className="finding-card" key={e.id}><div><span>{e.source_type}</span><span className="confidence"><i className={`confidence-dot ${e.confidence >= 0.8 ? 'high' : e.confidence >= 0.5 ? 'medium' : 'low'}`} />{Math.round(e.confidence * 100)}%</span></div><strong>{e.claim.slice(0, 80)}</strong><p>{e.claim.slice(0, 160)}</p><button className="text-button" onClick={() => inspect('finding', e.claim.slice(0, 60), `Evidence ${e.id} · confidence ${e.confidence}.`)}>Inspect <ArrowRight size={13} /></button></div>)}</div>
      </DataStates></Card>
      <Card><Head title="Analysis actions" sub="Run or queue analysis" /><Row icon={Bot} title="Queue a brief" detail="Run the local vertical slice from Creative Chat" status="Chat" tone="coral" click={() => go('chat')} /><Row icon={Network} title="Evidence graph" detail="Trace claims to their sources" status="Open" tone="sage" click={() => go('evidence')} /><button className="soft-action" onClick={() => announce('Analysis runs persist evidence claims to the local backend.')}><Check size={15} /> How findings persist</button></Card></div></>
}

export function Evidence({ inspect }: P) {
  const [table, setTable] = useState(false)
  const { items: evidence, loading, error, reload } = useBackendList<EvidenceRow>('/api/evidence?project_id=project_northstar&limit=100')
  const nodes = evidence.slice(0, 8)
  return <><div className="toolbar-row"><div className="legend-row"><span><i className="legend-dot amber" /> Source</span><span><i className="legend-dot coral" /> Claim</span><span><i className="legend-dot sage" /> High confidence</span></div><div><button className={`view-toggle ${!table ? 'active' : ''}`} onClick={() => setTable(false)}><Network size={14} /> Graph</button><button className={`view-toggle ${table ? 'active' : ''}`} onClick={() => setTable(true)}><ClipboardCheck size={14} /> Table</button><button className="secondary-button compact-button" onClick={reload}><RefreshCw size={14} /> Refresh</button></div></div>
    <DataStates loading={loading} error={error} retry={reload} empty={evidence.length === 0} emptyTitle="No evidence recorded." emptyDetail="Evidence claims persist here with their source and confidence.">
      {table ? <Card><Head title="Evidence table" sub="Accessible alternate to graph view" /><div className="table-wrap"><table className="data-table"><thead><tr><th>Claim</th><th>Source</th><th>Confidence</th><th>Recorded</th><th /></tr></thead><tbody>{evidence.map(e => <tr key={e.id}><td><b>{e.claim.slice(0, 70)}</b><small>{e.id}</small></td><td>{e.source_type}</td><td><span className="confidence">{Math.round(e.confidence * 100)}%</span></td><td>{formatDate(e.created_at)}</td><td><button className="icon-button subtle" onClick={() => inspect('evidence', e.id, e.claim)} aria-label={`Inspect ${e.id}`}><ChevronRight size={15} /></button></td></tr>)}</tbody></table></div></Card>
        : <Card className="graph-panel"><div className="graph-header"><div><span className="panel-kicker">Current path</span><strong>{nodes.length} persisted evidence node(s)</strong></div><span className="status-pill positive"><ShieldCheck size={13} /> Persisted</span></div><div className="evidence-graph"><svg viewBox="0 0 100 72" preserveAspectRatio="none">{nodes.slice(0, 5).map((_, i) => i < 4 ? <path key={i} d={`M${12 + i * 18} 46 C${16 + i * 18} 38 ${16 + i * 18} 29 ${24 + i * 18} 24`} /> : null)}</svg>{nodes.slice(0, 5).map((n, i) => <button className={`graph-node ${n.confidence >= 0.8 ? 'sage' : n.confidence >= 0.5 ? 'coral' : 'amber'}`} style={{ left: `${12 + i * 18}%`, top: `${i % 2 === 0 ? 46 : 24}%` }} key={n.id} onClick={() => inspect('evidence', n.id, n.claim)}><small>{n.source_type}</small><strong>{n.claim.slice(0, 42)}</strong></button>)}</div><div className="graph-foot"><span><span className="status-led good" /> {evidence.length} linked findings</span><span>Persisted in SQLite</span></div></Card>}
    </DataStates></>
}

export function Genome({ go, inspect, announce }: P) {
  const { items: genomes, loading, error, reload } = useBackendList<Genome>('/api/genomes?project_id=project_northstar')
  const [selectedId, setSelectedId] = useState('')
  const genome = genomes.find(g => g.id === selectedId) || genomes[0]
  const patterns = parseJsonArray(genome?.patterns_json || '')
  return <><div className="genome-hero"><div><div className="genome-title"><span className="genome-orbit"><Sparkles size={23} /></span><div><span className="panel-kicker">Genome / persisted</span><h2>{genome ? `${genome.name} ${genome.version}` : 'No genome yet'}</h2><p>{genome ? `Persisted ${formatDate(genome.created_at)} · ${patterns.length} pattern(s)` : 'Synthesize a genome from evidence to populate this surface.'}</p></div></div>{genomes.length > 1 && <div className="tag-row">{genomes.map(g => <button key={g.id} className={`tag ${genome?.id === g.id ? 'active' : ''}`} onClick={() => setSelectedId(g.id)}>{g.name} {g.version}</button>)}</div>}</div>
    <div className="genome-meta"><span className="status-pill positive"><span /> {genome ? 'Persisted' : 'Empty'}</span><button className="text-button" onClick={() => go('intake')}>View sources <ArrowRight size={14} /></button><button className="secondary-button compact-button" onClick={reload}><RefreshCw size={14} /> Refresh</button></div></div>
    <DataStates loading={loading} error={error} retry={reload} empty={!genome} emptyTitle="No genome recorded." emptyDetail="Genomes persist reusable creative grammar synthesized from evidence.">
      <div className="section-grid two-one"><Card><Head title="Creative grammar" sub="The persisted pattern library" /><div className="grammar-grid">{patterns.slice(0, 12).map((p, i) => { const text = typeof p === 'string' ? p : JSON.stringify(p); return <button className="grammar-card" key={i} onClick={() => inspect('genome', `Pattern ${i + 1}`, text)}><span>Pattern {i + 1}</span><strong>{text.slice(0, 80)}</strong><small>{genome?.version}</small></button> })}{patterns.length === 0 && <p className="panel-copy">This genome carries no parsed patterns.</p>}</div></Card>
        <Card><Head title="Genome actions" sub="Move the pattern forward" /><Row icon={GitCompare} title="Create blueprint" detail="Turn grammar into scenes" tone="coral" click={() => go('blueprint')} /><Row icon={RefreshCw} title="Generate mutation" detail="Change one variable" tone="sage" click={() => go('adaptation')} /><button className="soft-action" onClick={() => announce('Genome synthesis persists new versions to the local backend.')}><Sparkles size={14} /> How synthesis persists <ArrowRight size={14} /></button></Card></div>
    </DataStates></>
}

export function Blueprint({ go, inspect, announce }: P) {
  const { items: blueprints, loading, error, reload } = useBackendList<Blueprint>('/api/blueprints?project_id=project_northstar')
  const [selectedId, setSelectedId] = useState('')
  const [name, setName] = useState('')
  const blueprint = blueprints.find(b => b.id === selectedId) || blueprints[0]
  const scenes: string[] = parseJsonArray(blueprint?.scenes_json || '').map(s => typeof s === 'string' ? s : String((s as any)?.title || JSON.stringify(s)))
  const constraints: string[] = parseJsonArray(blueprint?.constraints_json || '').map(s => typeof s === 'string' ? s : JSON.stringify(s))
  const create = () => {
    if (!name.trim()) { announce('Name the blueprint first.'); return }
    backendFetch('/api/blueprints', { method: 'POST', body: JSON.stringify({ project_id: 'project_northstar', name: name.trim(), scenes: [], constraints: [] }) })
      .then(() => { setName(''); announce('Blueprint draft created in the local backend.'); reload() })
      .catch(() => announce('The blueprint could not be created.'))
  }
  return <><div className="blueprint-summary"><div><span className="panel-kicker">Blueprint / persisted</span><h2>{blueprint?.name || 'No blueprint yet'}</h2><p>{blueprint ? `${scenes.length} scene(s) · ${constraints.length} constraint(s)` : 'Draft a blueprint to make creative intent executable.'}</p></div>
    <div className="blueprint-stats"><Metric value={String(scenes.length).padStart(2, '0')} label="Scenes" /><Metric value={String(constraints.length).padStart(2, '0')} label="Constraints" /><button className="secondary-button compact-button" onClick={reload}><RefreshCw size={14} /> Refresh</button></div></div>
    <div className="toolbar-row"><select className="filter-button" value={blueprint?.id || ''} onChange={e => setSelectedId(e.target.value)} aria-label="Blueprint">{blueprints.map(b => <option key={b.id} value={b.id}>{b.name}</option>)}</select><div className="search-field"><input placeholder="Blueprint name" value={name} onChange={e => setName(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') create() }} /><button className="primary-button compact-button" onClick={create}><Plus size={15} /> Draft blueprint</button></div></div>
    <DataStates loading={loading} error={error} retry={reload} empty={!blueprint} emptyTitle="No blueprint drafted." emptyDetail="Draft a blueprint above; scenes and constraints persist in the local backend.">
      <div className="section-grid two-one"><Card><Head title="Scene graph" sub="Persisted scene order" action="Compile" click={() => go('compiler')} />{scenes.length === 0 ? <div className="empty-state"><GitCompare size={22} /><p>This blueprint has no scenes yet.</p></div> : scenes.map((s, i) => <div className="scene-card" key={`${s}-${i}`}><span className="scene-number">0{i + 1}</span><span className="scene-content"><div><strong>{s}</strong></div></span><span className="scene-actions"><button className="icon-button subtle" onClick={() => inspect('blueprint', s, `Scene ${i + 1} of ${blueprint?.name}.`)} aria-label={`Inspect ${s}`}><ChevronRight size={15} /></button></span></div>)}
        {constraints.length > 0 && <><Head title="Constraints" sub="Persisted" />{constraints.map((c, i) => <div className="review-item" key={i}><span><b>Constraint {i + 1}</b><small>{c}</small></span></div>)}</>}</Card>
        <Card><Head title="Blueprint detail" sub="Persisted record" /><div className="detail-list"><span>Drafted <b>{formatDate(blueprint?.created_at)}</b></span><span>Record <b>{blueprint?.id}</b></span><span>Genome <b>{blueprint?.genome_id || '--'}</b></span></div><button className="primary-button full-button" onClick={() => go('compiler')}>Compile blueprint <ArrowRight size={16} /></button></Card></div>
    </DataStates></>
}

export function Compiler({ go, announce }: P) {
  const { items: blueprints } = useBackendList<Blueprint>('/api/blueprints?project_id=project_northstar')
  const [blueprintId, setBlueprintId] = useState('')
  const [adapter, setAdapter] = useState('storyboard')
  const [plan, setPlan] = useState<{ run_id: string; status: string; plan: { intent_blocks: Array<{ order: number; scene: string; adapter: string; constraints: string[]; verification: string[] }>; scene_count: number } } | null>(null)
  const [compiling, setCompiling] = useState(false)
  const selected = blueprintId || blueprints[0]?.id || ''
  const { items: runs } = useBackendList<CompileRun>(selected ? `/api/compile-runs?blueprint_id=${selected}` : null)
  const compile = () => {
    if (!selected || compiling) return
    setCompiling(true)
    backendFetch<{ run_id: string; status: string; plan: any }>(`/api/blueprints/${selected}/compile`, { method: 'POST', body: JSON.stringify({ adapter }) })
      .then(result => { setPlan(result as any); announce(`Blueprint compiled deterministically: ${result.plan.scene_count} intent block(s). No model was contacted.`) })
      .catch(() => announce('The blueprint could not be compiled.'))
      .finally(() => setCompiling(false))
  }
  return <><div className="compiler-switcher"><div><span className="panel-kicker">Source blueprint</span><strong>{blueprints.find(b => b.id === selected)?.name || 'No blueprint'}</strong><small>{blueprints.length} persisted blueprint(s)</small></div><ArrowRight className="compiler-arrow" size={21} /><div><span className="panel-kicker">Adapter</span><select value={adapter} onChange={e => setAdapter(e.target.value)} aria-label="Adapter"><option value="storyboard">Storyboard adapter</option><option value="motion">Motion adapter</option><option value="voice">Voice adapter</option></select><small>Deterministic translation · no model call</small></div><button className="secondary-button compact-button" onClick={compile} disabled={compiling || !selected}><RefreshCw size={15} /> {compiling ? 'Compiling…' : 'Compile'}</button></div>
    <div className="compiler-layout"><Card><Head title="Source" sub="Persisted blueprint" /><select className="filter-button" value={selected} onChange={e => setBlueprintId(e.target.value)} aria-label="Blueprint">{blueprints.map(b => <option key={b.id} value={b.id}>{b.name}</option>)}</select>{blueprints.length === 0 && <p className="panel-copy">Draft a blueprint first.</p>}</Card>
      <Card><Head title="Compiled execution" sub={plan ? `Run ${plan.run_id} · persisted` : 'Compile to produce intent blocks'} />{plan ? plan.plan.intent_blocks.map(block => <div className="compiled-row" key={block.order}><span>Block {block.order}</span><b>{block.scene}</b><small>{block.adapter} · verify: {block.verification.join(', ')}</small></div>) : <div className="empty-state"><Code2 size={22} /><p>Compile a blueprint to see its deterministic intent blocks.</p></div>}
        {runs.length > 0 && <><Head title="Compile history" sub="Persisted runs" />{runs.slice(0, 4).map(r => <div className="review-item" key={r.id}><span><b>{r.id}</b><small>{formatTime(r.created_at)}</small></span><Pill tone="positive">{r.status}</Pill></div>)}</>}</Card></div>
    <Card><Head title="Translation contract" sub="The compiler stays stable when the model changes" /><p className="panel-copy">Intent, constraints, evidence, and verification remain portable. Compilation is a deterministic local translation, never a model call.</p><div className="hero-actions"><button className="primary-button" onClick={() => go('production')}>Send to production <ArrowRight size={16} /></button></div></Card></>
}

export function Governor({ go, inspect }: P) {
  const { items: advisors, loading, error, reload } = useBackendList<Advisor>('/api/governor/advisors')
  const { data: health } = useBackendObject<{ gateway?: { simulator_only?: boolean; model_count?: number } }>('/health')
  const { items: reviews } = useBackendList<GuardReview>('/api/projects/project_northstar/guard/reviews?limit=5')
  const simulatorOnly = health?.gateway?.simulator_only
  const tones = ['coral', 'amber', 'sage', 'slate']
  return <><div className="governor-banner"><div className="governor-orb"><Bot size={25} /></div><div><span className="panel-kicker">Flujo Governor / operating mode</span><h2>Evidence-weighted delegation</h2><p>{advisors.length} registered advisor(s) in the local Governor. {simulatorOnly === false ? 'A live gateway is configured.' : 'Advisor consultations run against the local simulator path until a provider credential is configured.'}</p></div><div className="governor-mode"><span className="status-pill positive"><span /> Balanced</span><small>Gateway models: {health?.gateway?.model_count ?? '--'}</small><small>Simulator: {simulatorOnly === false ? 'off' : 'on'}</small></div></div>
    <DataStates loading={loading} error={error} retry={reload} empty={advisors.length === 0} emptyTitle="No advisors registered." emptyDetail="The Governor controller registers advisors in the local backend.">
      <div className="advisor-grid">{advisors.map((a, i) => <div className="advisor-card" key={a.id}><span className={`advisor-icon ${tones[i % 4]}`}><Bot size={17} /></span><span>{a.name}</span><strong>{a.role}</strong><button onClick={() => inspect('advisor', a.name, `Status ${a.status}; registered in the local Governor.`)}>Inspect reasoning <ArrowRight size={14} /></button></div>)}</div>
    </DataStates>
    <div className="section-grid two-one"><Card><Head title="Decision queue" sub="Persisted guard reviews" action="Open review queue" click={() => go('reviewQueue')} />{reviews.length === 0 ? <div className="empty-state"><CheckCircle2 size={22} /><p>No reviews recorded.</p></div> : reviews.slice(0, 3).map(r => <button className="decision-row" key={r.id} onClick={() => inspect('review', r.id, r.reason)}><span className="decision-icon warning"><AlertTriangle size={16} /></span><span><strong>{r.id}</strong><small>{r.reason.slice(0, 80)}</small></span><ChevronRight size={15} /></button>)}</Card>
      <Card><Head title="Model Gateway" sub="Live gateway state" />{(health?.gateway ? [{ label: 'Gateway status', value: simulatorOnly === false ? 'Live' : 'Simulator-only', tone: simulatorOnly === false ? 'positive' : 'warning' }, { label: 'Advertised models', value: String(health.gateway.model_count ?? 0), tone: 'neutral' as string }] : []).map(x => <div className="gateway-row" key={x.label}><i className={`gateway-dot ${x.tone}`} /><b>{x.label}</b><span>{x.value}</span></div>)}<button className="soft-action" onClick={() => go('settings')}><LockKeyhole size={15} /> Manage integrations</button></Card></div></>
}

export function QA({ go, inspect, announce }: P) {
  const { items: runs, loading, error, reload } = useBackendList<QaRun>('/api/qa?project_id=project_northstar&limit=20')
  const [running, setRunning] = useState(false)
  const latest = runs[0]
  const findings: Array<{ check: string; status: string; detail: string }> = (() => { try { return JSON.parse(latest?.findings_json || '[]') } catch { return [] } })()
  const run = () => {
    if (running) return
    setRunning(true)
    backendFetch<QaRun>('/api/qa/run', { method: 'POST', body: JSON.stringify({ project_id: 'project_northstar' }) })
      .then(() => { announce('Verification run completed against persisted local state.'); reload() })
      .catch(() => announce('The verification run could not be completed.'))
      .finally(() => setRunning(false))
  }
  const status = latest?.status || 'not-run'
  return <><div className="qa-overview"><div className="qa-score"><div className="score-ring"><span>{status === 'passed' ? '100' : status === 'review' ? '--' : status === 'blocked' ? '!' : '·'}</span><small>{status}</small></div><div><span className="panel-kicker">Verification readiness</span><h2>{status === 'passed' ? 'Ready for review' : status === 'blocked' ? 'Blocked by findings' : status === 'review' ? 'Ready for review, not export' : 'No verification run yet'}</h2><p>{latest ? `Latest run ${formatTime(latest.created_at)} · ${findings.length} check(s)` : 'Run verification to evaluate persisted local state.'}</p></div></div><button className="secondary-button" onClick={run} disabled={running}><RefreshCw size={15} /> {running ? 'Verifying…' : 'Run verification'}</button></div>
    <div className="section-grid two-one"><Card><Head title="Verification scorecard" sub="Status is paired with reason" />
      <DataStates loading={loading} error={error} retry={reload} empty={findings.length === 0} emptyTitle="No verification run yet." emptyDetail="Run verification to evaluate identity, continuity, audio, caption, and format checks.">
        {findings.map(f => <button className="check-row" key={f.check} onClick={() => f.status === 'blocked' && inspect('finding', f.check, f.detail)}><span className={`check-state ${f.status}`}><Check size={13} /></span><span><b>{f.check}</b><small>{f.detail}</small></span><em className={f.status}>{f.status === 'passed' ? 'Passed' : f.status === 'blocked' ? 'Blocking' : 'Review'}</em></button>)}
      </DataStates></Card>
      <Card><Head title="Run history" sub="Persisted verification runs" />{runs.slice(0, 5).map(r => <div className="review-item" key={r.id}><span><b>{r.id}</b><small>{formatTime(r.created_at)}</small></span><Pill tone={r.status === 'passed' ? 'positive' : r.status === 'blocked' ? 'critical' : 'warning'}>{r.status}</Pill></div>)}{runs.length === 0 && !loading && <p className="panel-copy">No runs recorded.</p>}<button className="soft-action" onClick={() => go('reviewQueue')}>Open review queue <ArrowRight size={14} /></button></Card></div></>
}

type AffinityRow = { model_id: string; task_key: string; trials: number; successes: number; total_quality: number; total_cost: number; updated_at: string }

export function Performance({ go, inspect }: P) {
  const { data: usage } = useBackendObject<UsagePayload>('/api/usage?project_id=project_northstar&limit=200')
  const { items: affinity, loading, error, reload } = useBackendList<AffinityRow>('/api/adaptation/model-affinity?limit=50')
  const totals = usage?.totals
  return <><div className="performance-hero"><Card><span className="panel-kicker">Learning signal / local</span><h2>The useful question is not "what won?"</h2><p>Which creative choices created the result, and which can safely mutate next?</p><button className="inline-link" onClick={() => go('adaptation')}>Open adaptation loop <ArrowRight size={14} /></button></Card>
    <Card><Head title="Signal mix" sub="Persisted local signals" /><div className="signal-bar"><div><span>Requests</span><b>{totals?.requests ?? '--'}</b></div><div className="bar-track"><i className="coral" style={{ width: `${Math.min(100, (totals?.requests || 0))}%` }} /></div></div><div className="signal-bar"><div><span>Estimated cost</span><b>${(totals?.estimated_cost || 0).toFixed(2)}</b></div><div className="bar-track"><i className="sage" style={{ width: `${Math.min(100, (totals?.estimated_cost || 0) * 10)}%` }} /></div></div></Card></div>
    <Card><Head title="Model affinity" sub="Persisted adaptation outcomes" />
      <DataStates loading={loading} error={error} retry={reload} empty={affinity.length === 0} emptyTitle="No affinity records yet." emptyDetail="Model affinity is recorded when adaptation outcomes arrive.">
        <div className="table-wrap"><table className="data-table"><thead><tr><th>Model</th><th>Task</th><th>Trials</th><th>Successes</th><th>Avg quality</th></tr></thead><tbody>{affinity.slice(0, 10).map(a => <tr key={`${a.model_id}-${a.task_key}`}><td><b>{a.model_id}</b></td><td>{a.task_key}</td><td>{a.trials}</td><td>{a.successes}</td><td>{a.trials ? (a.total_quality / a.trials).toFixed(2) : '--'}</td></tr>)}</tbody></table></div>
      </DataStates>
      <button className="soft-action" onClick={() => inspect('performance', 'Local signals', 'Signals come from the persisted usage ledger and adaptation outcomes.')}>How signals persist <ArrowRight size={14} /></button></Card></>
}

export function Adaptation({ go, inspect, announce }: P) {
  const { items: candidates, loading, error, reload } = useBackendList<Candidate>('/api/adaptation/candidates?project_id=project_northstar&limit=50')
  const [selectedId, setSelectedId] = useState('')
  const selected = candidates.find(c => c.id === selectedId) || candidates[0]
  const [testing, setTesting] = useState(false)
  const test = () => {
    if (!selected || testing) return
    setTesting(true)
    backendFetch(`/api/adaptation/candidates/${selected.id}/evaluate`, { method: 'POST', body: JSON.stringify({ cases: [{ case: 'local-mutation-test', input: candidateTitle(selected) }] }) })
      .then(() => { announce('Mutation evaluated against a local test case.'); reload() })
      .catch(() => announce('The mutation test could not be recorded.'))
      .finally(() => setTesting(false))
  }
  return <><div className="adaptation-banner"><div className="mutation-icon"><RefreshCw size={21} /></div><div><span className="panel-kicker">Mutation lab / persisted</span><h2>Change one variable. Keep the evidence.</h2><p>Flujo evolves a successful recipe without breaking the pattern that earned trust.</p></div><span className="status-pill warning"><FlaskConical size={13} /> {candidates.length} candidate(s)</span></div>
    <DataStates loading={loading} error={error} retry={reload} empty={candidates.length === 0} emptyTitle="No mutation candidates." emptyDetail="Adaptation candidates appear here when the local Adaptation engine proposes them.">
      <div className="section-grid two-one"><Card><Head title="Candidate mutations" sub="Select a candidate to compare" />{candidates.map(c => <button className={`mutation-card ${selected?.id === c.id ? 'selected' : ''}`} key={c.id} onClick={() => setSelectedId(c.id)}><span className="mutation-mark"><GitCompare size={15} /></span><span><b>{candidateTitle(c)}</b><small>{c.candidate_type} · {c.status}</small></span><span className="confidence">{c.status}</span></button>)}</Card>
        <Card><Head title="Mutation evaluation" sub={selected ? candidateTitle(selected) : ''} /><div className="compare-block"><div><span>Current status</span><b>{selected?.status || '--'}</b><small>Persisted adaptation state.</small></div><ArrowRight size={17} /><div className="compare-new"><span>Candidate change</span><b>{selected ? candidateTitle(selected) : '--'}</b><small>Test against a local case.</small></div></div><button className="primary-button full-button" onClick={test} disabled={testing}><FlaskConical size={15} /> {testing ? 'Testing…' : 'Test mutation'}</button></Card></div>
      <Card><Head title="Promotion path" sub="How a test becomes reusable intelligence" /><div className="promotion-path">{[['01', 'Test', 'Run against a local case'], ['02', 'Evaluate', 'Compare outcome signals'], ['03', 'Promote', 'Carry winner into recipe/genome']].map((x, i) => <div className={`path-step ${i === 0 ? 'current' : ''}`} key={x[0]}><span>{x[0]}</span><div><b>{x[1]}</b><small>{x[2]}</small></div></div>)}</div><button className="inline-link" onClick={() => { inspect('mutation', selected ? candidateTitle(selected) : 'mutation', 'Promotion requires evidence from a recorded run.'); go('skills') }}>View registry destination <ArrowRight size={14} /></button></Card>
    </DataStates></>
}

export function Skills({ go, inspect, announce }: P) {
  const { items: skills, loading, error, reload } = useBackendList<SkillRow>('/api/skills?project_id=project_northstar')
  const [name, setName] = useState('')
  const [domain, setDomain] = useState('Hook')
  const draft = () => {
    if (!name.trim()) { announce('Name the skill first.'); return }
    backendFetch('/api/skills', { method: 'POST', body: JSON.stringify({ project_id: 'project_northstar', name: name.trim(), domain, confidence: 'Experimental', scope: 'Project', version: 'v0.1' }) })
      .then(() => { setName(''); announce('Skill drafted in the local registry.'); reload() })
      .catch(() => announce('The skill could not be drafted.'))
  }
  const validated = skills.filter(s => s.state === 'Validated' || s.confidence === 'High').length
  return <><div className="skills-kpis"><Metric value={String(skills.length).padStart(2, '0')} label="Registered skills" /><Metric value={String(validated).padStart(2, '0')} label="High confidence" /><Metric value={String(skills.filter(s => s.state === 'Experimental').length).padStart(2, '0')} label="Experimental" /><div className="registry-note"><Archive size={17} /><span><b>Versioned by evidence</b><small>Confidence grows with repeated use.</small></span></div></div>
    <Card><div className="table-toolbar"><div className="search-field"><input placeholder="Skill name" value={name} onChange={e => setName(e.target.value)} /><select value={domain} onChange={e => setDomain(e.target.value)} aria-label="Domain">{['Hook', 'Visual', 'Production', 'Voice', 'Editing'].map(d => <option key={d}>{d}</option>)}</select><button className="primary-button compact-button" onClick={draft} onKeyDown={e => { if (e.key === 'Enter') draft() }}><Plus size={15} /> Draft skill</button></div><button className="secondary-button compact-button" onClick={() => go('skillDiff')}><GitCompare size={14} /> Compare versions</button></div>
      <DataStates loading={loading} error={error} retry={reload} empty={skills.length === 0} emptyTitle="No skills registered." emptyDetail="Draft a skill to start the persisted registry.">
        <div className="table-wrap"><table className="data-table skills-table"><thead><tr><th>Skill</th><th>Domain</th><th>Confidence</th><th>Scope</th><th>Version</th><th>State</th><th /></tr></thead><tbody>{skills.map(x => <tr key={x.id}><td><b>{x.name}</b><small>Persisted skill record</small></td><td>{x.domain || '--'}</td><td><span className="confidence">{x.confidence}</span></td><td>{x.scope}</td><td className="mono">{x.version}</td><td><span className={`status-pill ${x.state === 'Validated' ? 'positive' : 'warning'}`}>{x.state}</span></td><td><button className="icon-button subtle" onClick={() => inspect('skill', x.name, `Domain ${x.domain}; confidence ${x.confidence}; scope ${x.scope}.`)} aria-label={`Inspect ${x.name}`}><ChevronRight size={15} /></button></td></tr>)}</tbody></table></div>
      </DataStates></Card></>
}

export function Automation({ announce, inspect }: P) {
  const { items: jobs, loading, error, reload } = useBackendList<GenerationJob>('/api/jobs?project_id=project_northstar&limit=50')
  const [enabled, setEnabled] = useState(false)
  return <><div className="automation-banner"><div className="automation-icon"><Workflow size={21} /></div><div><span className="panel-kicker">Automation Studio / local workflows</span><h2>Orchestrate the flow, then choose when to connect it.</h2><p>These are persisted local workflow jobs. Nothing is scheduled externally, published, or sent outside this computer.</p></div><span className="status-pill neutral"><LockKeyhole size={13} /> Local only</span></div>
    <div className="section-grid two-one"><Card><Head title="Workflows" sub="Persisted local workflow jobs" />
      <DataStates loading={loading} error={error} retry={reload} empty={jobs.length === 0} emptyTitle="No workflow jobs yet." emptyDetail="Queue a brief from Creative Chat to create the first persisted workflow job.">
        {jobs.slice(0, 6).map(j => <button className="workflow-row" key={j.id} onClick={() => inspect('job', j.id, `${j.workflow_name} · ${j.status} · checkpoint ${j.checkpoint}`)}><span className="workflow-node-icon"><Workflow size={15} /></span><span><b>{j.workflow_name}</b><small>Checkpoint {j.checkpoint}</small></span><span className={`status-pill ${j.status === 'completed' ? 'positive' : j.status === 'blocked' ? 'critical' : 'warning'}`}>{j.status}</span><ChevronRight size={14} /></button>)}
      </DataStates></Card>
      <Card><Head title="Run preview" sub="What automation can do locally" /><div className="workflow-preview">{[['Source', 'Job queued', 'coral'], ['Transform', 'Checkpoint advance', 'amber'], ['Review', 'Human checkpoint', 'sage'], ['Publish', 'Not connected', 'slate']].map((x, i) => <span key={x[0]}><span className={`workflow-node ${x[2]}`}><span>{x[0]}</span><b>{x[1]}</b></span>{i < 3 && <ArrowRight size={17} />}</span>)}</div>
        <button className={`toggle-row ${enabled ? 'enabled' : ''}`} onClick={() => { setEnabled(!enabled); announce(enabled ? 'Local automation preview paused.' : 'Local automation preview enabled. No external trigger was created.') }}><span className="toggle-track"><i /></span><span><b>Local automation preview</b><small>{enabled ? 'Preview is active locally' : 'Preview is paused'}</small></span></button></Card></div>
    <Card><Head title="Destinations" sub="Connectors are intentionally unavailable in Version A" /><div className="destination-grid">{[['Model gateway', 'gateway'], ['Storage', 'storage'], ['Publishing', 'publishing']].map(([label]) => <div className="destination-card" key={label}><span><LockKeyhole size={18} /></span><b>{label}</b><small>Not connected</small><LockKeyhole size={14} /></div>)}</div></Card></>
}
