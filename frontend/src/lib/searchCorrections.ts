import { isGenre, normalizeSuggestion, type DiscoveryIndex } from './discoveryIndex'

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

export interface SearchCorrection {
  query: string
  distance: number
  automatic: boolean
}

interface VocabularyEntry { value: string; variants: string[] }
const vocabularyCache = new WeakMap<DiscoveryIndex, { whole: VocabularyEntry[]; words: VocabularyEntry[]; contexts: string[][] }>()

function vocabulary(index: DiscoveryIndex) {
  const cached = vocabularyCache.get(index)
  if (cached) return cached
  const whole: VocabularyEntry[] = []
  const words = new Map<string, VocabularyEntry>()
  const addWord = (value: string, variants: string[] = []) => {
    const normalized = normalizeSuggestion(value)
    if (normalized.length < 4 || normalized.includes(' ') || /[+#]/.test(normalized)) return
    const existing = words.get(normalized)
    if (existing) existing.variants.push(...variants)
    else words.set(normalized, { value, variants: [...variants] })
  }
  for (const topic of index.topics) {
    if (!topic.count || topic.kind === 'legacy') continue
    if (isGenre(topic)) {
      // Genre labels describe a filter, not necessarily a searchable phrase.
      // Correct a typo in "デザイン" to that alias, not "デザイン・アート".
      for (const value of [topic.label, ...topic.aliases]) {
        whole.push({ value, variants: [] })
        addWord(value)
      }
    } else {
      whole.push({ value: topic.label, variants: topic.aliases })
      addWord(topic.label, topic.aliases)
      if (normalizeSuggestion(topic.label).includes(' ')) for (const alias of topic.aliases) addWord(alias)
    }
  }
  const contexts = index.books.map(book => {
    const variants = book.title_reading ? [book.title_reading] : []
    whole.push({ value: book.title, variants })
    addWord(book.title, variants)
    // Use words actually present in catalog titles; do not invent synonyms or
    // arbitrary Japanese substrings. Authors are evidence only, never corrected.
    for (const part of book.title.normalize('NFKC').split(/[\s・:：,，。!?！？『』「」()（）【】\[\]]+/)) addWord(part)
    for (const match of book.title.normalize('NFKC').matchAll(/[a-zA-Z][a-zA-Z0-9+#.-]{3,}/g)) addWord(match[0])
    return [book.title, book.authors, book.title_reading ?? '', book.authors_reading ?? ''].map(normalizeSuggestion)
  })
  const result = { whole, words: [...words.values()], contexts }
  vocabularyCache.set(index, result)
  return result
}

function correctable(value: string): boolean {
  const shortKana = value.length === 3 && /^[\p{Script=Hiragana}ー]+$/u.test(value)
  return (value.length >= 4 || shortKana) && value.length <= 80 && !/^[\d\s-x]+$/i.test(value) && !/[+#]/.test(value)
}

// For equal-length strings, a different kanji may be a different subject or
// volume. Keep it as a selectable suggestion, even when it is the sole match.
function changesKanji(left: string, right: string): boolean {
  const a = Array.from(left), b = Array.from(right)
  if (a.length !== b.length) return false
  return a.some((char, i) => char !== b[i] && /\p{Script=Han}/u.test(char + b[i]))
}

export function searchCorrectionCandidates(index: DiscoveryIndex, query: string): SearchCorrection[] {
  const q = normalizeSuggestion(query)
  if (!correctable(q) || !/[\p{L}\p{N}]/u.test(q) || /^isbn\s*:/i.test(q)) return []
  const { whole, words, contexts } = vocabulary(index)
  // Exact known titles/aliases are deliberate inputs, even under a filter that
  // removes all their books. Never replace them with neighboring vocabulary.
  if (whole.some(entry => [entry.value, ...entry.variants].some(text => normalizeSuggestion(text) === q))) return []
  const tokens = query.normalize('NFKC').trim().split(/\s+/)
  if (contexts.some(fields => tokens.every(token => fields.some(field => field.includes(normalizeSuggestion(token)))))) return []
  const candidates = new Map<string, SearchCorrection>()
  const add = (replacement: string, distance: number, safe: boolean) => {
    const key = normalizeSuggestion(replacement)
    const previous = candidates.get(key)
    if (!previous || distance < previous.distance) candidates.set(key, { query: replacement, distance, automatic: safe })
    else if (distance === previous.distance && safe) previous.automatic = true
  }
  const compare = (input: string, entries: VocabularyEntry[], accept: (value: string, matched: string, distance: number) => void) => {
    if (!correctable(input)) return
    const max = input.length < 8 ? 1 : 2
    for (const entry of entries) {
      for (const text of [entry.value, ...entry.variants]) {
        const normalized = normalizeSuggestion(text)
        if (normalized.length < 4 || !correctable(normalized)) continue
        const distance = typoDistance(input, normalized, max)
        if (distance > 0 && distance <= max) accept(entry.value, normalized, distance)
      }
    }
  }
  if (tokens.length === 1) compare(q, whole, (value, matched, distance) => add(value, distance, q.length >= 4 && !changesKanji(q, matched)))

  if (tokens.length > 1 && tokens.length <= 4) {
    tokens.forEach((token, position) => {
      const input = normalizeSuggestion(token)
      // A token already occurring in a title or author must not be guessed.
      if (contexts.some(fields => fields.some(field => field.includes(input))) || words.some(entry =>
        [entry.value, ...entry.variants].some(text => normalizeSuggestion(text) === input))) return
      compare(input, words, (value, matched, distance) => {
        const correctedTokens = tokens.map((original, i) => i === position ? value : original)
        const normalizedTokens = correctedTokens.map(normalizeSuggestion)
        // Preserve every other term, and require a real book matching their
        // conjunction. Independent plausible words do not prove a good query.
        if (!contexts.some(fields => normalizedTokens.every(term => fields.some(field => field.includes(term))))) return
        add(correctedTokens.join(' '), distance, input.length >= 4 && !changesKanji(input, matched))
      })
    })
  }
  const ranked = [...candidates.values()].sort((a, b) => a.distance - b.distance || a.query.localeCompare(b.query, 'ja'))
  const best = ranked[0]?.distance
  const uniqueBest = ranked.filter(candidate => candidate.distance === best).length === 1
  // Determine ambiguity before truncation; alphabetical ordering is for
  // display only and must never break a confidence tie.
  return ranked.slice(0, 3).map(candidate => ({ ...candidate, automatic: candidate.automatic && uniqueBest && candidate.distance === best }))
}

export function searchCorrections(index: DiscoveryIndex, query: string): string[] {
  return searchCorrectionCandidates(index, query).map(candidate => candidate.query)
}
