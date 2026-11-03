import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { LoaderCircle, RotateCcw } from 'lucide-react'
import AppHeader from '@/components/AppHeader'
import {
  getDossier,
  JobStatus,
  listSearches,
  SearchSummary,
  startResearch,
  startSearch,
} from '@/lib/api'
import { cn } from '@/lib/utils'

// Every research run, newest first and grouped by day: when it started, its status, and a link to the
// run. A search nobody was picked from yet is a row too (searching, not
// researched, or cancelled), linking back to its results. Reads only; refreshes every 5 s.
export default function SearchesPage() {
  const [searches, setSearches] = useState<SearchSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [retrying, setRetrying] = useState<string | null>(null) // the row being started again
  const navigate = useNavigate()

  useEffect(() => {
    let live = true
    const load = () =>
      listSearches()
        .then((s) => {
          if (!live) return
          setSearches(s)
          setError(null)
        })
        .catch((e) => live && setError(e instanceof Error ? e.message : 'Could not load searches'))
    load()
    const timer = setInterval(load, 5000)
    return () => {
      live = false
      clearInterval(timer)
    }
  }, [])

  const all = searches ?? []
  const searching = (s: SearchSummary) => s.status === 'queued' || s.status === 'running'
  const rows: Row[] = [
    ...all.flatMap((s) =>
      s.research.map((r) => ({
        key: r.dossier_id,
        name: r.display_name,
        status: r.status,
        created_at: r.created_at,
        href: `/research/${r.dossier_id}`,
        error: r.error,
        // the same person again: the run's dossier names the candidate it researched
        retry: () =>
          getDossier(r.dossier_id)
            .then((d) => startResearch(d.candidate_id))
            .then((id) => `/research/${id}`),
      }))
    ),
    // a search nobody was picked from yet: still searching, waiting for a pick, or cancelled
    ...all
      .filter((s) => !s.research.length)
      .map((s) => ({
        key: s.search_id,
        name: s.name,
        status: s.status,
        created_at: s.created_at,
        href: resultsHref(s),
        searching: searching(s),
        pick: s.status === 'done' ? s.candidates : undefined,
        error: s.status === 'failed' ? 'the search did not finish' : null,
        // the list does not carry the specialty or city, so the new search is by name only
        retry: () =>
          startSearch({ name: s.name, specialty: '', location: '' }).then(
            (id) => `/?name=${encodeURIComponent(s.name)}&search=${id}`
          ),
      })),
  ].sort((a, b) => b.created_at.localeCompare(a.created_at))
  const days = groupByDay(rows)

  return (
    <div className="bg-dots min-h-screen text-sm">
      <AppHeader />
      <main className="px-4 pb-16 pt-12">
        <div className="mx-auto flex max-w-[900px] flex-col gap-6">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <p className="text-[11px] font-bold uppercase tracking-[0.1em] text-primary">
                Your research
              </p>
              <h1 className="font-serif text-[34px] font-semibold leading-tight tracking-tight">
                Searches
              </h1>
            </div>
            <Link
              to="/"
              className="rounded-lg bg-primary px-4 py-2 text-[13px] font-semibold text-primary-foreground hover:bg-primary/90"
            >
              New search
            </Link>
          </div>
          {error && (
            <p role="alert" className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-red-800">
              {error}
            </p>
          )}
          {!searches ? (
            !error && (
              <p role="status" className="flex items-center gap-2 text-muted-foreground">
                <LoaderCircle className="size-4 animate-spin text-primary" /> Loading…
              </p>
            )
          ) : !searches.length ? (
            <p className="rounded-xl border bg-card px-5 py-4 text-muted-foreground">
              No searches yet.{' '}
              <Link to="/" className="text-primary hover:underline">
                Find an expert
              </Link>
            </p>
          ) : (
            <>
              {days.map(([day, dayRuns]) => (
                <section key={day} className="flex flex-col gap-2">
                  <h2 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
                    {day}
                  </h2>
                  <ul className="divide-y rounded-xl border bg-card">
                    {dayRuns.map((r) => (
                      <li
                        key={r.key}
                        className="flex flex-wrap items-center gap-x-4 gap-y-2 px-5 py-4"
                      >
                        <div className="flex min-w-0 flex-[1_1_280px] flex-col gap-0.5">
                          <span className="font-serif text-[19px] font-semibold leading-snug">
                            {r.name}
                          </span>
                          <span className="text-[13px] text-muted-foreground">
                            {time(r.created_at)}
                            {r.pick !== undefined &&
                              ` · ${r.pick} ${r.pick === 1 ? 'person' : 'people'} found`}
                          </span>
                          {r.status === 'failed' && r.error && (
                            <span className="text-[13px] text-red-800">Cancelled: {r.error}</span>
                          )}
                        </div>
                        <div className="ml-auto flex items-center gap-4">
                          <Status status={r.status} searching={r.searching} pick={r.pick !== undefined} />
                          {r.status === 'failed' && (
                            <button
                              type="button"
                              disabled={retrying !== null}
                              onClick={() => {
                                setRetrying(r.key)
                                r.retry()
                                  .then(navigate)
                                  .catch((e) => {
                                    setRetrying(null)
                                    setError(e instanceof Error ? e.message : 'Could not start again')
                                  })
                              }}
                              className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-lg border border-primary/40 px-3 py-1 text-[13px] font-semibold text-primary hover:bg-primary/[0.07] disabled:opacity-50"
                            >
                              {retrying === r.key ? (
                                <LoaderCircle className="size-3.5 animate-spin" />
                              ) : (
                                <RotateCcw className="size-3.5" />
                              )}
                              Try again
                            </button>
                          )}
                          <Link
                            to={r.href}
                            className="whitespace-nowrap font-semibold text-primary hover:underline"
                          >
                            {r.pick ? 'Pick →' : 'Open →'}
                          </Link>
                        </div>
                      </li>
                    ))}
                  </ul>
                </section>
              ))}
            </>
          )}
        </div>
      </main>
    </div>
  )
}

const STATUS: Record<JobStatus, { label: string; style: string }> = {
  queued: { label: 'Waiting', style: 'bg-muted text-muted-foreground' },
  running: { label: 'Researching', style: 'bg-[#FDE7D8] text-[#A23F0C]' },
  done: { label: 'Ready', style: 'bg-[#E3F1EA] text-[#1D5B40]' },
  failed: { label: 'Cancelled', style: 'bg-red-50 text-red-800' },
}
const SEARCHING = { label: 'Searching', style: 'bg-[#EFE8FB] text-[#5B3A9E]' }

function Status({
  status,
  searching,
  pick,
}: {
  status: JobStatus
  searching?: boolean
  pick?: boolean
}) {
  if (pick)
    return (
      <span className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-full bg-[#FFF1DC] px-2.5 py-0.5 text-[12px] font-semibold text-[#7A4E00]">
        <span className="size-1.5 rounded-full bg-current" />
        Not researched
      </span>
    )
  const look = searching ? SEARCHING : STATUS[status]
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-0.5 text-[12px] font-semibold',
        look.style
      )}
    >
      <span
        className={cn(
          'size-1.5 rounded-full bg-current',
          (searching || status === 'running') && 'animate-pulse motion-reduce:animate-none'
        )}
      />
      {look.label}
    </span>
  )
}

// One line of the list: a research run, or a search nobody was picked from yet.
type Row = {
  key: string
  name: string
  status: JobStatus
  created_at: string
  href: string
  error?: string | null
  searching?: boolean
  retry: () => Promise<string> // starts the run or search again; resolves to the page to open
  pick?: number // a finished search nobody was picked from: how many people it found
}

const resultsHref = (s: SearchSummary) =>
  `/?name=${encodeURIComponent(s.name)}&search=${s.search_id}`

// Rows (newest first) under "Today", "Yesterday" or a date.
function groupByDay(runs: Row[]): [string, Row[]][] {
  const groups = new Map<string, Row[]>()
  for (const r of runs) {
    const day = dayLabel(r.created_at)
    groups.set(day, [...(groups.get(day) ?? []), r])
  }
  return [...groups]
}

function dayLabel(iso: string) {
  const d = new Date(iso)
  const today = new Date()
  const yesterday = new Date(today)
  yesterday.setDate(today.getDate() - 1)
  if (d.toDateString() === today.toDateString()) return 'Today'
  if (d.toDateString() === yesterday.toDateString()) return 'Yesterday'
  return d.toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' })
}

function time(iso: string) {
  return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}
