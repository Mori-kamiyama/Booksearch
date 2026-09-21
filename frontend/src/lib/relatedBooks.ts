import { apiFetch } from './api'
import type { Book } from './types'

export interface RelatedBook extends Book { reasons: string[] }
export async function getRelatedBooks(id: string): Promise<RelatedBook[]> {
  const response = await apiFetch(`/api/books/${encodeURIComponent(id)}/related`)
  if (!response.ok) throw new Error('related books unavailable')
  const data = await response.json()
  if (!Array.isArray(data.books)) throw new Error('invalid related books')
  const seen = new Set<number>()
  return data.books.filter((book: RelatedBook) => {
    if (!book || !Number.isInteger(book.id) || book.id <= 0 || String(book.id) === id || typeof book.title !== 'string' || seen.has(book.id)) return false
    seen.add(book.id)
    return true
  }).slice(0, 6).map((book: RelatedBook) => ({ ...book, reasons: Array.isArray(book.reasons) ? book.reasons.filter(reason => typeof reason === 'string').slice(0, 3) : [] }))
}
