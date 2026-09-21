import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { BookOpen } from 'lucide-react'
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { EmptyState, ErrorState } from '../components/common'
import { CoverImage, SearchBar } from '../components/book'
import { searchBookResults } from '../lib/api'
import type { Book } from '../lib/types'
import { fallbackCoverForTitle } from '../data/figmaBooks'

export default function SearchResultsPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const [params] = useSearchParams()
  const sourceQuery = params.get('q')?.trim() ?? ''
  const [query, setQuery] = useState(sourceQuery)
  const [total, setTotal] = useState<number | null>(null)
  const [books, setBooks] = useState<Book[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const mountedRef = useRef(false)
  const requestIdRef = useRef(0)

  const load = useCallback(async () => {
    const requestId = ++requestIdRef.current
    const isCurrentRequest = () => mountedRef.current && requestId === requestIdRef.current

    if (!sourceQuery) {
      if (isCurrentRequest()) {
        setLoading(false)
        setError(false)
        setBooks([])
      }
      return
    }
    setLoading(true)
    setError(false)
    try {
      const results = await searchBookResults(sourceQuery)
      if (isCurrentRequest()) { setBooks(results.books); setTotal(results.total) }
    } catch {
      if (isCurrentRequest()) {
        setBooks([])
        setTotal(null)
        setError(true)
      }
    } finally {
      if (isCurrentRequest()) setLoading(false)
    }
  }, [sourceQuery])

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

  useEffect(() => {
    load()
    return () => { requestIdRef.current += 1 }
  }, [load, sourceQuery, location])

  const runSearch = () => {
    const q = query.trim()
    if (q) navigate(`/search?q=${encodeURIComponent(q)}`)
  }

  return (
    <div className="mx-auto min-h-[calc(100vh-72px)] w-full max-w-[402px] bg-white px-7 pt-[49px] md:min-h-[calc(100vh-88px)] md:max-w-[886px] md:px-7 md:pt-0 lg:px-0">
      <div className="mx-auto w-full md:mt-[54px]">
        <h1 className="sr-only">検索結果</h1>
        <SearchBar value={query} onChange={setQuery} onSubmit={runSearch} />

        {loading && <SearchGridSkeleton />}
        {!loading && error && <div className="mt-8"><ErrorState message="検索できませんでした。" onRetry={load} /></div>}
        {!loading && !error && books.length === 0 && (
          <div className="mt-8"><EmptyState icon={<BookOpen className="size-5" />} title="見つかりませんでした" hint="別の言葉か、書名の一部だけで検索してください。" /></div>
        )}
        {!loading && !error && books.length > 0 && (
          <>
            <p className="mt-[32px] text-right text-base leading-[19px] text-ink">{total === null ? `${books.length}件表示` : total > books.length ? `全${total}件中 ${books.length}件表示` : `${total}件ヒット`}</p>
            <div className="mt-[32px] grid grid-cols-2 gap-x-2 gap-y-[19px] md:mt-[28px] md:grid-cols-4 md:gap-x-[30px] md:gap-y-[30px] lg:grid-cols-5">
              {books.map(book => (
                <SearchResultCard key={book.id} book={book} onOpen={() => navigate(`/books/${book.id}?q=${encodeURIComponent(sourceQuery)}`)} />
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  )
}

function SearchResultCard({ book, onOpen }: { book: Book; onOpen: () => void }) {
  const cover = book.thumbnail || fallbackCoverForTitle(book.title)
  return (
    <button type="button" onClick={onOpen} className="tap-card flex min-w-0 flex-col items-center gap-2 rounded-lg text-center">
      <div className="flex h-[199px] w-[141px] max-w-full items-end justify-center md:h-[208px] md:w-[153px]">
        {cover ? <CoverImage src={cover} className="max-h-full max-w-full bg-[#d9d9d9] object-contain" fallbackClassName="grid h-full w-full place-items-center bg-[#d9d9d9]" /> : <div className="h-full w-full bg-[#d9d9d9]" />}
      </div>
      <span className="line-clamp-2 w-full text-base leading-normal text-ink md:text-[15px]">{book.title}</span>
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
