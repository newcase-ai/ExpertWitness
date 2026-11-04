import { useState, type ReactNode } from 'react'
import { CARD, Disclaimer, Pending, SourceLinks, Sources, SubHeading } from '@/components/profile/common'
import { FactGrid } from '@/components/profile/FactGrid'
import { CaseList, LitigationTimeline } from '@/components/profile/Litigation'
import { Career, EntryList, Facts, QualificationHighlights } from '@/components/profile/lists'
import type { SectionStatus } from '@/lib/api'
import { RED_FLAGS } from '@/lib/expert-profile-view'
import type { ResearchProfile as Profile } from '@/lib/research-profile'
import type { Fact } from '@/lib/types'
import { LoaderCircle, TriangleAlert } from 'lucide-react'
import { cn } from '@/lib/utils'

const TABS = [
  ['overview', 'Overview'],
  ['flags', 'Red flags'], // shown only when some were found
  ['cases', 'Cases and testimony'],
  ['background', 'Background'],
  ['publications', 'Publications'],
  ['career', 'Career'],
  ['sources', 'Sources'],
] as const
type Tab = (typeof TABS)[number][0]

// A section with no data yet: a skeleton while the run is going, else "Nothing found", or "Not
// checked" when no research step covered it (structured profile only).
const Empty = ({ running, status }: { running: boolean; status?: SectionStatus }) =>
  running ? (
    <Pending />
  ) : (
    <p className="text-sm text-muted-foreground">
      {status === 'not_checked' ? 'Not checked in this research.' : 'Nothing found.'}
    </p>
  )

// A research run's profile in the product's design (components/profile), from real backend data.
export function ResearchProfile({
  p,
  running,
  now,
  actions,
  photo,
}: {
  p: Profile
  running: boolean
  now?: string // the latest tool call's label, while running
  actions?: ReactNode
  photo?: string // image URL; the initials are shown when it is missing or fails to load
}) {
  const [tab, setTab] = useState<Tab>('overview')
  const [noPhoto, setNoPhoto] = useState(false)
  const count = (i: string) => p.cases.filter((c) => c.involvement === i).length
  const tiles = (
    [
      [p.cases.length, 'cases found'],
      [count('Expert witness'), 'as expert witness'],
      [count('Consultant or reviewer'), 'as consultant or reviewer'],
      [p.publications.length, 'publications'],
    ] as [number, string][]
  ).filter(([n]) => n > 0) // a count of 0 is not shown
  const redFlags = p.risk.find((f) => f.label === RED_FLAGS && f.flag)

  return (
    <div>
      <div className="mx-auto flex max-w-[1120px] flex-col gap-6 px-4 pt-10">
        <div className="flex flex-wrap items-center gap-4">
          {photo && !noPhoto ? (
            <img src={photo} alt={p.name} onError={() => setNoPhoto(true)}
              className="size-14 shrink-0 rounded-full object-cover" />
          ) : (
            <span className="flex size-14 shrink-0 items-center justify-center rounded-full bg-primary/10 font-serif text-xl font-semibold text-primary">
              {p.initials}
            </span>
          )}
          <div className="min-w-0 flex-[1_1_320px]">
            <p className="text-[11px] font-bold uppercase tracking-[0.1em] text-primary">Expert profile</p>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <h1 className="font-serif text-[34px] font-semibold leading-tight tracking-tight">{p.name}</h1>
            </div>
            <p className="text-sm text-muted-foreground">
              {[p.title, p.contact.find((c) => c.label === 'Based in')?.value].filter(Boolean).join(' · ')}
            </p>
            {p.tags.length > 0 && (
              <ul className="mt-2 flex flex-wrap gap-1.5">
                {p.tags.map((t) => (
                  <li key={t} className="rounded-full bg-primary/10 px-2.5 py-0.5 text-[12px] font-medium text-primary">
                    {t}
                  </li>
                ))}
              </ul>
            )}
          </div>
          {actions}
        </div>
        {running && <ResearchingPanel now={now} />}
        <div className="overflow-x-auto">
          <div role="tablist" aria-label="Profile sections" className="inline-flex gap-0.5 rounded-[10px] border bg-card/80 p-[3px] backdrop-blur-md">
            {TABS.filter(([id]) => id !== 'flags' || redFlags).map(([id, label]) => (
              <button
                key={id}
                type="button"
                role="tab"
                aria-selected={tab === id}
                onClick={() => setTab(id)}
                className={cn(
                  'flex items-center gap-1.5 whitespace-nowrap rounded-lg px-3.5 py-1.5 text-[13px] font-medium',
                  tab === id
                    ? 'bg-primary text-primary-foreground'
                    : id === 'flags'
                      ? 'text-amber-700 hover:text-amber-900 dark:text-amber-400'
                      : 'text-muted-foreground hover:text-foreground'
                )}
              >
                {id === 'flags' && <TriangleAlert className="size-3.5" />}
                {label}
                {id === 'flags' && (
                  <span className="rounded-full bg-amber-500 px-1.5 text-[11px] font-bold leading-4 text-white">
                    {redFlags?.details?.length}
                  </span>
                )}
              </button>
            ))}
          </div>
        </div>
      </div>


      <div className="mx-auto max-w-[1120px] px-4 py-6 pb-12 @container">
        {tab === 'overview' && (
          <div className="grid gap-4 @3xl:grid-cols-3">
            <section className={cn(CARD, 'space-y-4 @3xl:col-span-2')}>
              <h2 className="font-serif text-lg font-semibold">Summary</h2>
              {p.qualifications ? (
                <div>
                  <p className="text-[15px] leading-7">{p.qualifications}</p>
                  <SourceLinks urls={p.qualificationsLinks} />
                </div>
              ) : (
                <Empty running={running} />
              )}
              {tiles.length > 0 && (
                <div className="grid grid-cols-2 gap-4 border-t pt-4 @xl:grid-cols-4">
                  {tiles.map(([n, label]) => (
                    <div key={label}>
                      <div className="font-serif text-2xl font-semibold">{n}</div>
                      <div className="text-[13px] text-muted-foreground">{label}</div>
                    </div>
                  ))}
                </div>
              )}
            </section>
            <section className={cn(CARD, '@3xl:row-span-2')}>
              <h2 className="mb-3 font-serif text-lg font-semibold">Contact</h2>
              {p.contact.length ? (
                <dl className="space-y-3">
                  {p.contact.map((c) => (
                    <div key={c.label}>
                      <dt className="text-xs text-muted-foreground">{c.label}</dt>
                      <dd className="break-words text-sm">{c.value}</dd>
                      <SourceLinks urls={c.links} />
                    </div>
                  ))}
                </dl>
              ) : (
                <Empty running={running} />
              )}
            </section>
            <section className={cn(CARD, '@3xl:col-span-2')}>
              <h2 className="mb-3 font-serif text-lg font-semibold">Key facts</h2>
              {p.highlights.length ? <QualificationHighlights facts={p.highlights} /> : <Empty running={running} />}
            </section>
            <section className={cn(CARD, '@3xl:col-span-3')}>
              <div className="mb-1 flex items-baseline justify-between gap-3">
                <h2 className="font-serif text-lg font-semibold">Recent cases</h2>
                {p.cases.length > 3 && (
                  <button type="button" onClick={() => setTab('cases')} className="text-sm font-medium text-primary hover:underline">
                    All {p.cases.length} cases
                  </button>
                )}
              </div>
              {p.cases.length ? <CaseList cases={p.cases.slice(0, 3)} /> : <Empty running={running} />}
            </section>
          </div>
        )}

        {tab === 'flags' && redFlags && (
          <section className={CARD}>
            <SubHeading note="Weak spots in this expert's record, with their sources">{RED_FLAGS}</SubHeading>
            <FlagList fact={redFlags} />
          </section>
        )}

        {tab === 'cases' && (
          <div className="space-y-4">
            {p.cases.length > 0 && (
              <section className={CARD}>
                <LitigationTimeline cases={p.cases} />
              </section>
            )}
            <section className={CARD}>
              <SubHeading note="Court cases that name the expert. Which side hired the expert is not stated in these records.">
                Cases &amp; testimony
              </SubHeading>
              {p.cases.length ? <CaseList cases={p.cases} /> : <Empty running={running} status={p.status?.cases} />}
            </section>
            {p.documents.length > 0 && (
              <section className={CARD}>
                <SubHeading note="Court filings that name the expert, by case. Open a case for its filings.">
                  Court filings
                </SubHeading>
                <FactGrid facts={p.documents} />
              </section>
            )}
          </div>
        )}

        {tab === 'background' && (
          <section className={CARD}>
            <SubHeading note="Court records, federal exclusion lists and publication records">Discipline &amp; background</SubHeading>
            {p.risk.length ? <FactGrid facts={p.risk} /> : <Empty running={running} />}
          </section>
        )}

        {tab === 'publications' && (
          <div className="grid gap-4 @3xl:grid-cols-3">
            <section className={cn(CARD, '@3xl:col-span-2')}>
              <SubHeading note={p.authorStats ?? 'Papers and books that opposing counsel can quote back'}>Publications</SubHeading>
              {p.publications.length ? <EntryList entries={p.publications} /> : <Empty running={running} status={p.status?.publications} />}
            </section>
            {p.status && (
              <section className={CARD}>
                <SubHeading note="Websites, CVs and directory listings the research read">Online presence</SubHeading>
                {p.online.length ? <EntryList entries={p.online} /> : <Empty running={running} status={p.status.online} />}
              </section>
            )}
          </div>
        )}

        {tab === 'career' && (
          <div className="grid gap-4 @3xl:grid-cols-2">
            <section className={CARD}>
              <SubHeading>{p.career.length ? 'Career' : 'Roles & affiliations'}</SubHeading>
              {p.career.length ? (
                <Career roles={p.career} />
              ) : p.identity.length ? (
                <EntryList entries={p.identity} />
              ) : (
                <Empty running={running} status={p.status?.career} />
              )}
            </section>
            <div className="space-y-4">
              <section className={CARD}>
                <SubHeading>Education, licenses &amp; boards</SubHeading>
                {p.credentials.length ? <EntryList entries={p.credentials} strong /> : <Empty running={running} status={p.status?.credentials} />}
              </section>
              {p.professional.length > 0 && (
                <section className={CARD}>
                  <Facts facts={p.professional} />
                </section>
              )}
              {p.notHim.length > 0 && (
                <section className={CARD}>
                  <SubHeading note="People with the same name who are not this expert">Ruled out</SubHeading>
                  <EntryList entries={p.notHim} />
                </section>
              )}
            </div>
          </div>
        )}

        {tab === 'sources' && (
          <section className={cn(CARD, 'space-y-4')}>
            <SubHeading note="Where this profile's information comes from">Sources</SubHeading>
            {p.sourceLinks ? <EntryList entries={p.sourceLinks} /> : <Sources sources={p.sources} />}
            <Disclaimer />
          </section>
        )}
      </div>
    </div>
  )
}

// The red flags tab: each one with the sites it came from, linked to the first.
function FlagList({ fact }: { fact: Fact }) {
  return (
    <ol className="divide-y">
      {(fact.details ?? []).map((d, i) => (
        <li key={d.title} className="flex gap-3 py-3 first:pt-0 last:pb-0">
          <span className="w-5 shrink-0 text-right text-sm font-semibold text-amber-700 dark:text-amber-400">{i + 1}.</span>
          <div className="min-w-0">
            <div className="text-sm font-medium">
              {d.url ? (
                <a href={d.url} target="_blank" rel="noreferrer" className="hover:text-primary hover:underline">
                  {d.title}
                </a>
              ) : (
                d.title
              )}
            </div>
            {d.detail && <p className="mt-0.5 text-[13px] text-muted-foreground">{d.detail}</p>}
            {d.why && (
              <div className="mt-2 space-y-2 rounded-md bg-amber-50 px-3 py-2.5 text-[13px] leading-relaxed dark:bg-amber-500/10">
                {d.why.split('\n\n').map((para) => {
                  const [, label, text] = para.match(/^(What this is|Why it matters): ([\s\S]*)$/) ?? ['', '', para]
                  return (
                    <p key={para}>
                      {label && <span className="font-semibold text-amber-800 dark:text-amber-300">{label}: </span>}
                      {text}
                    </p>
                  )
                })}
              </div>
            )}
          </div>
        </li>
      ))}
    </ol>
  )
}

// While the run is going: a spinner and what the agent is doing now.
function ResearchingPanel({ now }: { now?: string }) {
  return (
    <div
      role="status"
      className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-xl border border-primary/30 bg-card px-5 py-4 shadow-[0_10px_30px_-18px_rgb(20_30_80/0.35)]"
    >
      <span className="grid size-10 shrink-0 place-items-center rounded-full bg-primary/10 text-primary">
        <LoaderCircle className="size-5 animate-spin motion-reduce:animate-none" />
      </span>
      <div className="min-w-0 flex-[1_1_240px]">
        <p className="text-[15px] font-semibold">Researching this expert…</p>
        <p className="truncate text-[13px] text-muted-foreground">
          {now ? `Now: ${now}` : 'Starting up'} · Sections fill in as we find them.
        </p>
      </div>
    </div>
  )
}
