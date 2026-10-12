import { useState } from 'react'
import { api, useStore } from '../ws'

const money = new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const fmt = (v: number | null | undefined) => (v == null ? '—' : money.format(v))
const px = (v: number | null | undefined) => (v == null ? '—' : v.toFixed(2))
const sign = (v: number | null | undefined) => (v == null || v === 0 ? '' : v > 0 ? 'up' : 'down')
const openChart = (symbol: string) => api('/api/chart', { symbol }).catch(() => {})

export function AccountSummary() {
  const s = useStore((st) => st.portfolio?.summary)
  if (!s) return <section className="side card-flat muted">Loading account…</section>
  const rows: [string, number | null | undefined, boolean][] = [
    ['Net liquidation', s.net_liq, false],
    ['Cash', s.cash, false],
    ['Buying power', s.buying_power, false],
    ['Day P/L', s.day_pnl, true],
    ['Unrealized', s.unrealized, true],
    ['Realized', s.realized, true],
  ]
  return (
    <section className="side card-flat">
      <h3>
        Account <span className="muted">{s.currency}</span>
      </h3>
      <dl className="kv">
        {rows.map(([k, v, signed]) => (
          <div key={k}>
            <dt>{k}</dt>
            <dd className={signed ? sign(v) : ''}>{fmt(v)}</dd>
          </div>
        ))}
      </dl>
    </section>
  )
}

export function PortfolioTabs() {
  const p = useStore((s) => s.portfolio)
  const connState = useStore((s) => s.connection?.state)
  const [tab, setTab] = useState<'positions' | 'orders' | 'fills'>('positions')
  const confirmed = connState === 'ready'

  return (
    <section className="bottom">
      <div className="tabs">
        {(['positions', 'orders', 'fills'] as const).map((t) => (
          <button key={t} className={tab === t ? 'on' : ''} onClick={() => setTab(t)}>
            {t[0].toUpperCase() + t.slice(1)} <span className="muted">{p?.[t].length ?? 0}</span>
          </button>
        ))}
      </div>
      <div className="table-wrap">
        {!p ? (
          <div className="muted pad">Loading…</div>
        ) : tab === 'positions' ? (
          <Table
            empty="No open positions"
            head={['Symbol', 'Qty', 'Avg', 'Last', 'Value', 'Unrl P/L', 'Stop', 'Target', 'R']}
            rows={p.positions.map((x) => [
              <button className="link" onClick={() => openChart(x.symbol)}>{x.symbol}</button>,
              <span className={x.qty > 0 ? 'up' : 'down'}>{x.qty}</span>,
              px(x.avg), px(x.last), fmt(x.value),
              <span className={sign(x.unrealized)}>{fmt(x.unrealized)}</span>,
              px(x.stop), px(x.target),
              <span className={sign(x.r)}>{x.r == null ? '—' : `${x.r > 0 ? '+' : ''}${x.r}R`}</span>,
            ])}
          />
        ) : tab === 'orders' ? (
          <Table
            empty="No working orders"
            head={['Id', 'Symbol', 'Side', 'Type', 'Qty', 'Limit', 'Stop', 'TIF', 'Filled', 'Status']}
            rows={p.orders.map((o) => [
              o.id, <button className="link" onClick={() => openChart(o.symbol)}>{o.symbol}</button>,
              <span className={o.action === 'BUY' ? 'up' : 'down'}>{o.action}</span>,
              o.type, o.qty, px(o.limit), px(o.stop), o.tif, o.filled,
              // Never show an order state IBKR hasn't confirmed on the current connection.
              confirmed ? o.status : <span className="warn-text">UNCONFIRMED</span>,
            ])}
          />
        ) : (
          <Table
            empty="No fills today"
            head={['Time', 'Symbol', 'Side', 'Qty', 'Price', 'Comm']}
            rows={p.fills.map((f) => [
              new Date(f.time).toLocaleTimeString('en-US', { timeZone: 'America/New_York', hour12: false }),
              <button className="link" onClick={() => openChart(f.symbol)}>{f.symbol}</button>,
              <span className={f.side === 'BOT' ? 'up' : 'down'}>{f.side}</span>,
              f.qty, px(f.price), fmt(f.commission),
            ])}
          />
        )}
      </div>
    </section>
  )
}

function Table({ head, rows, empty }: { head: string[]; rows: React.ReactNode[][]; empty: string }) {
  if (!rows.length) return <div className="muted pad">{empty}</div>
  return (
    <table>
      <thead>
        <tr>{head.map((h) => <th key={h}>{h}</th>)}</tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i}>{r.map((c, j) => <td key={j}>{c}</td>)}</tr>
        ))}
      </tbody>
    </table>
  )
}
