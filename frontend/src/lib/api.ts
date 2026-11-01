export interface ExpertCandidate {
  id: string // research id, matches DEMO_PROFILES for the experts we have mock research for
  name: string // display name, with title and credentials
  specialty: string
  location: string
  summary: string // one short sentence that tells this person apart from the others
  sources: { label: string; url: string }[]
  match: 'strong' | 'partial'
  matchReason: string // e.g. "Full name and specialty match"
}

export interface SearchQuery {
  name: string
  specialty: string
  location: string
}

interface BackendCandidate {
  candidate_id: string
  display_name: string
  summary: string
  field: string | null
  location: string | null
  evidence: { title: string; url: string }[]
  expert_evidence: boolean
  expert_note: string | null
}

async function getJson(url: string, init?: RequestInit) {
  const res = await fetch(url, init)
  if (!res.ok) throw new Error(`Request failed (${res.status})`)
  return res.json()
}

const postJson = (url: string, body: unknown) =>
  getJson(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })

// Polls a backend job (or a search) every 2 s until it is no longer queued or running.
async function pollDone<T extends { status: string }>(url: string, onEach?: (x: T) => void) {
  for (;;) {
    const x: T = await getJson(url)
    onEach?.(x)
    if (x.status !== 'queued' && x.status !== 'running') return x
    await new Promise((r) => setTimeout(r, 2000))
  }
}

// Starts a candidate search job on the backend (the Search button only) and returns its id.
export async function startSearch(query: SearchQuery): Promise<string> {
  const { search_id } = await postJson('/api/candidate-searches', {
    name: query.name,
    hints: { field: query.specialty || null, location: query.location || null },
  })
  return search_id
}

// Waits for a saved candidate search (started earlier, so a page reload starts nothing).
export async function loadSearch(searchId: string): Promise<ExpertCandidate[]> {
  const search = await pollDone<{
    status: string
    error: string | null
    candidates: BackendCandidate[]
  }>(`/api/candidate-searches/${searchId}`)
  if (search.status === 'failed') throw new Error(search.error ?? 'Search failed')
  return search.candidates.map((c): ExpertCandidate => ({
    id: c.candidate_id,
    name: c.display_name,
    specialty: c.field ?? '',
    location: c.location ?? '',
    summary: c.summary,
    sources: c.evidence.map((e) => ({ label: e.title, url: e.url })),
    match: c.expert_evidence ? 'strong' : 'partial',
    matchReason: c.expert_note ?? 'Expert-witness evidence found',
  }))
}

export interface ResearchJob {
  job_id: string
  status: string
  error: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  steps: { key: string; label: string; status: string; detail: string | null }[]
  activity: { seq: number; label: string; status: string; detail: string | null }[]
}

// One step of a research run's trace (backend app/trace.py): a model turn, a tool call starting or
// ending, a finding, a gap, an error the agent was told, the run starting or ending. `tool_seq` is
// the tool call it happened inside (model calls inside a tool, tools calling tools).
export type TraceKind =
  | 'run_start'
  | 'model_turn'
  | 'tool_start'
  | 'tool_end'
  | 'tool_error'
  | 'finding'
  | 'gap'
  | 'run_end'

export interface TraceEvent {
  seq: number
  at: string
  kind: TraceKind
  agent: string | null
  tool_seq: number | null
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  data: Record<string, any> & { debug: boolean }
}

export const getJob = (jobId: string): Promise<ResearchJob> => getJson(`/api/jobs/${jobId}`)

// The run's trace events after `after` (0 = all), oldest first; pass `last_seq` back to get only new ones.
export const getEvents = (
  jobId: string,
  after = 0
): Promise<{ events: TraceEvent[]; last_seq: number }> =>
  getJson(`/api/jobs/${jobId}/events?after=${after}&limit=2000`)

export const getFindings = (dossierId: string): Promise<ResearchFindings> =>
  getJson(`/api/dossiers/${dossierId}/findings`)

export interface Finding {
  finding_id: string
  kind: string
  title: string
  url: string
  confidence: 'high' | 'low'
  facts: Record<string, unknown>
  note: string | null
}

export interface ResearchFindings {
  findings: Finding[]
  gaps: { step: string; reason: string }[]
}

export interface Dossier {
  dossier_id: string
  job_id: string
  display_name: string
}

// Starts the research job for one candidate ("Research this expert" only) and returns its dossier id.
export async function startResearch(candidateId: string): Promise<string> {
  const { dossier_id } = await postJson('/api/research', { candidate_id: candidateId })
  return dossier_id
}

export const getDossier = (dossierId: string): Promise<Dossier> =>
  getJson(`/api/dossiers/${dossierId}`)

// Follows a saved research run until it ends, reporting each poll, and returns what it saved. A
// failed or killed run still returns its findings, with `error` saying why it stopped.
export async function waitForResearch(
  dossier: Dossier,
  onProgress: (job: ResearchJob) => void
): Promise<ResearchFindings & { error: string | null }> {
  const job = await pollDone<ResearchJob>(`/api/jobs/${dossier.job_id}`, onProgress)
  const saved: ResearchFindings = await getJson(`/api/dossiers/${dossier.dossier_id}/findings`)
  return { ...saved, error: job.status === 'failed' ? (job.error ?? 'Research failed') : null }
}

export type JobStatus = 'queued' | 'running' | 'done' | 'failed'

export interface SearchSummary {
  search_id: string
  name: string
  created_at: string
  status: JobStatus
  candidates: number
  research: {
    dossier_id: string
    display_name: string
    status: JobStatus
    error: string | null
    created_at: string
    tool_calls: number
    findings: number
    last_activity: string | null
  }[]
}

export const listSearches = (): Promise<SearchSummary[]> => getJson('/api/candidate-searches')

// What a research run knows about the expert so far, kept up to date by the backend after every
// tool that saves findings. Each item cites finding ids; `sources` maps them to url and title.
export const PROFILE_SECTIONS = [
  'identity',
  'expertise',
  'credentials',
  'cases',
  'publications',
  'red_flags',
  'not_him',
  'conflicts',
  'open_leads',
] as const

export interface ProfileItem {
  text: string
  refs: string[]
  confidence: 'high' | 'low'
}

export type Profile = Record<(typeof PROFILE_SECTIONS)[number], ProfileItem[]> & {
  version: number
  updated_at: string | null
  sources: Record<string, { url: string; title: string }>
}

export const getProfile = (dossierId: string): Promise<Profile> =>
  getJson(`/api/dossiers/${dossierId}/profile`)
