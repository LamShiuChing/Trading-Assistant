import { useEffect, useState } from 'react'
import { api, ApiError, pushAlert } from '../ws'

type Rule = { field: string; op: string; value: number | string; action: 'require' | 'score'; points: number | string; on: boolean }
type Meta = { rules: Rule[]; fields: Record<string, string>; ops: string[] }

/** Your screener rules: "field op value" as a filter (Required) or as +/- points in the ranking. */
export function RulesEditor({ onClose }: { onClose: () => void }) {
  const [meta, setMeta] = useState<Meta>()
  const [rules, setRules] = useState<Rule[]>([])
  const [error, setError] = useState<string>()

  useEffect(() => {
    api<Meta>('/api/screener/rules').then((m) => (setMeta(m), setRules(m.rules)))
  }, [])

  const upd = (i: number, patch: Partial<Rule>) => setRules(rules.map((r, j) => (j === i ? { ...r, ...patch } : r)))

  async function save() {
    setError(undefined)
    try {
      const r = await fetch('/api/screener/rules', {
        method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify(rules),
      })
      const j = await r.json()
      if (!r.ok) throw new ApiError(j.detail)
      setRules(j.rules)
      pushAlert('info', `Saved ${j.rules.length} rule${j.rules.length === 1 ? '' : 's'}; the list is re-ranked`)
      onClose()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    }
  }

  if (!meta) return <div className="rules pad muted">Loading…</div>
  return (
    <div className="rules">
      <div className="muted small">
        <b>Required</b> rules are filters: a stock that fails one drops below the passing names, with the reason shown.
        <b> Points</b> rules add to (or subtract from) the score, so they change the ranking. Saving re-ranks immediately.
      </div>
      {rules.map((r, i) => (
        <div key={i} className="rule-row">
          <input type="checkbox" checked={r.on} title="On / off" onChange={(e) => upd(i, { on: e.target.checked })} />
          <select value={r.field} onChange={(e) => upd(i, { field: e.target.value })}>
            {Object.entries(meta.fields).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
          </select>
          <select value={r.op} onChange={(e) => upd(i, { op: e.target.value })} className="op">
            {meta.ops.map((o) => <option key={o}>{o}</option>)}
          </select>
          <input type="number" step="any" value={r.value} onChange={(e) => upd(i, { value: e.target.value })} className="num" />
          <select value={r.action} onChange={(e) => upd(i, { action: e.target.value as Rule['action'] })}>
            <option value="require">Required</option>
            <option value="score">Points</option>
          </select>
          {r.action === 'score'
            ? <input type="number" step="1" value={r.points} onChange={(e) => upd(i, { points: e.target.value })} className="num" title="+/- points" />
            : <span className="num" />}
          <button className="x" title="Delete" onClick={() => setRules(rules.filter((_, j) => j !== i))}>×</button>
        </div>
      ))}
      {!rules.length && <div className="muted small">No rules yet. Example: RSI 14 on 5-min &lt; 70 (Required), or Relative volume &gt; 5 (+10 points).</div>}
      {error && <div className="error-text small">{error}</div>}
      <div className="row">
        <button onClick={() => setRules([...rules, { field: 'rvol', op: '>', value: 5, action: 'score', points: 10, on: true }])}>+ Add rule</button>
        <span className="spacer" />
        <button onClick={onClose}>Cancel</button>
        <button className="primary paper" onClick={save}>Save &amp; re-rank</button>
      </div>
    </div>
  )
}
