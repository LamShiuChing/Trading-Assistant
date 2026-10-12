import { useSyncExternalStore } from 'react'

// Shapes pushed by the backend (see backend/app/hub.py publishers).
export type Connection = {
  state: 'disconnected' | 'connecting' | 'reconciling' | 'ready' | 'degraded'
  mode: 'paper' | 'live' | null
  account: string | null
  mismatch: boolean
  blocked_live: boolean
}
export type LoginProgress = {
  step: 'launching' | 'logging_in' | 'waiting_2fa' | 'error'
  code?: string
  message?: string
}
export type Row = { time: number; open: number; high: number; low: number; close: number; volume: number; vwap: number | null }
export type Range = { high: number; low: number; start: number; end: number; complete: boolean }
export type Levels = { pdh?: number; pdl?: number; pdc?: number; pmh?: number; pml?: number; or5?: Range; or15?: Range }
export type ChartMsg = { symbol: string; loading?: boolean; error?: string; bars?: Row[]; levels?: Levels }
export type BarMsg = { symbol: string; rows: Row[]; levels: Levels }
export type Position = {
  symbol: string; conId: number; qty: number; avg: number | null; avg_src: 'fills' | 'ibkr'; avg_cost: number | null
  last: number | null; value: number | null
  unrealized: number | null; realized: number | null; stop: number | null; target: number | null; r: number | null
}
export type Order = {
  id: number; perm_id: number; symbol: string; action: string; type: string; qty: number
  kind: 'entry' | 'stop' | 'target' | 'backup' | 'sell' | 'other'; price: number | null
  tif: string; status: string; filled: number; parent: number; ref: string
}
export type Fill = { time: string; symbol: string; side: string; qty: number; price: number; commission: number | null }
export type Portfolio = {
  summary: {
    net_liq?: number; cash?: number; buying_power?: number; currency?: string
    day_pnl?: number | null; unrealized?: number | null; realized?: number | null
  }
  positions: Position[]
  orders: Order[]
  fills: Fill[]
}
export type Risk = {
  limits: { daily_max_loss: number; max_positions: number; max_open_risk: number
    max_trades_per_day: number; max_sector_exposure: number; max_position_value: number }
  used: { day_pnl: number | null; open_risk: number; positions: number; trades_today: number }
  unprotected: string[]
  session: { open: string; close: string; cutoff: string; flatten: string } | null
  blockers: string[]
}
export type Alert = { level: 'info' | 'warn' | 'error'; message: string; at: number }
export type TpMethod = 'r' | 'atr' | 'level' | 'trail'
/** The order being prepared (local only; nothing is sent until Confirm). */
export type Ticket = {
  symbol: string; stop: number  // long only, market entry: the entry is the live price
  tp_method: TpMethod; target_r: number; target: number | null; qty: number
}
/** The size you used last time: the default for the next ticket. */
export const lastQty = () => Number(localStorage.getItem('ta.qty')) || 100
export const rememberQty = (q: number) => localStorage.setItem('ta.qty', String(q))
type State = {
  ws: 'connecting' | 'open' | 'closed'
  restarting?: string
  risk?: Risk
  alerts: Alert[]
  ticket?: Ticket
  connection?: Connection
  login?: LoginProgress
  chart?: ChartMsg
  bar?: BarMsg
  portfolio?: Portfolio
  screener?: { updated: string; rows: import('./panels/Screener').ScreenRow[] }
}

let state: State = { ws: 'connecting', alerts: [] }
const subs = new Set<() => void>()

function set(patch: Partial<State>) {
  state = { ...state, ...patch }
  subs.forEach((f) => f())
}

function connect() {
  const ws = new WebSocket(`ws://${location.host}/ws`)
  ws.onopen = () => set({ ws: 'open' })
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data)
    if (m.type === 'alert') set({ alerts: [...state.alerts, { ...m.data, at: Date.now() }].slice(-5) })
    else set({ [m.type]: m.data })
  }
  ws.onclose = () => {
    set({ ws: 'closed' })
    setTimeout(connect, 1000)
  }
}
connect()

export const setTicket = (ticket: Ticket | undefined) => set({ ticket })
export const pushAlert = (level: Alert['level'], message: string) =>
  set({ alerts: [...state.alerts, { level, message, at: Date.now() }].slice(-5) })
export const dismissAlert = (at: number) => set({ alerts: state.alerts.filter((a) => a.at !== at) })

export function useStore<T>(select: (s: State) => T): T {
  return useSyncExternalStore(
    (f) => (subs.add(f), () => subs.delete(f)),
    () => select(state),
  )
}

/** Restart the backend (rebuilding the UI if needed), wait for it to come back, then reload the page.
 *  IB Gateway keeps running, so you stay logged in. */
export async function restartApp() {
  set({ restarting: 'Restarting…' })
  const up = () => fetch('/api/health', { cache: 'no-store' }).then((r) => r.ok, () => false)
  try {
    await api('/api/restart', {})
  } catch (e) {
    return set({ restarting: undefined })
  }
  let sawDown = false
  for (let i = 0; i < 240; i++) {  // up to 2 min (a UI rebuild takes a few seconds)
    await new Promise((r) => setTimeout(r, 500))
    const ok = await up()
    if (!ok) sawDown = true
    else if (sawDown) return location.reload()
  }
  set({ restarting: 'Restart is taking too long. See %LOCALAPPDATA%\\TradingAssistant\\logs\\backend.err.log' })
}

export class ApiError extends Error {
  detail: unknown
  constructor(detail: unknown) {
    super(typeof detail === 'string' ? detail : 'Request failed')
    this.detail = detail
  }
}

export async function api<T = any>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: body === undefined ? undefined : { 'content-type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const j = await r.json().catch(() => ({}))
  if (!r.ok) throw new ApiError(j.detail)
  return j
}
