export interface Case {
  id: string
  name: string
  type: string
  court: string
  client: 'plaintiff' | 'defendant' // the side the user's firm represents
}

export type ExpertRole = 'opposing' | 'ours'

export type Side = 'plaintiff' | 'defense' | 'unknown'
export type Involvement =
  | 'Deposition'
  | 'Trial testimony'
  | 'Mentioned'
  | 'Expert witness'
  | 'Consultant or reviewer'

export interface ExpertCase {
  year: number
  name: string
  description: string
  court: string
  side: Side
  involvement: Involvement
  url?: string // the case's CourtListener page, when it came from court records
}

export interface Fact {
  label: string
  value: string
  flag?: boolean // something opposing counsel could use
  details?: Entry[] // shown in the popup; empty means nothing was found
}

export interface Entry {
  title: string
  detail: string
}

export interface Role {
  title: string
  org: string
  dates?: string
  current?: boolean
}

export interface ExpertProfile {
  name: string
  initials: string
  title: string
  contact: Fact[]
  tags: string[]
  qualifications: string
  highlights: Fact[]
  cases: ExpertCase[]
  daubertChallenges: number
  documents: Fact[]
  risk: Fact[]
  publications: Entry[]
  news: Entry[]
  social: Entry[]
  online: Entry[]
  career: Role[]
  education: Entry[]
  professional: Fact[]
  sources: string[]
}

export type InvestigationTopic = 'Credibility' | 'Testimony' | 'Fees & practice'

export interface Investigation {
  question: string
  topic: InvestigationTopic
  /** Free-typed questions match the investigation with the most of these (lowercase) in them. */
  keywords: string[]
  steps: string[]
  answer: string // markdown
  sources: string[]
  highlights: { target: string; note: string }[]
}
