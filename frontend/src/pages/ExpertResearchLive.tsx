import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { LoaderCircle } from 'lucide-react'
import AppHeader from '@/components/AppHeader'
import { Finding, getDossier, ResearchFindings, ResearchJob, waitForResearch } from '@/lib/api'

// One saved research run (/research/:dossierId), started by "Research this expert": shows what the
// agent does as it runs, then lists the findings by kind and what could not be found. Loading or
// reloading the page only reads the run; it never starts one.
export default function ExpertResearchLive() {
  const navigate = useNavigate()
  const { dossierId = '' } = useParams()
  const [name, setName] = useState('Expert research')
  const [job, setJob] = useState<ResearchJob | null>(null)
  const [result, setResult] = useState<ResearchFindings | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    getDossier(dossierId)
      .then((dossier) => {
        if (live) setName(dossier.display_name)
        return waitForResearch(dossier, (j) => live && setJob(j))
      })
      .then((r) => {
        if (!live) return
        setResult(r)
        if (r.error) setError(`Research stopped: ${r.error}. Showing what it found before that.`)
      })
      .catch((e) => live && setError(e instanceof Error ? e.message : 'Research failed'))
    return () => {
      live = false
    }
  }, [dossierId])

  const groups: Record<string, Finding[]> = {}
  for (const f of result?.findings ?? []) (groups[f.kind] ??= []).push(f)
  const byKind = Object.entries(groups)

  return (
    <div className="min-h-screen text-sm">
      <AppHeader />
      <main className="mx-auto flex max-w-[800px] flex-col gap-5 px-4 pb-12 pt-8">
        <button
          type="button"
          onClick={() => navigate('/')}
          className="self-start font-medium text-primary hover:underline"
        >
          New search
        </button>
        <h1 className="font-serif text-[28px] font-semibold tracking-tight">{name}</h1>

        {error ? (
          <p role="alert" className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-red-800">
            {error}
          </p>
        ) : (
          !result && (
            <p role="status" className="flex items-center gap-2 text-muted-foreground">
              <LoaderCircle className="size-4 animate-spin text-primary" /> Researching… this can
              take up to 4 hours.
            </p>
          )
        )}

        {byKind.map(([kind, findings]) => (
          <section key={kind} className="space-y-2">
            <h2 className="font-semibold capitalize">
              {kind.replaceAll('_', ' ')} ({findings.length})
            </h2>
            <ul className="divide-y rounded-xl border bg-card">
              {findings.map((f) => (
                <li key={f.finding_id} className="space-y-1 px-5 py-3">
                  <a
                    href={f.url}
                    target="_blank"
                    rel="noreferrer"
                    className="font-medium text-primary hover:underline"
                  >
                    {f.title}
                  </a>
                  {f.confidence === 'low' && (
                    <span className="ml-2 rounded-full bg-[#FFF1DC] px-2 py-0.5 text-[12px] text-[#7A4E00]">
                      Low confidence
                    </span>
                  )}
                  {f.note && <p className="text-[#2E3442]">{f.note}</p>}
                  {Object.entries(f.facts).map(([k, v]) => (
                    <p key={k} className="text-muted-foreground">
                      {k.replaceAll('_', ' ')}: {typeof v === 'string' ? v : JSON.stringify(v)}
                    </p>
                  ))}
                </li>
              ))}
            </ul>
          </section>
        ))}
        {result && !result.findings.length && (
          <p className="rounded-xl border bg-card px-5 py-4 text-muted-foreground">
            The research found nothing.
          </p>
        )}

        {!!result?.gaps.length && (
          <section className="space-y-2">
            <h2 className="font-semibold">Not found</h2>
            <ul className="list-disc space-y-1 pl-5 text-muted-foreground">
              {result.gaps.map((g, i) => (
                <li key={i}>{g.reason}</li>
              ))}
            </ul>
          </section>
        )}

        {!!job?.activity.length && (
          <section className="space-y-2">
            <h2 className="font-semibold">What the agent did</h2>
            <ul className="divide-y rounded-xl border bg-card">
              {job.activity.map((a) => (
                <li key={a.seq} className="flex gap-3 px-5 py-2">
                  <span className="w-20 shrink-0 text-muted-foreground">{a.status}</span>
                  <span>
                    {a.label}
                    {a.detail && <span className="text-muted-foreground"> · {a.detail}</span>}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        )}
      </main>
    </div>
  )
}
