import { useEffect, useRef, useState } from 'react'
import { api, ApiError, pushAlert, setTicket, useStore, type Ticket as T, type TpMethod } from '../ws'

export type Preview = {
  id: string; symbol: string; side: 'BUY'; qty: number; entry: number; stop: number
  target: number | null; tp_method: string; risk: number; reward: number | null; rr: number | null
  trail: boolean; size_note: string; blockers: string[]
  add: { held: number; total: number; new_avg: number; merged_risk: number; merged_reward: number | null } | null
}

const METHODS: { v: TpMethod; label: string }[] = [
  { v: 'r', label: 'R multiple' },
  { v: 'atr', label: 'ATR (1.5×, 5m)' },
  { v: 'level', label: 'Next key level' },
  { v: 'trail', label: 'Trail after +1R' },
]
const usd = (v: number) => '$' + v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const METHOD_LABEL: Record<string, string> = { r: 'R multiple', atr: 'ATR', level: 'key level', trail: 'trailing', manual: 'dragged' }

/** Live preview of the current ticket (debounced, and refreshed every 3 s since the entry is the live price). */
export function usePreview(ticket: T | undefined) {
  const [p, setP] = useState<Preview>()
  const [err, setErr] = useState<string>()
  useEffect(() => {
    if (!ticket) return setP(undefined)
    let alive = true
    const load = () =>
      api<Preview>('/api/ticket/preview', ticket).then(
        (r) => { if (alive) (setP(r), setErr(undefined)) },
        (e) => { if (alive) (setP(undefined), setErr(e instanceof ApiError ? e.message : String(e))) },
      )
    const first = setTimeout(load, 250)
    const every = setInterval(load, 3000)
    return () => {
      alive = false
      clearTimeout(first)
      clearInterval(every)
    }
  }, [ticket])
  return { preview: p, error: err }
}

export function TicketPanel({ preview, error, onStage }: { preview?: Preview; error?: string; onStage: () => void }) {
  const ticket = useStore((s) => s.ticket)
  const chartSymbol = useStore((s) => s.chart?.symbol)
  const defaultRisk = useStore((s) => s.risk?.limits.risk_per_trade)

  if (!ticket) {
    return (
      <section className="side card-flat">
        <h3>Order ticket</h3>
        <p className="muted small">
          Press <kbd>B</kbd> to stage a market buy of {chartSymbol ?? 'the chart symbol'}. Drag the stop and target lines on the
          chart. Long only; you buy and sell at market.
        </p>
      </section>
    )
  }
  const upd = (patch: Partial<T>) => setTicket({ ...ticket, ...patch })
  const num = (v: string) => (v === '' ? NaN : Number(v))

  return (
    <section className="side card-flat ticket">
      <h3>
        <span className="up">BUY</span> {ticket.symbol} <span className="muted small">market</span>
        <button className="x" title="Cancel (Esc)" onClick={() => setTicket(undefined)}>✕</button>
      </h3>
      <div className="grid2">
        <label>Entry (est.)<input value={preview ? preview.entry.toFixed(2) : '…'} readOnly tabIndex={-1} /></label>
        <label>Stop<input type="number" step="0.01" value={ticket.stop} onChange={(e) => upd({ stop: num(e.target.value) })} /></label>
        <label>
          Take profit
          <select value={ticket.tp_method} onChange={(e) => upd({ tp_method: e.target.value as TpMethod, target: null })}>
            {METHODS.map((m) => <option key={m.v} value={m.v}>{m.label}</option>)}
          </select>
        </label>
        {ticket.tp_method === 'r' ? (
          <label>R multiple<input type="number" step="0.5" min="0.5" value={ticket.target_r} onChange={(e) => upd({ target_r: num(e.target.value), target: null })} /></label>
        ) : <span />}
        <label>
          Risk $
          <input type="number" min="1" step="25" value={ticket.risk_usd ?? ''} placeholder={defaultRisk ? String(defaultRisk) : ''}
            onChange={(e) => upd({ risk_usd: e.target.value ? num(e.target.value) : null })} />
        </label>
        <label>
          Shares
          <input type="number" min="1" step="1" value={ticket.qty ?? ''} placeholder="auto"
            onChange={(e) => upd({ qty: e.target.value ? Math.floor(num(e.target.value)) : null })} />
        </label>
      </div>
      {ticket.target != null && (
        <div className="muted small">
          Target dragged to {ticket.target.toFixed(2)} <button className="link" onClick={() => upd({ target: null })}>reset</button>
        </div>
      )}
      {error && <div className="error-text small">{error}</div>}
      {preview && (
        <>
          <dl className="kv small">
            <div><dt>Size</dt><dd>{preview.qty.toLocaleString()} sh</dd></div>
            <div><dt>Risk</dt><dd className="down">{usd(preview.risk)}</dd></div>
            <div><dt>Target</dt><dd>{preview.target?.toFixed(2) ?? 'trailing'}</dd></div>
            <div><dt>R:R</dt><dd>{preview.rr ?? '—'}</dd></div>
          </dl>
          <div className="muted small">{preview.size_note}</div>
          {preview.add && (
            <div className="warn-text small">
              Adds to {preview.add.held} held → {preview.add.total} sh @ avg {preview.add.new_avg.toFixed(2)}. The new stop and
              target will cover all {preview.add.total} shares (risk {usd(preview.add.merged_risk)}).
            </div>
          )}
        </>
      )}
      {preview?.blockers.map((b) => <div key={b} className="error-text small">⛔ {b}</div>)}
      <button className="primary buy" disabled={!preview || preview.blockers.length > 0} onClick={onStage}>
        Stage market buy ↵
      </button>
    </section>
  )
}

/** Second click: shows exactly what will be sent; a NEW Enter press confirms, Esc backs out. */
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

  const a = preview.add
  return (
    <div className="modal" onClick={onClose}>
      <div className="card confirm" onClick={(e) => e.stopPropagation()}>
        <h3>Confirm market buy</h3>
        <p className="big">
          <span className="up">BUY</span> {preview.qty.toLocaleString()} {preview.symbol} at market <span className="muted">(~{preview.entry.toFixed(2)})</span>
        </p>
        <dl className="kv">
          <div><dt>Stop (market)</dt><dd>{preview.stop.toFixed(2)}{preview.trail ? ' · trails by 1R after +1R' : ''}</dd></div>
          <div><dt>Take profit (limit)</dt><dd>{preview.target?.toFixed(2) ?? 'none, trailing stop'} <span className="muted">{METHOD_LABEL[preview.tp_method] ?? preview.tp_method}</span></dd></div>
          <div><dt>Risk</dt><dd className="down">{usd(preview.risk)}</dd></div>
          <div><dt>Reward</dt><dd className="up">{preview.reward != null ? usd(preview.reward) : '—'}</dd></div>
          <div><dt>R:R</dt><dd>{preview.rr ?? '—'}</dd></div>
        </dl>
        {a && (
          <p className="warn-text small">
            You hold {a.held}. After this: {a.total} sh @ ~{a.new_avg.toFixed(2)}. Your old stop/target are replaced: this stop and
            target will cover all {a.total} shares. Total risk {usd(a.merged_risk)}.
          </p>
        )}
        <p className="muted small">Market buy: the fill can differ from the estimate. Stop and take-profit cancel each other (one-cancels-other); force-flat before the close is the backup exit.</p>
        <div className="row">
          <button onClick={onClose}>Cancel (Esc)</button>
          <button className="primary buy" disabled={busy} onClick={confirm}>{busy ? 'Sending…' : 'Confirm (Enter)'}</button>
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
