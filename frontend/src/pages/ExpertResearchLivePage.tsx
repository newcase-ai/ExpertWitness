import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { Download, LoaderCircle } from 'lucide-react'
import AppHeader from '@/components/AppHeader'
import RunResults, { ProfileView } from '@/components/run/RunResults'
import { ResearchProfile } from '@/components/run/ResearchProfile'
import { toResearchProfile } from '@/lib/research-profile'
import Timeline from '@/components/run/Timeline'
import { clock, duration, liveFindings, runStats } from '@/lib/trace'
import {
  getDossier,
  getEvents,
  getFindings,
  getJob,
  getProfile,
  type Profile,
  type ResearchFindings,
  type ResearchJob,
  type TraceEvent,
} from '@/lib/api'
import { cn } from '@/lib/utils'

const POLL_MS = 2000
const STATUS: Record<string, { label: string; className: string }> = {
  queued: { label: 'Queued', className: 'bg-muted text-muted-foreground' },
  running: { label: 'Running', className: 'bg-[#E4EEFB] text-[#1F4E8C]' },
  done: { label: 'Done', className: 'bg-[#E3F1EA] text-[#1D5B40]' },
  failed: { label: 'Failed', className: 'bg-red-50 text-red-800' },
}
const TABS = ['Timeline', 'Results', 'Raw log'] as const
type Tab = (typeof TABS)[number]

// One research run (/research/:dossierId), started by "Research this expert" or opened from the
// searches list: what the agent does step by step (its trace), what it found, and the raw log.
// Loading or reloading the page only reads the run; it never starts one. The searcher sees the
// profile only; ?debug=1 shows the run itself (trace, findings, gaps, raw log).
export default function ExpertResearchLivePage() {
  const navigate = useNavigate()
  const { dossierId = '' } = useParams()
  const [name, setName] = useState('Expert research')
  const [job, setJob] = useState<ResearchJob | null>(null)
  const [events, setEvents] = useState<TraceEvent[]>([])
  const [result, setResult] = useState<ResearchFindings | null>(null)
  const [profile, setProfile] = useState<Profile | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [tab, setTab] = useState<Tab>('Timeline')
  const [now, setNow] = useState(Date.now())
  const [params, setParams] = useSearchParams()
  const debug = params.get('debug') === '1'
  const debugToggle = (
    <button
      type="button"
      role="switch"
      aria-checked={debug}
      onClick={() => setParams(debug ? {} : { debug: '1' })}
      className="flex items-center gap-2 text-sm font-medium text-muted-foreground hover:text-foreground"
    >
      Debug
      <span className={cn('flex h-5 w-9 items-center rounded-full p-0.5 transition-colors', debug ? 'bg-primary' : 'bg-muted-foreground/30')}>
        <span className={cn('size-4 rounded-full bg-white shadow transition-transform', debug && 'translate-x-4')} />
      </span>
    </button>
  )

  useEffect(() => {
    let live = true
    let after = 0
    const all: TraceEvent[] = []
    const pull = async (jobId: string) => {
      for (;;) {
        const page = await getEvents(jobId, after)
        if (!live || !page.events.length) return
        after = page.last_seq
        all.push(...page.events)
        setEvents([...all])
        if (page.events.length < 2000) return
      }
    }
    const loadProfile = () =>
      getProfile(dossierId)
        .then((p) => live && setProfile(p))
        .catch(() => {}) // the next poll tries again
    ;(async () => {
      try {
        const dossier = await getDossier(dossierId)
        if (!live) return
        setName(dossier.display_name)
        let current: ResearchJob
        for (;;) {
          current = await getJob(dossier.job_id)
          if (!live) return
          setJob(current)
          await pull(dossier.job_id)
          loadProfile()
          if (current.status !== 'queued' && current.status !== 'running') break
          await new Promise((r) => setTimeout(r, POLL_MS))
        }
        const saved = await getFindings(dossierId)
        if (!live) return
        setResult(saved)
        if (current.status === 'failed')
          setError(`Research stopped: ${current.error ?? 'failed'}. Showing what it found before that.`)
      } catch (e) {
        if (live) setError(e instanceof Error ? e.message : 'Could not load this run')
      }
    })()
    return () => {
      live = false
    }
  }, [dossierId])

  const running = !job || job.status === 'queued' || job.status === 'running'
  useEffect(() => {
    if (!running) return
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [running])

  const stats = useMemo(() => runStats(events), [events])
  const findings = useMemo(() => result?.findings ?? liveFindings(events), [result, events])
  const gaps = result?.gaps ?? events.filter((e) => e.kind === 'gap').map((e) => ({ step: e.data.step, reason: e.data.reason }))
  const started = job?.started_at ? Date.parse(job.started_at) : null
  const elapsed = started ? (job?.finished_at ? Date.parse(job.finished_at) : now) - started : null
  const status = STATUS[job?.status ?? 'queued'] ?? STATUS.queued
  const toolCalls = Object.values(stats.tools).reduce((a, b) => a + b, 0)

  const view = useMemo(
    () => profile && toResearchProfile(name, profile, findings, job?.activity ?? []),
    [name, profile, findings, job]
  )
  if (!debug)
    return (
      <div className="min-h-screen">
        <AppHeader />
        {error && (
          <p role="alert" className="mx-auto mt-4 max-w-[1120px] rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
            {error}
          </p>
        )}
        {view ? (
          <ResearchProfile p={view} running={running} actions={debugToggle} />
        ) : (
          !error && <p className="mx-auto max-w-[1120px] px-4 pt-10 text-sm text-muted-foreground">Loading profile…</p>
        )}
      </div>
    )

  return (
    <div className="min-h-screen text-sm">
      <AppHeader />
      <main className="mx-auto flex max-w-[1200px] flex-col gap-5 px-4 pb-12 pt-8">
        <div className="flex justify-between">
          <button type="button" onClick={() => navigate('/')} className="font-medium text-primary hover:underline">
            New search
          </button>
          {debugToggle}
        </div>

        <header className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <h1 className="font-serif text-[28px] font-semibold tracking-tight">{name}</h1>
          <span className={cn('flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[12px]', status.className)}>
            {running && <LoaderCircle className="size-3 animate-spin" />}
            {status.label}
          </span>
          {elapsed != null && <span className="text-muted-foreground">{duration(elapsed)}</span>}
          {(stats.stopped ?? job?.steps[0]?.detail) && !running && (
            <span className="text-muted-foreground">· {stats.stopped ?? job?.steps[0]?.detail}</span>
          )}
          {stats.debug && (
            <span
              title="DEBUG_TRACE is on: this run's trace keeps page text, the testimony list and tracebacks in full"
              className="rounded-full bg-[#FFF1DC] px-2 py-0.5 text-[12px] text-[#7A4E00]"
            >
              Debug log
            </span>
          )}
        </header>

        {error && (
          <p role="alert" className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-red-800">
            {error}
          </p>
        )}

        {profile && <ProfileView p={profile} />}

        <dl className="grid grid-cols-2 gap-2 sm:grid-cols-5">
          <Stat label="Model turns" value={stats.turns} />
          <Stat
            label="Tool calls"
            value={toolCalls}
            detail={Object.entries(stats.tools)
              .map(([s, n]) => `${n} ${s}`)
              .join(' · ')}
          />
          <Stat label="Findings" value={findings.length} />
          <Stat label="Gaps" value={gaps.length} />
          <Stat
            label="Tokens"
            value={(stats.tokensIn + stats.tokensOut).toLocaleString()}
            detail={`${stats.tokensIn.toLocaleString()} in · ${stats.tokensOut.toLocaleString()} out`}
          />
        </dl>

        <nav className="flex gap-1 border-b" role="tablist">
          {TABS.map((t) => (
            <button
              key={t}
              type="button"
              role="tab"
              aria-selected={tab === t}
              onClick={() => setTab(t)}
              className={cn(
                '-mb-px border-b-2 px-3 py-2 font-medium',
                tab === t ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'
              )}
            >
              {t}
              {t === 'Results' && findings.length > 0 && ` (${findings.length})`}
            </button>
          ))}
        </nav>

        {tab === 'Timeline' && <Timeline events={events} running={running} />}
        {tab === 'Results' && <RunResults findings={findings} gaps={gaps} running={running} />}
        {tab === 'Raw log' && <RawLog events={events} jobId={job?.job_id ?? ''} />}
      </main>
    </div>
  )
}

function Stat({ label, value, detail }: { label: string; value: number | string; detail?: string }) {
  return (
    <div className="rounded-xl border bg-card px-4 py-3">
      <dt className="text-[12px] text-muted-foreground">{label}</dt>
      <dd className="text-[20px] font-semibold tabular-nums">{value}</dd>
      {detail && <dd className="truncate text-[12px] text-muted-foreground">{detail}</dd>}
    </div>
  )
}

function logLine(e: TraceEvent): string {
  const d = e.data
  const where = e.tool_seq != null ? ` [in #${e.tool_seq}]` : ''
  const body =
    e.kind === 'model_turn'
      ? `${e.agent} ${(d.tool_calls ?? []).map((c: { name: string }) => c.name).join(', ') || JSON.stringify(String(d.text ?? '').slice(0, 160))}`
      : e.kind === 'tool_start'
        ? `#${d.seq} ${d.tool} ${JSON.stringify(d.args)}`
        : e.kind === 'tool_end'
          ? `#${d.seq} ${d.tool} ${d.status}${d.ms != null ? ` ${d.ms}ms` : ''}${d.detail ? ` · ${d.detail}` : ''}${d.error ? ` · ${d.error.type}: ${d.error.message}` : ''}`
          : e.kind === 'finding'
            ? `${d.kind} ${d.confidence} ${d.title} ${d.url}`
            : e.kind === 'gap'
              ? `${d.step}: ${d.reason}`
              : e.kind === 'tool_error'
                ? `${d.tool}: ${d.error}`
                : JSON.stringify(Object.fromEntries(Object.entries(d).filter(([k]) => !['message', 'instructions', 'caps', 'tools'].includes(k))))
  return `${String(e.seq).padStart(4)} ${clock(e.at)} ${e.kind.padEnd(10)}${where} ${body}`
}

// Every event as one line, and the whole trace as JSON to download.
function RawLog({ events, jobId }: { events: TraceEvent[]; jobId: string }) {
  const download = () => {
    const blob = new Blob([JSON.stringify(events, null, 2)], { type: 'application/json' })
    const link = document.createElement('a')
    link.href = URL.createObjectURL(blob)
    link.download = `${jobId || 'run'}-trace.json`
    link.click()
    URL.revokeObjectURL(link.href)
  }
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <p className="text-muted-foreground">{events.length} events</p>
        <button
          type="button"
          onClick={download}
          disabled={!events.length}
          className="flex items-center gap-1.5 rounded-md border bg-card px-3 py-1.5 font-medium hover:bg-muted disabled:opacity-50"
        >
          <Download className="size-4" /> Download JSON
        </button>
      </div>
      <pre className="max-h-[70vh] overflow-auto rounded-xl border bg-card px-4 py-3 font-mono text-[12px] leading-relaxed">
        {events.map(logLine).join('\n') || 'No events yet.'}
      </pre>
    </div>
  )
}
