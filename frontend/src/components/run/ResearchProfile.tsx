import { useState } from 'react'
import { LoaderCircle } from 'lucide-react'
import { CARD, Disclaimer, Sources, SubHeading } from '@/components/profile/common'
import { FactGrid } from '@/components/profile/FactGrid'
import { CaseList, LitigationTimeline } from '@/components/profile/Litigation'
import { EntryList, QualificationHighlights } from '@/components/profile/lists'
import { ThingsToKnow } from '@/components/profile/ThingsToKnow'
import type { ResearchProfile as Profile } from '@/lib/research-profile'
import { cn } from '@/lib/utils'

const TABS = [
  ['overview', 'Overview'],
  ['cases', 'Cases and testimony'],
  ['background', 'Background'],
  ['publications', 'Publications'],
  ['career', 'Career'],
  ['sources', 'Sources'],
] as const
type Tab = (typeof TABS)[number][0]

const Empty = ({ running }: { running: boolean }) => (
  <p className="text-sm text-muted-foreground">{running ? 'Still researching…' : 'Nothing found.'}</p>
)

// A research run's profile in the product's design (components/profile), from real backend data.
export function ResearchProfile({ p, running }: { p: Profile; running: boolean }) {
  const [tab, setTab] = useState<Tab>('overview')
  const count = (i: string) => p.cases.filter((c) => c.involvement === i).length
  const tiles = [
    [p.cases.length, 'cases found'],
    [count('Expert witness'), 'as expert witness'],
    [count('Consultant or reviewer'), 'as consultant or reviewer'],
    [p.publications.length, 'publications'],
  ] as const

  return (
    <div>
      <div className="border-b bg-card">
        <div className="mx-auto flex max-w-[1120px] flex-col gap-5 px-4 pt-6">
          <div className="flex flex-wrap items-center gap-4">
            <span className="flex size-14 shrink-0 items-center justify-center rounded-full bg-[#EEF0FA] text-lg font-bold text-primary">
              {p.initials}
            </span>
            <div className="min-w-0 flex-[1_1_320px] space-y-1">
              <h1 className="text-[22px] font-semibold">{p.name}</h1>
              <p className="text-sm text-muted-foreground">
                {[p.title, p.contact.find((c) => c.label === 'Based in')?.value].filter(Boolean).join(' · ')}
              </p>
            </div>
          </div>
          <div role="tablist" aria-label="Profile sections" className="-mb-px flex gap-1 overflow-x-auto">
            {TABS.map(([id, label]) => (
              <button
                key={id}
                type="button"
                role="tab"
                aria-selected={tab === id}
                onClick={() => setTab(id)}
                className={cn(
                  'whitespace-nowrap border-b-2 px-3 py-2.5 text-sm',
                  tab === id
                    ? 'border-primary font-semibold text-foreground'
                    : 'border-transparent font-medium text-muted-foreground hover:text-foreground'
                )}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {running && (
        <div role="status" className="flex items-center gap-2 border-b border-[#DDE1F5] bg-[#EEF0FA] px-6 py-2.5 text-sm text-[#1C2766]">
          <LoaderCircle className="size-4 animate-spin" />
          <span className="font-semibold">Research in progress</span>
          <span>Sections fill in as we find them.</span>
        </div>
      )}

      <div className="mx-auto max-w-[1120px] px-4 py-6 pb-12 @container">
        {tab === 'overview' && (
          <div className="grid gap-4 @3xl:grid-cols-3">
            <section className={cn(CARD, 'space-y-4 @3xl:col-span-2')}>
              <h2 className="text-base font-semibold">Summary</h2>
              {p.qualifications ? <p className="text-[15px] leading-7">{p.qualifications}</p> : <Empty running={running} />}
              <div className="grid grid-cols-2 gap-4 border-t pt-4 @xl:grid-cols-4">
                {tiles.map(([n, label]) => (
                  <div key={label}>
                    <div className="text-xl font-semibold">{n}</div>
                    <div className="text-[13px] text-muted-foreground">{label}</div>
                  </div>
                ))}
              </div>
            </section>
            <ThingsToKnow p={p} />
            <section className={cn(CARD, '@3xl:col-span-2')}>
              <h2 className="mb-3 text-base font-semibold">Key facts</h2>
              {p.highlights.length ? <QualificationHighlights facts={p.highlights} /> : <Empty running={running} />}
            </section>
            <section className={cn(CARD, '@3xl:col-span-3')}>
              <div className="mb-1 flex items-baseline justify-between gap-3">
                <h2 className="text-base font-semibold">Recent cases</h2>
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
              {p.cases.length ? <CaseList cases={p.cases} /> : <Empty running={running} />}
            </section>
          </div>
        )}

        {tab === 'background' && (
          <section className={CARD}>
            <SubHeading note="Court records, federal exclusion lists and publication records">Discipline &amp; background</SubHeading>
            <FactGrid facts={p.risk} />
          </section>
        )}

        {tab === 'publications' && (
          <section className={CARD}>
            <SubHeading note={p.authorStats ?? 'Papers and books that opposing counsel can quote back'}>Publications</SubHeading>
            {p.publications.length ? <EntryList entries={p.publications} /> : <Empty running={running} />}
          </section>
        )}

        {tab === 'career' && (
          <div className="grid gap-4 @3xl:grid-cols-2">
            <section className={CARD}>
              <SubHeading>Roles &amp; affiliations</SubHeading>
              {p.identity.length ? <EntryList entries={p.identity} /> : <Empty running={running} />}
            </section>
            <div className="space-y-4">
              <section className={CARD}>
                <SubHeading>Education, licenses &amp; boards</SubHeading>
                {p.credentials.length ? <EntryList entries={p.credentials} strong /> : <Empty running={running} />}
              </section>
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
            <Sources sources={p.sources} />
            <Disclaimer />
          </section>
        )}
      </div>
    </div>
  )
}
