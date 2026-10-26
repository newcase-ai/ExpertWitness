import { createContext, useContext, useEffect, useMemo, useState } from 'react'
import { Link, Navigate, useParams } from 'react-router-dom'
import {
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Highlighter,
  Sparkles,
  TriangleAlert,
  X,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
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

// Finished research for one of the mock experts the search returns.
export default function ExpertProfilePage() {
  const { expertId: demoId = '' } = useParams()
  const profile = DEMO_PROFILES[demoId]
  if (!profile) return <Navigate to="/" replace />
  return <ProfileView key={demoId} demoId={demoId} p={profile} />
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
  const [investigating, setInvestigating] = useState(false)
  const [active, setActive] = useState<Investigation | null>(null)
  const [current, setCurrent] = useState(1)
  const marks = useMemo(
    () => new Map(active?.highlights.map((h, i) => [h.target, { n: i + 1, note: h.note }])),
    [active]
  )

  const highlight = (investigation: Investigation) => {
    setActive(investigation)
    setCurrent(1)
    setInvestigating(false)
    // After the sheet slides out and any collapsed section opens.
    setTimeout(() => scrollToMark(1), 350)
  }
  const step = (n: number) => {
    setCurrent(n)
    scrollToMark(n)
  }

  return (
    <div className="mx-auto max-w-screen-2xl px-4 py-6 md:px-8">
      <header className="flex items-center justify-between gap-4">
        <div className="space-y-1">
          <Link
            to="/"
            className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
          >
            <ChevronLeft className="size-4" /> New search
          </Link>
          <h1 className="text-2xl font-semibold tracking-tight">Expert research</h1>
        </div>
        <Button onClick={() => setInvestigating(true)} className="gap-2">
          <Sparkles className="size-4" />
          Expert intel
        </Button>
      </header>
      <div className="mt-6 w-full pb-12 @container">
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
          <Dossier p={p} />
        </HighlightContext.Provider>
      </div>
      <ExpertInvestigate
        demoId={demoId}
        name={`Dr. ${p.name.split(' ').at(-1)}`}
        open={investigating}
        onOpenChange={setInvestigating}
        onHighlight={highlight}
      />
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

function Dossier({ p }: { p: ExpertProfile }) {
  return (
    <div className="flex flex-col overflow-hidden rounded-xl border @4xl:flex-row">
      <aside className="flex flex-col gap-6 border-b bg-muted/40 p-7 @4xl:w-80 @4xl:shrink-0 @4xl:border-b-0 @4xl:border-r">
        <div className="space-y-3">
          <Avatar initials={p.initials} />
          <h2 className="text-2xl font-semibold tracking-tight">{p.name}</h2>
          <p className="text-[15px] leading-relaxed text-muted-foreground">{p.title}</p>
        </div>
        <dl className="space-y-3.5 text-sm">
          {p.contact.map((c) => (
            <div key={c.label}>
              <dt className="text-xs text-muted-foreground">{c.label}</dt>
              <dd className="mt-0.5 leading-snug">{c.value}</dd>
            </div>
          ))}
        </dl>
        <div>
          <Label>Areas of expertise</Label>
          <Tags tags={p.tags} />
        </div>
        <div className="border-t pt-5">
          <Label>Compiled from public and proprietary sources</Label>
          <Sources sources={p.sources} />
        </div>
        <Disclaimer className="rounded-lg bg-muted px-3 py-2.5" />
      </aside>

      <div className="min-w-0 flex-1 space-y-4 p-8 @container">
        <Section id="qualifications" title="Qualifications">
          <div className="grid gap-6 @3xl:grid-cols-[minmax(0,1fr)_17rem]">
            <p className="text-[15px] leading-7">{p.qualifications}</p>
            <QualificationHighlights facts={p.highlights} />
          </div>
        </Section>

        <Section id="litigation" title="Litigation history">
          <div className="space-y-5 rounded-lg border p-6">
            <div className="grid grid-cols-2 gap-4 @3xl:grid-cols-4">
              {litigationCounts(p).map((s) => (
                <div key={s.label}>
                  <div className="text-3xl font-semibold tracking-tight">{s.n}</div>
                  <div className="text-sm text-muted-foreground">{s.label}</div>
                </div>
              ))}
            </div>
            <LitigationTimeline cases={p.cases} />
          </div>
          <div>
            <SubHeading note="Every case where the expert was deposed, testified at trial or was named in a filing">
              Cases &amp; testimony
            </SubHeading>
            <CaseList cases={p.cases} />
          </div>
          <div>
            <SubHeading note="Transcripts, filings and challenges tied to the expert. Open any item for details.">
              Records found
            </SubHeading>
            <FactGrid facts={p.documents} />
          </div>
        </Section>

        <Section id="risk" title="Risk & credibility">
          <div>
            <SubHeading note="Licensing boards, court records and background checks">
              Discipline &amp; background
            </SubHeading>
            <FactGrid facts={p.risk} />
          </div>
          <div>
            <SubHeading note="Papers, talks and CLE lectures that opposing counsel can quote back">
              Publications, lectures &amp; presentations
            </SubHeading>
            <EntryList entries={p.publications} />
          </div>
          <div className="grid gap-8 @3xl:grid-cols-3">
            <div>
              <SubHeading note="Press articles about or quoting the expert">
                News features
              </SubHeading>
              <EntryList entries={p.news} />
            </div>
            <div>
              <SubHeading note="Public accounts and what they post">
                Social media presence
              </SubHeading>
              <EntryList entries={p.social} />
            </div>
            <div>
              <SubHeading note="Websites, CVs and directory listings">Online presence</SubHeading>
              <EntryList entries={p.online} />
            </div>
          </div>
        </Section>

        <Section id="professional" title="Professional history">
          <div className="grid gap-8 @3xl:grid-cols-2">
            <div>
              <SubHeading>Career</SubHeading>
              <Career roles={p.career} />
            </div>
            <div className="space-y-6">
              <div>
                <SubHeading>Education &amp; training</SubHeading>
                <EntryList entries={p.education} strong />
              </div>
              <Facts facts={p.professional} />
            </div>
          </div>
        </Section>
      </div>
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

function Avatar({ initials }: { initials: string }) {
  return (
    <span className="flex size-16 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xl font-semibold text-primary">
      {initials}
    </span>
  )
}

function Tags({ tags }: { tags: string[] }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {tags.map((t) => (
        <span key={t} className="rounded-full border bg-background px-2.5 py-1 text-xs">
          {t}
        </span>
      ))}
    </div>
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

function Section({
  id,
  title,
  children,
}: {
  id: string
  title: string
  children: React.ReactNode
}) {
  const marks = useContext(HighlightContext)
  const hasMarks = [...marks.keys()].some((k) => k.startsWith(`${id}:`))
  const [open, setOpen] = useState(true)
  // A new set of highlights reopens any section the user collapsed that holds one.
  useEffect(() => {
    if (hasMarks) setOpen(true)
  }, [hasMarks, marks])

  return (
    <SectionContext.Provider value={id}>
      <Collapsible open={open} onOpenChange={setOpen} asChild>
        <section className="border-b pb-4 last:border-b-0">
          <CollapsibleTrigger className="group -mx-2 flex w-[calc(100%+1rem)] items-center gap-2 rounded-md p-2 text-left hover:bg-muted/50">
            <ChevronRight className="size-5 text-muted-foreground transition-transform group-data-[state=open]:rotate-90" />
            <h2 className="text-xl font-semibold">{title}</h2>
          </CollapsibleTrigger>
          <CollapsibleContent className="space-y-8 py-4">{children}</CollapsibleContent>
        </section>
      </Collapsible>
    </SectionContext.Provider>
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

function Label({ children }: { children: React.ReactNode }) {
  return <h3 className="mb-2.5 text-xs font-medium text-muted-foreground">{children}</h3>
}
