import type { ExpertProfileView, RiskCheck, Sourced } from '@/lib/api'
import type { Entry, Fact, Involvement } from '@/lib/types'
import { host, initialsOf, type ResearchProfile } from '@/lib/research-profile'

// The backend's structured profile (GET /dossiers/{id}/expert-profile) in the shape the product's
// profile components take. Only what the searcher needs: no confidence, conflicts or gaps.

const INVOLVEMENT: Record<string, Involvement> = {
  'expert witness': 'Expert witness',
  'consultant or reviewer': 'Consultant or reviewer',
  party: 'Party',
}
const ONLINE_KIND = {
  cv: 'CV',
  practice_site: 'Practice website',
  expert_directory: 'Expert directory',
  physician_directory: 'Physician directory',
  other: 'Web page',
}
const RISK_LABEL = {
  license_status: 'License status',
  board_discipline: 'Board discipline',
  exclusion_lists: 'Federal exclusion list (OIG LEIE)',
  retractions: 'Retracted publications',
}

export const RED_FLAGS = 'Things opposing counsel could use'

const join = (parts: (string | number | false | null | undefined)[], sep = ' · ') => parts.filter(Boolean).join(sep)
const selfReported = (x: { source_kind?: string | null }) => (x.source_kind === 'self_reported' ? ' (self-reported)' : '')
const values = (xs: Sourced[]) => xs.map((x) => x.value + selfReported(x)).join('; ')

// A flagged item in a popup: its text, the sites it came from, and a link to the first one.
function sourcedEntry(x: Sourced, urlOf: Map<string, string>, detail = ''): Entry {
  const urls = x.refs.map((r) => urlOf.get(r)).filter((u): u is string => !!u)
  return { title: x.value, detail: join([detail, [...new Set(urls.map(host))].join(', ')]), url: urls[0] }
}

function riskFact(label: string, r: RiskCheck, urlOf: Map<string, string>): Fact {
  if (r.status === 'flagged')
    return { label, value: r.summary, flag: true, details: r.items.map((x) => sourcedEntry(x, urlOf, x.note ?? '')) }
  if (r.status === 'clear')
    return { label, value: /^No(ne)? /.test(r.summary) ? r.summary : `None found: ${r.summary}`, details: [] }
  return { label, value: 'Not checked', details: [] }
}

export function fromExpertProfile(v: ExpertProfileView, fallbackName: string): ResearchProfile {
  const name = v.name || fallbackName
  const urlOf = new Map(v.sources.map((s) => [s.ref, s.url]))
  const linksOf = (refs: string[]) => refs.map((r) => urlOf.get(r)).filter((u): u is string => !!u)
  const c = v.contact
  const contact: Fact[] = [
    c.based_in && { label: 'Based in', value: c.based_in.value, links: linksOf(c.based_in.refs) },
    c.current_role && { label: 'Currently', value: join([c.current_role.title, c.current_role.org], ', '),
      links: linksOf(c.current_role.refs) },
    c.business_address && { label: 'Business address', value: c.business_address.value, links: linksOf(c.business_address.refs) },
    c.phone && { label: 'Phone', value: join([c.phone.value, c.phone.note], ' · '), links: linksOf(c.phone.refs) },
    c.email && { label: 'Email', value: c.email.value, links: linksOf(c.email.refs) },
    c.website && { label: 'Website', value: c.website.value, links: linksOf(c.website.refs) },
  ].filter(Boolean) as Fact[]

  const highlights: Fact[] = v.highlights.map((h) => ({ label: h.label, value: h.value + selfReported(h), links: linksOf(h.refs) }))
  const stats = v.publications.stats
  if (stats && !highlights.some((h) => h.label === 'Publications'))
    highlights.push({ label: 'Publications', value: `${stats.works} works, ${stats.citations} citations` })

  // Court filings grouped by case: one row per case, its filings in the popup.
  const byCase = new Map<string, Entry[]>()
  for (const d of v.documents.items) {
    const key = d.case_name ?? d.title
    byCase.set(key, [...(byCase.get(key) ?? []), { title: d.title, detail: join([d.date, d.page_count && `${d.page_count} pages`]), url: d.url }])
  }
  const documents: Fact[] = [...byCase].map(([label, details]) => ({
    label,
    value: `${details.length} filing${details.length === 1 ? '' : 's'}`,
    details,
  }))

  const risk: Fact[] = Object.entries(RISK_LABEL).map(([k, label]) => riskFact(label, v.risk[k as keyof typeof RISK_LABEL], urlOf))
  if (v.red_flags.status !== 'not_checked')
    risk.unshift(
      v.red_flags.items.length
        ? { label: RED_FLAGS, value: `${v.red_flags.items.length} found`, flag: true,
            details: v.red_flags.items.map((x) => ({ ...sourcedEntry(x, urlOf), why: x.note ?? undefined })) }
        : { label: RED_FLAGS, value: 'None found', details: [] }
    )

  const credentials: Entry[] = [
    ...v.education.items.map((e) => ({ title: join([e.degree, e.field], ', '), detail: join([e.institution, e.year]), url: linksOf(e.refs)[0] })),
    ...v.licenses.items.map((l) => ({
      title: join([l.state, l.kind, !/licen[cs]e|certif/i.test(l.kind) && 'license'], ' '),
      detail: join([l.number && `No. ${l.number}`, l.status !== 'unknown' && l.status, l.since && `since ${l.since}`]),
      url: linksOf(l.refs)[0],
    })),
    ...v.board_certifications.items.map((b) => ({
      title: `Board certified: ${b.specialty}`,
      detail: join([b.board, b.since && `since ${b.since}`]),
      url: linksOf(b.refs)[0],
    })),
  ]
  const allLinks = (xs: { refs: string[] }[]) => linksOf(xs.flatMap((x) => x.refs))
  const professional: Fact[] = [
    v.affiliations.items.length && { label: 'Affiliations', value: values(v.affiliations.items), links: allLinks(v.affiliations.items) },
    v.memberships.items.length && { label: 'Memberships', value: values(v.memberships.items), links: allLinks(v.memberships.items) },
    v.fees.items.length && { label: 'Fees', value: v.fees.items.map((f) => `${f.amount} ${f.service}`).join('; '), links: allLinks(v.fees.items) },
    v.testimony_summary.items.length && { label: 'Expert work', value: values(v.testimony_summary.items), links: allLinks(v.testimony_summary.items) },
  ].filter(Boolean) as Fact[]

  const status = (s: { status: 'found' | 'none_found' | 'not_checked' }[]) =>
    s.some((x) => x.status === 'found') ? 'found' : s.some((x) => x.status === 'none_found') ? 'none_found' : 'not_checked'

  return {
    name,
    initials: initialsOf(name),
    title: v.headline?.value ?? v.specialties.map((s) => s.value).join(', '),
    contact,
    tags: (v.expertise_tags.length ? v.expertise_tags : v.specialties).map((t) => t.value),
    qualifications: v.summary?.value ?? '',
    qualificationsLinks: linksOf(v.summary?.refs ?? []),
    highlights,
    cases: v.cases.items.map((x) => ({
      year: x.year ?? 0,
      name: x.name,
      description: join([(x.nature_of_suit ?? '').replace(/^\d+\s*/, ''), x.docket_number]),
      court: x.court.replace('District Court, ', ''),
      side: x.side,
      involvement: INVOLVEMENT[x.involvement ?? ''] ?? 'Mentioned',
      url: x.url,
      summary: x.summary ?? undefined,
    })),
    daubertChallenges: 0,
    documents,
    risk,
    publications: v.publications.items.map((w) => ({
      title: w.retracted ? `${w.title} (retracted)` : w.title,
      detail: join([w.year, w.venue, w.cited_by ? `cited ${w.cited_by}×` : '', host(w.url)]),
      url: w.url,
    })),
    news: [],
    social: [],
    online: v.online.items.map((o) => ({ title: o.title, detail: join([ONLINE_KIND[o.kind], host(o.url)]), url: o.url })),
    career: v.career.items.map((r) => ({
      title: r.title,
      org: join([r.org, r.location], ', '),
      dates: r.start_year ? `${r.start_year}–${r.current ? 'present' : (r.end_year ?? '')}` : undefined,
      current: r.current,
      links: linksOf(r.refs),
    })),
    education: [],
    professional,
    sources: v.sources.map((s) => `${s.title} (${host(s.url)})`),
    sourceLinks: v.sources.map((s) => ({ title: s.title, detail: host(s.url), url: s.url })),
    identity: [],
    credentials,
    notHim: v.not_him.items.map((x) => ({ title: x.value, detail: '', url: linksOf(x.refs)[0] })),
    authorStats: stats ? join([`${stats.works} works`, `${stats.citations} citations`, stats.h_index != null && `h-index ${stats.h_index}`]) : null,
    status: {
      career: v.career.status,
      credentials: status([v.education, v.licenses, v.board_certifications]),
      cases: v.cases.status,
      publications: v.publications.status,
      online: v.online.status,
    },
  }
}
