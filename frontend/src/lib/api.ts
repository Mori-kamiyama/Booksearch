import type { Book, ShelfCandidate } from './types'

export const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

export function apiUrl(path: string): string {
  if (/^https?:\/\//.test(path)) return path
  return API_BASE ? `${API_BASE}${path}` : path
}

export async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  const externalSignal = init?.signal
  const timeoutSignal = typeof AbortSignal.timeout === 'function'
    ? AbortSignal.timeout(30_000)
    : undefined
  const signal = externalSignal
    ? timeoutSignal && typeof AbortSignal.any === 'function'
      ? AbortSignal.any([externalSignal, timeoutSignal])
      : fallbackTimeoutSignal(externalSignal)
    : timeoutSignal ?? fallbackTimeoutSignal()
  return fetch(apiUrl(path), { ...init, signal })
}

function fallbackTimeoutSignal(externalSignal?: AbortSignal): AbortSignal {
  const controller = new AbortController()
  const abort = () => {
    controller.abort()
    externalSignal?.removeEventListener('abort', abort)
  }
  if (externalSignal) {
    if (externalSignal.aborted) abort()
    else externalSignal.addEventListener('abort', abort, { once: true })
  }
  // Keep this timer alive until the response body is consumed: apiFetch returns
  // Response before callers finish json()/blob() parsing on older browsers.
  setTimeout(abort, 30_000)
  return controller.signal
}

async function jsonFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await apiFetch(path, init)
  if (!res.ok) {
    throw new Error(`request failed: ${res.status}`)
  }
  return res.json() as Promise<T>
}

export interface SearchBookResults {
  books: Book[]
  total: number | null
}

export async function searchBookResults(query: string, limit = 30): Promise<SearchBookResults> {
  const data = await jsonFetch<{ books?: Book[]; total?: number | null }>(`/api/books/search?q=${encodeURIComponent(query)}&limit=${limit}`)
  return {
    books: data.books ?? [],
    total: typeof data.total === 'number' && Number.isFinite(data.total) ? data.total : null,
  }
}

export async function searchBooks(query: string, limit = 30): Promise<Book[]> {
  return (await searchBookResults(query, limit)).books
}

export async function getBook(id: string | number): Promise<Book> {
  return jsonFetch<Book>(`/api/books/${id}`)
}

const featuredCache = new Map<number, { expires: number; books: Book[] }>()
const featuredRequests = new Map<number, Promise<Book[]>>()

export async function getFeaturedBooks(limit = 6): Promise<Book[]> {
  const cached = featuredCache.get(limit)
  if (cached && cached.expires > Date.now()) return cached.books
  const staleBooks = cached?.books.length ? cached.books : null
  const pending = featuredRequests.get(limit)
  if (pending) return pending
  const request = jsonFetch<{ books?: Book[] }>(`/api/books/featured?limit=${limit}`)
    .then(data => {
      const books = (data.books ?? []).filter(book => Number.isInteger(book.id) && book.id > 0)
      featuredCache.set(limit, { books, expires: Date.now() + 5 * 60_000 })
      return books
    }).catch(error => {
      if (staleBooks) return staleBooks
      throw error
    }).finally(() => { featuredRequests.delete(limit) })
  featuredRequests.set(limit, request)
  return request
}

export async function getShelfCandidates(limit = 1000): Promise<ShelfCandidate[]> {
  const data = await jsonFetch<{ candidates?: ShelfCandidate[] }>(`/api/shelf-candidates?limit=${limit}`)
  return data.candidates ?? []
}

export interface IndexBookResponse {
  id: number
  title: string
  title_reading: string
  thumbnail: string | null
}

export async function getIndexBooks(): Promise<IndexBookResponse[]> {
  const data = await jsonFetch<{ books?: IndexBookResponse[] }>('/api/books/index')
  return data.books ?? []
}
