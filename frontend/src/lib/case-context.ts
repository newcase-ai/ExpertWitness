import { Case, ExpertRole } from '@/lib/types'

// The documents we suggest, most useful first. A file's type is one of these ids, or 'other'.
export const DOC_TYPES = [
  {
    id: 'report',
    label: 'Expert report in this case',
    hint: 'Opinions we test against past testimony and publications',
    key: true,
  },
  {
    id: 'deposition',
    label: 'Deposition in this case',
    hint: 'Admissions we compare with the report and prior cases',
    key: true,
  },
  {
    id: 'complaint',
    label: 'Complaint or case summary',
    hint: 'Parties, claims and issues, so we spot conflicts',
    key: true,
  },
  {
    id: 'records',
    label: 'Records the expert relied on',
    hint: 'Medical records, imaging, data: what was missed or misread',
    key: true,
  },
  {
    id: 'testimony-list',
    label: 'Testimony list (last 4 years)',
    hint: 'From the disclosure; we check it for omissions',
  },
  { id: 'cv', label: 'CV from the disclosure', hint: 'Compared with licensing and board records' },
  {
    id: 'engagement',
    label: 'Engagement letter, invoices',
    hint: 'Hours, fees and who referred the expert',
  },
  {
    id: 'prior',
    label: 'Prior transcripts or rulings',
    hint: 'Ones you hold beyond what we found',
  },
  { id: 'other-expert', label: "Other experts' reports", hint: 'Where the experts disagree' },
] as const

export type DocType = (typeof DOC_TYPES)[number]['id'] | 'other'

// Filename guesses; the user can correct the type.
const PATTERNS: [RegExp, DocType][] = [
  [/testimony.?list/i, 'testimony-list'],
  [/depo|transcript/i, 'deposition'],
  [/report|opinion/i, 'report'],
  [/complaint|petition|pleading|summary/i, 'complaint'],
  [/\bcv\b|resume|curriculum/i, 'cv'],
  [/invoice|engagement|retention|fee|billing/i, 'engagement'],
  [/mri|\bct\b|imaging|x-?ray|medical|records/i, 'records'],
  [/ruling|order|daubert|motion/i, 'prior'],
]

export function detectType(fileName: string): DocType {
  const name = fileName.replace(/[_.-]+/g, ' ')
  return PATTERNS.find(([re]) => re.test(name))?.[1] ?? 'other'
}

export interface ContextDoc {
  name: string
  size: number // bytes
  file?: File // missing on mock documents
  type: DocType
}

export interface CaseContext {
  role: ExpertRole
  case: Case
  testimony: string // what the expert will testify about
  notes: string
  docs: ContextDoc[]
}

export const ROLE_LABEL: Record<ExpertRole, string> = {
  opposing: "Other side's expert",
  ours: 'Our expert',
}

// ponytail: in memory, so a reload loses it; move to the backend with the analysis.
export const caseContexts = new Map<string, CaseContext>()
