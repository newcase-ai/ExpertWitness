import type { Finding, Profile } from '@/lib/api'
import { PROFILE_SECTIONS } from '@/lib/api'
import { cn } from '@/lib/utils'

// What the run found: the findings by kind and what could not be found. While the run works,
// findings come from its trace; at the end, from the saved list. (The profile is on the page itself.)
export default function RunResults({
  findings,
  gaps,
  running,
}: {
  findings: Finding[]
  gaps: { step: string; reason: string }[]
  running: boolean
}) {
  const groups: Record<string, Finding[]> = {}
  for (const f of findings) (groups[f.kind] ??= []).push(f)
  const byKind = Object.entries(groups)
  return (
    <div className="space-y-5">
      {byKind.length > 0 && <h2 className="text-[15px] font-semibold">All findings ({findings.length})</h2>}
      {byKind.map(([kind, list]) => (
        <section key={kind} className="space-y-2">
          <h3 className="font-semibold capitalize">
            {kind.replaceAll('_', ' ')} ({list.length})
          </h3>
          <ul className="divide-y rounded-xl border bg-card">
            {list.map((f) => (
              <li key={f.finding_id} className="space-y-1 px-5 py-3">
                <a href={f.url} target="_blank" rel="noreferrer" className="font-medium text-primary hover:underline">
                  {f.title}
                </a>
                {f.confidence === 'low' && (
                  <span className="ml-2 rounded-full bg-[#FFF1DC] px-2 py-0.5 text-[12px] text-[#7A4E00]">
                    Low confidence
                  </span>
                )}
                {f.note && <p className="text-[#2E3442]">{f.note}</p>}
                {Object.entries(f.facts).map(([k, v]) => (
                  <p key={k} className="break-words text-muted-foreground">
                    {k.replaceAll('_', ' ')}: {typeof v === 'string' ? v : JSON.stringify(v)}
                  </p>
                ))}
              </li>
            ))}
          </ul>
        </section>
      ))}
      {!findings.length && (
        <p className="rounded-xl border bg-card px-5 py-4 text-muted-foreground">
          {running ? 'No findings yet.' : 'The research found nothing.'}
        </p>
      )}

      {gaps.length > 0 && (
        <section className="space-y-2">
          <h2 className="font-semibold">Not found ({gaps.length})</h2>
          <ul className="divide-y rounded-xl border bg-card">
            {gaps.map((g, i) => (
              <li key={i} className="flex gap-3 px-5 py-2">
                <span className="w-36 shrink-0 font-mono text-[12px] text-muted-foreground">{g.step}</span>
                <span>{g.reason}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}

const TITLES: Record<(typeof PROFILE_SECTIONS)[number], string> = {
  identity: 'Identity',
  expertise: 'Expertise',
  credentials: 'Credentials',
  cases: 'Cases',
  publications: 'Publications',
  red_flags: 'Red flags',
  not_him: 'Not him (same name, another person)',
  conflicts: 'Conflicts',
  open_leads: 'Open leads',
}

// The profile: one block per section that has items; each item links to the findings it rests on.
export function ProfileView({ p }: { p: Profile }) {
  const filled = PROFILE_SECTIONS.filter((key) => p[key].length)
  return (
    <section className="space-y-4 rounded-xl border bg-card px-5 py-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-[15px] font-semibold">Profile</h2>
        {p.updated_at && (
          <span className="text-[12px] text-muted-foreground">
            version {p.version} · updated{' '}
            {new Date(p.updated_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
          </span>
        )}
      </div>
      {!filled.length ? (
        <p className="text-muted-foreground">The profile fills in as the agent finds things.</p>
      ) : (
        filled.map((key) => (
          <div key={key} className="space-y-1.5">
            <h3
              className={cn(
                'text-[13px] font-semibold',
                key === 'red_flags' && 'text-red-800',
                key === 'not_him' && 'text-muted-foreground'
              )}
            >
              {TITLES[key]}
            </h3>
            <ul className="space-y-1.5">
              {p[key].map((item, i) => (
                <li key={i} className="leading-normal text-[#2E3442]">
                  {item.text}
                  {item.confidence === 'low' && (
                    <span className="ml-2 rounded-full bg-[#FFF1DC] px-2 py-0.5 text-[12px] text-[#7A4E00]">
                      unconfirmed
                    </span>
                  )}
                  {item.refs.map((ref, n) =>
                    p.sources[ref] ? (
                      <a
                        key={ref}
                        href={p.sources[ref].url}
                        target="_blank"
                        rel="noreferrer"
                        title={p.sources[ref].title}
                        className="ml-1 text-[12px] text-primary hover:underline"
                      >
                        [{n + 1}]
                      </a>
                    ) : null
                  )}
                </li>
              ))}
            </ul>
          </div>
        ))
      )}
    </section>
  )
}
