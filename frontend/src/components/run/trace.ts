import type { Finding, TraceEvent } from '@/lib/api'

// The run trace as a timeline: each tool call is one item (its start and end merged) holding what
// happened inside it (model calls, findings, gaps, nested tool calls); everything else is one item
// per event, in order.

export type ToolItem = {
  type: 'tool'
  seq: number // the tool call's activity seq
  at: string
  tool: string
  label: string
  status: string // running until its tool_end arrives
  start?: TraceEvent
  end?: TraceEvent
  told?: string // the error text the agent got back
  children: Item[]
}
export type EventItem = { type: 'event'; event: TraceEvent }
export type Item = ToolItem | EventItem

export function buildTimeline(events: TraceEvent[]): Item[] {
  const top: Item[] = []
  const tools = new Map<number, ToolItem>()
  const place = (item: Item, parent: number | null) => {
    const owner = parent != null ? tools.get(parent) : undefined
    ;(owner ? owner.children : top).push(item)
  }
  const toolFor = (e: TraceEvent): ToolItem => {
    const seq = e.data.seq as number
    let item = tools.get(seq)
    if (!item) {
      item = {
        type: 'tool',
        seq,
        at: e.at,
        tool: e.data.tool,
        label: e.data.label ?? e.data.tool,
        status: 'running',
        children: [],
      }
      tools.set(seq, item)
      place(item, e.tool_seq)
    }
    return item
  }
  for (const e of events) {
    if (e.kind === 'tool_start') {
      toolFor(e).start = e
    } else if (e.kind === 'tool_end') {
      const item = toolFor(e) // refusals have no start
      item.end = e
      item.status = e.data.status
    } else if (e.kind === 'tool_error') {
      // usually the reply of the tool call that just failed or was refused: show it there
      const last = [...tools.values()]
        .reverse()
        .find((t) => t.tool === e.data.tool && t.status !== 'done' && t.status !== 'running' && !t.told)
      if (last) last.told = e.data.error
      else place({ type: 'event', event: e }, e.tool_seq)
    } else {
      place({ type: 'event', event: e }, e.tool_seq)
    }
  }
  return top
}

export interface RunStats {
  turns: number
  tokensIn: number
  tokensOut: number
  tools: Record<string, number> // by status: done, failed, refused, skipped, running
  findings: number
  gaps: number
  debug: boolean
  stopped: string | null
}

export function runStats(events: TraceEvent[]): RunStats {
  const s: RunStats = {
    turns: 0,
    tokensIn: 0,
    tokensOut: 0,
    tools: {},
    findings: 0,
    gaps: 0,
    debug: false,
    stopped: null,
  }
  const open = new Set<number>()
  for (const e of events) {
    s.debug ||= !!e.data.debug
    if (e.kind === 'model_turn') {
      s.turns += 1
      s.tokensIn += e.data.tokens?.in ?? 0
      s.tokensOut += e.data.tokens?.out ?? 0
    } else if (e.kind === 'tool_start') open.add(e.data.seq)
    else if (e.kind === 'tool_end') {
      open.delete(e.data.seq)
      s.tools[e.data.status] = (s.tools[e.data.status] ?? 0) + 1
    } else if (e.kind === 'finding') s.findings += 1
    else if (e.kind === 'gap') s.gaps += 1
    else if (e.kind === 'run_end') s.stopped = e.data.stopped
  }
  if (open.size) s.tools.running = open.size
  return s
}

// Findings as the run saves them (from the trace), before the final list can be read.
export function liveFindings(events: TraceEvent[]): Finding[] {
  const seen = new Map<string, Finding>()
  for (const e of events) {
    if (e.kind !== 'finding' || !e.data.ref) continue
    seen.set(e.data.ref, {
      finding_id: e.data.ref,
      kind: e.data.kind,
      title: e.data.title,
      url: e.data.url,
      confidence: e.data.confidence,
      facts: e.data.facts ?? {},
      note: e.data.note ?? null,
    })
  }
  return [...seen.values()]
}

// What a search box matches: everything in the item, as text.
export function itemText(item: Item): string {
  return item.type === 'tool'
    ? JSON.stringify([item.tool, item.label, item.start?.data, item.end?.data, item.told]) +
        item.children.map(itemText).join(' ')
    : JSON.stringify([item.event.kind, item.event.agent, item.event.data])
}

export const isProblem = (item: Item): boolean =>
  item.type === 'tool'
    ? ['failed', 'refused', 'skipped'].includes(item.status) || item.children.some(isProblem)
    : ['gap', 'tool_error'].includes(item.event.kind) ||
      (item.event.kind === 'run_end' && !!item.event.data.error)

export const clock = (at: string) =>
  new Date(at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })

export function duration(ms: number): string {
  if (ms < 1000) return `${ms} ms`
  const s = Math.round(ms / 1000)
  return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${s % 60} s`
}
