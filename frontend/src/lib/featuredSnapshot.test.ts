import { describe, expect, it } from 'vitest'
import { parseFeaturedSnapshot } from './featuredSnapshot'

const book = (id: number, thumbnail = 'data:image/jpeg;base64,AA==') => ({
  id,
  title: `Book ${id}`,
  authors: 'Author',
  publisher: 'Publisher',
  published_date: '2026',
  class_number: '000',
  registration_number: `R-${id}`,
  isbn: `978000000000${id}`,
  thumbnail,
  info_link: null,
})

describe('featured bootstrap snapshot', () => {
  it('accepts a bounded weekly snapshot with embedded cover data', () => {
    const snapshot = parseFeaturedSnapshot({ week: '2026-39', books: [book(1), book(2)] })
    expect(snapshot?.books.map(item => item.id)).toEqual([1, 2])
  })

  it('rejects external image URLs and duplicate or oversized payloads', () => {
    expect(parseFeaturedSnapshot({ week: '2026-39', books: [book(1, 'https://example.test/cover.jpg')] })).toBeNull()
    expect(parseFeaturedSnapshot({ week: '2026-39', books: [book(1), book(1)] })).toBeNull()
    expect(parseFeaturedSnapshot({ week: '2026-39', books: Array.from({ length: 6 }, (_, index) => book(index + 1)) })).toBeNull()
  })
})
