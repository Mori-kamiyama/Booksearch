import { normalizeSuggestion, type DiscoveryIndex } from './discoveryIndex'

// Restricted Damerau-Levenshtein: an adjacent transposition counts as one typo.
export function typoDistance(a: string, b: string, max = 2): number {
  const left = Array.from(a), right = Array.from(b)
  if (Math.abs(left.length - right.length) > max) return max + 1
  let previousPrevious: number[] = []
  let previous = Array.from({ length: right.length + 1 }, (_, i) => i)
  for (let i = 1; i <= left.length; i++) {
    const current = [i]
    for (let j = 1; j <= right.length; j++) {
      current[j] = Math.min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + Number(left[i - 1] !== right[j - 1]))
      if (i > 1 && j > 1 && left[i - 1] === right[j - 2] && left[i - 2] === right[j - 1]) current[j] = Math.min(current[j], previousPrevious[j - 2] + 1)
    }
    previousPrevious = previous; previous = current
  }
  return previous[right.length]
}

export function searchCorrections(index: DiscoveryIndex, query: string): string[] {
  const q = normalizeSuggestion(query)
  // Never guess ISBNs, C/C++/C#, short names or unrestricted long sentences.
  if (q.length < 4 || q.length > 80 || /^[\d\s-x]+$/i.test(q) || /[+#]/.test(q)) return []
  const max = q.length < 8 ? 1 : 2
  const candidates = new Map<string, number>()
  let exact = false
  const consider = (value: string, variants: string[]) => {
    const canonical = normalizeSuggestion(value)
    for (const text of [value, ...variants]) {
      const normalized = normalizeSuggestion(text)
      if (normalized === q) { exact = true; continue }
      if (normalized.length < 4 || /[+#]/.test(normalized)) continue
      const distance = typoDistance(q, normalized, max)
      if (distance > 0 && distance <= max && canonical !== q) candidates.set(value, Math.min(candidates.get(value) ?? Infinity, distance))
    }
  }
  for (const topic of index.topics) if (topic.count && topic.kind !== 'legacy') consider(topic.label, topic.aliases)
  // Authors are omitted: similar person names are not necessarily misspellings.
  for (const book of index.books) consider(book.title, book.title_reading ? [book.title_reading] : [])
  if (exact) return []
  const seen = new Set<string>()
  return [...candidates].sort((a, b) => a[1] - b[1] || a[0].localeCompare(b[0], 'ja')).map(([value]) => value)
    .filter(value => { const key = normalizeSuggestion(value); if (seen.has(key)) return false; seen.add(key); return true }).slice(0, 3)
}
