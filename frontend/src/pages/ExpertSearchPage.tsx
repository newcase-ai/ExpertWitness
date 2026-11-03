// Search → pick the right person → research (/research/:dossierId). Only the buttons start work: the
// query and the saved search's id live in the URL, so Back or a reload shows the same results
// without searching again.
import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import AppHeader from '@/components/AppHeader'
import NameSearch from '@/components/search/NameSearch'
import SearchingPulse from '@/components/search/SearchingPulse'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { ExpertCandidate, loadSearch, SearchQuery, startResearch, startSearch } from '@/lib/api'

type Results = { key: string; people: ExpertCandidate[] } | { key: string; error: string }

export default function ExpertSearchPage() {
  const [params, setParams] = useSearchParams()
  const query: SearchQuery = {
    name: params.get('name') ?? '',
    specialty: params.get('specialty') ?? '',
    location: params.get('location') ?? '',
  }
  const searchId = params.get('search')
  const key = params.toString()
  const [results, setResults] = useState<Results | null>(null)

  useEffect(() => {
    if (!searchId) return
    let live = true
    loadSearch(searchId)
      .then((people) => live && setResults({ key: searchId, people }))
      .catch(
        (e) =>
          live &&
          setResults({ key: searchId, error: e instanceof Error ? e.message : 'Search failed' })
      )
    return () => {
      live = false
    }
  }, [searchId])

  const search = (q: SearchQuery) => {
    const trimmed = { name: q.name.trim(), specialty: q.specialty.trim(), location: q.location.trim() }
    setResults(null)
    startSearch(trimmed)
      .then((id) =>
        setParams({ ...Object.fromEntries(Object.entries(trimmed).filter(([, v]) => v)), search: id })
      )
      .catch((e) =>
        setResults({ key: '', error: e instanceof Error ? e.message : 'Search failed to start' })
      )
  }

  return (
    <div className="bg-dots min-h-screen text-sm">
      <AppHeader />
      {!searchId ? (
        <main className={HERO}>
          <Eyebrow />
          <h1 className={HEADLINE}>
            Know your expert
            <br />
            before they testify.
          </h1>
          <p className="max-w-[48ch] text-[15px] text-muted-foreground">
            Find the person, then get a cited profile: cases, rulings, fees and red flags.
          </p>
          <NameSearch initial={query} onSearch={search} className="mt-6" />
          <div className="flex flex-wrap justify-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
            {['Web', 'Federal court opinions', 'Publications'].map((s) => (
              <span key={s}>
                <span className="font-bold text-[#1D5B40]">✓</span> {s}
              </span>
            ))}
          </div>
          {results?.key === '' && 'error' in results && (
            <p role="alert" className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-red-800">
              {results.error}
            </p>
          )}
        </main>
      ) : results?.key !== searchId ? (
        // the same hero as the start page, so starting a search feels like the page moving on
        <main className={HERO}>
          <Eyebrow />
          <SearchingPulse name={query.name} headline={HEADLINE} />
        </main>
      ) : (
        <main className="px-4 pb-16 pt-12">
          <div className="mx-auto flex max-w-[800px] flex-col gap-5">
            <div className="flex flex-col gap-1">
              <Eyebrow />
              <NameSearch key={key} initial={query} onSearch={search} focus={false} className="mt-2 max-w-none" />
            </div>
            {'error' in results ? (
              <p
                role="alert"
                className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-red-800"
              >
                {results.error}
              </p>
            ) : results.people.length ? (
              <CandidateList people={results.people} />
            ) : (
              <p className="rounded-xl border bg-card px-5 py-4 text-muted-foreground">
                No expert witness named {query.name} turned up. Check the spelling, or try without
                the specialty or city.
              </p>
            )}
          </div>
        </main>
      )}
    </div>
  )
}

// The start page's hero, shared by the searching view.
const HERO = 'flex flex-col items-center gap-5 px-4 pb-16 pt-24 text-center'
const HEADLINE =
  'text-balance font-serif text-[40px] font-semibold leading-[1.1] tracking-tight max-sm:text-[30px]'

function Eyebrow() {
  return (
    <p className="text-[11px] font-bold uppercase tracking-[0.1em] text-primary">
      Expert witness research
    </p>
  )
}

function CandidateList({ people }: { people: ExpertCandidate[] }) {
  const navigate = useNavigate()
  const [starting, setStarting] = useState(false)
  const research = (candidateId: string) => {
    setStarting(true)
    startResearch(candidateId)
      .then((dossierId) => navigate(`/research/${dossierId}`))
      .catch((e) => {
        setStarting(false)
        alert(e instanceof Error ? e.message : 'Research failed to start')
      })
  }
  return (
    <>
      <div className="space-y-1">
        <h1 className="font-serif text-[26px] font-semibold tracking-tight">
          {people.length} possible {people.length === 1 ? 'match' : 'matches'}
        </h1>
        <p className="text-muted-foreground">
          Choose the right person and we'll research them in depth.
        </p>
      </div>
      <ul className="divide-y rounded-xl border bg-card">
        {people.map((p) => (
          <li key={p.id} className="flex flex-wrap items-center gap-x-5 gap-y-3 px-5 py-4">
            <div className="flex min-w-0 flex-[1_1_380px] flex-col gap-1">
              <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
                <span className="text-[15px] font-semibold">{p.name}</span>
                <span className="text-muted-foreground">
                  {[p.specialty, p.location].filter(Boolean).join(' · ')}
                </span>
                <span
                  title={p.matchReason}
                  className={cn(
                    'whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-semibold',
                    p.match === 'strong'
                      ? 'bg-[#E3F1EA] text-[#1D5B40]'
                      : 'bg-[#FFF1DC] text-[#7A4E00]'
                  )}
                >
                  {p.match === 'strong' ? 'Strong match' : 'Partial match'}
                </span>
              </div>
              <p className="leading-normal text-[#2E3442]">{p.summary}</p>
              <SiteChips sources={p.sources} />
            </div>
            <Button
              variant={p.match === 'strong' ? 'default' : 'outline'}
              className={cn(p.match !== 'strong' && 'border-primary text-primary')}
              disabled={starting}
              onClick={() => research(p.id)}
            >
              Research this expert
            </Button>
          </li>
        ))}
      </ul>
      <p className="text-muted-foreground">
        Not the person you're looking for? Add a specialty or city above.
      </p>
    </>
  )
}

const SHOWN_SITES = 3

// One chip per site (host without "www."), linking to the first page found there.
function SiteChips({ sources }: { sources: ExpertCandidate['sources'] }) {
  const sites = new Map<string, string>()
  for (const s of sources) {
    try {
      const host = new URL(s.url).hostname.replace(/^www\./, '')
      if (!sites.has(host)) sites.set(host, s.url)
    } catch {
      // not a URL: skip it
    }
  }
  if (!sites.size) return null
  const all = [...sites]
  const hidden = all.slice(SHOWN_SITES).map(([host]) => host)
  return (
    <div className="flex flex-wrap gap-1.5 text-xs text-muted-foreground">
      {all.slice(0, SHOWN_SITES).map(([host, url]) => (
        <a
          key={host}
          href={url}
          target="_blank"
          rel="noreferrer"
          className="rounded-md border bg-[#F5F6F8] px-1.5 py-px hover:text-primary"
        >
          {host}
        </a>
      ))}
      {hidden.length > 0 && (
        <span title={hidden.join(', ')} className="rounded-md border bg-[#F5F6F8] px-1.5 py-px">
          +{hidden.length} more
        </span>
      )}
    </div>
  )
}
