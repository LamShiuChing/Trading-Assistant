import { useEffect, useRef, useState } from 'react'
import {
  CandlestickSeries, CrosshairMode, HistogramSeries, LineSeries, LineStyle, createChart,
  type IChartApi, type IPriceLine, type ISeriesApi, type ISeriesPrimitive, type SeriesAttachedParameter,
  type Time, type UTCTimestamp,
} from 'lightweight-charts'
import type { CanvasRenderingTarget2D } from 'fancy-canvas' // ships with lightweight-charts
import { api, setTicket, useStore, type Levels, type Range, type Row, type Ticket } from '../ws'
import type { Preview } from './Ticket'

const TIMEFRAMES = [
  { tf: 1, key: '1', label: '1m' },
  { tf: 5, key: '5', label: '5m' },
  { tf: 15, key: 'f', label: '15m' },
]
const UP = '#22c55e'
const DOWN = '#ef4444'

/** Build N-minute bars from 1-min rows. Times are ET wall-clock, so buckets align to 09:30. */
function aggregate(rows: Row[], tf: number): Row[] {
  if (tf === 1) return rows
  const s = tf * 60
  const out: Row[] = []
  for (const r of rows) {
    const t = Math.floor(r.time / s) * s
    const b = out[out.length - 1]
    if (b && b.time === t) {
      b.high = Math.max(b.high, r.high)
      b.low = Math.min(b.low, r.low)
      b.close = r.close
      b.volume += r.volume
      b.vwap = r.vwap
    } else out.push({ ...r, time: t })
  }
  return out
}

const t = (x: number) => x as UTCTimestamp
const candle = (r: Row) => ({ time: t(r.time), open: r.open, high: r.high, low: r.low, close: r.close })
const vol = (r: Row) => ({ time: t(r.time), value: r.volume, color: r.close >= r.open ? '#22c55e55' : '#ef444455' })
// VWAP restarts each day. A segment takes its start point's color, so the last point before
// a gap (after-hours) is transparent: no line joins one session to the next.
const vwap = (r: Row, next?: Row) =>
  r.vwap == null ? { time: t(r.time) }
  : next && next.vwap == null ? { time: t(r.time), value: r.vwap, color: 'transparent' }
  : { time: t(r.time), value: r.vwap }

/** Shaded opening-range boxes drawn behind the candles. */
class RangeBoxes implements ISeriesPrimitive<Time> {
  boxes: { range: Range; color: string }[] = []
  tf = 1
  private p?: SeriesAttachedParameter<Time>

  attached(p: SeriesAttachedParameter<Time>) { this.p = p }
  detached() { this.p = undefined }
  set(boxes: { range: Range; color: string }[], tf: number) {
    this.boxes = boxes
    this.tf = tf
    this.p?.requestUpdate()
  }
  paneViews() {
    return [{
      zOrder: () => 'bottom' as const,
      renderer: () => ({
        draw: (target: CanvasRenderingTarget2D) => {
          const p = this.p
          if (!p) return
          const ts = p.chart.timeScale()
          const half = ts.options().barSpacing / 2
          const s = this.tf * 60
          target.useMediaCoordinateSpace(({ context: ctx }) => {
            for (const { range, color } of this.boxes) {
              // Box spans the bars (at this timeframe) that fall inside the range window.
              const x1 = ts.timeToCoordinate(t(Math.floor(range.start / s) * s))
              const x2 = ts.timeToCoordinate(t(Math.floor((range.end - 60) / s) * s))
              const y1 = p.series.priceToCoordinate(range.high)
              const y2 = p.series.priceToCoordinate(range.low)
              if (x1 == null || y1 == null || y2 == null) continue
              const right = (x2 ?? x1) + half
              ctx.fillStyle = color + '33'
              ctx.strokeStyle = color + 'cc'
              ctx.setLineDash(range.complete ? [] : [4, 3])
              ctx.fillRect(x1 - half, y1, right - x1 + half, y2 - y1)
              ctx.strokeRect(x1 - half, y1, right - x1 + half, y2 - y1)
            }
          })
        },
      }),
    }]
  }
}

const LEVELS: { key: keyof Levels; title: string; color: string; style: LineStyle }[] = [
  { key: 'pdh', title: 'PDH', color: '#94a3b8', style: LineStyle.Dashed },
  { key: 'pdl', title: 'PDL', color: '#94a3b8', style: LineStyle.Dashed },
  { key: 'pdc', title: 'PDC', color: '#64748b', style: LineStyle.Dotted },
  { key: 'pmh', title: 'PMH', color: '#a78bfa', style: LineStyle.Dashed },
  { key: 'pml', title: 'PML', color: '#a78bfa', style: LineStyle.Dashed },
]

type Api = {
  chart: IChartApi
  candles: ISeriesApi<'Candlestick'>
  vwap: ISeriesApi<'Line'>
  volume: ISeriesApi<'Histogram'>
  boxes: RangeBoxes
  lines: IPriceLine[]
}

const round2 = (x: number) => Math.round(x * 100) / 100
type LineKey = 'entry' | 'stop' | 'target'

export function Chart({ preview }: { preview?: Preview }) {
  const el = useRef<HTMLDivElement>(null)
  const apiRef = useRef<Api>(null)
  const rows = useRef<Row[]>([])
  const levels = useRef<Levels>({})
  const [tf, setTf] = useState(1)
  const [legend, setLegend] = useState<Row>()
  const symbolRef = useRef<string>(undefined)
  const chartMsg = useStore((s) => s.chart)
  symbolRef.current = chartMsg?.bars ? chartMsg.symbol : undefined
  const barMsg = useStore((s) => s.bar)
  const conn = useStore((s) => s.connection)
  const ticket = useStore((s) => s.ticket)
  const ticketRef = useRef<Ticket>(undefined)
  ticketRef.current = ticket
  const tlines = useRef<Partial<Record<LineKey, IPriceLine>>>({})
  const olines = useRef<IPriceLine[]>([])
  const olinesKey = useRef('')
  const portfolio = useStore((s) => s.portfolio)

  // Create the chart once.
  useEffect(() => {
    const chart = createChart(el.current!, {
      autoSize: true,
      layout: { background: { color: '#0e1116' }, textColor: '#8a919c', fontFamily: 'system-ui, Segoe UI, sans-serif' },
      grid: { vertLines: { color: '#1a1f27' }, horzLines: { color: '#1a1f27' } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: '#242b35' },
      timeScale: { borderColor: '#242b35', timeVisible: true, secondsVisible: false, rightOffset: 8 },
    })
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: UP, downColor: DOWN, wickUpColor: UP, wickDownColor: DOWN, borderVisible: false,
    })
    const vw = chart.addSeries(LineSeries, {
      color: '#f59e0b', lineWidth: 2, priceLineVisible: false, lastValueVisible: false,
      crosshairMarkerVisible: false, title: 'VWAP',
    })
    const volume = chart.addSeries(HistogramSeries, { priceFormat: { type: 'volume' }, priceLineVisible: false, lastValueVisible: false }, 1)
    chart.panes()[1].setStretchFactor(0.25)
    const boxes = new RangeBoxes()
    candles.attachPrimitive(boxes)
    chart.subscribeCrosshairMove((p) => {
      const c = p.seriesData.get(candles) as { open: number; high: number; low: number; close: number } | undefined
      const v = p.seriesData.get(volume) as { value: number } | undefined
      setLegend(c && p.time ? { time: p.time as number, ...c, volume: v?.value ?? 0, vwap: null } : undefined)
    })
    apiRef.current = { chart, candles, vwap: vw, volume, boxes, lines: [] }
    return () => chart.remove()
  }, [])

  function drawLevels(tfNow: number) {
    const a = apiRef.current!
    a.lines.forEach((l) => a.candles.removePriceLine(l))
    const lv = levels.current
    a.lines = LEVELS.filter((l) => lv[l.key] != null).map((l) =>
      a.candles.createPriceLine({
        price: lv[l.key] as number, color: l.color, lineStyle: l.style, lineWidth: 1, title: l.title, axisLabelVisible: true,
      }),
    )
    a.boxes.set(
      [lv.or15 && { range: lv.or15, color: '#38bdf8' }, lv.or5 && { range: lv.or5, color: '#f472b6' }].filter(Boolean) as {
        range: Range; color: string
      }[],
      tfNow,
    )
  }

  function drawAll(tfNow: number) {
    const a = apiRef.current!
    const data = aggregate(rows.current, tfNow)
    a.candles.setData(data.map(candle))
    a.vwap.setData(data.map((r, i) => vwap(r, data[i + 1])))
    a.volume.setData(data.map(vol))
    drawLevels(tfNow)
  }

  // New symbol (full history).
  useEffect(() => {
    if (!chartMsg?.bars) return
    rows.current = chartMsg.bars
    if (ticketRef.current && ticketRef.current.symbol !== chartMsg.symbol) setTicket(undefined)
    levels.current = chartMsg.levels ?? {}
    drawAll(tf)
    apiRef.current!.chart.timeScale().scrollToRealTime()
  }, [chartMsg]) // eslint-disable-line react-hooks/exhaustive-deps

  // Live updates: merge the last 1-min rows, then redraw only the affected bar(s).
  useEffect(() => {
    if (!barMsg || barMsg.symbol !== chartMsg?.symbol || !rows.current.length) return
    const rs = rows.current
    for (const r of barMsg.rows) {
      const i = rs.findLastIndex((x) => x.time === r.time)
      if (i >= 0) rs[i] = r
      else if (r.time > rs[rs.length - 1].time) rs.push(r)
    }
    const a = apiRef.current!
    const tail = aggregate(rs.slice(-(tf * 2 + 2)), tf)
    const firstNew = Math.floor(barMsg.rows[0].time / (tf * 60)) * tf * 60
    // update() only accepts the newest drawn bar or a newer one.
    const lastDrawn = (a.candles.data().at(-1)?.time as number | undefined) ?? 0
    tail.forEach((b, i) => {
      if (b.time < Math.max(firstNew, lastDrawn)) return
      a.candles.update(candle(b))
      a.vwap.update(vwap(b, tail[i + 1]))
      a.volume.update(vol(b))
    })
    levels.current = barMsg.levels
    drawLevels(tf)
  }, [barMsg]) // eslint-disable-line react-hooks/exhaustive-deps

  // Timeframe switch.
  useEffect(() => {
    if (rows.current.length) drawAll(tf)
  }, [tf]) // eslint-disable-line react-hooks/exhaustive-deps

  // Entry / stop / target lines for the staged ticket.
  useEffect(() => {
    const a = apiRef.current!
    Object.values(tlines.current).forEach((l) => l && a.candles.removePriceLine(l))
    tlines.current = {}
    if (!ticket || ticket.symbol !== chartMsg?.symbol) return
    const mk = (price: number, color: string, title: string) =>
      a.candles.createPriceLine({ price, color, lineWidth: 2, lineStyle: LineStyle.Solid, title, axisLabelVisible: true })
    tlines.current.entry = mk(ticket.entry, '#3b82f6', `${ticket.side === 'BUY' ? 'BUY' : 'SELL'} ${preview?.qty ?? ''}`)
    tlines.current.stop = mk(ticket.stop, '#ef4444', `STOP ${preview ? '-$' + preview.risk.toFixed(0) : ''}`)
    const tgt = ticket.target ?? preview?.target
    if (tgt != null) tlines.current.target = mk(tgt, '#22c55e', `TARGET ${preview?.rr ? preview.rr + 'R' : ''}`)
  }, [ticket, preview, chartMsg?.symbol])

  // Your position and working orders on this symbol, as dashed lines (read-only).
  useEffect(() => {
    const a = apiRef.current!
    const sym = chartMsg?.symbol
    const lines: { price: number; color: string; title: string; style: LineStyle }[] = []
    for (const p of portfolio?.positions ?? []) {
      if (p.symbol === sym && p.avg != null)
        lines.push({ price: p.avg, color: '#e5e7eb', style: LineStyle.Dashed,
                     title: `POS ${p.qty > 0 ? '+' : ''}${p.qty}${p.r != null ? ` ${p.r > 0 ? '+' : ''}${p.r}R` : ''}` })
    }
    for (const o of portfolio?.orders ?? []) {
      if (o.symbol !== sym) continue
      const left = o.qty - o.filled
      if (o.stop != null)
        lines.push({ price: o.stop, color: '#ef4444', style: LineStyle.LargeDashed, title: `${o.type === 'TRAIL' ? 'TRAIL' : 'STOP'} ${left}` })
      else if (o.limit != null)
        lines.push(o.parent
          ? { price: o.limit, color: '#22c55e', style: LineStyle.LargeDashed, title: `TP ${left}` }
          : { price: o.limit, color: '#3b82f6', style: LineStyle.LargeDashed, title: `${o.action} ${left} LMT` })
    }
    const key = JSON.stringify(lines)
    if (key === olinesKey.current) return  // portfolio pushes ~3/s; only redraw when something changed
    olinesKey.current = key
    olines.current.forEach((l) => a.candles.removePriceLine(l))
    olines.current = lines.map((l) => a.candles.createPriceLine({ ...l, lineWidth: 1, axisLabelVisible: true }))
  }, [portfolio, chartMsg?.symbol])

  // Drag those lines with the mouse. The chart's own panning is paused while dragging.
  useEffect(() => {
    const box = el.current!
    let drag: LineKey | null = null
    const y = (e: MouseEvent) => e.clientY - box.getBoundingClientRect().top
    const hit = (py: number): LineKey | null => {
      const a = apiRef.current!
      for (const k of ['stop', 'target', 'entry'] as const) {
        const l = tlines.current[k]
        const ly = l && a.candles.priceToCoordinate(l.options().price)
        if (ly != null && Math.abs(ly - py) < 6) return k
      }
      return null
    }
    const down = (e: MouseEvent) => {
      const k = hit(y(e))
      if (!k) return
      drag = k
      apiRef.current!.chart.applyOptions({ handleScroll: false, handleScale: false })
      e.preventDefault()
      e.stopPropagation()
    }
    const move = (e: MouseEvent) => {
      if (!drag) {
        box.style.cursor = hit(y(e)) ? 'ns-resize' : ''
        return
      }
      const p = apiRef.current!.candles.coordinateToPrice(y(e))
      if (p != null) tlines.current[drag]!.applyOptions({ price: round2(p) })
    }
    const up = () => {
      if (!drag) return
      const t = ticketRef.current
      if (t) setTicket({ ...t, [drag]: tlines.current[drag]!.options().price })
      drag = null
      apiRef.current!.chart.applyOptions({ handleScroll: true, handleScale: true })
    }
    box.addEventListener('mousedown', down, true)
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
    return () => {
      box.removeEventListener('mousedown', down, true)
      window.removeEventListener('mousemove', move)
      window.removeEventListener('mouseup', up)
    }
  }, [])

  // Keyboard: 1 / 5 / F switch timeframe, B / S stage a long / short, Esc drops it (ignored while typing).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).closest('input, textarea') || e.ctrlKey || e.metaKey || e.altKey) return
      const k = e.key.toLowerCase()
      const hit = TIMEFRAMES.find((x) => x.key === k)
      if (hit) setTf(hit.tf)
      if ((k === 'b' || k === 's') && rows.current.length && symbolRef.current) {
        // Stage at the last price; stop just beyond the last 5 one-minute bars. Drag the lines to adjust.
        const rs = rows.current, last = rs[rs.length - 1].close, recent = rs.slice(-5), long = k === 'b'
        let stop = round2(long ? Math.min(...recent.map((r) => r.low)) - 0.01 : Math.max(...recent.map((r) => r.high)) + 0.01)
        if (long ? stop >= last : stop <= last) stop = round2(long ? last * 0.995 : last * 1.005)
        setTicket({ symbol: symbolRef.current, side: long ? 'BUY' : 'SELL', entry: round2(last), stop,
                    tp_method: 'r', target_r: 2, target: null, qty: null })
      }
      if (e.key === 'Escape' && !document.querySelector('.modal')) setTicket(undefined)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const last = rows.current.length ? aggregate(rows.current.slice(-tf * 2), tf).at(-1) : undefined
  const shown = legend ?? last
  return (
    <section className="chart">
      <div className="chart-bar">
        <SymbolSearch current={chartMsg?.symbol ?? ''} disabled={conn?.state !== 'ready' && conn?.state !== 'degraded'} />
        <div className="seg small">
          {TIMEFRAMES.map((x) => (
            <button key={x.tf} className={tf === x.tf ? 'on' : ''} onClick={() => setTf(x.tf)} title={`Shortcut: ${x.key.toUpperCase()}`}>
              {x.label}
            </button>
          ))}
        </div>
        {shown && (
          <span className="legend">
            O <b>{shown.open.toFixed(2)}</b> H <b>{shown.high.toFixed(2)}</b> L <b>{shown.low.toFixed(2)}</b> C{' '}
            <b className={shown.close >= shown.open ? 'up' : 'down'}>{shown.close.toFixed(2)}</b> V <b>{fmtVol(shown.volume)}</b>
          </span>
        )}
        <span className="legend keys">
          <i style={{ color: '#f59e0b' }}>VWAP</i> <i style={{ color: '#f472b6' }}>OR5</i> <i style={{ color: '#38bdf8' }}>OR15</i>
        </span>
      </div>
      <div className="chart-canvas" ref={el}>
        {chartMsg?.loading && <div className="chart-overlay">Loading {chartMsg.symbol}…</div>}
        {chartMsg?.error && <div className="chart-overlay error-text">{chartMsg.error}</div>}
      </div>
    </section>
  )
}

function fmtVol(v: number) {
  return v >= 1e6 ? (v / 1e6).toFixed(2) + 'M' : v >= 1e3 ? (v / 1e3).toFixed(1) + 'K' : String(Math.round(v))
}

function SymbolSearch({ current, disabled }: { current: string; disabled: boolean }) {
  const input = useRef<HTMLInputElement>(null)
  const [text, setText] = useState('')
  const [matches, setMatches] = useState<{ symbol: string; exchange: string; name: string }[]>([])
  const [error, setError] = useState<string>()

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === '/' && !(e.target as HTMLElement).closest('input, textarea')) {
        e.preventDefault()
        input.current?.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  useEffect(() => {
    const q = text.trim()
    if (!q) return setMatches([])
    const id = setTimeout(() => api(`/api/symbols?q=${encodeURIComponent(q)}`).then(setMatches).catch(() => setMatches([])), 250)
    return () => clearTimeout(id)
  }, [text])

  async function choose(symbol: string) {
    setText('')
    setMatches([])
    setError(undefined)
    input.current?.blur()
    try {
      await api('/api/chart', { symbol })
    } catch (e) {
      setError(String((e as Error).message))
    }
  }

  return (
    <div className="search">
      <input
        ref={input}
        value={text}
        disabled={disabled}
        placeholder={current || 'Symbol'}
        title="Shortcut: /"
        onChange={(e) => setText(e.target.value.toUpperCase())}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && text.trim()) choose(text.trim())
          if (e.key === 'Escape') (setText(''), input.current?.blur())
        }}
        onBlur={() => setTimeout(() => setMatches([]), 150)}
      />
      {matches.length > 0 && (
        <ul className="matches">
          {matches.slice(0, 8).map((m) => (
            <li key={m.symbol + m.exchange} onMouseDown={() => choose(m.symbol)}>
              <b>{m.symbol}</b> <span className="muted">{m.name || m.exchange}</span>
            </li>
          ))}
        </ul>
      )}
      {error && <span className="error-text">{error}</span>}
    </div>
  )
}
