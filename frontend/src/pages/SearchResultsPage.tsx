import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { BookOpen } from 'lucide-react'
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { EmptyState, ErrorState } from '../components/common'
import { CoverImage, SearchBar } from '../components/book'
import { searchBookResults } from '../lib/api'
import type { Book } from '../lib/types'
import { formatShelfLabel } from '../lib/shelf'
import { fallbackCoverForTitle } from '../data/figmaBooks'

const SEARCH_PAGE_SIZE = 30

function parsePage(value: string | null): number {
  const page = Number(value)
  return Number.isSafeInteger(page) && page > 0 ? page : 1
}

function searchResultsPath(query: string, page: number): string {
  const params = new URLSearchParams({ q: query })
  if (page > 1) params.set('page', String(page))
  return `/search?${params.toString()}`
}

function bookDetailPath(id: number, query: string, page: number): string {
  const params = new URLSearchParams({ q: query })
  if (page > 1) params.set('page', String(page))
  return `/books/${id}?${params.toString()}`
}

function rememberScroll(key: string): void {
  try {
    sessionStorage.setItem(key, String(Math.max(0, Math.round(window.scrollY))))
  } catch {
    // Private browsing and storage quota failures do not block navigation.
  }
}

function restoreScroll(key: string): void {
  let value: string | null = null
  try {
    value = sessionStorage.getItem(key)
  } catch {
    value = null
  }
  const top = value === null ? 0 : Number(value)
  if (Number.isFinite(top) && top >= 0) window.scrollTo({ top, left: 0, behavior: 'auto' })
}

export default function SearchResultsPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const [params] = useSearchParams()
  const sourceQuery = params.get('q')?.trim() ?? ''
  const page = parsePage(params.get('page'))
  const candidateOffset = (page - 1) * SEARCH_PAGE_SIZE
  const offset = Number.isSafeInteger(candidateOffset) ? candidateOffset : 0
  const scrollKey = `booksearch:search-scroll:${sourceQuery}:${page}`
  const [query, setQuery] = useState(sourceQuery)
  const [total, setTotal] = useState<number | null>(null)
  const [books, setBooks] = useState<Book[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const mountedRef = useRef(false)
  const requestIdRef = useRef(0)
  const loadedScrollKeyRef = useRef<string | null>(null)
  const pointerNavigationScrollKeyRef = useRef<string | null>(null)

  const load = useCallback(async () => {
    const requestId = ++requestIdRef.current
    const isCurrentRequest = () => mountedRef.current && requestId === requestIdRef.current
    if (isCurrentRequest()) loadedScrollKeyRef.current = null

    if (!sourceQuery) {
      if (isCurrentRequest()) {
        loadedScrollKeyRef.current = scrollKey
        setLoading(false)
        setError(false)
        setBooks([])
        setTotal(null)
      }
      return
    }
    setLoading(true)
    setError(false)
    try {
      const results = await searchBookResults(sourceQuery, SEARCH_PAGE_SIZE, offset)
      if (isCurrentRequest()) { setBooks(results.books); setTotal(results.total) }
    } catch {
      if (isCurrentRequest()) {
        setBooks([])
        setTotal(null)
        setError(true)
      }
    } finally {
      if (isCurrentRequest()) {
        // Mark the result before clearing loading so the layout effect restores
        // after cards have been committed, without an early scroll listener
        // persisting the skeleton's scroll position over the saved value.
        loadedScrollKeyRef.current = scrollKey
        setLoading(false)
      }
    }
  }, [offset, scrollKey, sourceQuery])

  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])

  // Back may arrive before React commits the intermediate search route. In
  // that case the final URL/key equal the previous render and URL effects do
  // not run, although the input draft changed. Reset it on every POP event.
  useEffect(() => {
    const onPopState = () => {
      setQuery(new URLSearchParams(window.location.search).get('q')?.trim() ?? '')
    }
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [])

  useLayoutEffect(() => {
    setQuery(sourceQuery)
  }, [location.key, sourceQuery])

  useLayoutEffect(() => {
    if (loading || loadedScrollKeyRef.current !== scrollKey) return
    restoreScroll(scrollKey)
    const frame = window.requestAnimationFrame(() => restoreScroll(scrollKey))
    return () => window.cancelAnimationFrame(frame)
  }, [loading, scrollKey])

  useEffect(() => {
    const save = () => {
      if (loadedScrollKeyRef.current === scrollKey) rememberScroll(scrollKey)
    }
    if (loadedScrollKeyRef.current !== scrollKey) return
    window.addEventListener('scroll', save, { passive: true })
    return () => window.removeEventListener('scroll', save)
  }, [loading, scrollKey])

  useEffect(() => {
    load()
    return () => { requestIdRef.current += 1 }
  }, [load, location])

  const runSearch = () => {
    const q = query.trim()
    if (q) navigate(searchResultsPath(q, 1))
  }

  const totalPages = total === null ? 1 : Math.max(1, Math.ceil(total / SEARCH_PAGE_SIZE))
  useEffect(() => {
    const rawPage = params.get('page')
    const numericPage = rawPage === null ? null : Number(rawPage)
    if (rawPage !== null && (numericPage === null || !Number.isSafeInteger(numericPage) || numericPage <= 0)) {
      navigate(searchResultsPath(sourceQuery, 1), { replace: true })
    }
  }, [navigate, params, sourceQuery])

  useEffect(() => {
    if (!loading && !error && total !== null && page > totalPages) {
      navigate(searchResultsPath(sourceQuery, totalPages), { replace: true })
    }
  }, [error, loading, navigate, page, sourceQuery, total, totalPages])

  const goToPage = (nextPage: number) => {
    if (nextPage < 1 || nextPage > totalPages || nextPage === page) return
    rememberScroll(scrollKey)
    navigate(searchResultsPath(sourceQuery, nextPage))
  }

  return (
    <div className="mx-auto min-h-[calc(100vh-72px)] w-full max-w-[402px] bg-white px-7 pt-[49px] md:min-h-[calc(100vh-88px)] md:max-w-[886px] md:px-7 md:pt-0 lg:px-0">
      <div className="mx-auto w-full md:mt-[54px]">
        <h1 className="sr-only">検索結果</h1>
        <SearchBar value={query} onChange={setQuery} onSubmit={runSearch} />

        {loading && <SearchGridSkeleton />}
        {!loading && error && <div className="mt-8"><ErrorState message="検索できませんでした。" onRetry={load} /></div>}
        {!loading && !error && books.length === 0 && (
          <div className="mt-8">
            <EmptyState icon={<BookOpen className="size-5" />} title="見つかりませんでした" hint="書名の一部や著者名で試してください。複数の語を入れた場合は、語を減らすと見つかることがあります。" />
            <div className="mt-4 flex flex-wrap justify-center gap-2">
              <button type="button" className="min-h-11 rounded-lg border border-line px-4 text-sm text-primary" onClick={() => {
                const input = document.querySelector<HTMLInputElement>('input[type="search"]')
                input?.focus(); input?.select()
              }}>検索条件を見直す</button>
              {sourceQuery.split(/\s+/).filter(Boolean).length > 1 && [...new Set(sourceQuery.split(/\s+/).filter(Boolean))].slice(0, 5).map(term => (
                <button key={term} type="button" className="min-h-11 rounded-lg border border-line px-4 text-sm text-primary" onClick={() => navigate(searchResultsPath(term, 1))}>「{term}」で検索</button>
              ))}
            </div>
          </div>
        )}
        {!loading && !error && books.length > 0 && (
          <>
            <p className="mt-[32px] text-right text-base leading-[19px] text-ink">{total === null ? `${books.length}件表示` : total > books.length ? `全${total}件中 ${books.length}件表示` : `${total}件ヒット`}</p>
            <div className="mt-[32px] grid grid-cols-2 gap-x-2 gap-y-[19px] md:mt-[28px] md:grid-cols-4 md:gap-x-[30px] md:gap-y-[30px] lg:grid-cols-5">
              {books.map(book => (
                <SearchResultCard
                  key={book.id}
                  book={book}
                  onPointerDown={() => {
                    // Save before the browser focuses the card and potentially
                    // scrolls it into view.
                    rememberScroll(scrollKey)
                    pointerNavigationScrollKeyRef.current = scrollKey
                  }}
                  onOpen={() => {
                    if (pointerNavigationScrollKeyRef.current !== scrollKey) rememberScroll(scrollKey)
                    pointerNavigationScrollKeyRef.current = null
                    navigate(bookDetailPath(book.id, sourceQuery, page))
                  }}
                />
              ))}
            </div>
            {totalPages > 1 && (
              <nav aria-label="検索結果のページ" className="mt-10 flex items-center justify-center gap-4 pb-4 text-sm">
                <button
                  type="button"
                  disabled={page === 1}
                  onClick={() => goToPage(page - 1)}
                  className="min-h-11 rounded-lg border border-line px-4 text-ink enabled:hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-40"
                  aria-label="前のページ"
                >
                  前へ
                </button>
                <span aria-live="polite">{page} / {totalPages}ページ</span>
                <button
                  type="button"
                  disabled={page === totalPages}
                  onClick={() => goToPage(page + 1)}
                  className="min-h-11 rounded-lg border border-line px-4 text-ink enabled:hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-40"
                  aria-label="次のページ"
                >
                  次へ
                </button>
              </nav>
            )}
          </>
        )}
      </div>
    </div>
  )
}

function SearchResultCard({ book, onOpen, onPointerDown }: { book: Book; onOpen: () => void; onPointerDown: () => void }) {
  const cover = book.thumbnail || fallbackCoverForTitle(book.title)
  return (
    <button type="button" aria-labelledby={`search-title-${book.id}`} aria-describedby={`search-meta-${book.id}`} onPointerDown={onPointerDown} onClick={onOpen} className="tap-card flex min-w-0 flex-col items-center gap-2 rounded-lg text-center">
      <div className="flex h-[199px] w-[141px] max-w-full items-end justify-center md:h-[208px] md:w-[153px]">
        {cover ? <CoverImage src={cover} className="max-h-full max-w-full bg-[#d9d9d9] object-contain" fallbackClassName="grid h-full w-full place-items-center bg-[#d9d9d9]" /> : <div className="h-full w-full bg-[#d9d9d9]" />}
      </div>
      <span id={`search-title-${book.id}`} className="line-clamp-2 w-full text-base leading-normal text-ink md:text-[15px]">{book.title}</span>
      <span id={`search-meta-${book.id}`} className="flex w-full flex-col gap-1 text-xs text-ink-muted">
        {book.authors && <span className="line-clamp-1">{book.authors}</span>}
        <span>{[book.published_date?.match(/^\d{4}/)?.[0], book.class_number ? `分類 ${book.class_number}` : null].filter(Boolean).join(' / ')}</span>
        <span>{book.shelf_candidates?.[0]?.shelf_id ? `棚候補: ${formatShelfLabel(book.shelf_candidates[0].shelf_id)}` : '棚の位置情報なし'}</span>
      </span>
    </button>
  )
}

function SearchGridSkeleton() {
  return (
    <>
      <div className="mt-6 h-[19px] w-20 animate-pulse justify-self-end bg-zinc-100 md:mt-[11px]" />
      <div className="mt-6 grid grid-cols-2 gap-x-2 gap-y-[19px] md:mt-[11px] md:grid-cols-4 md:gap-x-[30px] md:gap-y-[30px] lg:grid-cols-5">
        {Array.from({ length: 10 }, (_, index) => (
          <div key={index} className="flex min-w-0 flex-col items-center gap-2">
            <div className="h-[199px] w-[141px] max-w-full animate-pulse bg-[#d9d9d9] md:h-[208px] md:w-[153px]" />
            <div className="h-4 w-24 animate-pulse rounded bg-zinc-100" />
          </div>
        ))}
      </div>
    </>
  )
}
