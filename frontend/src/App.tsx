import { useEffect, useRef, useState } from 'react'
import { Boundary } from './Boundary'
import { Chart } from './panels/Chart'
import { Login } from './panels/Login'
import { AccountSummary, PortfolioTabs } from './panels/Portfolio'
import { ConfirmModal, RiskPanel, TicketPanel, usePreview } from './panels/Ticket'
import { api, ApiError, dismissAlert, pushAlert, restartApp, useStore } from './ws'

const STATE_LABEL = {
  disconnected: 'Disconnected',
  connecting: 'Connecting…',
  reconciling: 'Syncing orders…',
  ready: 'Connected',
  degraded: 'IB servers unreachable',
} as const

export default function App() {
  const ws = useStore((s) => s.ws)
  const restarting = useStore((s) => s.restarting)
  const conn = useStore((s) => s.connection)
  const login = useStore((s) => s.login)
  // After a first successful connection, a disconnect shows a banner (daily restart, network)
  // instead of bouncing back to the login screen. If Gateway exits, we go back to login.
  const wasConnected = useRef(false)
  if (conn?.state === 'ready') wasConnected.current = true
  if (login?.step === 'error' && conn?.state === 'disconnected') wasConnected.current = false

  if (restarting) return <div className="center muted">{restarting} IBKR stays logged in.</div>
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
        <Workspace />
      )}
      <Alerts />
    </div>
  )
}

function Workspace() {
  const ticket = useStore((s) => s.ticket)
  const { preview, error } = usePreview(ticket)
  const [staged, setStaged] = useState(false)
  const stage = () => preview && !preview.blockers.length && setStaged(true)

  // Enter stages the ticket (first click); the confirm dialog takes the second Enter.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Enter' && !staged && ticket && !(e.target as HTMLElement).closest('select, button')) stage()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  return (
    <main className="workspace">
      <Boundary name="Chart"><Chart preview={preview} /></Boundary>
      <div className="right">
        <Boundary name="Account"><AccountSummary /></Boundary>
        <Boundary name="Ticket"><TicketPanel preview={preview} error={error} onStage={stage} /></Boundary>
        <Boundary name="Risk"><RiskPanel /></Boundary>
      </div>
      <Boundary name="Portfolio"><PortfolioTabs /></Boundary>
      {staged && preview && <ConfirmModal preview={preview} onClose={() => setStaged(false)} />}
    </main>
  )
}

function Alerts() {
  const alerts = useStore((s) => s.alerts)
  useEffect(() => {
    // Info disappears after 8 s; warnings and errors stay until dismissed.
    const ids = alerts.filter((a) => a.level === 'info').map((a) => setTimeout(() => dismissAlert(a.at), 8000))
    return () => ids.forEach(clearTimeout)
  }, [alerts])
  return (
    <div className="alerts">
      {alerts.map((a) => (
        <div key={a.at} className={`alert ${a.level}`} onClick={() => dismissAlert(a.at)}>
          {a.message}
        </div>
      ))}
    </div>
  )
}

/** One click: cancel every order and close every position. Also Ctrl+Shift+K. */
async function killSwitch() {
  pushAlert('warn', 'Kill switch: cancelling all orders and closing all positions…')
  try {
    await api('/api/kill', {})  // the result arrives as an alert once IBKR confirms
  } catch (e) {
    pushAlert('error', e instanceof ApiError ? e.message : String(e))
  }
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
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setBlockers(undefined)
      if (e.ctrlKey && e.shiftKey && e.key.toLowerCase() === 'k') (e.preventDefault(), killSwitch())
    }
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
      <button className="kill" onClick={killSwitch} disabled={conn.state !== 'ready' && conn.state !== 'degraded'}
        title="Cancel ALL orders and close ALL positions now (Ctrl+Shift+K)">
        KILL SWITCH
      </button>
      <button onClick={restartApp} title="Restart the app and load the latest code. IBKR stays logged in. (Ctrl+R only reloads the screen.)">
        Restart
      </button>
      <button onClick={quit}>Quit</button>
      {quitting && <div className="modal">Shut down. You can close this window.</div>}
      {blockers && (
        <div className="modal" onClick={() => setBlockers(undefined)}>
          <div className="card" onClick={(e) => e.stopPropagation()}>
            <h3>Can't quit yet</h3>
            <p>These are still open. Flatten everything and quit, or close the window instead: the app keeps running in the background and still force-flattens before the close.</p>
            <ul>
              {blockers.map((b) => (
                <li key={b}>{b}</li>
              ))}
            </ul>
            <div className="row">
              <button onClick={() => setBlockers(undefined)}>Keep running</button>
              <button className="kill" onClick={async () => {
                setBlockers(undefined)
                await killSwitch()
                quit()
              }}>Flatten everything, then quit</button>
            </div>
          </div>
        </div>
      )}
    </header>
  )
}
