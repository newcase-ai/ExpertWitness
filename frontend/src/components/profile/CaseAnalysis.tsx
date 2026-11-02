import { Link } from 'react-router-dom'
import { FileText } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { caseContexts, DOC_TYPES, ROLE_LABEL } from '@/lib/case-context'
import { ExpertProfile } from '@/lib/types'
import { cn } from '@/lib/utils'
import { CARD } from '@/components/profile/common'

// Before the user adds context: what they get and what to add. After: what they added.
// ponytail: the analysis itself (re-analysis progress and insights) is the next step to build.
export function CaseAnalysis({ p, demoId }: { p: ExpertProfile; demoId: string }) {
  const context = caseContexts.get(demoId)
  const shortName = `Dr. ${p.name.split(' ').at(-1)}`
  const contextLink = (
    <Button asChild className="self-start">
      <Link to={`/experts/${demoId}/context`}>
        {context ? 'Edit case context' : 'Add case context'}
      </Link>
    </Button>
  )

  if (context)
    return (
      <section className={cn(CARD, 'space-y-4 text-sm')}>
        <div className="space-y-1">
          <h2 className="text-base font-semibold">Case context</h2>
          <p className="text-muted-foreground">
            {context.case.name} · {ROLE_LABEL[context.role]}
          </p>
        </div>
        {context.testimony && <p className="leading-relaxed">{context.testimony}</p>}
        <ul className="divide-y rounded-lg border">
          {context.docs.map((d, i) => (
            <li key={i} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3.5 py-2.5">
              <FileText className="size-4 text-primary" />
              <span className="min-w-0 flex-1 truncate font-medium">{d.name}</span>
              <span className="text-[13px] text-muted-foreground">
                {DOC_TYPES.find((t) => t.id === d.type)?.label ?? 'Other'}
              </span>
            </li>
          ))}
        </ul>
        {context.notes && <p className="text-muted-foreground">{context.notes}</p>}
        <p className="rounded-lg bg-[#F4F5FC] px-3.5 py-3 text-[#1C2766]">
          The analysis of these documents against {shortName}'s record will appear here.
        </p>
        {contextLink}
      </section>
    )

  return (
    <div className="space-y-4 text-sm">
      <section className={cn(CARD, 'flex flex-col gap-5 p-7')}>
        <div className="max-w-[640px] space-y-1.5">
          <h2 className="font-serif text-2xl font-semibold">
            Turn this profile into a case strategy
          </h2>
          <p className="text-[15px] leading-relaxed">
            Share what you have on {shortName} in your case. We'll read it against everything in the
            public record and show where the expert is vulnerable, or needs preparing.
          </p>
        </div>
        <div className="grid gap-3 @3xl:grid-cols-2">
          <div className="space-y-1.5 rounded-lg bg-[#F7F8FA] p-4">
            <div className="font-semibold">If they're the other side's expert</div>
            <p className="leading-normal">
              Impeachment points, contradictions with prior testimony, Daubert grounds, bias and
              conflicts, cross-examination questions.
            </p>
          </div>
          <div className="space-y-1.5 rounded-lg bg-[#F7F8FA] p-4">
            <div className="font-semibold">If they're your expert</div>
            <p className="leading-normal">
              Weak spots opposing counsel will find, likely cross questions with suggested answers,
              gaps in the report, a prep checklist.
            </p>
          </div>
        </div>
        {contextLink}
      </section>
      <section className="space-y-3">
        <div>
          <h2 className="text-base font-semibold">What you can add</h2>
          <p className="text-muted-foreground">
            Anything helps. The highlighted ones give the most.
          </p>
        </div>
        <div className="grid gap-2.5 @xl:grid-cols-2 @4xl:grid-cols-3">
          {DOC_TYPES.map((t) => (
            <div
              key={t.id}
              className={cn(
                'space-y-1 rounded-lg border bg-card px-4 py-3.5',
                'key' in t && 'border-[#C9CEEA]'
              )}
            >
              <div className="font-semibold">{t.label}</div>
              <div className="text-[13px] leading-normal text-muted-foreground">{t.hint}</div>
            </div>
          ))}
        </div>
      </section>
    </div>
  )
}
