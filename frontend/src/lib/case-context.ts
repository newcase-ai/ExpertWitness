import { Case, ExpertRole } from './cases-mock'

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

const MB = 1e6
// What the Add case context form starts with for the demo experts, so the demo isn't empty.
export const MOCK_DRAFTS: Record<
  string,
  Pick<CaseContext, 'role' | 'testimony' | 'docs'> & { caseId: string }
> = {
  'demo-1': {
    role: 'opposing',
    caseId: 'reyes',
    testimony:
      'Defense says the failed L4–L5 fusion was caused by pre-existing degenerative disc disease, not by the screw placement.',
    docs: [
      { name: 'Chen_Expert_Report_Reyes.pdf', size: 2.4 * MB, type: 'report' },
      { name: 'Chen_Depo_2026-09-14.pdf', size: 5.1 * MB, type: 'deposition' },
      { name: 'Reyes_Complaint.pdf', size: 0.6 * MB, type: 'complaint' },
      { name: 'Reyes_Imaging_2024-2025.pdf', size: 8.3 * MB, type: 'records' },
    ],
  },
  'demo-2': {
    role: 'opposing',
    caseId: 'hale',
    testimony:
      'Defense says the driver could not have stopped in time; the pedestrian entered the crosswalk against the signal.',
    docs: [
      { name: 'Alvarez_Reconstruction_Report_Hale.pdf', size: 3.2 * MB, type: 'report' },
      { name: 'Alvarez_Depo_2026-08-21.pdf', size: 4.4 * MB, type: 'deposition' },
      { name: 'Hale_Complaint.pdf', size: 0.5 * MB, type: 'complaint' },
      { name: 'Hale_Vehicle_EDR_Download.pdf', size: 1.9 * MB, type: 'records' },
    ],
  },
}
