// The wait while a candidate search runs (about a minute): the name as the page headline, a line that
// cycles through the steps the search really takes (web, court opinions, publications, grouping).
import { useEffect, useState } from 'react'
import { Search } from 'lucide-react'
import { cn } from '@/lib/utils'

const LINES = [
  'Searching the web for expert-witness listings…',
  'Reading federal court opinions that name them…',
  'Looking up their publications…',
  'Checking CVs and professional directories…',
  'Telling apart people who share the name…',
]
const EVERY_MS = 2800
const FADE_MS = 350

export default function SearchingPulse({ name, headline }: { name: string; headline: string }) {
  const [line, setLine] = useState(0)
  const [fading, setFading] = useState(false)

  useEffect(() => {
    let fade: ReturnType<typeof setTimeout>
    const id = setInterval(() => {
      setFading(true)
      fade = setTimeout(() => {
        setLine((n) => (n + 1) % LINES.length)
        setFading(false)
      }, FADE_MS)
    }, EVERY_MS)
    return () => {
      clearInterval(id)
      clearTimeout(fade)
    }
  }, [])

  return (
    <div role="status" className="flex flex-col items-center gap-5">
      <h1 className={headline}>Looking for {name}</h1>
      <p
        aria-live="polite"
        className={cn(
          'min-h-[1.5em] text-[15px] text-muted-foreground transition-all duration-300 motion-reduce:transition-none',
          fading && 'translate-y-1 opacity-0'
        )}
      >
        {LINES[line]}
      </p>
      <div className="relative mt-6 grid size-[72px] place-items-center">
        {[0, 1.2].map((delay) => (
          <span
            key={delay}
            style={{ animationDelay: `${delay}s` }}
            className="absolute inset-0 animate-[pulse-ring_2.4s_ease-out_infinite] rounded-full bg-primary opacity-0 motion-reduce:hidden"
          />
        ))}
        <span className="relative grid size-11 place-items-center rounded-full bg-primary text-primary-foreground">
          <Search className="size-5" strokeWidth={2.2} />
        </span>
      </div>
      <p className="text-xs text-muted-foreground">Usually under a minute</p>
    </div>
  )
}
