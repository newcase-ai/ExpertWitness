import { Entry, Fact, Role } from '@/lib/types'
import { cn } from '@/lib/utils'
import { MARKED, MarkNote, MarkNum, useMarkOf } from '@/components/profile/highlights'
import { SourceLinks } from '@/components/profile/common'

export function QualificationHighlights({ facts }: { facts: Fact[] }) {
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
            <SourceLinks urls={h.links} />
            <MarkNote mark={mark} />
          </div>
        )
      })}
    </dl>
  )
}

export function Facts({ facts }: { facts: Fact[] }) {
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
            <SourceLinks urls={f.links} />
            <MarkNote mark={mark} />
          </div>
        )
      })}
    </dl>
  )
}

export function EntryList({ entries, strong }: { entries: Entry[]; strong?: boolean }) {
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
              {e.url ? (
                <a href={e.url} target="_blank" rel="noreferrer" className="hover:text-primary hover:underline">
                  {e.title}
                </a>
              ) : (
                e.title
              )}
            </div>
            <div className="text-[13px] text-muted-foreground">{e.detail}</div>
            <MarkNote mark={mark} />
          </li>
        )
      })}
    </ul>
  )
}

export function Career({ roles }: { roles: Role[] }) {
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
              <SourceLinks urls={r.links} />
              <MarkNote mark={mark} />
            </div>
          </li>
        )
      })}
    </ol>
  )
}
