import { createContext, useContext, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import {
  Check,
  ChevronDown,
  ChevronLeft,
  FileText,
  Link2,
  LoaderCircle,
  ChevronRight,
  ChevronUp,
  Highlighter,
  Sparkles,
  TriangleAlert,
  X,
} from 'lucide-react'
import AppHeader from '@/components/AppHeader'
import { caseContexts, DOC_TYPES, ROLE_LABEL } from '@/lib/case-context'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { cn } from '@/lib/utils'
import {
  DEMO_PROFILES,
  Entry,
  ExpertCase,
  ExpertProfile,
  Fact,
  Role,
  Side,
} from '@/lib/expert-profile-mock'
import { Investigation } from '@/lib/expert-investigate-mock'
import ExpertInvestigate from './ExpertInvestigate'

// Research for one of the experts the search returns. The research is mocked: sections fill in
// one step at a time over the canned profile.
export default function ExpertProfilePage() {
  const { expertId: demoId = '' } = useParams()
  const profile = DEMO_PROFILES[demoId]
  if (!profile)
    return (
      <div className="min-h-screen text-sm">
        <AppHeader />
        <main className="mx-auto max-w-[640px] space-y-3 px-4 pt-24">
          <h1 className="text-xl font-semibold">No research data for this expert yet</h1>
          <p className="text-muted-foreground">
            This demo has research for Dr. Margaret Chen and Dr. Robert Alvarez only.
          </p>
          <BackButton className="font-medium text-primary hover:underline" />
        </main>
      </div>
    )
  return <ProfileView key={demoId} demoId={demoId} p={profile} />
}

function BackButton({ className, children }: { className?: string; children?: React.ReactNode }) {
  const navigate = useNavigate()
  return (
    <button type="button" onClick={() => navigate(-1)} className={className}>
      {children}
      Back to results
    </button>
  )
}

// One step per research area, in the order they fill in. Each tab shows once its area is done.
const RESEARCH_STEPS = [
  { section: 'qualifications', label: 'Reading CV and bio' },
  { section: 'litigation', label: 'Searching court records and transcripts' },
  { section: 'risk', label: 'Checking licensing boards, background and media' },
  { section: 'professional', label: 'Building career and education history' },
]
const STEP_MS = 2500
const researched = new Set<string>() // ponytail: in memory, so a reload researches again
const ReadyContext = createContext<(section: string) => boolean>(() => true)

function useResearch(demoId: string) {
  const [done, setDone] = useState(researched.has(demoId) ? RESEARCH_STEPS.length : 0)
  const finished = done >= RESEARCH_STEPS.length
  useEffect(() => {
    if (finished) return void researched.add(demoId)
    const t = setTimeout(() => setDone((d) => d + 1), STEP_MS)
    return () => clearTimeout(t)
  }, [done, finished, demoId])
  const ready = (section: string) => RESEARCH_STEPS.findIndex((s) => s.section === section) < done
  return { done, finished, ready }
}

type Tab =
  | 'overview'
  | 'analysis'
  | 'cases'
  | 'records'
  | 'background'
  | 'publications'
  | 'career'
  | 'sources'
const TABS: { id: Tab; label: string; section: string }[] = [
  { id: 'overview', label: 'Overview', section: 'qualifications' },
  { id: 'analysis', label: 'Case analysis', section: 'professional' }, // once research is done
  { id: 'cases', label: 'Cases and testimony', section: 'litigation' },
  { id: 'records', label: 'Records', section: 'litigation' },
  { id: 'background', label: 'Background', section: 'risk' },
  { id: 'publications', label: 'Publications and media', section: 'risk' },
  { id: 'career', label: 'Career', section: 'professional' },
  { id: 'sources', label: 'Sources', section: 'professional' },
]

// Highlight targets are `${section}:${label}`; two tabs share a section, so look the label up.
function tabOf(p: ExpertProfile, target: string): Tab {
  const [section, label] = [
    target.slice(0, target.indexOf(':')),
    target.slice(target.indexOf(':') + 1),
  ]
  if (section === 'qualifications') return 'overview'
  if (section === 'litigation') return p.cases.some((c) => c.name === label) ? 'cases' : 'records'
  if (section === 'risk')
    return p.risk.some((f) => f.label === label) ? 'background' : 'publications'
  return 'career'
}

// A highlight marks one profile item, addressed as `${section id}:${item label}`.
type Mark = { n: number; note: string }
const HighlightContext = createContext<Map<string, Mark>>(new Map())
const SectionContext = createContext('')
const MARKED =
  'rounded-lg bg-amber-50 ring-1 ring-amber-300 dark:bg-amber-500/10 dark:ring-amber-500/40'

function useMarkOf() {
  const section = useContext(SectionContext)
  const marks = useContext(HighlightContext)
  return (label: string) => marks.get(`${section}:${label}`)
}

const scrollToMark = (n: number) =>
  document
    .querySelector(`[data-highlight="${n}"]`)
    ?.scrollIntoView({ behavior: 'smooth', block: 'center' })

function ProfileView({ demoId, p }: { demoId: string; p: ExpertProfile }) {
  const research = useResearch(demoId)
  const [investigating, setInvestigating] = useState(false)
  const [active, setActive] = useState<Investigation | null>(null)
  const [current, setCurrent] = useState(1)
  const [params] = useSearchParams()
  const [tab, setTab] = useState<Tab>(
    TABS.find((t) => t.id === params.get('tab'))?.id ?? 'overview'
  )
  const context = caseContexts.get(demoId)
  const shortName = `Dr. ${p.name.split(' ').at(-1)}`
  const marks = useMemo(
    () => new Map(active?.highlights.map((h, i) => [h.target, { n: i + 1, note: h.note }])),
    [active]
  )

  // Show the tab holding highlight n, then scroll to it once that tab has rendered.
  const goToMark = (investigation: Investigation, n: number, delay: number) => {
    setCurrent(n)
    setTab(tabOf(p, investigation.highlights[n - 1].target))
    setTimeout(() => scrollToMark(n), delay)
  }
  const highlight = (investigation: Investigation) => {
    setActive(investigation)
    setInvestigating(false)
    goToMark(investigation, 1, 350) // after the sheet slides out
  }
  const step = (n: number) => goToMark(active!, n, 50)
  const marksIn = (t: Tab) => active?.highlights.filter((h) => tabOf(p, h.target) === t).length ?? 0

  return (
    <div className="min-h-screen">
      <AppHeader />
      {!research.finished && (
        <div
          role="status"
          className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-[#DDE1F5] bg-[#EEF0FA] px-6 py-2.5 text-sm text-[#1C2766]"
        >
          <LoaderCircle className="size-4 animate-spin" />
          <span className="font-semibold">Research in progress</span>
          <span>{RESEARCH_STEPS[research.done].label}… Sections fill in as we find them.</span>
          <span className="ml-auto text-[13px]">
            Step {research.done + 1} of {RESEARCH_STEPS.length}
          </span>
        </div>
      )}
      <div className="border-b bg-card">
        <div className="mx-auto flex max-w-[1120px] flex-col gap-5 px-4 pt-5">
          <BackButton className="flex items-center gap-1 self-start text-[13px] text-muted-foreground hover:text-foreground">
            <ChevronLeft className="size-4" />
          </BackButton>
          <div className="flex flex-wrap items-center gap-4">
            <span className="flex size-14 shrink-0 items-center justify-center rounded-full bg-[#EEF0FA] text-lg font-bold text-primary">
              {p.initials}
            </span>
            <div className="min-w-0 flex-[1_1_320px] space-y-1">
              <h1 className="text-[22px] font-semibold">{p.name}</h1>
              <p className="text-sm text-muted-foreground">
                {p.title}
                {basedIn(p) && ` · ${basedIn(p)}`}
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              {context ? (
                <Link
                  to={`/experts/${demoId}/context`}
                  className="flex h-9 items-center gap-2 rounded-lg border border-primary/40 bg-[#F4F5FC] px-3 text-[13px]"
                >
                  <Link2 className="size-4 text-primary" />
                  <span>
                    <strong className="font-semibold">{context.case.name}</strong>
                    <span className="text-muted-foreground"> · {ROLE_LABEL[context.role]}</span>
                  </span>
                  <span className="font-medium text-primary">Edit</span>
                </Link>
              ) : (
                <Button
                  asChild
                  variant="outline"
                  className="gap-1.5 border-dashed border-primary/60 text-primary"
                >
                  <Link to={`/experts/${demoId}/context`}>
                    <Link2 className="size-4" />
                    Add case context
                  </Link>
                </Button>
              )}
              {research.finished && (
                <Button onClick={() => setInvestigating(true)} className="gap-2">
                  <Sparkles className="size-4" />
                  Ask about this expert
                </Button>
              )}
            </div>
          </div>
          <div
            role="tablist"
            aria-label="Profile sections"
            className="-mb-px flex gap-1 overflow-x-auto"
          >
            {TABS.map((t) => {
              const ready = research.ready(t.section)
              const n = marksIn(t.id)
              return (
                <button
                  key={t.id}
                  type="button"
                  role="tab"
                  aria-selected={tab === t.id}
                  onClick={() => setTab(t.id)}
                  className={cn(
                    'flex items-center gap-1.5 whitespace-nowrap border-b-2 px-3 py-2.5 text-sm',
                    tab === t.id
                      ? 'border-primary font-semibold text-foreground'
                      : 'border-transparent font-medium text-muted-foreground hover:text-foreground'
                  )}
                >
                  {!ready && <LoaderCircle className="size-3.5 animate-spin text-primary" />}
                  {t.label}
                  {n > 0 && (
                    <span className="flex size-[18px] items-center justify-center rounded-full bg-amber-500 text-[11px] font-bold text-white">
                      {n}
                    </span>
                  )}
                </button>
              )
            })}
          </div>
        </div>
      </div>
      <div className="mx-auto max-w-[1120px] px-4 py-6">
        <div className="w-full pb-12 @container">
          {active && (
            <div
              role="status"
              className="sticky top-2 z-30 mb-4 flex items-center gap-3 rounded-xl border border-amber-300 bg-amber-50/95 py-2 pl-4 pr-2 text-amber-950 shadow-md backdrop-blur dark:border-amber-500/40 dark:bg-amber-950/90 dark:text-amber-50"
            >
              <Highlighter className="size-4 shrink-0 text-amber-700 dark:text-amber-400" />
              <p className="min-w-0 flex-1 truncate text-[13px]">
                <strong>
                  {active.highlights.length} highlight{active.highlights.length === 1 ? '' : 's'}
                </strong>{' '}
                for “{active.question}”
              </p>
              <div className="flex items-center text-[13px]">
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-8 hover:bg-amber-100 dark:hover:bg-amber-900"
                  aria-label="Previous highlight"
                  disabled={current <= 1}
                  onClick={() => step(current - 1)}
                >
                  <ChevronUp className="size-4" />
                </Button>
                <span className="min-w-12 text-center tabular-nums">
                  {current} of {active.highlights.length}
                </span>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-8 hover:bg-amber-100 dark:hover:bg-amber-900"
                  aria-label="Next highlight"
                  disabled={current >= active.highlights.length}
                  onClick={() => step(current + 1)}
                >
                  <ChevronDown className="size-4" />
                </Button>
              </div>
              <Button
                variant="outline"
                size="sm"
                className="h-8 border-amber-300 bg-transparent hover:bg-amber-100 dark:border-amber-500/40 dark:hover:bg-amber-900"
                onClick={() => setInvestigating(true)}
              >
                Back to answer
              </Button>
              <Button
                variant="ghost"
                size="icon"
                className="size-8 hover:bg-amber-100 dark:hover:bg-amber-900"
                aria-label="Clear highlights"
                onClick={() => setActive(null)}
              >
                <X className="size-4" />
              </Button>
            </div>
          )}
          <HighlightContext.Provider value={marks}>
            <ReadyContext.Provider value={research.ready}>
              <TabPanel p={p} demoId={demoId} tab={tab} onTab={setTab} />
            </ReadyContext.Provider>
          </HighlightContext.Provider>
        </div>
        {research.finished && (
          <ExpertInvestigate
            demoId={demoId}
            name={shortName}
            open={investigating}
            onOpenChange={setInvestigating}
            onHighlight={highlight}
          />
        )}
      </div>
    </div>
  )
}

function MarkNum({ mark }: { mark?: Mark }) {
  if (!mark) return null
  return (
    <span className="mr-2 inline-flex size-5 items-center justify-center rounded-full bg-amber-500 align-[1px] text-[11px] font-bold not-italic text-white">
      {mark.n}
    </span>
  )
}

function MarkNote({ mark }: { mark?: Mark }) {
  if (!mark) return null
  return (
    <p className="mt-1.5 text-[13px] font-normal not-italic">
      <mark className="rounded bg-amber-200/70 px-1 py-0.5 text-amber-950 dark:bg-amber-500/30 dark:text-amber-50">
        {mark.note}
      </mark>
    </p>
  )
}

const basedIn = (p: ExpertProfile) => p.contact.find((c) => c.label === 'Based in')?.value

const CARD = 'rounded-xl border bg-card p-5'

function TabPanel({
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
            <div className="grid grid-cols-2 gap-4 border-t pt-4 @xl:grid-cols-4">
              {litigationCounts(p).map((c) => (
                <div key={c.label}>
                  <div className="text-xl font-semibold">{c.n}</div>
                  <div className="text-[13px] text-muted-foreground">{c.label}</div>
                </div>
              ))}
            </div>
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

// Before the user adds context: what they get and what to add. After: what they added.
// ponytail: the analysis itself (re-analysis progress and insights) is the next step to build.
function CaseAnalysis({ p, demoId }: { p: ExpertProfile; demoId: string }) {
  const context = caseContexts.get(demoId)
  const shortName = `Dr. ${p.name.split(' ').at(-1)}`
  const contextLink = (
    <Button asChild className="self-start">
      <Link to={`/experts/${demoId}/context`}>
        {context ? 'Edit case context' : 'Add case context'}
      </Link>
    </Button>
  )

  if (context)
    return (
      <section className={cn(CARD, 'space-y-4 text-sm')}>
        <div className="space-y-1">
          <h2 className="text-base font-semibold">Case context</h2>
          <p className="text-muted-foreground">
            {context.case.name} · {ROLE_LABEL[context.role]}
          </p>
        </div>
        {context.testimony && <p className="leading-relaxed">{context.testimony}</p>}
        <ul className="divide-y rounded-lg border">
          {context.docs.map((d, i) => (
            <li key={i} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3.5 py-2.5">
              <FileText className="size-4 text-primary" />
              <span className="min-w-0 flex-1 truncate font-medium">{d.name}</span>
              <span className="text-[13px] text-muted-foreground">
                {DOC_TYPES.find((t) => t.id === d.type)?.label ?? 'Other'}
              </span>
            </li>
          ))}
        </ul>
        {context.notes && <p className="text-muted-foreground">{context.notes}</p>}
        <p className="rounded-lg bg-[#F4F5FC] px-3.5 py-3 text-[#1C2766]">
          The analysis of these documents against {shortName}'s record will appear here.
        </p>
        {contextLink}
      </section>
    )

  return (
    <div className="space-y-4 text-sm">
      <section className={cn(CARD, 'flex flex-col gap-5 p-7')}>
        <div className="max-w-[640px] space-y-1.5">
          <h2 className="font-serif text-2xl font-semibold">
            Turn this profile into a case strategy
          </h2>
          <p className="text-[15px] leading-relaxed">
            Share what you have on {shortName} in your case. We'll read it against everything in the
            public record and show where the expert is vulnerable, or needs preparing.
          </p>
        </div>
        <div className="grid gap-3 @3xl:grid-cols-2">
          <div className="space-y-1.5 rounded-lg bg-[#F7F8FA] p-4">
            <div className="font-semibold">If they're the other side's expert</div>
            <p className="leading-normal">
              Impeachment points, contradictions with prior testimony, Daubert grounds, bias and
              conflicts, cross-examination questions.
            </p>
          </div>
          <div className="space-y-1.5 rounded-lg bg-[#F7F8FA] p-4">
            <div className="font-semibold">If they're your expert</div>
            <p className="leading-normal">
              Weak spots opposing counsel will find, likely cross questions with suggested answers,
              gaps in the report, a prep checklist.
            </p>
          </div>
        </div>
        {contextLink}
      </section>
      <section className="space-y-3">
        <div>
          <h2 className="text-base font-semibold">What you can add</h2>
          <p className="text-muted-foreground">
            Anything helps. The highlighted ones give the most.
          </p>
        </div>
        <div className="grid gap-2.5 @xl:grid-cols-2 @4xl:grid-cols-3">
          {DOC_TYPES.map((t) => (
            <div
              key={t.id}
              className={cn(
                'space-y-1 rounded-lg border bg-card px-4 py-3.5',
                'key' in t && 'border-[#C9CEEA]'
              )}
            >
              <div className="font-semibold">{t.label}</div>
              <div className="text-[13px] leading-normal text-muted-foreground">{t.hint}</div>
            </div>
          ))}
        </div>
      </section>
    </div>
  )
}

// Flagged records first, then a one-line all-clear for the background checks that found nothing.
function ThingsToKnow({ p }: { p: ExpertProfile }) {
  const ready = useContext(ReadyContext)
  if (!ready('litigation') || !ready('risk'))
    return <Pending className={cn(CARD, '@3xl:row-span-2')} />
  const flagged = [...p.documents, ...p.risk].filter((f) => f.flag)
  const clear = p.risk.filter((f) => f.value.startsWith('None'))
  return (
    <section className={cn(CARD, 'space-y-3 @3xl:row-span-2')}>
      <h2 className="text-base font-semibold">Things to know</h2>
      {flagged.map((f) => (
        <div key={f.label} className="space-y-1 rounded-lg bg-[#FFF8EC] p-3">
          <div className="font-semibold text-[#7A4E00]">{f.label}</div>
          <div className="text-sm leading-normal">{f.value}</div>
        </div>
      ))}
      {clear.length > 0 && (
        <div className="space-y-1 rounded-lg bg-[#F2FAF6] p-3">
          <div className="font-semibold text-[#1D5B40]">Clean record</div>
          <div className="text-sm leading-normal">
            No {clear.map((f) => f.label.toLowerCase()).join(', ')} found.
          </div>
        </div>
      )}
    </section>
  )
}

function Pending({ className }: { className?: string }) {
  return (
    <div aria-busy="true" className={cn('animate-pulse space-y-2.5', className)}>
      <div className="h-3 w-2/3 rounded bg-muted" />
      <div className="h-3 w-1/2 rounded bg-muted" />
      <div className="h-3 w-3/5 rounded bg-muted" />
    </div>
  )
}

function QualificationHighlights({ facts }: { facts: Fact[] }) {
  const markOf = useMarkOf()
  return (
    <dl className="divide-y self-start rounded-lg bg-muted/50 px-4">
      {facts.map((h) => {
        const mark = markOf(h.label)
        return (
          <div
            key={h.label}
            data-highlight={mark?.n}
            className={cn('scroll-mt-24 py-3', mark && cn(MARKED, '-mx-2 px-2'))}
          >
            <dt className="text-xs text-muted-foreground">
              <MarkNum mark={mark} />
              {h.label}
            </dt>
            <dd className="mt-0.5 text-sm font-medium leading-snug">{h.value}</dd>
            <MarkNote mark={mark} />
          </div>
        )
      })}
    </dl>
  )
}

function litigationCounts(p: ExpertProfile) {
  const count = (involvement: string) => p.cases.filter((c) => c.involvement === involvement).length
  const plural = (n: number, one: string, many: string) => (n === 1 ? one : many)
  const depositions = count('Deposition')
  const trials = count('Trial testimony')
  return [
    { n: String(p.cases.length), label: plural(p.cases.length, 'case found', 'cases found') },
    { n: String(depositions), label: plural(depositions, 'deposition', 'depositions') },
    { n: String(trials), label: plural(trials, 'trial testimony', 'trial testimonies') },
    {
      n: String(p.daubertChallenges),
      label: plural(p.daubertChallenges, 'Daubert challenge', 'Daubert challenges'),
    },
  ]
}

// ponytail: one dot per case on a year axis; two cases in the same year and side overlap.
// Stack them if real data ever has that.
function LitigationTimeline({ cases }: { cases: ExpertCase[] }) {
  const years = cases.map((c) => c.year)
  const min = Math.min(...years) - 1
  const max = Math.max(...years) + 1
  const left = (year: number) => `${((year - min) / (max - min)) * 100}%`
  const ticks = Array.from({ length: max - min + 1 }, (_, i) => min + i).filter((y) => y % 2 === 0)

  const row = (rowCases: ExpertCase[]) => (
    <div className="relative h-8">
      {rowCases.map((c) => {
        const label = `${c.name} (${c.year}) · ${c.involvement}`
        return (
          <span
            key={c.name}
            role="img"
            aria-label={label}
            title={label}
            style={{ left: left(c.year) }}
            className={cn(
              'absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2',
              c.involvement === 'Trial testimony' ? 'rounded-[2px]' : 'rounded-full',
              c.side === 'plaintiff' ? 'bg-primary' : 'bg-orange-600 dark:bg-orange-400'
            )}
          />
        )
      })}
    </div>
  )

  return (
    <div className="space-y-1.5">
      <div className="text-xs font-semibold text-primary">Plaintiff</div>
      {row(cases.filter((c) => c.side === 'plaintiff'))}
      <div className="relative h-6 border-t border-muted-foreground/60">
        {ticks.map((y) => (
          <span
            key={y}
            style={{ left: left(y) }}
            className="absolute top-1 -translate-x-1/2 text-[11px] tabular-nums text-muted-foreground"
          >
            {y}
          </span>
        ))}
      </div>
      {row(cases.filter((c) => c.side !== 'plaintiff'))}
      <div className="text-xs font-semibold text-orange-700 dark:text-orange-400">
        Defense or unknown
      </div>
      <div className="mt-3 flex gap-5 border-t pt-3 text-xs text-muted-foreground">
        <span className="flex items-center gap-1.5">
          <span className="size-2.5 rounded-full bg-muted-foreground" />
          Deposition or other
        </span>
        <span className="flex items-center gap-1.5">
          <span className="size-2.5 rounded-[2px] bg-muted-foreground" />
          Trial testimony
        </span>
      </div>
    </div>
  )
}

const SIDE_LABEL: Record<Side, string> = {
  plaintiff: 'For plaintiff',
  defense: 'For defense',
  unknown: 'Side unknown',
}

function CaseList({ cases }: { cases: ExpertCase[] }) {
  const markOf = useMarkOf()
  return (
    <ol>
      {cases.map((c) => {
        const mark = markOf(c.name)
        return (
          <li
            key={c.name}
            data-highlight={mark?.n}
            className={cn(
              'grid scroll-mt-24 grid-cols-[3.5rem_minmax(0,1fr)_auto] gap-4 border-b py-4 last:border-b-0',
              mark && cn(MARKED, '-mx-3 my-1 border-b-0 px-3')
            )}
          >
            <span className="pt-0.5 text-sm tabular-nums text-muted-foreground">{c.year}</span>
            <div>
              <div className="text-[15px] font-semibold italic">
                <MarkNum mark={mark} />
                {c.name}
              </div>
              <p className="mt-1 text-sm leading-relaxed text-muted-foreground">
                {c.description} {c.court}
              </p>
              <MarkNote mark={mark} />
            </div>
            <div className="text-right text-sm">
              <div className="font-medium">{SIDE_LABEL[c.side]}</div>
              <div className="text-muted-foreground">{c.involvement}</div>
            </div>
          </li>
        )
      })}
    </ol>
  )
}

function FactGrid({ facts }: { facts: Fact[] }) {
  const [open, setOpen] = useState<Fact | null>(null)
  const markOf = useMarkOf()
  return (
    <>
      <div className="grid gap-2 @3xl:grid-cols-2">
        {facts.map((f) => {
          const mark = markOf(f.label)
          return (
            <button
              key={f.label}
              type="button"
              onClick={() => setOpen(f)}
              data-highlight={mark?.n}
              className={cn(
                'group flex scroll-mt-24 flex-wrap items-center justify-between gap-x-3 rounded-lg border px-3.5 py-2.5 text-left text-sm transition-colors hover:border-primary/40 hover:bg-muted/50',
                mark && cn(MARKED, 'border-transparent hover:bg-amber-100')
              )}
            >
              <span>
                <MarkNum mark={mark} />
                {f.label}
              </span>
              <span className="flex items-center gap-2">
                <FactValue fact={f} />
                <ChevronRight className="size-4 text-muted-foreground/60 group-hover:text-foreground" />
              </span>
              {mark && (
                <span className="basis-full">
                  <MarkNote mark={mark} />
                </span>
              )}
            </button>
          )
        })}
      </div>
      <Dialog open={!!open} onOpenChange={(o) => !o && setOpen(null)}>
        <DialogContent className="max-w-lg">
          {open && (
            <>
              <DialogHeader>
                <DialogTitle>{open.label}</DialogTitle>
                <DialogDescription asChild>
                  <div>
                    <FactValue fact={open} />
                  </div>
                </DialogDescription>
              </DialogHeader>
              {open.details?.length ? (
                <ul className="max-h-[60vh] divide-y overflow-y-auto">
                  {open.details.map((d) => (
                    <li key={d.title} className="py-3 first:pt-0 last:pb-0">
                      <div className="text-sm font-medium">{d.title}</div>
                      <p className="mt-1 text-[13px] leading-relaxed text-muted-foreground">
                        {d.detail}
                      </p>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-muted-foreground">
                  Nothing found in the public and proprietary sources searched for this profile.
                </p>
              )}
            </>
          )}
        </DialogContent>
      </Dialog>
    </>
  )
}

function FactValue({ fact: f }: { fact: Fact }) {
  const clear = f.value.startsWith('None') || f.value.startsWith('No ')
  return (
    <span
      className={cn(
        'flex shrink-0 items-center gap-1.5 text-[13px]',
        f.flag
          ? 'font-medium text-amber-600'
          : clear
            ? 'text-green-700 dark:text-green-500'
            : 'text-muted-foreground'
      )}
    >
      {f.flag ? <TriangleAlert className="size-3.5" /> : clear && <Check className="size-3.5" />}
      {f.value}
    </span>
  )
}

function Facts({ facts }: { facts: Fact[] }) {
  const markOf = useMarkOf()
  return (
    <dl className="space-y-4">
      {facts.map((f) => {
        const mark = markOf(f.label)
        return (
          <div
            key={f.label}
            data-highlight={mark?.n}
            className={cn('scroll-mt-24', mark && cn(MARKED, '-mx-3 px-3 py-2.5'))}
          >
            <dt className="mb-1 text-xs text-muted-foreground">
              <MarkNum mark={mark} />
              {f.label}
            </dt>
            <dd className="text-sm leading-snug">{f.value}</dd>
            <MarkNote mark={mark} />
          </div>
        )
      })}
    </dl>
  )
}

function EntryList({ entries, strong }: { entries: Entry[]; strong?: boolean }) {
  const markOf = useMarkOf()
  return (
    <ul className="space-y-3">
      {entries.map((e) => {
        const mark = markOf(e.title)
        return (
          <li
            key={e.title}
            data-highlight={mark?.n}
            className={cn('scroll-mt-24', mark && cn(MARKED, '-mx-3 px-3 py-2.5'))}
          >
            <div className={cn('text-sm', strong ? 'font-semibold' : 'font-medium')}>
              <MarkNum mark={mark} />
              {e.title}
            </div>
            <div className="text-[13px] text-muted-foreground">{e.detail}</div>
            <MarkNote mark={mark} />
          </li>
        )
      })}
    </ul>
  )
}

function Career({ roles }: { roles: Role[] }) {
  const markOf = useMarkOf()
  return (
    <ol className="ml-1 border-l">
      {roles.map((r) => {
        const mark = markOf(r.title)
        return (
          <li key={r.title + r.org} className="relative pb-5 pl-5 last:pb-0">
            <span
              className={cn(
                'absolute left-[-5.5px] top-1 size-2.5 rounded-full border-2 border-primary',
                r.current ? 'bg-primary' : 'bg-background'
              )}
            />
            <div
              data-highlight={mark?.n}
              className={cn('scroll-mt-24', mark && cn(MARKED, '-mx-2 -my-1.5 px-2 py-1.5'))}
            >
              <div className="text-sm font-semibold">
                <MarkNum mark={mark} />
                {r.title}{' '}
                {r.current && <span className="text-xs font-normal text-primary">Current</span>}
              </div>
              <div className="text-[13px] text-muted-foreground">
                {r.org}
                {r.dates && ` · ${r.dates}`}
              </div>
              <MarkNote mark={mark} />
            </div>
          </li>
        )
      })}
    </ol>
  )
}

function Sources({ sources }: { sources: string[] }) {
  return (
    <ul className="space-y-1.5 text-[13px]">
      {sources.map((s) => (
        <li key={s} className="underline decoration-muted-foreground/40 underline-offset-4">
          {s}
        </li>
      ))}
    </ul>
  )
}

function Disclaimer({ className }: { className?: string }) {
  return (
    <p className={cn('text-xs leading-relaxed text-muted-foreground', className)}>
      Compiled automatically from public and proprietary sources. Verify before relying on it.
    </p>
  )
}

function SubHeading({ note, children }: { note?: string; children: React.ReactNode }) {
  return (
    <div className="mb-3">
      <h3 className="text-[15px] font-semibold">{children}</h3>
      {note && <p className="mt-0.5 text-[13px] text-muted-foreground">{note}</p>}
    </div>
  )
}
