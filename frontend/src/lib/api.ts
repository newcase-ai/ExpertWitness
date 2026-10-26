export interface ExpertCandidate {
  id: string // research id, matches DEMO_PROFILES for the experts we have mock research for
  name: string // display name, with title and credentials
  specialty: string
  location: string
  summary: string // one short sentence that tells this person apart from the others
  sources: { label: string; url: string }[]
  match: 'strong' | 'partial'
  matchReason: string // e.g. "Full name and specialty match"
}

export interface SearchQuery {
  name: string
  specialty: string
  location: string
}

export async function searchExperts(query: SearchQuery): Promise<ExpertCandidate[]> {
  const res = await fetch('/api/search', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(query),
  })
  if (!res.ok) throw new Error(`Search failed (${res.status})`)
  return (await res.json()).candidates
}
