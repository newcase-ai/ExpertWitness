// Run: npx tsx src/lib/case-context.check.ts
import { detectType } from './case-context'

const eq = (a: unknown, b: unknown) => {
  if (a !== b) throw new Error(`${a} !== ${b}`)
}

eq(detectType('Chen_Expert_Report_Reyes.pdf'), 'report')
eq(detectType('Chen_Depo_2026-09-14.pdf'), 'deposition')
eq(detectType('Reyes_Complaint.pdf'), 'complaint')
eq(detectType('Reyes_Imaging_2024-2025.pdf'), 'records')
eq(detectType('Chen testimony list 2022-2026.docx'), 'testimony-list')
eq(detectType('Chen_CV.pdf'), 'cv')
eq(detectType('engagement-letter.pdf'), 'engagement')
eq(detectType('photo.jpg'), 'other')
console.log('ok')
