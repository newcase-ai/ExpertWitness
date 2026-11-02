import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import {
  ArrowRight,
  Check,
  ChevronRight,
  FileText,
  Highlighter,
  Loader2,
  Sparkles,
} from 'lucide-react'
import ChatInput from '@/components/ChatInput'
import { Sheet, SheetContent, SheetDescription, SheetTitle } from '@/components/ui/sheet'
import { cn } from '@/lib/utils'
import {
  findInvestigation,
  INVESTIGATIONS,
  Investigation,
  InvestigationTopic,
} from '@/lib/expert-investigate-mock'

const STEP_MS = 850
const TOPICS: InvestigationTopic[] = ['Credibility', 'Testimony', 'Fees & practice']

interface Message {
  id: number
  question: string
  investigation?: Investigation
  stepsDone: number
}

// A fake AI investigation over the mock profile. Steps tick by on a timer,
// then the canned answer shows. State lives here, outside the Sheet, so closing the sheet (to
// look at highlights) keeps the thread and lets a running investigation finish.
export default function ExpertInvestigate({
  demoId,
  name,
  open,
  onOpenChange,
  onHighlight,
}: {
  demoId: string
  name: string
  open: boolean
  onOpenChange: (open: boolean) => void
  onHighlight: (investigation: Investigation) => void
}) {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [showAll, setShowAll] = useState(false)
  const endRef = useRef<HTMLDivElement>(null)
  const last = messages.at(-1)
  const running = !!last?.investigation && last.stepsDone < last.investigation.steps.length

  useEffect(() => {
    if (!running) return
    const t = setTimeout(
      () =>
        setMessages((ms) =>
          ms.map((m, i) => (i === ms.length - 1 ? { ...m, stepsDone: m.stepsDone + 1 } : m))
        ),
      STEP_MS
    )
    return () => clearTimeout(t)
  }, [running, last?.stepsDone])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages.length, last?.stepsDone, open])

  const ask = (question: string) => {
    if (!question.trim() || running) return
    setMessages((ms) => [
      ...ms,
      {
        id: Date.now(),
        question: question.trim(),
        investigation: findInvestigation(demoId, question),
        stepsDone: 0,
      },
    ])
    setInput('')
    setShowAll(false)
  }

  const questions = INVESTIGATIONS[demoId] ?? []
  const asked = new Set(messages.map((m) => m.investigation?.question))
  const unasked = questions.filter((q) => !asked.has(q.question))

  return (
    <>
      <Sheet open={open} onOpenChange={onOpenChange}>
        <SheetContent
          aria-describedby={undefined}
          className="flex w-[580px] flex-col gap-0 p-0 sm:max-w-[580px]"
        >
          {messages.length === 0 ? (
            <div className="flex-1 space-y-6 overflow-y-auto px-7 pb-6 pt-12">
              <div className="space-y-3.5">
                <span className="flex size-11 items-center justify-center rounded-xl bg-primary text-primary-foreground shadow-md shadow-primary/30">
                  <Sparkles className="size-5" />
                </span>
                <SheetTitle className="text-[22px] leading-snug">
                  What do you want to know about {name}?
                </SheetTitle>
              </div>
              <QuestionList questions={questions} onAsk={ask} />
            </div>
          ) : (
            <>
              <header className="flex items-center gap-3 border-b px-5 py-4 pr-12">
                <span className="flex size-8 items-center justify-center rounded-lg bg-primary/10 text-primary">
                  <Sparkles className="size-4" />
                </span>
                <div>
                  <SheetTitle className="text-base">Expert intel · {name}</SheetTitle>
                  <SheetDescription className="text-xs">
                    Answers from the public records in this profile
                  </SheetDescription>
                </div>
              </header>
              <div className="flex-1 space-y-5 overflow-y-auto p-5">
                {messages.map((m) => (
                  <MessageView key={m.id} message={m} onHighlight={onHighlight} />
                ))}
                {!running && unasked.length > 0 && (
                  <div className="space-y-2 border-t pt-4">
                    <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-primary">
                      {showAll ? 'All questions' : 'Suggested next'}
                    </div>
                    {showAll ? (
                      <QuestionList questions={unasked} onAsk={ask} />
                    ) : (
                      <div className="flex flex-col items-start gap-1.5">
                        {unasked.slice(0, 3).map((q) => (
                          <button
                            key={q.question}
                            type="button"
                            onClick={() => ask(q.question)}
                            className="rounded-lg border px-3 py-2 text-left text-[13px] transition-colors hover:border-primary/40 hover:bg-accent"
                          >
                            {q.question}
                          </button>
                        ))}
                      </div>
                    )}
                    {unasked.length > 3 && (
                      <button
                        type="button"
                        onClick={() => setShowAll((v) => !v)}
                        className="text-[13px] font-medium text-primary hover:underline"
                      >
                        {showAll ? 'Show fewer' : `See all ${unasked.length} questions`}
                      </button>
                    )}
                  </div>
                )}
                <div ref={endRef} />
              </div>
            </>
          )}
          <div className="border-t p-4">
            <ChatInput
              value={input}
              onChange={setInput}
              onSend={() => ask(input)}
              disabled={running}
              loading={running}
              placeholder={messages.length ? 'Ask another question…' : 'Ask your own question…'}
            />
          </div>
        </SheetContent>
      </Sheet>

      {!open && (
        <button
          type="button"
          onClick={() => onOpenChange(true)}
          aria-label={
            messages.length
              ? `Open Expert intel, ${messages.length} questions`
              : 'Open Expert intel'
          }
          className="fixed right-0 top-1/2 z-40 flex -translate-y-1/2 flex-col items-center gap-3 rounded-l-xl border border-r-0 bg-background px-2.5 py-3.5 shadow-lg transition-colors animate-in slide-in-from-right hover:bg-muted"
        >
          <span className="flex size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
            {running ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Sparkles className="size-4" />
            )}
          </span>
          <span className="rotate-180 text-[13px] font-semibold [writing-mode:vertical-rl]">
            Expert intel
          </span>
          {messages.length > 0 && (
            <span className="flex size-5 items-center justify-center rounded-full bg-primary/10 text-[11px] font-bold text-primary">
              {messages.length}
            </span>
          )}
        </button>
      )}
    </>
  )
}

function QuestionList({
  questions,
  onAsk,
}: {
  questions: Investigation[]
  onAsk: (q: string) => void
}) {
  return (
    <div className="space-y-5">
      {TOPICS.map((topic) => {
        const inTopic = questions.filter((q) => q.topic === topic)
        if (!inTopic.length) return null
        return (
          <div key={topic}>
            <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-primary">
              {topic}
            </div>
            {inTopic.map((q) => (
              <button
                key={q.question}
                type="button"
                onClick={() => onAsk(q.question)}
                className="group flex w-full items-center justify-between gap-3 border-b py-2.5 text-left text-sm last:border-b-0 hover:text-primary"
              >
                {q.question}
                <ArrowRight className="size-4 shrink-0 text-muted-foreground/60 transition-transform group-hover:translate-x-0.5 group-hover:text-primary" />
              </button>
            ))}
          </div>
        )
      })}
    </div>
  )
}

function MessageView({
  message: m,
  onHighlight,
}: {
  message: Message
  onHighlight: (i: Investigation) => void
}) {
  const inv = m.investigation
  const done = !inv || m.stepsDone >= inv.steps.length

  return (
    <div className="space-y-3">
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-tr-none border bg-muted/80 px-4 py-2.5 text-sm leading-relaxed shadow-sm">
          {m.question}
        </div>
      </div>

      {!inv ? (
        <p className="text-sm leading-relaxed text-muted-foreground">
          In this demo I can answer from this profile’s research. Try one of the suggested questions
          below.
        </p>
      ) : !done ? (
        <div className="space-y-2.5 rounded-xl border bg-muted/20 px-4 py-3.5">
          <div className="animate-pulse text-xs font-semibold text-primary">Investigating…</div>
          <Steps steps={inv.steps} done={m.stepsDone} />
        </div>
      ) : (
        <div className="space-y-3 duration-500 animate-in fade-in slide-in-from-bottom-1">
          <details className="group text-xs text-muted-foreground">
            <summary className="flex cursor-pointer list-none items-center gap-1.5 hover:text-foreground">
              <Check className="size-3.5 text-green-700" strokeWidth={2.5} />
              Investigated {inv.steps.length} steps
              <ChevronRight className="size-3.5 transition-transform group-open:rotate-90" />
            </summary>
            <div className="mt-2.5 pl-1">
              <Steps steps={inv.steps} done={inv.steps.length} />
            </div>
          </details>
          <div className="prose prose-sm max-w-none text-foreground prose-p:my-2 prose-strong:text-foreground prose-ul:my-2 prose-li:my-1">
            <ReactMarkdown>{inv.answer}</ReactMarkdown>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {inv.sources.map((s) => (
              <span
                key={s}
                className="flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs text-muted-foreground"
              >
                <FileText className="size-3 text-primary" />
                {s}
              </span>
            ))}
          </div>
          <button
            type="button"
            onClick={() => onHighlight(inv)}
            className="flex h-9 items-center gap-2 rounded-lg border border-primary px-3 text-[13px] font-medium text-primary transition-colors hover:bg-primary hover:text-primary-foreground"
          >
            <Highlighter className="size-4" />
            Highlight in profile
            <span className="flex size-[18px] items-center justify-center rounded-full bg-primary/10 text-[11px] font-bold">
              {inv.highlights.length}
            </span>
          </button>
        </div>
      )}
    </div>
  )
}

function Steps({ steps, done }: { steps: string[]; done: number }) {
  return (
    <ol className="space-y-2.5">
      {steps.map((s, i) => (
        <li
          key={s}
          className={cn(
            'flex items-center gap-2.5 text-[13px]',
            i < done
              ? 'text-muted-foreground'
              : i === done
                ? 'font-medium'
                : 'text-muted-foreground/60'
          )}
        >
          {i < done ? (
            <Check className="size-4 shrink-0 text-green-700" strokeWidth={2.5} />
          ) : i === done ? (
            <Loader2 className="size-4 shrink-0 animate-spin text-primary" />
          ) : (
            <span className="m-0.5 size-3 shrink-0 rounded-full border-[1.5px] border-muted-foreground/30" />
          )}
          {s}
        </li>
      ))}
    </ol>
  )
}
