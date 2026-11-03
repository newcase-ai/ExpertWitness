import { useContext } from 'react'
import { ExpertProfile } from '@/lib/types'
import { cn } from '@/lib/utils'
import { CaseAnalysis } from '@/components/profile/CaseAnalysis'
import { CARD, Disclaimer, Pending, Sources, SubHeading } from '@/components/profile/common'
import { FactGrid } from '@/components/profile/FactGrid'
import { SectionContext } from '@/components/profile/highlights'
import { CaseList, LitigationTimeline, litigationCounts } from '@/components/profile/Litigation'
import { Career, EntryList, Facts, QualificationHighlights } from '@/components/profile/lists'
import { ReadyContext } from '@/components/profile/research'
import { Tab, TABS } from '@/components/profile/tabs'
import { ThingsToKnow } from '@/components/profile/ThingsToKnow'

export function TabPanel({
  p,
  demoId,
  tab,
  onTab,
}: {
  p: ExpertProfile
  demoId: string
  tab: Tab
  onTab: (t: Tab) => void
}) {
  const ready = useContext(ReadyContext)
  const t = TABS.find((x) => x.id === tab)!
  if (!ready(t.section)) return <Pending className={CARD} />

  return (
    <SectionContext.Provider value={t.section}>
      {tab === 'overview' && (
        <div className="grid gap-4 @3xl:grid-cols-3">
          <section className={cn(CARD, 'space-y-4 @3xl:col-span-2')}>
            <h2 className="text-base font-semibold">Summary</h2>
            <p className="text-[15px] leading-7">{p.qualifications}</p>
            {litigationCounts(p).length > 0 && (
              <div className="grid grid-cols-2 gap-4 border-t pt-4 @xl:grid-cols-4">
                {litigationCounts(p).map((c) => (
                  <div key={c.label}>
                    <div className="text-xl font-semibold">{c.n}</div>
                    <div className="text-[13px] text-muted-foreground">{c.label}</div>
                  </div>
                ))}
              </div>
            )}
          </section>
          <ThingsToKnow p={p} />
          <section className={cn(CARD, '@3xl:col-span-2')}>
            <h2 className="mb-3 text-base font-semibold">Key facts</h2>
            <QualificationHighlights facts={p.highlights} />
          </section>
          {ready('litigation') ? (
            <section className={cn(CARD, '@3xl:col-span-3')}>
              <div className="mb-1 flex items-baseline justify-between gap-3">
                <h2 className="text-base font-semibold">Recent testimony</h2>
                <button
                  type="button"
                  onClick={() => onTab('cases')}
                  className="text-sm font-medium text-primary hover:underline"
                >
                  All {p.cases.length} cases
                </button>
              </div>
              <SectionContext.Provider value="litigation">
                <CaseList cases={p.cases.slice(0, 3)} />
              </SectionContext.Provider>
            </section>
          ) : (
            <Pending className={cn(CARD, '@3xl:col-span-3')} />
          )}
        </div>
      )}

      {tab === 'analysis' && <CaseAnalysis p={p} demoId={demoId} />}

      {tab === 'cases' && (
        <div className="space-y-4">
          <section className={CARD}>
            <LitigationTimeline cases={p.cases} />
          </section>
          <section className={CARD}>
            <SubHeading note="Every case where the expert was deposed, testified at trial or was named in a filing">
              Cases &amp; testimony
            </SubHeading>
            <CaseList cases={p.cases} />
          </section>
        </div>
      )}

      {tab === 'records' && (
        <section className={CARD}>
          <SubHeading note="Transcripts, filings and challenges tied to the expert. Open any item for details.">
            Records found
          </SubHeading>
          <FactGrid facts={p.documents} />
        </section>
      )}

      {tab === 'background' && (
        <section className={CARD}>
          <SubHeading note="Licensing boards, court records and background checks">
            Discipline &amp; background
          </SubHeading>
          <FactGrid facts={p.risk} />
        </section>
      )}

      {tab === 'publications' && (
        <div className="space-y-4">
          <section className={CARD}>
            <SubHeading note="Papers, talks and CLE lectures that opposing counsel can quote back">
              Publications, lectures &amp; presentations
            </SubHeading>
            <EntryList entries={p.publications} />
          </section>
          <div className="grid gap-4 @3xl:grid-cols-3">
            <section className={CARD}>
              <SubHeading note="Press articles about or quoting the expert">News</SubHeading>
              <EntryList entries={p.news} />
            </section>
            <section className={CARD}>
              <SubHeading note="Public accounts and what they post">Social media</SubHeading>
              <EntryList entries={p.social} />
            </section>
            <section className={CARD}>
              <SubHeading note="Websites, CVs and directory listings">Online presence</SubHeading>
              <EntryList entries={p.online} />
            </section>
          </div>
        </div>
      )}

      {tab === 'career' && (
        <div className="grid gap-4 @3xl:grid-cols-2">
          <section className={CARD}>
            <SubHeading>Career</SubHeading>
            <Career roles={p.career} />
          </section>
          <div className="space-y-4">
            <section className={CARD}>
              <SubHeading>Education &amp; training</SubHeading>
              <EntryList entries={p.education} strong />
            </section>
            <section className={CARD}>
              <Facts facts={p.professional} />
            </section>
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
    </SectionContext.Provider>
  )
}
