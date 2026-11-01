import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { LoaderCircle } from 'lucide-react'
import AppHeader from '@/components/AppHeader'
import { JobStatus, listSearches, SearchSummary } from '@/lib/api'
import { cn } from '@/lib/utils'

// Every saved search, newest first, with the research runs started from it: whether each is still
// running, what the agent is doing now, and links back to the results and the run. Reads only;
// refreshes every 5 s.
export default function SearchesPage() {
  const [searches, setSearches] = useState<SearchSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)

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

  return (
    <div className="min-h-screen text-sm">
      <AppHeader />
      <main className="mx-auto flex max-w-[900px] flex-col gap-5 px-4 pb-12 pt-8">
        <div className="space-y-1">
          <h1 className="font-serif text-[28px] font-semibold tracking-tight">Searches</h1>
          <p className="text-muted-foreground">
            Every search and research run, newest first. Updates every few seconds.
          </p>
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
            No searches yet. <Link to="/" className="text-primary hover:underline">Find an expert</Link>
          </p>
        ) : (
          <ul className="space-y-3">
            {searches.map((s) => (
              <li key={s.search_id} className="rounded-xl border bg-card">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-5 py-3">
                  <span className="text-[15px] font-semibold">{s.name}</span>
                  <Status status={s.status} />
                  <span className="text-muted-foreground">
                    {when(s.created_at)} · {s.candidates}{' '}
                    {s.candidates === 1 ? 'person' : 'people'} found
                  </span>
                  <Link
                    to={`/?name=${encodeURIComponent(s.name)}&search=${s.search_id}`}
                    className="ml-auto font-medium text-primary hover:underline"
                  >
                    Open results
                  </Link>
                </div>
                {s.research.length > 0 && (
                  <ul className="divide-y border-t">
                    {s.research.map((r) => (
                      <li
                        key={r.dossier_id}
                        className="flex flex-wrap items-center gap-x-3 gap-y-1 px-5 py-2.5"
                      >
                        <span>Research: {r.display_name}</span>
                        <Status status={r.status} />
                        <span className="text-muted-foreground">
                          {when(r.created_at)} · {r.tool_calls} tool calls · {r.findings} findings
                        </span>
                        <Link
                          to={`/research/${r.dossier_id}`}
                          className="ml-auto font-medium text-primary hover:underline"
                        >
                          Open run
                        </Link>
                        {r.status === 'running' && r.last_activity && (
                          <span className="w-full text-muted-foreground">Now: {r.last_activity}</span>
                        )}
                        {r.status === 'failed' && r.error && (
                          <span className="w-full text-red-800">Stopped: {r.error}</span>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ul>
        )}
      </main>
    </div>
  )
}

const STATUS: Record<JobStatus, { label: string; style: string }> = {
  queued: { label: 'Waiting', style: 'bg-muted text-muted-foreground' },
  running: { label: 'Running', style: 'bg-[#E4EEFB] text-[#1F4E8C]' },
  done: { label: 'Done', style: 'bg-[#E3F1EA] text-[#1D5B40]' },
  failed: { label: 'Stopped', style: 'bg-red-50 text-red-800' },
}

function Status({ status }: { status: JobStatus }) {
  return (
    <span className={cn('rounded-full px-2 py-0.5 text-[12px] font-semibold', STATUS[status].style)}>
      {STATUS[status].label}
    </span>
  )
}

function when(iso: string) {
  return new Date(iso).toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}
