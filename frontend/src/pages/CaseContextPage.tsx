// "Add case context": the expert's role, the case, and the user's documents about the expert.
// Analyze saves it and returns to the profile's Case analysis tab.
import { useState } from 'react'
import { Link, Navigate, useNavigate, useParams } from 'react-router-dom'
import { Check, ChevronLeft, Upload, X } from 'lucide-react'
import AppHeader from '@/components/AppHeader'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { Case, ExpertRole } from '@/lib/types'
import { CASES } from '@/lib/mocks/cases'
import { DEMO_PROFILES } from '@/lib/mocks/expert-profile'
import { MOCK_DRAFTS } from '@/lib/mocks/case-drafts'
import { caseContexts, ContextDoc, detectType, DOC_TYPES, DocType } from '@/lib/case-context'

const ROLES: { id: ExpertRole; label: string; note: string }[] = [
  { id: 'opposing', label: "The other side's expert", note: 'Challenge, impeach or disqualify' },
  { id: 'ours', label: 'Our expert', note: 'Prepare for deposition and trial' },
]

// Each section is a fieldset whose legend floats so it can sit inside the card; everything after it
// must clear the float or it renders beside the legend.
const SECTION = 'space-y-3 rounded-xl border bg-card p-5 [&>*:not(legend)]:clear-both'
const FIELD = 'rounded-lg border border-input bg-card px-3 py-2 outline-none focus:border-primary'

// ponytail: adds to the in-memory mock list; becomes a POST once the backend has cases.
function createCase(c: Omit<Case, 'id'>): Case {
  const created = { ...c, name: c.name.trim(), id: `case-${Date.now()}` }
  CASES.push(created)
  return created
}

const size = (bytes: number) =>
  bytes < 1e6 ? `${Math.max(1, Math.round(bytes / 1e3))} KB` : `${(bytes / 1e6).toFixed(1)} MB`

export default function CaseContextPage() {
  const { expertId = '' } = useParams()
  const navigate = useNavigate()
  const p = DEMO_PROFILES[expertId]
  const saved = caseContexts.get(expertId)
  const draft = MOCK_DRAFTS[expertId]
  const [role, setRole] = useState<ExpertRole>(saved?.role ?? draft?.role ?? 'opposing')
  const [caseId, setCaseId] = useState(saved?.case.id ?? draft?.caseId ?? CASES[0].id)
  // caseId 'new': the user is creating the case here because it isn't in the system yet
  const [newCase, setNewCase] = useState<Omit<Case, 'id'>>({
    name: '',
    type: '',
    court: '',
    client: 'plaintiff',
  })
  const [testimony, setTestimony] = useState(saved?.testimony ?? draft?.testimony ?? '')
  const [notes, setNotes] = useState(saved?.notes ?? '')
  const [docs, setDocs] = useState<ContextDoc[]>(saved?.docs ?? draft?.docs ?? [])
  const [dragging, setDragging] = useState(false)

  if (!p) return <Navigate to="/" replace />
  const shortName = `Dr. ${p.name.split(' ').at(-1)}`
  const types = new Set(docs.map((d) => d.type))
  const addFiles = (files: FileList | null) =>
    setDocs((ds) => [
      ...ds,
      ...[...(files ?? [])].map((file) => ({
        name: file.name,
        size: file.size,
        file,
        type: detectType(file.name),
      })),
    ])

  const analyze = (e: React.FormEvent) => {
    e.preventDefault()
    caseContexts.set(expertId, {
      role,
      case: caseId === 'new' ? createCase(newCase) : CASES.find((c) => c.id === caseId)!,
      testimony: testimony.trim(),
      notes: notes.trim(),
      docs,
    })
    navigate(`/experts/${expertId}?tab=analysis`, { replace: true })
  }

  return (
    <div className="min-h-screen text-sm">
      <AppHeader />
      <main className="mx-auto flex max-w-[1120px] flex-col gap-5 px-4 pb-12 pt-6">
        <div className="space-y-1">
          <Link
            to={`/experts/${expertId}`}
            className="flex items-center gap-1 text-[13px] font-medium text-primary hover:underline"
          >
            <ChevronLeft className="size-4" /> {p.name}
          </Link>
          <h1 className="text-[22px] font-semibold">Add case context</h1>
          <p className="text-muted-foreground">Everything here stays private to your firm.</p>
        </div>

        <div className="flex flex-wrap items-start gap-5">
          <form onSubmit={analyze} className="flex min-w-0 flex-[999_1_560px] flex-col gap-4">
            <fieldset className={SECTION}>
              <legend className="float-left w-full pb-1 text-[15px] font-semibold">
                1. {shortName}'s role in your case
              </legend>
              <div className="grid gap-2 sm:grid-cols-2">
                {ROLES.map((r) => (
                  <label
                    key={r.id}
                    className={cn(
                      'cursor-pointer space-y-0.5 rounded-lg border px-3.5 py-3',
                      role === r.id && 'border-primary bg-[#F4F5FC]'
                    )}
                  >
                    <span className="flex items-center gap-2 font-semibold">
                      <input
                        type="radio"
                        name="role"
                        checked={role === r.id}
                        onChange={() => setRole(r.id)}
                        className="accent-primary"
                      />
                      {r.label}
                    </span>
                    <span className="block pl-5 text-[13px] text-muted-foreground">{r.note}</span>
                  </label>
                ))}
              </div>
            </fieldset>

            <fieldset className={SECTION}>
              <legend className="float-left w-full pb-1 text-[15px] font-semibold">
                2. The case
              </legend>
              <label className="flex flex-col gap-1.5">
                <span className="text-[13px] font-medium text-muted-foreground">Case</span>
                <select
                  value={caseId}
                  onChange={(e) => setCaseId(e.target.value)}
                  className={cn(FIELD, 'h-9 py-0')}
                >
                  {CASES.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name} · we represent the {c.client}
                    </option>
                  ))}
                  <option value="new">+ New case</option>
                </select>
              </label>
              {caseId === 'new' && (
                <div className="grid gap-3 rounded-lg bg-[#F7F8FA] p-4 sm:grid-cols-2">
                  <label className="flex flex-col gap-1.5 sm:col-span-2">
                    <span className="text-[13px] font-medium">Case name</span>
                    <input
                      required
                      autoFocus
                      value={newCase.name}
                      onChange={(e) => setNewCase({ ...newCase, name: e.target.value })}
                      placeholder="e.g. Reyes v. Mid-Ohio Regional Medical Center"
                      className={cn(FIELD, 'h-9')}
                    />
                  </label>
                  <fieldset className="sm:col-span-2">
                    <legend className="pb-1.5 text-[13px] font-medium">We represent the</legend>
                    <div className="flex gap-5">
                      {(['plaintiff', 'defendant'] as const).map((side) => (
                        <label key={side} className="flex items-center gap-2 capitalize">
                          <input
                            type="radio"
                            name="client"
                            checked={newCase.client === side}
                            onChange={() => setNewCase({ ...newCase, client: side })}
                            className="accent-primary"
                          />
                          {side}
                        </label>
                      ))}
                    </div>
                  </fieldset>
                  <label className="flex flex-col gap-1.5">
                    <span className="text-[13px] font-medium text-muted-foreground">
                      Case type <span className="font-normal">(optional)</span>
                    </span>
                    <input
                      value={newCase.type}
                      onChange={(e) => setNewCase({ ...newCase, type: e.target.value })}
                      placeholder="e.g. Medical malpractice"
                      className={cn(FIELD, 'h-9')}
                    />
                  </label>
                  <label className="flex flex-col gap-1.5">
                    <span className="text-[13px] font-medium text-muted-foreground">
                      Court <span className="font-normal">(optional)</span>
                    </span>
                    <input
                      value={newCase.court}
                      onChange={(e) => setNewCase({ ...newCase, court: e.target.value })}
                      placeholder="e.g. Franklin County, OH"
                      className={cn(FIELD, 'h-9')}
                    />
                  </label>
                </div>
              )}
              <label className="flex flex-col gap-1.5">
                <span className="text-[13px] font-medium text-muted-foreground">
                  What will {shortName} testify about?{' '}
                  <span className="font-normal">(optional)</span>
                </span>
                <textarea
                  rows={3}
                  value={testimony}
                  onChange={(e) => setTestimony(e.target.value)}
                  placeholder="e.g. Defense says the failed L4–L5 fusion was caused by pre-existing degeneration, not surgical error."
                  className={cn(FIELD, 'resize-y leading-normal')}
                />
              </label>
            </fieldset>

            <fieldset className={SECTION}>
              <legend className="float-left w-full pb-1 text-[15px] font-semibold">
                3. Documents
              </legend>
              <label
                onDragOver={(e) => {
                  e.preventDefault()
                  setDragging(true)
                }}
                onDragLeave={() => setDragging(false)}
                onDrop={(e) => {
                  e.preventDefault()
                  setDragging(false)
                  addFiles(e.dataTransfer.files)
                }}
                className={cn(
                  'flex cursor-pointer flex-col items-center gap-1.5 rounded-lg border-[1.5px] border-dashed border-[#AEB5C4] bg-[#FAFBFC] p-6 text-center focus-within:border-primary',
                  dragging && 'border-primary bg-[#F4F5FC]'
                )}
              >
                <Upload className="size-5 text-primary" />
                <span className="font-semibold">
                  Drop files or <span className="text-primary">browse</span>
                </span>
                <span className="text-[13px] text-muted-foreground">
                  PDF, Word, text or images · we detect what each file is
                </span>
                <input
                  type="file"
                  multiple
                  accept=".pdf,.doc,.docx,.txt,.rtf,image/*"
                  onChange={(e) => {
                    addFiles(e.target.files)
                    e.target.value = '' // so the same file can be picked again after removing it
                  }}
                  className="sr-only"
                />
              </label>

              {docs.length > 0 && (
                <ul className="divide-y rounded-lg border">
                  {docs.map((d, i) => (
                    <li key={i} className="flex flex-wrap items-center gap-x-3 gap-y-2 px-3.5 py-3">
                      <span className="min-w-0 flex-[1_1_240px]">
                        <span className="block truncate font-medium">{d.name}</span>
                        <span className="text-[13px] text-muted-foreground">{size(d.size)}</span>
                      </span>
                      <label className="flex items-center gap-1.5 text-[13px]">
                        <span className="text-muted-foreground">Type</span>
                        <select
                          value={d.type}
                          onChange={(e) =>
                            setDocs((ds) =>
                              ds.map((x, j) =>
                                j === i ? { ...x, type: e.target.value as DocType } : x
                              )
                            )
                          }
                          className="h-8 rounded-md border bg-card px-1.5"
                        >
                          {DOC_TYPES.map((t) => (
                            <option key={t.id} value={t.id}>
                              {t.label}
                            </option>
                          ))}
                          <option value="other">Other</option>
                        </select>
                      </label>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="size-8"
                        aria-label={`Remove ${d.name}`}
                        onClick={() => setDocs((ds) => ds.filter((_, j) => j !== i))}
                      >
                        <X className="size-4" />
                      </Button>
                    </li>
                  ))}
                </ul>
              )}

              <label className="flex flex-col gap-1.5">
                <span className="text-[13px] font-medium text-muted-foreground">
                  Anything else we should know? <span className="font-normal">(optional)</span>
                </span>
                <textarea
                  rows={2}
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  placeholder="e.g. Opposing counsel retained her through a referral from the hospital's insurer."
                  className={cn(FIELD, 'resize-y leading-normal')}
                />
              </label>
            </fieldset>

            <div className="flex flex-wrap items-center justify-end gap-3">
              <span className="text-muted-foreground">
                {docs.length
                  ? `${docs.length} document${docs.length === 1 ? '' : 's'} · ${size(docs.reduce((n, d) => n + d.size, 0))}`
                  : 'Add at least one document to analyze'}
              </span>
              <Button type="submit" disabled={!docs.length} className="h-10 px-5">
                Analyze
              </Button>
            </div>
          </form>

          <aside className="flex max-w-[340px] flex-[1_1_280px] flex-col gap-3 rounded-xl border bg-card p-5">
            <h2 className="text-[15px] font-semibold">Suggested documents</h2>
            <p className="text-[13px] leading-normal text-muted-foreground">
              The more of these you add, the more we can cross-check.
            </p>
            <ul className="flex flex-col gap-2.5">
              {DOC_TYPES.map((t) => {
                const added = types.has(t.id)
                return (
                  <li key={t.id} className="flex gap-2.5">
                    {added ? (
                      <Check
                        className="mt-px size-[18px] shrink-0 text-[#1D7A50]"
                        strokeWidth={2.5}
                      />
                    ) : (
                      <span className="m-0.5 size-3.5 shrink-0 rounded-full border-2 border-[#CDD2DB]" />
                    )}
                    <span className="space-y-0.5">
                      <span className="block">
                        {t.label}
                        <span className="sr-only">{added ? ' (added)' : ''}</span>
                      </span>
                      {!added && (
                        <span className="block text-xs leading-snug text-muted-foreground">
                          {t.hint}
                        </span>
                      )}
                    </span>
                  </li>
                )
              })}
            </ul>
          </aside>
        </div>
      </main>
    </div>
  )
}
