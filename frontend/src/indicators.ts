/** Simple moving average over a series with leading nulls (e.g. RSI); null until `n` values exist. */
export function sma(values: (number | null)[], n = 14): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null)
  let sum = 0
  let count = 0
  for (let i = 0; i < values.length; i++) {
    const v = values[i]
    if (v == null) continue
    sum += v
    count++
    if (count > n) sum -= values[i - n]!
    if (count >= n) out[i] = sum / n
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
