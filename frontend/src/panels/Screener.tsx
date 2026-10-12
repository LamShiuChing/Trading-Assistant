import { Fragment, useState } from 'react'
import { api, ApiError, pushAlert, useStore } from '../ws'
import { RulesEditor } from './Rules'

export type ScreenRow = {
  symbol: string; price: number; change_pct: number; gap_atr: number; rvol: number | null
  avg_usd_vol: number; spread_pct: number | null; industry: string; catalyst: string | null
  score: number; parts: Record<string, number>; fails: string[]; rule_hits?: string[]
  rsi_5m?: number | null; vwap_dist_pct?: number | null; pct_from_hod?: number | null
}

const PART_COLOR: Record<string, string> = {
  gap: '#3b82f6', rvol: '#a78bfa', liquidity: '#22c55e', catalyst: '#f59e0b', spread: '#ef4444', rules: '#e879f9',
}
const money = (v: number) => (v >= 1e9 ? `${(v / 1e9).toFixed(1)}B` : `${(v / 1e6).toFixed(0)}M`)

export function ScreenerPanel({ onOpen }: { onOpen: () => void }) {
  const data = useStore((s) => s.screener)
  const current = useStore((s) => s.chart?.symbol)
  const [open, setOpen] = useState<string>()
  const [scanning, setScanning] = useState(false)
  const [editing, setEditing] = useState(false)

  async function rescan() {
    setScanning(true)
    try {
      await api('/api/screener/scan', {})
    } catch (e) {
      pushAlert('error', e instanceof ApiError ? e.message : String(e))
    }
    setScanning(false)
  }

  return (
    <section className="screener">
      <div className="panel-head">
        <h3>Screener</h3>
        <span className="muted small">{data ? `updated ${data.updated}` : 'waiting for first scan…'}</span>
        <button className="mini rules-btn" onClick={() => setEditing(!editing)}>Rules</button>
        <button className="mini" disabled={scanning} onClick={rescan}>{scanning ? 'Scanning…' : 'Rescan'}</button>
      </div>
      {editing && <RulesEditor onClose={() => setEditing(false)} />}
      <div className="table-wrap">
        <table className="screen">
          <thead>
            <tr><th>Symbol</th><th>Price</th><th title="vs prior close">Chg%</th><th title="Gap vs prior close, in daily ATRs">Gap (ATR)</th>
              <th title="Volume so far vs average at this time of day">RVOL</th>
              <th title="RSI 14 on 5-min bars">RSI</th><th title="Distance from VWAP">vs VWAP</th><th title="Below high of day">from HOD</th>
              <th>Avg $vol</th><th>Spread</th>
              <th>Catalyst</th><th>Sector</th><th>Score</th><th>Fails</th></tr>
          </thead>
          <tbody>
            {data?.rows.map((r) => (
              <Fragment key={r.symbol}>
                <tr className={`${r.fails.length ? 'dim' : ''} ${r.symbol === current ? 'sel' : ''}`}
                  onClick={() => setOpen(open === r.symbol ? undefined : r.symbol)}>
                  <td>
                    <button className="link" title="Open in the Trade view" onClick={(e) => {
                      e.stopPropagation()
                      api('/api/chart', { symbol: r.symbol }).catch(() => {})
                      onOpen()
                    }}><b>{r.symbol}</b></button>
                  </td>
                  <td>{r.price.toFixed(2)}</td>
                  <td className={r.change_pct >= 0 ? 'up' : 'down'}>{r.change_pct > 0 ? '+' : ''}{r.change_pct.toFixed(1)}%</td>
                  <td>{r.gap_atr.toFixed(1)}</td>
                  <td>{r.rvol?.toFixed(1) ?? '—'}</td>
                  <td>{r.rsi_5m?.toFixed(0) ?? '—'}</td>
                  <td className={(r.vwap_dist_pct ?? 0) >= 0 ? 'up' : 'down'}>{r.vwap_dist_pct != null ? r.vwap_dist_pct.toFixed(1) + '%' : '—'}</td>
                  <td>{r.pct_from_hod != null ? r.pct_from_hod.toFixed(1) + '%' : '—'}</td>
                  <td>{money(r.avg_usd_vol)}</td>
                  <td>{r.spread_pct != null ? r.spread_pct.toFixed(2) + '%' : '—'}</td>
                  <td>{r.catalyst ? <span className="chip">{r.catalyst}</span> : <span className="muted">—</span>}</td>
                  <td className="muted">{r.industry || '—'}</td>
                  <td>
                    <span className="score">{r.score.toFixed(0)}</span>
                    <span className="partbar">
                      {Object.entries(r.parts).filter(([, v]) => v > 0).map(([k, v]) => (
                        <i key={k} style={{ width: `${v}%`, background: PART_COLOR[k] }} />
                      ))}
                    </span>
                  </td>
                  <td className="error-text small">{r.fails.join(', ')}</td>
                </tr>
                {open === r.symbol && (
                  <tr className="detail">
                    <td colSpan={14}>
                      <div className="parts">
                        {Object.entries(r.parts).map(([k, v]) => (
                          <span key={k}><i style={{ background: PART_COLOR[k] }} />{k} {v > 0 ? '+' : ''}{v}</span>
                        ))}
                      </div>
                      <div className="muted small">
                        ${r.price.toFixed(2)} · avg $vol {money(r.avg_usd_vol)} · spread {r.spread_pct?.toFixed(2) ?? '—'}% · {r.industry || 'n/a'}
                      </div>
                      {r.rule_hits && r.rule_hits.length > 0 && <div className="small" style={{ color: '#e879f9' }}>Your rules: {r.rule_hits.join(', ')}</div>}
                      {r.fails.length > 0 && <div className="error-text small">Fails: {r.fails.join(', ')}</div>}
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
        {data && !data.rows.length && <div className="muted pad">No candidates right now.</div>}
      </div>
    </section>
  )
}
