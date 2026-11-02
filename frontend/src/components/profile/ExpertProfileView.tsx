import { useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ChevronDown, ChevronLeft, ChevronUp, Highlighter, Link2, LoaderCircle, Sparkles, X } from 'lucide-react'
import AppHeader from '@/components/AppHeader'
import { Button } from '@/components/ui/button'
import { caseContexts, ROLE_LABEL } from '@/lib/case-context'
import { ExpertProfile, Investigation } from '@/lib/types'
import { cn } from '@/lib/utils'
import { BackButton } from '@/components/profile/BackButton'
import ExpertInvestigate from '@/components/profile/ExpertInvestigate'
import { HighlightContext, scrollToMark } from '@/components/profile/highlights'
import { ReadyContext, RESEARCH_STEPS, useResearch } from '@/components/profile/research'
import { Tab, TABS, tabOf } from '@/components/profile/tabs'
import { TabPanel } from '@/components/profile/TabPanel'

export function ExpertProfileView({ demoId, p }: { demoId: string; p: ExpertProfile }) {
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

const basedIn = (p: ExpertProfile) => p.contact.find((c) => c.label === 'Based in')?.value
