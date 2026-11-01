import { type ReactNode, useEffect, useMemo, useRef, useState } from 'react'
import {
  Ban,
  Bot,
  Check,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  Flag,
  LoaderCircle,
  MinusCircle,
  Play,
  Search,
  X,
} from 'lucide-react'
import type { TraceEvent } from '@/lib/api'
import { cn } from '@/lib/utils'
import { buildTimeline, clock, duration, isProblem, itemText, type Item, type ToolItem } from './trace'

const KINDS = { model: 'Main agent', tool: 'Tools', finding: 'Results', gap: 'Gaps' } as const
type KindFilter = keyof typeof KINDS

function kindOf(item: Item): KindFilter | 'run' {
  if (item.type === 'tool') return 'tool'
  const k = item.event.kind
  return k === 'model_turn' ? 'model' : k === 'finding' ? 'finding' : k === 'gap' || k === 'tool_error' ? 'gap' : 'run'
}

// The run as it happens: every model turn, tool call (with what happened inside it), finding and gap.
export default function Timeline({ events, running }: { events: TraceEvent[]; running: boolean }) {
  const items = useMemo(() => buildTimeline(events), [events])
  const tools = useMemo(
    () => [...new Set(events.filter((e) => e.kind === 'tool_start' || e.kind === 'tool_end').map((e) => e.data.tool))].sort(),
    [events]
  )
  const [shown, setShown] = useState<Record<KindFilter, boolean>>({ model: true, tool: true, finding: true, gap: true })
  const [tool, setTool] = useState('')
  const [problems, setProblems] = useState(false)
  const [query, setQuery] = useState('')
  const [follow, setFollow] = useState(true)
  const end = useRef<HTMLDivElement>(null)

  const visible = items.filter((item) => {
    const kind = kindOf(item)
    if (kind !== 'run' && !shown[kind]) return false
    if (tool && !(item.type === 'tool' && (item.tool === tool || itemText(item).includes(`"${tool}"`)))) return false
    if (problems && !isProblem(item)) return false
    return !query || itemText(item).toLowerCase().includes(query.toLowerCase())
  })

  useEffect(() => {
    if (follow && running) end.current?.scrollIntoView({ block: 'end' })
  }, [events.length, follow, running])

  return (
    <div className="space-y-3">
      <div className="sticky top-0 z-10 flex flex-wrap items-center gap-2 rounded-xl border bg-card px-3 py-2">
        {(Object.keys(KINDS) as KindFilter[]).map((k) => (
          <button
            key={k}
            type="button"
            onClick={() => setShown({ ...shown, [k]: !shown[k] })}
            className={cn(
              'rounded-full border px-2.5 py-0.5 text-[12px]',
              shown[k] ? 'border-primary bg-primary/10 text-primary' : 'text-muted-foreground'
            )}
          >
            {KINDS[k]}
          </button>
        ))}
        <select
          value={tool}
          onChange={(e) => setTool(e.target.value)}
          className="rounded-md border bg-background px-2 py-1 text-[12px]"
          aria-label="Filter by tool"
        >
          <option value="">All tools</option>
          {tools.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        <label className="flex items-center gap-1.5 text-[12px]">
          <input type="checkbox" checked={problems} onChange={(e) => setProblems(e.target.checked)} />
          Problems only
        </label>
        <div className="flex min-w-[160px] flex-1 items-center gap-1.5 rounded-md border bg-background px-2">
          <Search className="size-3.5 text-muted-foreground" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search the run"
            className="h-7 w-full bg-transparent text-[12px] outline-none"
          />
        </div>
        <label className="flex items-center gap-1.5 text-[12px]">
          <input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} />
          Follow live
        </label>
      </div>

      {!events.length ? (
        <p className="rounded-xl border bg-card px-5 py-4 text-muted-foreground">
          {running ? 'Waiting for the agent’s first step…' : 'This run has no trace (it ran before tracing was added).'}
        </p>
      ) : !visible.length ? (
        <p className="px-1 text-muted-foreground">Nothing matches these filters.</p>
      ) : (
        <ol className="space-y-2">
          {visible.map((item) => (
            <li key={item.type === 'tool' ? `t${item.seq}` : `e${item.event.seq}`}>
              <ItemCard item={item} />
            </li>
          ))}
        </ol>
      )}
      <div ref={end} />
    </div>
  )
}

function ItemCard({ item }: { item: Item }) {
  if (item.type === 'tool') return <ToolCard item={item} />
  const e = item.event
  switch (e.kind) {
    case 'run_start':
      return <RunStart e={e} />
    case 'run_end':
      return <RunEnd e={e} />
    case 'model_turn':
      return <ModelTurn e={e} />
    case 'finding':
      return (
        <Row icon={<Flag className="size-4 text-[#1D5B40]" />} at={e.at}>
          <span className="rounded-full bg-[#E3F1EA] px-2 py-0.5 text-[12px] text-[#1D5B40]">
            {String(e.data.kind).replaceAll('_', ' ')}
          </span>
          <a href={e.data.url} target="_blank" rel="noreferrer" className="font-medium text-primary hover:underline">
            {e.data.title}
          </a>
          {e.data.confidence === 'low' && <span className="text-[12px] text-[#7A4E00]">low confidence</span>}
        </Row>
      )
    case 'gap':
      return (
        <Row icon={<MinusCircle className="size-4 text-[#7A4E00]" />} at={e.at}>
          <span className="text-[#7A4E00]">Gap</span>
          <span className="font-mono text-[12px] text-muted-foreground">{e.data.step}</span>
          <span>{e.data.reason}</span>
        </Row>
      )
    case 'tool_error':
      return (
        <Row icon={<CircleAlert className="size-4 text-red-700" />} at={e.at}>
          <span className="text-red-800">
            The agent was told about <span className="font-mono">{e.data.tool}</span>:
          </span>
          <span>{e.data.error}</span>
        </Row>
      )
    default:
      return (
        <Row icon={<Play className="size-4" />} at={e.at}>
          {e.kind}
        </Row>
      )
  }
}

function Row({ icon, at, children }: { icon: ReactNode; at: string; children: ReactNode }) {
  return (
    <div className="flex items-start gap-3 rounded-xl border bg-card px-4 py-2.5">
      <span className="mt-0.5 shrink-0">{icon}</span>
      <div className="flex min-w-0 flex-1 flex-wrap items-baseline gap-x-2 gap-y-1">{children}</div>
      <time className="shrink-0 text-[12px] tabular-nums text-muted-foreground">{clock(at)}</time>
    </div>
  )
}

// One line of a card's always-visible summary; `tone` colors it.
type Line = { text: string; tone?: 'muted' | 'error' | 'good' | 'warn' | 'thought' }
const TONE = {
  muted: 'text-muted-foreground',
  error: 'text-red-800',
  good: 'text-[#1D5B40]',
  warn: 'text-[#7A4E00]',
  thought: 'italic text-muted-foreground',
}

function Summary({ lines }: { lines: Line[] }) {
  const shown = lines.filter((l) => l.text.trim())
  if (!shown.length) return null
  return (
    <div className="mt-1 space-y-0.5 pl-6">
      {shown.map((l, i) => (
        <p key={i} className={cn('line-clamp-2 break-words text-[13px] leading-snug', TONE[l.tone ?? 'muted'])}>
          {l.text}
        </p>
      ))}
    </div>
  )
}

const oneLine = (text: unknown) => String(text ?? '').replace(/\s+/g, ' ').trim()
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`

// A card with a head and a short summary that are always shown; it opens to show every detail.
function Expandable({
  head,
  summary = [],
  children,
  className,
  open: initial = false,
}: {
  head: ReactNode
  summary?: Line[]
  children: ReactNode
  className?: string
  open?: boolean
}) {
  const [open, setOpen] = useState(initial)
  return (
    <div className={cn('rounded-xl border bg-card', className)}>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex w-full items-start gap-2 px-4 py-2.5 text-left"
      >
        {open ? (
          <ChevronDown className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronRight className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
        )}
        <div className="min-w-0 flex-1">
          {head}
          <Summary lines={summary} />
        </div>
      </button>
      {open && <div className="space-y-3 border-t px-4 py-3">{children}</div>}
    </div>
  )
}

export function JsonBlock({ label, value }: { label: string; value: unknown }) {
  if (value == null || (typeof value === 'object' && !Object.keys(value as object).length)) return null
  return (
    <div className="space-y-1">
      <p className="text-[12px] font-semibold text-muted-foreground">{label}</p>
      <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-muted px-3 py-2 font-mono text-[12px] leading-relaxed">
        {typeof value === 'string' ? value : JSON.stringify(value, null, 2)}
      </pre>
    </div>
  )
}

const STATUS_ICON: Record<string, ReactNode> = {
  running: <LoaderCircle className="size-4 animate-spin text-primary" />,
  done: <Check className="size-4 text-[#1D5B40]" />,
  failed: <X className="size-4 text-red-700" />,
  refused: <Ban className="size-4 text-[#7A4E00]" />,
  skipped: <MinusCircle className="size-4 text-muted-foreground" />,
}

function shortArgs(args: Record<string, unknown> | undefined): string {
  if (!args) return ''
  return Object.entries(args)
    .map(([k, v]) => `${k}=${typeof v === 'string' ? JSON.stringify(v.length > 60 ? v.slice(0, 60) + '…' : v) : JSON.stringify(v)}`)
    .join(', ')
}

// What a tool call did, for its summary: errors first, then what it saved, then what ran inside it.
function toolLines(item: ToolItem): Line[] {
  const end = item.end?.data
  const lines: Line[] = []
  if (item.status === 'running') lines.push({ text: 'Running…' })
  if (end?.error) lines.push({ text: `Error: ${end.error.type}${end.error.message ? ` · ${end.error.message}` : ''}`, tone: 'error' })
  if (item.told) lines.push({ text: `The agent was told: ${oneLine(item.told)}`, tone: 'error' })
  const inside = item.children.map((c) => (c.type === 'tool' ? null : c.event)).filter((e) => e != null)
  const found = inside.filter((e) => e.kind === 'finding')
  if (found.length)
    lines.push({ text: `Saved ${plural(found.length, 'finding')}: ${found.map((e) => oneLine(e.data.title)).join(' · ')}`, tone: 'good' })
  for (const g of inside.filter((e) => e.kind === 'gap')) lines.push({ text: `Gap: ${oneLine(g.data.reason)}`, tone: 'warn' })
  const tools = item.children.filter((c): c is ToolItem => c.type === 'tool')
  if (tools.length) lines.push({ text: `Called ${tools.map((t) => `${t.label} (${t.status})`).join(' · ')}` })
  const calls = inside.filter((e) => e.kind === 'model_turn')
  if (calls.length) lines.push({ text: `${plural(calls.length, 'model call')}: ${oneLine(calls[calls.length - 1].data.text)}` })
  if (!lines.length && end?.reply && item.status === 'done') lines.push({ text: `Replied: ${oneLine(JSON.stringify(end.reply))}` })
  return lines
}

function ToolCard({ item }: { item: ToolItem }) {
  const end = item.end?.data
  const args = item.start?.data.args ?? end?.args
  const problem = ['failed', 'refused'].includes(item.status)
  return (
    <Expandable
      summary={toolLines(item)}
      className={cn(problem && 'border-red-200', item.status === 'running' && 'border-primary/40')}
      head={
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          {STATUS_ICON[item.status] ?? STATUS_ICON.done}
          <span className="font-medium">{item.label}</span>
          <span className="font-mono text-[12px] text-muted-foreground">
            {item.tool}({shortArgs(args)})
          </span>
          {end?.detail && <span className="text-muted-foreground">· {end.detail}</span>}
          <span className="ml-auto flex items-center gap-2 text-[12px] tabular-nums text-muted-foreground">
            {item.children.length > 0 && <span>{item.children.length} inside</span>}
            {end?.ms != null && <span>{duration(end.ms)}</span>}
            <time>{clock(item.at)}</time>
          </span>
        </div>
      }
    >
      <JsonBlock label="Arguments" value={args} />
      {end?.error && (
        <div className="space-y-1">
          <p className="text-[12px] font-semibold text-red-800">
            Error: {end.error.type} {end.error.message && `· ${end.error.message}`}
          </p>
          {end.error.traceback && <JsonBlock label="Traceback" value={end.error.traceback} />}
        </div>
      )}
      {item.told && <JsonBlock label="What the agent was told" value={item.told} />}
      <JsonBlock
        label={`Reply${end?.reply_chars != null ? ` (${end.reply_chars.toLocaleString()} characters)` : ''}`}
        value={end?.reply}
      />
      {item.children.length > 0 && (
        <div className="space-y-2">
          <p className="text-[12px] font-semibold text-muted-foreground">Inside this call</p>
          <ol className="space-y-2 border-l-2 pl-3">
            {item.children.map((child) => (
              <li key={child.type === 'tool' ? `t${child.seq}` : `e${child.event.seq}`}>
                <ItemCard item={child} />
              </li>
            ))}
          </ol>
        </div>
      )}
    </Expandable>
  )
}

function ModelTurn({ e }: { e: TraceEvent }) {
  const d = e.data
  const calls: { name: string; args: Record<string, unknown> }[] = d.tool_calls ?? []
  const nested = e.tool_seq != null
  return (
    <Expandable
      summary={[
        { text: oneLine(d.thought), tone: 'thought' },
        { text: oneLine(d.text), tone: nested ? 'muted' : undefined },
        ...(d.error ? [{ text: `Error: ${oneLine(d.error)}`, tone: 'error' as const }] : []),
      ]}
      head={
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <Bot className={cn('size-4', nested ? 'text-muted-foreground' : 'text-primary')} />
          <span className="font-medium">{nested ? `Model call (${e.agent})` : 'Agent'}</span>
          {calls.map((c, i) => (
            <span key={i} className="rounded-full bg-[#E4EEFB] px-2 py-0.5 font-mono text-[12px] text-[#1F4E8C]">
              {c.name}
            </span>
          ))}
          <span className="ml-auto flex items-center gap-2 text-[12px] tabular-nums text-muted-foreground">
            {d.tokens && (
              <span>
                {d.tokens.in.toLocaleString()} in / {d.tokens.out.toLocaleString()} out
              </span>
            )}
            <time>{clock(e.at)}</time>
          </span>
        </div>
      }
    >
      {d.text && <JsonBlock label="Text" value={d.text} />}
      {d.thought && <JsonBlock label="Reasoning" value={d.thought} />}
      {calls.map((c, i) => (
        <JsonBlock key={i} label={`Calls ${c.name}`} value={c.args} />
      ))}
      {d.error && <JsonBlock label="Error" value={d.error} />}
    </Expandable>
  )
}

function RunStart({ e }: { e: TraceEvent }) {
  const d = e.data
  const expert = String(d.message ?? '').split('\n').filter((l) => /^(Expert|Field|Location):/.test(l))
  return (
    <Expandable
      summary={[
        { text: expert.join(' · ') },
        { text: `Tools: ${(d.tools ?? []).join(', ')}` },
      ]}
      head={
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <Play className="size-4 text-primary" />
          <span className="font-medium">Run started</span>
          <span className="text-muted-foreground">
            {d.model} · {d.tools?.length} tools · up to {d.max_model_calls} model calls ·{' '}
            {Math.round((d.seconds ?? 0) / 60)} min
          </span>
          <time className="ml-auto text-[12px] tabular-nums text-muted-foreground">{clock(e.at)}</time>
        </div>
      }
    >
      <JsonBlock label="First message to the agent" value={d.message} />
      <JsonBlock label="Instructions" value={d.instructions} />
      <JsonBlock label="Tool caps (calls per run)" value={d.caps} />
    </Expandable>
  )
}

function RunEnd({ e }: { e: TraceEvent }) {
  const d = e.data
  return (
    <Expandable
      open={!!d.error}
      summary={[
        ...(d.error ? [{ text: `Error: ${d.error.type} · ${oneLine(d.error.message)}`, tone: 'error' as const }] : []),
        { text: d.final_text ? `Last words: ${oneLine(d.final_text)}` : '' },
      ]}
      className={cn(d.error && 'border-red-200')}
      head={
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          {d.error ? <CircleAlert className="size-4 text-red-700" /> : <Check className="size-4 text-[#1D5B40]" />}
          <span className="font-medium">Run ended</span>
          <span className="text-muted-foreground">
            {d.stopped} · {d.turns} model turns · {(d.tokens?.in ?? 0).toLocaleString()} in /{' '}
            {(d.tokens?.out ?? 0).toLocaleString()} out tokens
          </span>
          <time className="ml-auto text-[12px] tabular-nums text-muted-foreground">{clock(e.at)}</time>
        </div>
      }
    >
      {d.error && <JsonBlock label={`Error: ${d.error.type}`} value={d.error.traceback ?? d.error.message} />}
      <JsonBlock label="The agent's last words" value={d.final_text} />
    </Expandable>
  )
}
