import { api, useStore } from '../ws'

export type NewsItem = {
  symbol: string; provider: string; headline: string; time: string; offering: boolean
  tags: { catalyst: string; direction: 'bullish' | 'bearish' | 'neutral'; surprise: 'surprise' | 'routine'; is_offering: boolean } | null
}

const LABEL: Record<string, string> = {
  earnings: 'Earnings', guidance: 'Guidance', analyst: 'Analyst', 'm&a': 'M&A', fda_regulatory: 'FDA/Reg',
  contract_product: 'Contract/Product', offering: 'Offering', insider: 'Insider', legal: 'Legal',
  macro_sector: 'Macro/Sector', other: 'Other', none: 'No news',
}

/** Headlines with their Claude tags. `symbol` limits it to one stock (compact, in the Trade view). */
export function NewsPanel({ symbol, compact }: { symbol?: string; compact?: boolean }) {
  const news = useStore((s) => s.news)
  const items = (news?.items ?? []).filter((n) => !symbol || n.symbol === symbol).slice(0, compact ? 8 : 150)
  return (
    <section className={`news ${compact ? 'side card-flat' : ''}`}>
      <div className="panel-head">
        <h3>{symbol ? `News · ${symbol}` : 'News & catalysts'}</h3>
        {!compact && news?.model && !news.tagger_error && <span className="muted small">tagged by {news.model}</span>}
      </div>
      {news?.tagger_error && <div className="warn-text small pad-x">{news.tagger_error}</div>}
      <div className={compact ? '' : 'table-wrap'}>
        {items.map((n, i) => (
          <div key={i} className={`news-item ${n.offering ? 'offering' : ''}`}>
            <div className="news-meta">
              <span className="muted">{n.time}</span>
              {!symbol && (
                <button className="link" onClick={() => api('/api/chart', { symbol: n.symbol }).catch(() => {})}>{n.symbol}</button>
              )}
              {n.offering && <span className="chip danger">OFFERING · dilution</span>}
              {n.tags && n.tags.catalyst !== 'none' && <span className="chip">{LABEL[n.tags.catalyst] ?? n.tags.catalyst}</span>}
              {n.tags && <span className={`chip ${n.tags.direction === 'bullish' ? 'up' : n.tags.direction === 'bearish' ? 'down' : ''}`}>{n.tags.direction}</span>}
              {n.tags?.surprise === 'surprise' && <span className="chip warn">surprise</span>}
              <span className="muted provider">{n.provider}</span>
            </div>
            <div className="news-headline">{n.headline}</div>
          </div>
        ))}
        {news && !items.length && <div className="muted pad small">No headlines{symbol ? ` for ${symbol}` : ''} in the last 2 days.</div>}
        {!news && <div className="muted pad small">Loading headlines…</div>}
      </div>
    </section>
  )
}
