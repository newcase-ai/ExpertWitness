// Search → pick the right person → research (/research/:dossierId). Only the buttons start work: the
// query and the saved search's id live in the URL, so Back or a reload shows the same results
// without searching again.
import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { LoaderCircle } from 'lucide-react'
import AppHeader from '@/components/AppHeader'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
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
    <div className="min-h-screen text-sm">
      <AppHeader />
      {!searchId ? (
        <main className="mx-auto flex max-w-[640px] flex-col gap-6 px-4 pb-12 pt-24">
          <div className="space-y-1.5">
            <h1 className="font-serif text-[28px] font-semibold tracking-tight">
              Find an expert witness
            </h1>
            <p className="text-muted-foreground">
              We look them up across the web, court records and licensing boards.
            </p>
          </div>
          <SearchForm initial={query} onSearch={search} />
          {results?.key === '' && 'error' in results && (
            <p role="alert" className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-red-800">
              {results.error}
            </p>
          )}
        </main>
      ) : (
        <main className="mx-auto flex max-w-[800px] flex-col gap-5 px-4 pb-12 pt-8">
          <SearchForm key={key} initial={query} onSearch={search} compact />
          {results?.key !== searchId ? (
            <p role="status" className="flex items-center gap-2 text-muted-foreground">
              <LoaderCircle className="size-4 animate-spin text-primary" /> Searching for{' '}
              {query.name}…
            </p>
          ) : 'error' in results ? (
            <p
              role="alert"
              className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-red-800"
            >
              {results.error}
            </p>
          ) : results.people.length ? (
            <CandidateList query={query} people={results.people} />
          ) : (
            <p className="rounded-xl border bg-card px-5 py-4 text-muted-foreground">
              No expert witness named {query.name} turned up. Check the spelling, or try without the
              specialty or city.
            </p>
          )}
        </main>
      )}
    </div>
  )
}

function SearchForm({
  initial,
  onSearch,
  compact,
}: {
  initial: SearchQuery
  onSearch: (q: SearchQuery) => void
  compact?: boolean
}) {
  const [q, setQ] = useState(initial)
  const field = (k: keyof SearchQuery) => ({
    value: q[k],
    onChange: (e: React.ChangeEvent<HTMLInputElement>) => setQ({ ...q, [k]: e.target.value }),
  })

  if (compact)
    return (
      <form
        className="flex flex-wrap gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          onSearch(q)
        }}
      >
        <Field label="Full name" className="flex-[2_1_220px]">
          <Input {...field('name')} required className="bg-card" />
        </Field>
        <Field label="Specialty" className="flex-[1_1_150px]">
          <Input {...field('specialty')} placeholder="Any" className="bg-card" />
        </Field>
        <Field label="City or state" className="flex-[1_1_150px]">
          <Input {...field('location')} placeholder="Anywhere" className="bg-card" />
        </Field>
        <Button type="submit" variant="outline" className="self-end">
          Update
        </Button>
      </form>
    )

  return (
    <form
      className="flex flex-col gap-3.5 rounded-xl border bg-card p-5"
      onSubmit={(e) => {
        e.preventDefault()
        onSearch(q)
      }}
    >
      <Field label="Full name" strong>
        <Input
          {...field('name')}
          required
          autoFocus
          placeholder="e.g. Margaret Chen"
          className="h-10 text-[15px]"
        />
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Specialty" optional>
          <Input {...field('specialty')} placeholder="e.g. Orthopedic surgery" className="h-10" />
        </Field>
        <Field label="City or state" optional>
          <Input {...field('location')} placeholder="e.g. Ohio" className="h-10" />
        </Field>
      </div>
      <Button type="submit" className="h-10 self-end px-5">
        Search
      </Button>
    </form>
  )
}

function Field({
  label,
  optional,
  strong,
  className,
  children,
}: {
  label: string
  optional?: boolean
  strong?: boolean
  className?: string
  children: React.ReactNode
}) {
  return (
    <label className={cn('flex flex-col gap-1.5', className)}>
      <span
        className={cn(
          'text-[13px]',
          strong ? 'font-semibold' : 'font-medium text-muted-foreground'
        )}
      >
        {label} {optional && <span className="font-normal">(optional)</span>}
      </span>
      {children}
    </label>
  )
}

function CandidateList({ query, people }: { query: SearchQuery; people: ExpertCandidate[] }) {
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
        <h1 className="text-xl font-semibold">
          {people.length} possible {people.length === 1 ? 'match' : 'matches'} for {query.name}
        </h1>
        <p className="text-muted-foreground">
          Choose the right person and we'll research them in depth.
        </p>
      </div>
      <ul className="divide-y rounded-xl border bg-card">
        {people.map((p) => (
          <li key={p.id} className="flex flex-wrap items-center gap-4 px-5 py-4">
            <div className="flex min-w-0 flex-[1_1_400px] flex-col gap-1.5">
              <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
                <span className="text-[15px] font-semibold">{p.name}</span>
                <span className="text-muted-foreground">
                  {p.specialty} · {p.location}
                </span>
              </div>
              <div className="flex flex-wrap items-center gap-2 text-[13px]">
                <span
                  className={cn(
                    'rounded-full px-2 py-0.5 font-semibold',
                    p.match === 'strong'
                      ? 'bg-[#E3F1EA] text-[#1D5B40]'
                      : 'bg-[#FFF1DC] text-[#7A4E00]'
                  )}
                >
                  {p.match === 'strong' ? 'Strong match' : 'Partial match'}
                </span>
                <span className="text-muted-foreground">{p.matchReason}</span>
              </div>
              <p className="leading-normal text-[#2E3442]">{p.summary}</p>
              {p.sources.length > 0 && (
                <span className="text-[13px] text-muted-foreground">
                  Found on{' '}
                  {p.sources.map((s, i) => (
                    <span key={s.url}>
                      {i > 0 && ', '}
                      <a
                        href={s.url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-primary hover:underline"
                      >
                        {s.label}
                      </a>
                    </span>
                  ))}
                </span>
              )}
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
