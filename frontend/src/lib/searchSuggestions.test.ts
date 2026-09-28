import { describe, expect, it } from 'vitest'
import { buildSearchSuggestions } from './searchSuggestions'
import type { DiscoveryIndex } from './discoveryIndex'
const index: DiscoveryIndex = { version: 1, books: [
  { id: 1, title: 'C言語入門', authors: '入門 太郎' }, { id: 2, title: 'Ｃ言語入門', authors: '入門 太郎' },
  { id: 3, title: 'Python', authors: '田中' }, { id: 4, title: 'パイソン実践', authors: '田中' },
], topics: [{ id: 'c-language', label: 'C言語', aliases: ['C言語'], count: 2 }], coverage: { books: 4, page_count: 0, level: 0 } }
describe('local search suggestions', () => {
  it('ranks exact topics before partial titles and deduplicates width variants', () => {
    expect(buildSearchSuggestions(index, 'Ｃ言語')).toEqual([
      { kind: 'topic', value: 'C言語', topicId: 'c-language' }, { kind: 'title', value: 'C言語入門', topicId: undefined },
    ])
  })
  it('matches kana variants and authors without inventing readings', () => {
    expect(buildSearchSuggestions(index, 'ぱいそん')[0].value).toBe('パイソン実践')
    expect(buildSearchSuggestions(index, '田中')).toHaveLength(1)
    expect(buildSearchSuggestions(index, 'たなか')).toEqual([])
  })
  it('bounds results and ignores empty input', () => {
    expect(buildSearchSuggestions(index, '')).toEqual([])
    expect(buildSearchSuggestions({ ...index, books: Array.from({ length: 20 }, (_, i) => ({ id: i, title: `本 ${i}`, authors: '' })) }, '本')).toHaveLength(6)
  })
})
