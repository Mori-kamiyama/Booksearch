export interface DiscoveryBook { id: number; title: string; authors: string }
export interface DiscoveryTopic { id: string; label: string; aliases: string[]; count: number; genres?: string[] }
export interface DiscoveryIndex { version: number; books: DiscoveryBook[]; topics: DiscoveryTopic[]; coverage: { books: number; page_count: number; level: number } }
let cached: Promise<DiscoveryIndex> | undefined
export function loadDiscoveryIndex(): Promise<DiscoveryIndex> {
  if (!cached) {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), 10_000)
    cached = fetch('/search-index.json', { signal: controller.signal }).then(async response => {
    if (!response.ok) throw new Error('Suggestion index unavailable')
    const data = await response.json() as DiscoveryIndex
    if (data.version !== 1 || !Array.isArray(data.books) || !Array.isArray(data.topics)) throw new Error('Invalid suggestion index')
    return data
  }).catch(error => { cached = undefined; throw error }).finally(() => clearTimeout(timer))
  }
  return cached
}
export function normalizeSuggestion(text: string): string {
  return text.normalize('NFKC').toLocaleLowerCase().replace(/[ァ-ヶ]/g, char => String.fromCharCode(char.charCodeAt(0) - 0x60)).replace(/\s+/g, ' ').trim()
}
