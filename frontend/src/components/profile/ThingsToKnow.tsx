import { useContext } from 'react'
import { ExpertProfile } from '@/lib/types'
import { cn } from '@/lib/utils'
import { CARD, Pending } from '@/components/profile/common'
import { ReadyContext } from '@/components/profile/research'

// Flagged records first, then a one-line all-clear for the background checks that found nothing.
export function ThingsToKnow({ p }: { p: ExpertProfile }) {
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
        <div className="space-y-1 rounded-lg bg-muted p-3">
          <div className="font-semibold">Nothing found</div>
          <div className="text-sm leading-normal">
            No {clear.map((f) => f.label.toLowerCase()).join(', ')} found.
          </div>
        </div>
      )}
    </section>
  )
}
