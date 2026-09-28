import type { DiscoveryIndex } from './discoveryIndex'
import { normalizeSuggestion } from './discoveryIndex'
export interface SearchSuggestion { value: string; kind: 'title' | 'author' | 'topic'; topicId?: string }
export function buildSearchSuggestions(index: DiscoveryIndex, query: string): SearchSuggestion[] {
  const q = normalizeSuggestion(query)
  if (!q) return []
  const terms = q.split(' ')
  const candidates: Array<SearchSuggestion & { score: number }> = []
  const seen = new Set<string>()
  const add = (value: string, kind: SearchSuggestion['kind'], topicId?: string, aliases: string[] = []) => {
    const key = `${kind}:${normalizeSuggestion(value)}`
    if (!value.trim() || seen.has(key)) return
    seen.add(key)
    const matches = [value, ...aliases].map(normalizeSuggestion).filter(text => terms.every(term => text.includes(term)))
    if (!matches.length) return
    const score = Math.max(...matches.map(text => text === q ? 3 : text.startsWith(q) ? 2 : 1))
    candidates.push({ value, kind, topicId, score })
  }
  for (const topic of index.topics) if (topic.count > 0) add(topic.label, 'topic', topic.id, topic.aliases)
  for (const book of index.books) { add(book.title, 'title'); add(book.authors, 'author') }
  return candidates.sort((a, b) => b.score - a.score || (a.kind === 'topic' ? 0 : 1) - (b.kind === 'topic' ? 0 : 1) || a.value.localeCompare(b.value, 'ja')).slice(0, 6).map(({ score: _, ...item }) => item)
}
