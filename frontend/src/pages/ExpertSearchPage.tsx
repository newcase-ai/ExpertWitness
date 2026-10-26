// search → searching → results → the picked expert's profile.
import { useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ArrowRight, Check, LoaderCircle, MapPin, RotateCcw, Search } from 'lucide-react'
import { ShimmeringText } from '@/components/ui/shimmering-text'
import { cn } from '@/lib/utils'
import { searchExperts, ExpertCandidate } from '@/lib/api'

type Screen = 'search' | 'searching' | 'results'

const BLUE =
  'bg-[#3849AB] text-white hover:bg-[#2B3888] disabled:bg-[#E3E5EC] disabled:text-[#6B7080]'

const host = (url: string) => new URL(url).hostname.replace(/^www\./, '')

export default function ExpertSearchPage() {
  const navigate = useNavigate()
  const [screen, setScreen] = useState<Screen>('search')
  const [name, setName] = useState('')
  const [hint, setHint] = useState('')
  const [query, setQuery] = useState('') // what was searched, even if the box changes
  const [people, setPeople] = useState<ExpertCandidate[]>([])
  const [picked, setPicked] = useState<ExpertCandidate | null>(null)
  const [error, setError] = useState('')
  const run = useRef(0) // bumped on cancel / new search so a stale search can't update the page

  async function startSearch(n: string, h: string) {
    n = n.trim()
    h = h.trim()
    if (!n) return
    const id = ++run.current
    const live = () => id === run.current
    setName(n)
    setQuery(n)
    setPicked(null)
    setError('')
    setScreen('searching')
    try {
      const found = await searchExperts(n, h)
      if (!live()) return
      if (found.length) {
        setPeople(found)
        setPicked(found.length === 1 ? found[0] : null)
        setScreen('results')
      } else {
        setError(
          `No expert witness named ${n} turned up. Check the spelling, or add a specialty or city.`
        )
        setScreen('search')
      }
    } catch (e) {
      if (!live()) return
      setError(e instanceof Error ? e.message : 'The search failed. Please try again.')
      setScreen('search')
    }
  }

  const searchAnother = () => {
    run.current++
    setName('')
    setHint('')
    setError('')
    setScreen('search')
  }

  return (
    <div className="flex min-h-screen flex-col bg-[#F6F7FB] font-[-apple-system,BlinkMacSystemFont,'Segoe_UI',system-ui,sans-serif] text-[#16181D]">
      <header className="flex h-20 shrink-0 items-center justify-between gap-4 border-b border-[#E3E5EC] bg-white px-4 md:px-14">
        <Link to="/" onClick={searchAnother} className="text-2xl font-bold tracking-tight">
          ExpertWitness
        </Link>
        <button
          type="button"
          onClick={searchAnother}
          className="flex h-11 items-center gap-2 rounded-[10px] border-[1.5px] border-[#D5D8E2] bg-white px-[18px] text-base font-semibold hover:bg-[#F8F9FC]"
        >
          <RotateCcw className="size-[18px]" /> New search
        </button>
      </header>

      {screen === 'search' && (
        <main className="flex flex-1 flex-col items-center justify-center gap-10 px-4 pb-20 pt-10 md:px-[120px]">
          <div className="flex flex-col items-center gap-3.5 text-center">
            <h1 className="text-4xl font-bold leading-[1.08] tracking-[-0.02em] md:text-[56px]">
              Who are you looking for?
            </h1>
            <p className="text-[22px] text-[#4A4F5C]">
              Enter the expert's full name. Add a specialty or city if you know it.
            </p>
          </div>
          <form
            className="flex w-full max-w-[960px] flex-col gap-3 rounded-[20px] border border-[#E3E5EC] bg-white p-3 shadow-[0_12px_40px_rgba(22,24,29,0.06)] md:flex-row"
            onSubmit={(e) => {
              e.preventDefault()
              startSearch(name, hint)
            }}
          >
            <label className="flex flex-[3] items-center gap-3.5 rounded-xl border-2 border-transparent bg-[#F6F7FB] px-[18px] focus-within:border-[#3849AB]">
              <Search className="size-[26px] shrink-0 text-[#3849AB]" strokeWidth={2.2} />
              <span className="sr-only">Expert name</span>
              <input
                autoFocus
                autoComplete="off"
                placeholder="Expert's full name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="h-[72px] min-w-0 flex-1 bg-transparent text-[26px] font-medium outline-none placeholder:text-[#8A8F9C]"
              />
            </label>
            <label className="flex flex-[2] items-center rounded-xl border-2 border-transparent bg-[#F6F7FB] px-[18px] focus-within:border-[#3849AB]">
              <span className="sr-only">Specialty or location (optional)</span>
              <input
                autoComplete="off"
                placeholder="Specialty or city (optional)"
                value={hint}
                onChange={(e) => setHint(e.target.value)}
                className="h-[72px] min-w-0 flex-1 bg-transparent text-xl outline-none placeholder:text-[#8A8F9C]"
              />
            </label>
            <button
              type="submit"
              disabled={!name.trim()}
              className={cn(BLUE, 'h-[72px] rounded-xl px-9 text-[22px] font-semibold md:h-auto')}
            >
              Search
            </button>
          </form>
          {error && (
            <p
              role="alert"
              className="w-full max-w-[960px] rounded-xl border border-[#F1C9C6] bg-[#FDF2F1] px-5 py-4 text-[17px] text-[#8A2F2A]"
            >
              {error}
            </p>
          )}
        </main>
      )}

      {screen === 'searching' && (
        <main className="flex flex-1 flex-col items-center justify-center gap-8 px-4 pb-20 md:px-[120px]">
          <h1 className="text-center text-4xl font-bold leading-[1.1] tracking-[-0.02em] md:text-5xl">
            Looking for {query}…
          </h1>
          <div aria-live="polite" className="flex items-center gap-3 text-xl font-semibold">
            <LoaderCircle className="size-6 animate-spin text-[#3849AB]" strokeWidth={2.4} />
            <ShimmeringText text="Searching…" color="#3849AB" shimmeringColor="#9AA4DB" />
          </div>
          <button
            type="button"
            onClick={searchAnother}
            className="h-12 rounded-[10px] px-[22px] text-[17px] font-semibold text-[#4A4F5C] hover:text-[#16181D]"
          >
            Cancel
          </button>
        </main>
      )}

      {screen === 'results' && (
        <main className="flex flex-1 flex-col gap-8 px-4 pb-14 pt-12 md:px-[120px]">
          <div className="flex flex-col gap-2.5">
            <h1 className="text-3xl font-bold leading-[1.1] tracking-[-0.02em] md:text-[44px]">
              We found {people.length} {people.length === 1 ? 'person' : 'people'} named {query}
            </h1>
            <p className="text-xl text-[#4A4F5C]">
              {people.length === 1
                ? 'Is this your expert?'
                : 'Which one is your expert? Tap a card to choose.'}
            </p>
          </div>

          <ul className="grid gap-6 md:grid-cols-3">
            {people.map((p) => {
              const sel = picked === p
              return (
                <li
                  key={p.id}
                  className={cn(
                    'flex flex-col overflow-hidden rounded-[20px] border-2',
                    sel
                      ? 'border-[#3849AB] bg-[#F3F5FD] shadow-[0_0_0_4px_rgba(56,73,171,0.15)]'
                      : 'border-[#E3E5EC] bg-white shadow-[0_4px_16px_rgba(22,24,29,0.04)]'
                  )}
                >
                  <button
                    type="button"
                    aria-pressed={sel}
                    onClick={() => setPicked(sel ? null : p)}
                    className="flex flex-1 flex-col gap-3.5 rounded-t-[18px] px-7 pb-5 pt-7 text-left hover:bg-[#F8F9FC]"
                  >
                    <div className="flex w-full items-start justify-between gap-3">
                      <div className="flex flex-col gap-1.5">
                        <span className="text-2xl font-bold">{p.name}</span>
                        <span className="text-lg font-semibold text-[#3849AB]">{p.specialty}</span>
                      </div>
                      {sel ? (
                        <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-[#3849AB]">
                          <Check className="size-[18px] text-white" strokeWidth={3} />
                        </span>
                      ) : (
                        <span className="size-8 shrink-0 rounded-full border-2 border-[#C3C7D3]" />
                      )}
                    </div>
                    <div className="flex items-start gap-2 text-base text-[#4A4F5C]">
                      <MapPin className="mt-px size-[18px] shrink-0" />
                      <span>
                        <strong className="font-semibold text-[#16181D]">{p.location}</strong>
                        {p.otherLocations.length > 0 &&
                          ` · also works in ${p.otherLocations.join(', ')}`}
                      </span>
                    </div>
                    <p className="text-[17px] leading-normal text-[#2E323B]">{p.summary}</p>
                  </button>
                  {p.urls.length > 0 && (
                    <div className="flex flex-wrap items-center gap-x-3.5 gap-y-2 border-t border-[#EEF0F4] px-7 pb-5 pt-3.5 text-[15px]">
                      <span className="text-[#5B6070]">Sources</span>
                      {p.urls.map((u) => (
                        <a
                          key={u}
                          href={u}
                          target="_blank"
                          rel="noreferrer"
                          className="text-[#3849AB] underline hover:text-[#2B3888]"
                        >
                          {host(u)}
                        </a>
                      ))}
                    </div>
                  )}
                </li>
              )
            })}
          </ul>

          <button
            type="button"
            disabled={!picked}
            onClick={() => picked && navigate(`/experts/${picked.id}`)}
            className={cn(
              BLUE,
              'flex h-16 items-center gap-2.5 self-center rounded-xl px-9 text-xl font-semibold'
            )}
          >
            This is my expert <ArrowRight className="size-[22px]" strokeWidth={2.2} />
          </button>
        </main>
      )}
    </div>
  )
}
