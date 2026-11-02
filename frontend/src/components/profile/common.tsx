import { cn } from '@/lib/utils'

export const CARD = 'rounded-xl border bg-card p-5'

export function Pending({ className }: { className?: string }) {
  return (
    <div aria-busy="true" className={cn('animate-pulse space-y-2.5', className)}>
      <div className="h-3 w-2/3 rounded bg-muted" />
      <div className="h-3 w-1/2 rounded bg-muted" />
      <div className="h-3 w-3/5 rounded bg-muted" />
    </div>
  )
}

export function Sources({ sources }: { sources: string[] }) {
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

export function Disclaimer({ className }: { className?: string }) {
  return (
    <p className={cn('text-xs leading-relaxed text-muted-foreground', className)}>
      Compiled automatically from public and proprietary sources. Verify before relying on it.
    </p>
  )
}

export function SubHeading({ note, children }: { note?: string; children: React.ReactNode }) {
  return (
    <div className="mb-3">
      <h3 className="text-[15px] font-semibold">{children}</h3>
      {note && <p className="mt-0.5 text-[13px] text-muted-foreground">{note}</p>}
    </div>
  )
}
