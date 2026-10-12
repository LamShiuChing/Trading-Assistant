import { api, useStore } from '../ws'

export type SignalItem = {
  id: number; time: string; symbol: string; setup: string; entry: number; stop: number; now_r: number | null
  features: { bucket: string; risk_pct: number; rvol: number | null; catalyst: string | null; rs_qqq?: number; rs_sector?: number; note: string }
  outcome: { result_r: number; mfe_r: number; mae_r: number; r_15m?: number } | null
}
type Row = { n: number; win_pct: number; avg_r: number; avg_mfe: number; avg_mae: number }
export type SignalStats = { total: number; setup: Record<string, Row>; time: Record<string, Row>; catalyst: Record<string, Row> }

const r = (v: number | null | undefined) => (v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(2)}R`)
const cls = (v: number | null | undefined) => (v == null ? '' : v > 0 ? 'up' : v < 0 ? 'down' : '')

export function SignalsView({ onOpen }: { onOpen: () => void }) {
  const s = useStore((st) => st.signals)
  const open = (symbol: string) => (api('/api/chart', { symbol }).catch(() => {}), onOpen())

  return (
    <main className="workspace signals-view">
      <section className="signals">
        <div className="panel-head">
          <h3>Signals today</h3>
          <span className="muted small">watching {s?.watching.join(', ') || '…'} · long only · logged whether you take them or not</span>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Time</th><th>Symbol</th><th>Setup</th><th>Entry</th><th>Stop</th><th>Risk</th><th>Now</th>
                <th>RVOL</th><th>Catalyst</th><th title="Today's % change vs QQQ">RS QQQ</th><th title="vs sector ETF">RS sect</th><th>Result</th><th>Why</th></tr>
            </thead>
            <tbody>
              {s?.today.map((x) => (
                <tr key={x.id}>
                  <td>{x.time}</td>
                  <td><button className="link" onClick={() => open(x.symbol)}>{x.symbol}</button></td>
                  <td>{x.setup}</td>
                  <td>{x.entry.toFixed(2)}</td>
                  <td>{x.stop.toFixed(2)}</td>
                  <td>{x.features.risk_pct.toFixed(2)}%</td>
                  <td className={cls(x.now_r)}>{r(x.now_r)}</td>
                  <td>{x.features.rvol?.toFixed(1) ?? '—'}</td>
                  <td>{x.features.catalyst ? <span className="chip">{x.features.catalyst}</span> : '—'}</td>
                  <td className={cls(x.features.rs_qqq)}>{x.features.rs_qqq?.toFixed(2) ?? '—'}</td>
                  <td className={cls(x.features.rs_sector)}>{x.features.rs_sector?.toFixed(2) ?? '—'}</td>
                  <td className={cls(x.outcome?.result_r)}>{x.outcome ? r(x.outcome.result_r) : <span className="muted">after close</span>}</td>
                  <td className="muted small">{x.features.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {s && !s.today.length && <div className="muted pad">No signals yet today. They appear 09:30–16:00 for the stocks being watched.</div>}
        </div>
      </section>
      <section className="stats">
        <div className="panel-head">
          <h3>Stats</h3>
          <span className="muted small">{s?.stats.total ?? 0} finished signals · simulated trade: stop = −1R, 2R target, else exit at the close</span>
        </div>
        <div className="table-wrap pad-x">
          {s && s.stats.total === 0 && <p className="muted small">Stats fill in after the close each day, once outcomes are computed.</p>}
          {s && s.stats.total > 0 && (['setup', 'time', 'catalyst'] as const).map((g) => (
            <StatsTable key={g} title={g === 'setup' ? 'By setup' : g === 'time' ? 'By time of day' : 'By catalyst'} rows={s.stats[g]} />
          ))}
          <p className="muted small">Small samples lie: trust a row only after ~30+ signals. Expectancy = average R per signal.</p>
        </div>
      </section>
    </main>
  )
}

function StatsTable({ title, rows }: { title: string; rows: Record<string, Row> }) {
  return (
    <>
      <h4>{title}</h4>
      <table>
        <thead><tr><th>Group</th><th>n</th><th>Win %</th><th>Expectancy</th><th>Avg MFE</th><th>Avg MAE</th></tr></thead>
        <tbody>
          {Object.entries(rows).map(([k, v]) => (
            <tr key={k} className={v.n < 30 ? 'dim' : ''}>
              <td>{k}</td><td>{v.n}</td><td>{v.win_pct.toFixed(0)}%</td>
              <td className={cls(v.avg_r)}>{r(v.avg_r)}</td><td>{r(v.avg_mfe)}</td><td>{r(v.avg_mae)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  )
}
