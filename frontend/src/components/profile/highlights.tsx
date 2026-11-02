import { createContext, useContext } from 'react'

// A highlight marks one profile item, addressed as `${section id}:${item label}`.
type Mark = { n: number; note: string }
export const HighlightContext = createContext<Map<string, Mark>>(new Map())
export const SectionContext = createContext('')
export const MARKED =
  'rounded-lg bg-amber-50 ring-1 ring-amber-300 dark:bg-amber-500/10 dark:ring-amber-500/40'

export function useMarkOf() {
  const section = useContext(SectionContext)
  const marks = useContext(HighlightContext)
  return (label: string) => marks.get(`${section}:${label}`)
}

export const scrollToMark = (n: number) =>
  document
    .querySelector(`[data-highlight="${n}"]`)
    ?.scrollIntoView({ behavior: 'smooth', block: 'center' })

export function MarkNum({ mark }: { mark?: Mark }) {
  if (!mark) return null
  return (
    <span className="mr-2 inline-flex size-5 items-center justify-center rounded-full bg-amber-500 align-[1px] text-[11px] font-bold not-italic text-white">
      {mark.n}
    </span>
  )
}

export function MarkNote({ mark }: { mark?: Mark }) {
  if (!mark) return null
  return (
    <p className="mt-1.5 text-[13px] font-normal not-italic">
      <mark className="rounded bg-amber-200/70 px-1 py-0.5 text-amber-950 dark:bg-amber-500/30 dark:text-amber-50">
        {mark.note}
      </mark>
    </p>
  )
}
