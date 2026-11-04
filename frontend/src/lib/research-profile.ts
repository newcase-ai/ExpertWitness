import type { Finding, Profile, ProfileItem, ResearchJob, SectionStatus } from '@/lib/api'
import type { Entry, ExpertCase, ExpertProfile, Fact, Involvement } from '@/lib/types'

// A research run's profile (backend Profile + findings + tool results) in the shape the product's
// profile components take. Only what the searcher needs: no confidence, conflicts, gaps or leads.
// ponytail: title and "Based in" are read from the AI's text; the backend spec
// (docs/expert-profile-spec.md) asks for them as fields.

export const host = (url: string) => {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return url
  }
}

const ROLE: Record<string, Involvement> = {
  'expert witness': 'Expert witness',
  'consultant or reviewer': 'Consultant or reviewer',
}

const credLabel = (t: string) =>
  /licen/i.test(t) ? 'License' : /board/i.test(t) ? 'Board certification' : /degree|college|school/i.test(t) ? 'Education' : 'Credential'

const tidy = (t: string) => {
  const s = t.replace(/^(Reported as|Reported|Holds)\s+/i, '').replace(/\.$/, '')
  return s[0].toUpperCase() + s.slice(1)
}

export interface ResearchProfile extends ExpertProfile {
  identity: Entry[]
  credentials: Entry[]
  notHim: Entry[]
  authorStats: string | null
  qualificationsLinks?: string[] // pages the summary came from
  sourceLinks?: Entry[] // every cited page, linked (structured profile only)
  // From the structured profile: a section whose status is 'not_checked' says so instead of "Nothing found".
  status?: Partial<Record<'career' | 'credentials' | 'cases' | 'publications' | 'online', SectionStatus>>
}

export const initialsOf = (name: string) =>
  name.split(/\s+/).filter((w) => /^[A-Z][a-z]/.test(w)).map((w) => w[0]).slice(0, 2).join('')

export function toResearchProfile(
  name: string,
  p: Profile,
  findings: Finding[],
  activity: ResearchJob['activity']
): ResearchProfile {
  const byId = new Map(findings.map((f) => [f.finding_id, f]))
  const sourced = (items: ProfileItem[]): Entry[] =>
    items.map((x) => {
      const urls = x.refs.map((r) => p.sources[r]?.url ?? byId.get(r)?.url).filter((u): u is string => !!u)
      return { title: x.text, detail: [...new Set(urls.map(host))].slice(0, 3).join(' · ') }
    })
  const text = [...p.identity, ...p.credentials].map((x) => x.text).join(' ')
  const basedIn = text.match(/Based in ([A-Z][\w .]+, [A-Z]{2})/)?.[1]
  const post = p.credentials.map((x) => x.text.match(/\b(MD|DO|PhD|PE|CPA)(, [A-Z]{2,5})*/)?.[0]).find(Boolean)
  const toolDetail = (tool: string) => activity.find((a) => a.tool === tool && a.status === 'done')?.detail

  const cases: ExpertCase[] = p.cases
    .map((x) => {
      const f = x.refs.map((r) => byId.get(r)).find((f) => f?.kind === 'court_docket')
      const d = (f?.facts ?? {}) as Record<string, string> & { match?: { role?: string } }
      return {
        year: +(d.date_filed ?? '').slice(0, 4) || +(x.text.match(/\b(19|20)\d{2}\b/)?.[0] ?? 0),
        name: d.case_name || x.text.split(',')[0],
        description: [(d.nature_of_suit ?? '').replace(/^\d+\s*/, ''), d.docket_number].filter(Boolean).join(' · '),
        court: (d.court ?? '').replace('District Court, ', ''),
        side: 'unknown' as const,
        involvement: ROLE[d.match?.role ?? ''] ?? 'Mentioned',
      }
    })
    .sort((a, b) => b.year - a.year)

  type Work = { year?: number; venue?: string; cited_by_count?: number }
  const works = findings
    .filter((f) => f.kind === 'work')
    .map((f) => ({ f, x: f.facts as Work }))
    .sort((a, b) => (b.x.year ?? 0) - (a.x.year ?? 0))
  const author = findings.find((f) => f.kind === 'author_stats')?.facts as
    | { works_count: number; cited_by_count: number; h_index: number }
    | undefined

  const risk: Fact[] = [
    p.red_flags.length
      ? { label: 'Criticized or excluded testimony', value: `${p.red_flags.length} found`, flag: true,
          details: p.red_flags.map((x) => {
            const urls = x.refs.map((r) => p.sources[r]?.url ?? byId.get(r)?.url).filter((u): u is string => !!u)
            return { title: x.text, detail: [...new Set(urls.map(host))].join(', '), url: urls[0] }
          }) }
      : { label: 'Criticized or excluded testimony', value: 'None found', details: [] },
  ]
  const exclusion = toolDetail('exclusion_check')
  if (exclusion)
    risk.push(/^0 /.test(exclusion)
      ? { label: 'Federal exclusion list (OIG LEIE)', value: 'None found', details: [] }
      : { label: 'Federal exclusion list (OIG LEIE)', value: exclusion, flag: true })
  const retraction = toolDetail('retraction_check')
  if (retraction)
    risk.push(/\b0 retractions/.test(retraction)
      ? { label: 'Retracted publications', value: 'None found', details: [] }
      : { label: 'Retracted publications', value: retraction, flag: true })

  // Key facts: credentials grouped by kind, one row each.
  const groups = new Map<string, string[]>()
  for (const x of p.credentials) {
    const label = credLabel(x.text)
    groups.set(label, [...(groups.get(label) ?? []), tidy(x.text)])
  }
  const highlights: Fact[] = [...groups]
    .map(([label, values]) => ({ label, value: values.join('; ') }))
    .filter((h) => h.value !== post) // already the title
  if (author)
    highlights.push({ label: 'Publications', value: `${author.works_count} works, ${author.cited_by_count} citations` })

  const hosts = new Map<string, number>()
  for (const f of findings) hosts.set(host(f.url), (hosts.get(host(f.url)) ?? 0) + 1)

  return {
    name,
    initials: initialsOf(name),
    title: post ?? '',
    contact: basedIn ? [{ label: 'Based in', value: basedIn }] : [],
    tags: [],
    qualifications: p.expertise.map((x) => x.text).join(' '),
    highlights,
    cases,
    daubertChallenges: 0,
    documents: [],
    risk,
    publications: works.map(({ x, f }) => ({
      title: f.title,
      detail: [x.year, x.venue, x.cited_by_count ? `cited ${x.cited_by_count}×` : ''].filter(Boolean).join(' · '),
      url: f.url,
    })),
    news: [],
    social: [],
    online: [],
    career: [],
    education: [],
    professional: [],
    sources: [...hosts].sort((a, b) => b[1] - a[1]).map(([h]) => h),
    identity: sourced(p.identity),
    credentials: sourced(p.credentials),
    notHim: sourced(p.not_him),
    authorStats: author ? `${author.works_count} works · ${author.cited_by_count} citations · h-index ${author.h_index}` : null,
  }
}
