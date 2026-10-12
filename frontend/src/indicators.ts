export type Divergence = { kind: 'bull' | 'bear'; i1: number; i2: number }

/** Regular RSI divergences between confirmed RSI pivots (extreme of `left` bars before and `right` after).
 *  bull: price lower low + RSI higher low; bear: price higher high + RSI lower high; pivots minBars..maxBars apart.
 *  A pivot is only known `right` bars later, so signals appear with that lag and never repaint. */
export function rsiDivergences(lows: number[], highs: number[], r: (number | null)[],
                               left = 5, right = 5, minBars = 5, maxBars = 60): Divergence[] {
  // Strictly beyond the bars before it, at least as extreme as the bars after it (so a flat stretch
  // doesn't produce a pivot on every bar).
  const isPivot = (i: number, sign: 1 | -1) => {
    const v = r[i]
    if (v == null || i - left < 0 || i + right >= r.length) return false
    for (let j = i - left; j <= i + right; j++) {
      if (j === i) continue
      const w = r[j]
      if (w == null) return false
      const d = sign * (v - w)  // >0: v more extreme than w
      if (j < i ? d <= 0 : d < 0) return false
    }
    return true
  }
  const out: Divergence[] = []
  let lastLow = -1
  let lastHigh = -1
  for (let i = 0; i < r.length; i++) {
    if (isPivot(i, -1)) {
      if (lastLow >= 0 && i - lastLow >= minBars && i - lastLow <= maxBars &&
          lows[i] < lows[lastLow] && r[i]! > r[lastLow]!) out.push({ kind: 'bull', i1: lastLow, i2: i })
      lastLow = i
    }
    if (isPivot(i, 1)) {
      if (lastHigh >= 0 && i - lastHigh >= minBars && i - lastHigh <= maxBars &&
          highs[i] > highs[lastHigh] && r[i]! < r[lastHigh]!) out.push({ kind: 'bear', i1: lastHigh, i2: i })
      lastHigh = i
    }
  }
  return out
}

/** Wilder's RSI. Returns one value per close (null until there are `n` changes). */
export function rsi(closes: number[], n = 14): (number | null)[] {
  const out: (number | null)[] = new Array(closes.length).fill(null)
  let gain = 0
  let loss = 0
  for (let i = 1; i < closes.length; i++) {
    const ch = closes[i] - closes[i - 1]
    const g = Math.max(ch, 0)
    const l = Math.max(-ch, 0)
    if (i <= n) {
      // Seed with a simple average of the first n changes.
      gain += g
      loss += l
      if (i < n) continue
      gain /= n
      loss /= n
    } else {
      // Wilder's smoothing.
      gain = (gain * (n - 1) + g) / n
      loss = (loss * (n - 1) + l) / n
    }
    out[i] = loss === 0 ? 100 : 100 - 100 / (1 + gain / loss)
  }
  return out
}
