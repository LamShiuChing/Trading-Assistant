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
type State = { ws: 'connecting' | 'open' | 'closed'; connection?: Connection; login?: LoginProgress }

let state: State = { ws: 'connecting' }
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
    set({ [m.type]: m.data })
  }
  ws.onclose = () => {
    set({ ws: 'closed' })
    setTimeout(connect, 1000)
  }
}
connect()

export function useStore<T>(select: (s: State) => T): T {
  return useSyncExternalStore(
    (f) => (subs.add(f), () => subs.delete(f)),
    () => select(state),
  )
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
