import { useEffect, useRef, useState } from 'react'
import { api, ApiError, pushAlert, setTicket, useStore, type Ticket as T, type TpMethod } from '../ws'

export type Preview = {
  id: string; symbol: string; side: 'BUY' | 'SELL'; qty: number; entry: number; stop: number
  target: number | null; tp_method: string; risk: number; reward: number | null; rr: number | null
  trail: boolean; blockers: string[]
}

const METHODS: { v: TpMethod; label: string }[] = [
  { v: 'r', label: 'R multiple' },
  { v: 'atr', label: 'ATR (1.5×, 5m)' },
  { v: 'level', label: 'Next key level' },
  { v: 'trail', label: 'Trail after +1R' },
]
const usd = (v: number) => '$' + v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

/** Live preview of the current ticket (debounced). Also used by the chart to draw the target line. */
export function usePreview(ticket: T | undefined) {
  const [p, setP] = useState<Preview>()
  const [err, setErr] = useState<string>()
  useEffect(() => {
    if (!ticket) return setP(undefined)
    const id = setTimeout(() => {
      api<Preview>('/api/ticket/preview', ticket).then(
        (r) => (setP(r), setErr(undefined)),
        (e) => (setP(undefined), setErr(e instanceof ApiError ? e.message : String(e))),
      )
    }, 250)
    return () => clearTimeout(id)
  }, [ticket])
  return { preview: p, error: err }
}

export function TicketPanel({ preview, error, onStage }: { preview?: Preview; error?: string; onStage: () => void }) {
  const ticket = useStore((s) => s.ticket)
  const chartSymbol = useStore((s) => s.chart?.symbol)

  if (!ticket) {
    return (
      <section className="side card-flat">
        <h3>Order ticket</h3>
        <p className="muted small">
          Press <kbd>B</kbd> (long) or <kbd>S</kbd> (short) to stage {chartSymbol ?? 'the chart symbol'} at the last price.
          Drag the entry, stop and target lines on the chart.
        </p>
      </section>
    )
  }
  const upd = (patch: Partial<T>) => setTicket({ ...ticket, ...patch })
  const num = (v: string) => (v === '' ? NaN : Number(v))

  return (
    <section className="side card-flat ticket">
      <h3>
        <span className={ticket.side === 'BUY' ? 'up' : 'down'}>{ticket.side === 'BUY' ? 'LONG' : 'SHORT'}</span> {ticket.symbol}
        <button className="x" title="Cancel (Esc)" onClick={() => setTicket(undefined)}>✕</button>
      </h3>
      <div className="grid2">
        <label>Entry (LMT)<input type="number" step="0.01" value={ticket.entry} onChange={(e) => upd({ entry: num(e.target.value) })} /></label>
        <label>Stop<input type="number" step="0.01" value={ticket.stop} onChange={(e) => upd({ stop: num(e.target.value) })} /></label>
        <label>
          Take profit
          <select value={ticket.tp_method} onChange={(e) => upd({ tp_method: e.target.value as TpMethod, target: null })}>
            {METHODS.map((m) => <option key={m.v} value={m.v}>{m.label}</option>)}
          </select>
        </label>
        {ticket.tp_method === 'r' && (
          <label>R multiple<input type="number" step="0.5" min="0.5" value={ticket.target_r} onChange={(e) => upd({ target_r: num(e.target.value), target: null })} /></label>
        )}
        <label>
          Max size <span className="muted">(optional)</span>
          <input type="number" min="1" step="1" value={ticket.qty ?? ''} placeholder="auto" onChange={(e) => upd({ qty: e.target.value ? Math.floor(num(e.target.value)) : null })} />
        </label>
      </div>
      {ticket.target != null && (
        <div className="muted small">
          Target dragged to {ticket.target.toFixed(2)} <button className="link" onClick={() => upd({ target: null })}>reset</button>
        </div>
      )}
      {error && <div className="error-text small">{error}</div>}
      {preview && (
        <dl className="kv small">
          <div><dt>Size</dt><dd>{preview.qty} sh</dd></div>
          <div><dt>Risk</dt><dd className="down">{usd(preview.risk)}</dd></div>
          <div><dt>Target</dt><dd>{preview.target?.toFixed(2) ?? 'trailing'}</dd></div>
          <div><dt>R:R</dt><dd>{preview.rr ?? '—'}</dd></div>
        </dl>
      )}
      {preview?.blockers.map((b) => <div key={b} className="error-text small">⛔ {b}</div>)}
      <button className={`primary ${ticket.side === 'BUY' ? 'buy' : 'sell'}`} disabled={!preview || preview.blockers.length > 0} onClick={onStage}>
        Stage {ticket.side === 'BUY' ? 'buy' : 'sell'} ↵
      </button>
    </section>
  )
}

/** Second click: shows exactly what will be sent; Enter confirms, Esc backs out. */
export function ConfirmModal({ preview, onClose }: { preview: Preview; onClose: () => void }) {
  const [busy, setBusy] = useState(false)

  async function confirm() {
    setBusy(true)
    try {
      const r = await api<{ ref: string; statuses: { id: number; type: string; status: string }[]; errors: string[] }>(
        '/api/ticket/confirm', { id: preview.id })
      const sts = r.statuses.map((s) => `${s.type}: ${s.status}`).join(', ')
      pushAlert(r.errors.length ? 'error' : 'info', `${r.ref} sent. ${sts}${r.errors.length ? ' | ' + r.errors.join('; ') : ''}`)
      setTicket(undefined)
    } catch (e) {
      pushAlert('error', e instanceof ApiError ? e.message : String(e))
    }
    onClose()
  }

  // Enter confirms only on a NEW press: the Enter that staged the ticket must be released first,
  // and auto-repeat is ignored. Otherwise one keypress could stage and send in one go.
  const armed = useRef(false)
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
      if (e.key === 'Enter') {
        e.preventDefault()
        if (armed.current && !e.repeat && !busy) confirm()
      }
    }
    const up = (e: KeyboardEvent) => {
      if (e.key === 'Enter') armed.current = true
    }
    window.addEventListener('keydown', down)
    window.addEventListener('keyup', up)
    return () => {
      window.removeEventListener('keydown', down)
      window.removeEventListener('keyup', up)
    }
  })

  const long = preview.side === 'BUY'
  return (
    <div className="modal" onClick={onClose}>
      <div className="card confirm" onClick={(e) => e.stopPropagation()}>
        <h3>Confirm bracket order</h3>
        <p className="big">
          <span className={long ? 'up' : 'down'}>{long ? 'BUY' : 'SELL SHORT'}</span> {preview.qty} {preview.symbol} @ {preview.entry.toFixed(2)} LMT
        </p>
        <dl className="kv">
          <div><dt>Stop</dt><dd>{preview.stop.toFixed(2)}{preview.trail ? ' (trails by 1R after +1R)' : ''}</dd></div>
          <div><dt>Target</dt><dd>{preview.target?.toFixed(2) ?? 'none (trailing stop)'} <span className="muted">{preview.tp_method}</span></dd></div>
          <div><dt>Risk</dt><dd className="down">{usd(preview.risk)}</dd></div>
          <div><dt>Reward</dt><dd className="up">{preview.reward != null ? usd(preview.reward) : '—'}</dd></div>
          <div><dt>R:R</dt><dd>{preview.rr ?? '—'}</dd></div>
        </dl>
        <p className="muted small">Day order. Exits are one-cancels-other, with a market exit at force-flat time as an IBKR-side backup.</p>
        <div className="row">
          <button onClick={onClose}>Cancel (Esc)</button>
          <button className={`primary ${long ? 'buy' : 'sell'}`} disabled={busy} onClick={confirm}>
            {busy ? 'Sending…' : 'Confirm (Enter)'}
          </button>
        </div>
      </div>
    </div>
  )
}

export function RiskPanel() {
  const r = useStore((s) => s.risk)
  if (!r) return null
  const { limits: l, used: u } = r
  const loss = u.day_pnl != null && u.day_pnl < 0 ? -u.day_pnl : 0
  const bars: [string, number, number, string][] = [
    ['Daily loss', loss, l.daily_max_loss, `${usd(loss)} / ${usd(l.daily_max_loss)}`],
    ['Open risk', u.open_risk, l.max_open_risk, `${usd(u.open_risk)} / ${usd(l.max_open_risk)}`],
    ['Positions', u.positions, l.max_positions, `${u.positions} / ${l.max_positions}`],
    ['Trades today', u.trades_today, l.max_trades_per_day, `${u.trades_today} / ${l.max_trades_per_day}`],
  ]
  return (
    <section className="side card-flat">
      <h3>Risk <span className="muted">{usd(l.risk_per_trade)}/trade</span></h3>
      {bars.map(([k, v, max, text]) => {
        const pct = Math.min(100, (v / max) * 100)
        return (
          <div key={k} className="meter">
            <div className="meter-label"><span className="muted">{k}</span><span>{text}</span></div>
            <div className="meter-track"><div className={pct >= 100 ? 'full' : pct >= 70 ? 'high' : ''} style={{ width: `${pct}%` }} /></div>
          </div>
        )
      })}
      {r.session && (
        <div className="muted small">
          Entries until {r.session.cutoff} · force-flat {r.session.flatten} · close {r.session.close}
        </div>
      )}
      {r.unprotected.length > 0 && <div className="warn-text small">No stop: {r.unprotected.join(', ')}</div>}
      {r.blockers.map((b) => <div key={b} className="error-text small">⛔ {b}</div>)}
    </section>
  )
}
