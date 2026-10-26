export interface ExpertCandidate {
  id: string // profile id, matches DEMO_PROFILES
  name: string
  specialty: string
  location: string // where they are based
  otherLocations: string[] // other places they take work
  summary: string // one short sentence that tells this person apart from the others
  urls: string[] // sources, best first
}

export async function searchExperts(name: string, hint = ''): Promise<ExpertCandidate[]> {
  const res = await fetch('/api/search', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, hint }),
  })
  if (!res.ok) throw new Error(`Search failed (${res.status})`)
  return (await res.json()).candidates
}
