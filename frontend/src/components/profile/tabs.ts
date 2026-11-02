import { ExpertProfile } from '@/lib/types'

export type Tab =
  | 'overview'
  | 'analysis'
  | 'cases'
  | 'records'
  | 'background'
  | 'publications'
  | 'career'
  | 'sources'
export const TABS: { id: Tab; label: string; section: string }[] = [
  { id: 'overview', label: 'Overview', section: 'qualifications' },
  { id: 'analysis', label: 'Case analysis', section: 'professional' }, // once research is done
  { id: 'cases', label: 'Cases and testimony', section: 'litigation' },
  { id: 'records', label: 'Records', section: 'litigation' },
  { id: 'background', label: 'Background', section: 'risk' },
  { id: 'publications', label: 'Publications and media', section: 'risk' },
  { id: 'career', label: 'Career', section: 'professional' },
  { id: 'sources', label: 'Sources', section: 'professional' },
]

// Highlight targets are `${section}:${label}`; two tabs share a section, so look the label up.
export function tabOf(p: ExpertProfile, target: string): Tab {
  const [section, label] = [
    target.slice(0, target.indexOf(':')),
    target.slice(target.indexOf(':') + 1),
  ]
  if (section === 'qualifications') return 'overview'
  if (section === 'litigation') return p.cases.some((c) => c.name === label) ? 'cases' : 'records'
  if (section === 'risk')
    return p.risk.some((f) => f.label === label) ? 'background' : 'publications'
  return 'career'
}
