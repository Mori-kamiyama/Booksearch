import { describe, expect, it } from 'vitest'
import { groupBooksForIndex, indexGroupForTitle } from './indexGrouping'

describe('index grouping', () => {
  it('uses a kana row for hiragana and katakana titles', () => {
    expect(indexGroupForTitle('あの本')).toBe('あ')
    expect(indexGroupForTitle('ガラスの本')).toBe('か')
  })

  it('keeps the leading kanji as an index heading', () => {
    expect(indexGroupForTitle('老人と海')).toBe('老')
    expect(indexGroupForTitle('図書館の本')).toBe('図')
  })

  it('separates alphabetic titles by their initial, including full-width characters', () => {
    expect(indexGroupForTitle('Apple')).toBe('A')
    expect(indexGroupForTitle('Zebra')).toBe('Z')
    expect(indexGroupForTitle('ｂook')).toBe('B')
  })

  it('creates separate groups for distinct kanji and alphabetic initials', () => {
    const groups = groupBooksForIndex([
      { title: 'Zebra' },
      { title: '老人と海' },
      { title: 'Apple' },
      { title: '図書館の本' },
    ])

    expect(groups.map(group => group.label)).toEqual(['A', 'Z', '図', '老'])
  })
})
