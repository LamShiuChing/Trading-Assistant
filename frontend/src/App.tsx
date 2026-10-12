import { useEffect, useRef, useState } from 'react'
import { Login } from './panels/Login'
import { api, ApiError, useStore } from './ws'

const STATE_LABEL = {
  disconnected: 'Disconnected',
  connecting: 'Connecting…',
  reconciling: 'Syncing orders…',
  ready: 'Connected',
  degraded: 'IB servers unreachable',
} as const

export default function App() {
  const ws = useStore((s) => s.ws)
  const conn = useStore((s) => s.connection)
  const login = useStore((s) => s.login)
  // After a first successful connection, a disconnect shows a banner (daily restart, network)
  // instead of bouncing back to the login screen. If Gateway exits, we go back to login.
  const wasConnected = useRef(false)
  if (conn?.state === 'ready') wasConnected.current = true
  if (login?.step === 'error' && conn?.state === 'disconnected') wasConnected.current = false

  if (ws === 'closed') return <div className="center error-screen">Backend not reachable. Retrying…</div>
  if (!conn) return <div className="center muted">Loading…</div>

  const showLogin = conn.state === 'disconnected' && !wasConnected.current
  return (
    <div className="app">
      <Header />
      {conn.blocked_live && (
        <div className="banner live">
          A LIVE Gateway is running, but allow_live = false in config.toml. The app won't connect to it.
        </div>
      )}
      {conn.mismatch && (
        <div className="banner live">
          Account {conn.account} doesn't match {conn.mode?.toUpperCase()} mode. All orders are blocked.
        </div>
      )}
      {!showLogin && conn.state !== 'ready' && (
        <div className="banner warn">
          {STATE_LABEL[conn.state]}. New entries are blocked, and order states are unconfirmed until reconnected.
        </div>
      )}
      {showLogin ? (
        <Login />
      ) : (
        <main className="center muted">
          {conn.account && <>Connected to {conn.account}. The chart and portfolio come in phase 1b.</>}
        </main>
      )}
    </div>
  )
}

function Header() {
  const conn = useStore((s) => s.connection)!
  const [blockers, setBlockers] = useState<string[]>()
  const [quitting, setQuitting] = useState(false)

  async function quit() {
    if (!confirm('Quit the app and shut down IB Gateway?')) return
    try {
      await api('/api/quit', {})
      setQuitting(true)
      window.close()
    } catch (e) {
      const d = e instanceof ApiError ? (e.detail as { blockers?: string[] }) : undefined
      setBlockers(d?.blockers ?? [String(e)])
    }
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setBlockers(undefined)
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  return (
    <header>
      <strong>Trading Assistant</strong>
      {conn.mode && <span className={`badge ${conn.mode}`}>{conn.mode.toUpperCase()}</span>}
      <span className={`pill ${conn.state}`}>{STATE_LABEL[conn.state]}</span>
      {conn.account && <span className="muted">{conn.account}</span>}
      <span className="spacer" />
      <button onClick={quit}>Quit</button>
      {quitting && <div className="modal">Shut down. You can close this window.</div>}
      {blockers && (
        <div className="modal" onClick={() => setBlockers(undefined)}>
          <div className="card" onClick={(e) => e.stopPropagation()}>
            <h3>Can't quit yet</h3>
            <p>Flatten and cancel these first. The flatten button comes in phase 2.</p>
            <ul>
              {blockers.map((b) => (
                <li key={b}>{b}</li>
              ))}
            </ul>
            <button onClick={() => setBlockers(undefined)}>OK</button>
          </div>
        </div>
      )}
    </header>
  )
}
