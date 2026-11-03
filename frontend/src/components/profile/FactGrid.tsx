import { useState } from 'react'
import { ChevronRight, TriangleAlert } from 'lucide-react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Fact } from '@/lib/types'
import { cn } from '@/lib/utils'
import { MARKED, MarkNote, MarkNum, useMarkOf } from '@/components/profile/highlights'

export function FactGrid({ facts }: { facts: Fact[] }) {
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
                      <div className="text-sm font-medium">
                        {d.url ? (
                          <a href={d.url} target="_blank" rel="noreferrer" className="hover:text-primary hover:underline">
                            {d.title}
                          </a>
                        ) : (
                          d.title
                        )}
                      </div>
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

// "None found" means our search came up empty, not a verified clean record: plain text, no check.
function FactValue({ fact: f }: { fact: Fact }) {
  return (
    <span
      className={cn(
        'flex shrink-0 items-center gap-1.5 text-[13px]',
        f.flag ? 'font-medium text-amber-600' : 'text-muted-foreground'
      )}
    >
      {f.flag && <TriangleAlert className="size-3.5" />}
      {f.value}
    </span>
  )
}
