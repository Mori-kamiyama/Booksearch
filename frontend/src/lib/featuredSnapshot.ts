import type { Book, FeaturedSnapshot } from './types'

const WEEK_PATTERN = /^\d{4}-\d{1,2}$/
const DATA_IMAGE_PATTERN = /^data:image\/(?:jpeg|png|webp);base64,[A-Za-z0-9+/]+={0,2}$/

function isSnapshotBook(value: unknown): value is Book {
  if (!value || typeof value !== 'object') return false
  const book = value as Partial<Book>
  if (!Number.isInteger(book.id) || Number(book.id) <= 0 || typeof book.title !== 'string') return false
  if (typeof book.authors !== 'string' || typeof book.publisher !== 'string' || typeof book.published_date !== 'string') return false
  if (typeof book.class_number !== 'string' || typeof book.registration_number !== 'string' || typeof book.isbn !== 'string') return false
  if (typeof book.thumbnail !== 'string' || !DATA_IMAGE_PATTERN.test(book.thumbnail)) return false
  if (book.info_link !== null && typeof book.info_link !== 'string') return false
  return true
}

export function parseFeaturedSnapshot(value: unknown): FeaturedSnapshot | null {
  if (!value || typeof value !== 'object') return null
  const snapshot = value as Partial<FeaturedSnapshot>
  if (typeof snapshot.week !== 'string' || !WEEK_PATTERN.test(snapshot.week)) return null
  if (!Array.isArray(snapshot.books) || snapshot.books.length === 0 || snapshot.books.length > 5) return null
  if (snapshot.books.some(book => !isSnapshotBook(book))) return null
  const ids = new Set(snapshot.books.map(book => (book as Book).id))
  if (ids.size !== snapshot.books.length) return null
  return { week: snapshot.week, books: snapshot.books as Book[] }
}

export function readFeaturedSnapshot(document: Document): FeaturedSnapshot | null {
  const script = document.getElementById('featured-bootstrap')
  if (!(script instanceof HTMLScriptElement) || script.type !== 'application/json') return null
  try {
    return parseFeaturedSnapshot(JSON.parse(script.textContent ?? ''))
  } catch {
    return null
  }
}
