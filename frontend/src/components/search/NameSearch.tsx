// The start page's search: one large underlined name field whose placeholder types sample names, and
// "+ Specialty" / "+ City or state" buttons that open the two optional fields.
import { useEffect, useState } from 'react'
import { cn } from '@/lib/utils'
import type { SearchQuery } from '@/lib/api'

const SAMPLES = ['John Doe', 'Jane Roe', 'Richard Roe'] // placeholder names, not real people
const TYPE_MS = 110

function useTypedSample() {
  const [text, setText] = useState(SAMPLES[0])
  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    let sample = 0
    let chars = 0
    let deleting = false
    const id = setInterval(() => {
      const word = SAMPLES[sample]
      chars += deleting ? -1 : 1
      if (chars > word.length + 8) deleting = true // hold the full name for a moment
      if (chars <= 0) {
        deleting = false
        sample = (sample + 1) % SAMPLES.length
      }
      setText(word.slice(0, Math.max(0, Math.min(chars, word.length))))
    }, TYPE_MS)
    return () => clearInterval(id)
  }, [])
  return text
}

const OPTIONAL = [
  { key: 'specialty', label: 'Specialty', placeholder: 'Specialty, e.g. Toxicology' },
  { key: 'location', label: 'City or state', placeholder: 'City or state, e.g. Ohio' },
] as const

export default function NameSearch({
  initial,
  onSearch,
  focus = true,
  className,
}: {
  initial: SearchQuery
  onSearch: (q: SearchQuery) => void
  focus?: boolean // false on the results page, where the name is already filled in
  className?: string
}) {
  const [q, setQ] = useState(initial)
  const [open, setOpen] = useState({ specialty: !!initial.specialty, location: !!initial.location })
  const sample = useTypedSample()
  const set = (k: keyof SearchQuery) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setQ({ ...q, [k]: e.target.value })

  return (
    <form
      className={cn('flex w-full max-w-[640px] flex-col gap-4 text-left', className)}
      onSubmit={(e) => {
        e.preventDefault()
        onSearch(q)
      }}
    >
      <div className="flex items-center gap-3 border-b-2 border-foreground pb-2 focus-within:border-primary">
        <input
          value={q.name}
          onChange={set('name')}
          required
          autoFocus={focus}
          aria-label="Expert's full name"
          placeholder={sample || ' '}
          className="min-w-0 flex-1 bg-transparent font-serif text-[28px] font-semibold leading-tight outline-none placeholder:font-normal placeholder:text-muted-foreground/70 max-sm:text-[22px]"
        />
        <button
          type="submit"
          className="shrink-0 rounded-lg bg-primary px-4 py-2 text-[13px] font-semibold text-primary-foreground hover:bg-primary/90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
        >
          Search ↵
        </button>
      </div>
      <div className="flex flex-wrap gap-2">
        {OPTIONAL.map(({ key, label }) => (
          <button
            key={key}
            type="button"
            aria-pressed={open[key]}
            onClick={() => {
              if (open[key]) setQ({ ...q, [key]: '' }) // closing a field drops its value
              setOpen({ ...open, [key]: !open[key] })
            }}
            className={cn(
              'rounded-full border border-dashed px-3 py-1 text-[13px] font-semibold text-primary',
              open[key] && 'border-solid border-primary/40 bg-primary/[0.07]'
            )}
          >
            {open[key] ? '−' : '+'} {label}
          </button>
        ))}
      </div>
      {OPTIONAL.filter(({ key }) => open[key]).map(({ key, label, placeholder }) => (
        <input
          key={key}
          value={q[key]}
          onChange={set(key)}
          autoFocus
          aria-label={label}
          placeholder={placeholder}
          className="border-b bg-transparent py-1.5 text-base outline-none placeholder:text-muted-foreground/80 focus:border-primary"
        />
      ))}
    </form>
  )
}
