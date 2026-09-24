import { describe, expect, it } from 'vitest'
import { groupBooksForIndex, indexGroupForTitle } from './indexGrouping'

describe('index grouping', () => {
  it('uses a kana row for hiragana and katakana titles', () => {
    expect(indexGroupForTitle('あの本')).toBe('あ')
    expect(indexGroupForTitle('ガラスの本')).toBe('か')
    expect(indexGroupForTitle('ヴォート生化学')).toBe('あ')
  })

  it('uses the morphological reading instead of a kanji heading', () => {
    expect(indexGroupForTitle('老人と海', 'ロウジントウミ')).toBe('ら')
    expect(indexGroupForTitle('図書館の本', 'トショカンノホン')).toBe('た')
    expect(indexGroupForTitle('未解析漢字')).toBe('その他')
  })

  it('separates alphabetic titles by their initial, including full-width characters', () => {
    expect(indexGroupForTitle('Apple')).toBe('A')
    expect(indexGroupForTitle('Zebra')).toBe('Z')
    expect(indexGroupForTitle('ｂook')).toBe('B')
  })

  it('groups kanji titles by reading alongside alphabetic initials', () => {
    const groups = groupBooksForIndex([
      { title: 'Zebra' },
      { title: '老人と海', title_reading: 'ロウジントウミ' },
      { title: 'Apple' },
      { title: '図書館の本', title_reading: 'トショカンノホン' },
    ])

    expect(groups.map(group => group.label)).toEqual(['た', 'ら', 'A', 'Z'])
  })
})
