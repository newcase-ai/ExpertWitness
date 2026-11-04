import { ExpertCase, ExpertProfile, Side } from '@/lib/types'
import { cn } from '@/lib/utils'
import { MARKED, MarkNote, MarkNum, useMarkOf } from '@/components/profile/highlights'

export function litigationCounts(p: ExpertProfile) {
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
  ].filter((c) => c.n !== '0') // a count of 0 is not shown
}

// ponytail: one dot per case on a year axis; two cases in the same year and side overlap.
// Stack them if real data ever has that.
export function LitigationTimeline({ cases }: { cases: ExpertCase[] }) {
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

export function CaseList({ cases }: { cases: ExpertCase[] }) {
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
                {c.url ? (
                  <a href={c.url} target="_blank" rel="noreferrer" className="hover:text-primary hover:underline">
                    {c.name}
                  </a>
                ) : (
                  c.name
                )}
              </div>
              <p className="mt-1 text-sm leading-relaxed text-muted-foreground">
                {c.description} {c.court}
              </p>
              {c.summary && <p className="mt-2 text-sm leading-relaxed">{c.summary}</p>}
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
