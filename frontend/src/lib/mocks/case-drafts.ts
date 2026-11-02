import { CaseContext } from '@/lib/case-context'

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
