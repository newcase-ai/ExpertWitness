import { createContext, useEffect, useState } from 'react'

// One step per research area, in the order they fill in. Each tab shows once its area is done.
export const RESEARCH_STEPS = [
  { section: 'qualifications', label: 'Reading CV and bio' },
  { section: 'litigation', label: 'Searching court records and transcripts' },
  { section: 'risk', label: 'Checking licensing boards, background and media' },
  { section: 'professional', label: 'Building career and education history' },
]
const STEP_MS = 2500
const researched = new Set<string>() // ponytail: in memory, so a reload researches again
export const ReadyContext = createContext<(section: string) => boolean>(() => true)

export function useResearch(demoId: string) {
  const [done, setDone] = useState(researched.has(demoId) ? RESEARCH_STEPS.length : 0)
  const finished = done >= RESEARCH_STEPS.length
  useEffect(() => {
    if (finished) return void researched.add(demoId)
    const t = setTimeout(() => setDone((d) => d + 1), STEP_MS)
    return () => clearTimeout(t)
  }, [done, finished, demoId])
  const ready = (section: string) => RESEARCH_STEPS.findIndex((s) => s.section === section) < done
  return { done, finished, ready }
}
