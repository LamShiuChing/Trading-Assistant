import { useEffect, useState, type FormEvent } from 'react'
import { api, ApiError, useStore } from '../ws'

type Setup = {
  ibc: boolean
  gateway: boolean
  ibc_path: string
  gateway_path: string
  allow_live: boolean
  username: string
  saved_password: boolean
}

const STEPS = [
  ['launching', 'Launching Gateway'],
  ['logging_in', 'Logging in'],
  ['waiting_2fa', 'Waiting for 2FA: approve the alert in IBKR Mobile'],
  ['connected', 'Connected'],
  ['ready', 'API ready'],
] as const

export function Login() {
  const [setup, setSetup] = useState<Setup>()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [mode, setMode] = useState<'paper' | 'live'>('paper')
  const [remember, setRemember] = useState(false)
  const [submitError, setSubmitError] = useState<string>()
  const [busy, setBusy] = useState(false)
  const login = useStore((s) => s.login)
  const conn = useStore((s) => s.connection)

  useEffect(() => {
    api<Setup>('/api/setup').then((s) => {
      setSetup(s)
      setUsername(s.username)
    })
  }, [])

  const step =
    conn?.state === 'reconciling' || conn?.state === 'ready' ? 'ready'
    : conn?.state === 'connecting' ? 'connected'
    : login?.step
  const inProgress = busy && step !== 'error'
  const stepIndex = STEPS.findIndex(([k]) => k === step)

  async function submit(e: FormEvent, takeover = false) {
    e.preventDefault()
    setSubmitError(undefined)
    setBusy(true)
    try {
      await api('/api/login', {
        username,
        password: password || null,
        mode,
        remember,
        takeover,
      })
    } catch (err) {
      setSubmitError(err instanceof ApiError ? err.message : String(err))
      setBusy(false)
    }
  }

  if (!setup) return <div className="center muted">Loading…</div>

  if (!setup.ibc || !setup.gateway) {
    return (
      <div className="center">
        <div className="card">
          <h2>Setup needed</h2>
          {!setup.gateway && (
            <p>
              IB Gateway (offline installer) not found at <code>{setup.gateway_path}</code>.
            </p>
          )}
          {!setup.ibc && (
            <p>
              IBC not found at <code>{setup.ibc_path}</code>.
            </p>
          )}
          <p className="muted">
            Run <code>launcher\check-setup.ps1</code>. It prints the download links and the steps, and the README covers them
            too.
          </p>
        </div>
      </div>
    )
  }

  const canSubmit = username && (password || setup.saved_password) && !inProgress
  return (
    <div className="center">
      <form className="card login" onSubmit={(e) => submit(e)}>
        <h2>Log in to IBKR</h2>
        <div className="seg">
          <button type="button" className={mode === 'paper' ? 'on paper' : ''} onClick={() => setMode('paper')}>
            PAPER
          </button>
          <button
            type="button"
            className={mode === 'live' ? 'on live' : ''}
            disabled={!setup.allow_live}
            title={setup.allow_live ? '' : 'LIVE is off. Set allow_live = true in config.toml first.'}
            onClick={() => setMode('live')}
          >
            LIVE
          </button>
        </div>
        <label>
          Username
          <input autoFocus value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="off" />
        </label>
        <label>
          Password
          <input
            type="password"
            value={password}
            placeholder={setup.saved_password ? 'Saved in Windows Credential Manager' : ''}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="off"
          />
        </label>
        <label className="check">
          <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
          Remember the password in Windows Credential Manager
        </label>
        <button className={`primary ${mode}`} disabled={!canSubmit}>
          Log in to {mode.toUpperCase()}
        </button>

        {busy && (
          <ol className="steps">
            {STEPS.map(([k, label], i) => (
              <li key={k} className={i < stepIndex ? 'done' : i === stepIndex ? 'now' : ''}>
                {label}
              </li>
            ))}
          </ol>
        )}
        {(submitError || (busy && login?.step === 'error')) && (
          <div className="error">
            {submitError ?? login?.message}
            {login?.code === 'existing_session' && !submitError && (
              <button type="button" onClick={(e) => submit(e, true)}>
                Take over the session (logs out the other one)
              </button>
            )}
          </div>
        )}
      </form>
    </div>
  )
}
