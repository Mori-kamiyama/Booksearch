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

const FEATURED_CACHE_VERSION = 1
const FEATURED_CACHE_TTL_MS = 5 * 60_000

interface FeaturedCacheEntry {
  week: string
  expires: number
  books: Book[]
}

interface PersistedFeaturedCache {
  version: number
  week: string
  savedAt: number
  books: Book[]
}

type FeaturedListener = (books: Book[]) => void

const featuredCache = new Map<number, FeaturedCacheEntry>()
const featuredRequests = new Map<number, Promise<Book[]>>()
const featuredListeners = new Map<number, Set<FeaturedListener>>()

function featuredWeekKey(date = new Date()): string {
  const target = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()))
  const day = target.getUTCDay() || 7
  target.setUTCDate(target.getUTCDate() + 4 - day)
  const yearStart = new Date(Date.UTC(target.getUTCFullYear(), 0, 1))
  const week = Math.ceil(((target.getTime() - yearStart.getTime()) / 86_400_000 + 1) / 7)
  return `${target.getUTCFullYear()}-${week}`
}

function featuredStorageKey(limit: number): string {
  const environment = API_BASE || (typeof window !== 'undefined' ? window.location.origin : 'relative')
  return `booksearch:featured:v${FEATURED_CACHE_VERSION}:${encodeURIComponent(environment)}:${limit}`
}

function getStorage(): Storage | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage
  } catch {
    return null
  }
}

function isFeaturedBook(value: unknown): value is Book {
  if (!value || typeof value !== 'object') return false
  const book = value as Partial<Book>
  if (!Number.isInteger(book.id) || Number(book.id) <= 0 || typeof book.title !== 'string') return false
  for (const key of ['authors', 'publisher', 'published_date', 'class_number', 'registration_number', 'isbn'] as const) {
    if (key in book && typeof book[key] !== 'string') return false
  }
  for (const key of ['thumbnail', 'info_link'] as const) {
    if (key in book && book[key] !== null && typeof book[key] !== 'string') return false
  }
  for (const key of ['description', 'summary'] as const) {
    if (key in book && book[key] !== undefined && typeof book[key] !== 'string') return false
  }
  if ('reading_time_minutes' in book && book.reading_time_minutes !== undefined &&
      (!Number.isFinite(book.reading_time_minutes) || book.reading_time_minutes < 0)) return false
  if ('shelf_ids' in book && book.shelf_ids !== undefined &&
      (!Array.isArray(book.shelf_ids) || book.shelf_ids.some(shelfId => typeof shelfId !== 'string'))) return false
  if ('shelf_candidates' in book && book.shelf_candidates !== undefined &&
      (!Array.isArray(book.shelf_candidates) || book.shelf_candidates.some(candidate => !isShelfCandidate(candidate)))) return false
  return true
}

function isShelfCandidate(value: unknown): boolean {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Partial<ShelfCandidate>
  if (typeof candidate.shelf_id !== 'string' ||
      typeof candidate.confidence !== 'number' || !Number.isFinite(candidate.confidence) ||
      typeof candidate.observations !== 'number' || !Number.isFinite(candidate.observations)) return false
  if ('book_id' in candidate && candidate.book_id !== undefined && !Number.isInteger(candidate.book_id)) return false
  if ('avg_score' in candidate && candidate.avg_score !== undefined &&
      (typeof candidate.avg_score !== 'number' || !Number.isFinite(candidate.avg_score))) return false
  for (const key of ['title', 'title_reading', 'last_seen_at', 'updated_at', 'crop_url'] as const) {
    if (key in candidate && candidate[key] !== undefined && typeof candidate[key] !== 'string') return false
  }
  return !('thumbnail' in candidate) || candidate.thumbnail === null || typeof candidate.thumbnail === 'string'
}

function parsePersistedFeaturedCache(raw: string): FeaturedCacheEntry | null {
  try {
    const parsed = JSON.parse(raw) as Partial<PersistedFeaturedCache>
    if (
      parsed.version !== FEATURED_CACHE_VERSION ||
      typeof parsed.week !== 'string' ||
      !/^\d{4}-\d{1,2}$/.test(parsed.week) ||
      typeof parsed.savedAt !== 'number' ||
      !Number.isFinite(parsed.savedAt) ||
      !Array.isArray(parsed.books) ||
      parsed.books.some(book => !isFeaturedBook(book))
    ) return null
    return { week: parsed.week, expires: parsed.savedAt + FEATURED_CACHE_TTL_MS, books: parsed.books }
  } catch {
    return null
  }
}

function readPersistedFeaturedCache(limit: number): FeaturedCacheEntry | null {
  const storage = getStorage()
  if (!storage) return null
  try {
    const raw = storage.getItem(featuredStorageKey(limit))
    if (!raw) return null
    const parsed = parsePersistedFeaturedCache(raw)
    if (parsed) return parsed
    storage.removeItem(featuredStorageKey(limit))
  } catch {
    // Safari private mode and quota/security failures are a cache miss.
  }
  return null
}

function writePersistedFeaturedCache(limit: number, entry: FeaturedCacheEntry): void {
  const storage = getStorage()
  if (!storage) return
  try {
    const record: PersistedFeaturedCache = {
      version: FEATURED_CACHE_VERSION,
      week: entry.week,
      savedAt: Date.now(),
      books: entry.books,
    }
    storage.setItem(featuredStorageKey(limit), JSON.stringify(record))
  } catch {
    // The in-memory cache remains useful when storage is unavailable/full.
  }
}

function getFeaturedCache(limit: number): FeaturedCacheEntry | undefined {
  const memory = featuredCache.get(limit)
  if (memory) return memory
  const persisted = readPersistedFeaturedCache(limit)
  if (persisted) featuredCache.set(limit, persisted)
  return persisted ?? undefined
}

function notifyFeaturedListeners(limit: number, books: Book[]): void {
  for (const listener of featuredListeners.get(limit) ?? []) {
    try { listener(books) } catch { /* a subscriber must not break the refresh */ }
  }
}

export function subscribeFeaturedBooks(limit: number, listener: FeaturedListener): () => void {
  const listeners = featuredListeners.get(limit) ?? new Set<FeaturedListener>()
  listeners.add(listener)
  featuredListeners.set(limit, listeners)
  return () => {
    listeners.delete(listener)
    if (listeners.size === 0) featuredListeners.delete(limit)
  }
}

function parseFeaturedResponse(value: unknown): Book[] {
  if (!value || typeof value !== 'object' || !('books' in value)) {
    throw new Error('invalid featured response')
  }
  const books = (value as { books?: unknown }).books
  if (!Array.isArray(books) || books.some(book => !isFeaturedBook(book))) {
    throw new Error('invalid featured response')
  }
  return books
}

function refreshFeaturedBooks(limit: number, stale: FeaturedCacheEntry | undefined): Promise<Book[]> {
  const pending = featuredRequests.get(limit)
  if (pending) return pending
  const request = jsonFetch<{ books?: Book[] }>(`/api/books/featured?limit=${limit}`)
    .then(data => {
      const books = parseFeaturedResponse(data)
      const entry = { week: featuredWeekKey(), books, expires: Date.now() + FEATURED_CACHE_TTL_MS }
      featuredCache.set(limit, entry)
      writePersistedFeaturedCache(limit, entry)
      notifyFeaturedListeners(limit, books)
      return books
    })
    .catch(error => {
      if (stale) return stale.books
      throw error
    })
    .finally(() => { featuredRequests.delete(limit) })
  featuredRequests.set(limit, request)
  return request
}

export async function getFeaturedBooks(limit = 6): Promise<Book[]> {
  const cached = getFeaturedCache(limit)
  const currentWeek = featuredWeekKey()
  if (cached && cached.week === currentWeek && cached.expires > Date.now()) return cached.books
  const pending = refreshFeaturedBooks(limit, cached)
  // Stale data is useful immediately while the weekly/TTL refresh runs. The
  // subscriber is notified when the refresh succeeds.
  return cached
    ? Promise.resolve().then(() => getFeaturedCache(limit)?.books ?? cached.books)
    : pending
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
