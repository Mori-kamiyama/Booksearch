import { useCallback, useEffect, useState } from 'react'
import { BookOpen } from 'lucide-react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { EmptyState, ErrorState } from '../components/common'
import { SearchBar } from '../components/book'
import { searchBooks } from '../lib/api'
import type { Book } from '../lib/types'
import { fallbackCoverForTitle, featuredBooks, figmaResultBooks } from '../data/figmaBooks'

export default function SearchResultsPage() {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const sourceQuery = params.get('q')?.trim() ?? ''
  const [query, setQuery] = useState(sourceQuery)
  const [books, setBooks] = useState<Book[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)

  const load = useCallback(async () => {
    if (!sourceQuery) {
      setLoading(false)
      setBooks([])
      return
    }
    setLoading(true)
    setError(false)
    try {
      const [results] = await Promise.all([searchBooks(sourceQuery), minimumDelay(350)])
      setBooks(results.length > 0 ? results : fallbackSearchBooks())
    } catch {
      const fallback = fallbackSearchBooks()
      setBooks(fallback)
      setError(fallback.length === 0)
    } finally {
      setLoading(false)
    }
  }, [sourceQuery])

  useEffect(() => {
    setQuery(sourceQuery)
    load()
  }, [load, sourceQuery])

  const runSearch = () => {
    const q = query.trim()
    if (q) navigate(`/search?q=${encodeURIComponent(q)}`)
  }

  return (
    <div className="mx-auto min-h-[calc(100vh-72px)] w-full max-w-[402px] bg-white px-7 pt-[49px] md:min-h-[calc(100vh-88px)] md:max-w-none md:px-0 md:pt-0">
      <div className="mx-auto w-full md:mx-0 md:ml-[calc((100vw-886px)/2)] md:mt-[54px] md:w-[886px]">
        <SearchBar value={query} onChange={setQuery} onSubmit={runSearch} />

        {loading && <SearchGridSkeleton />}
        {!loading && error && <div className="mt-8"><ErrorState message="検索できませんでした。" onRetry={load} /></div>}
        {!loading && !error && books.length === 0 && (
          <div className="mt-8"><EmptyState icon={<BookOpen className="size-5" />} title="見つかりませんでした" hint="別の言葉か、書名の一部だけで検索してください。" /></div>
        )}
        {!loading && !error && books.length > 0 && (
          <>
            <p className="mt-6 text-right text-base leading-[19px] text-ink md:mt-[11px]">{books.length}件ヒット</p>
            <div className="mt-6 grid grid-cols-2 gap-x-2 gap-y-[19px] md:mt-[11px] md:grid-cols-5 md:gap-x-[30px] md:gap-y-[30px]">
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
      {cover ? <img src={cover} alt="" className="h-[199px] w-[141px] bg-[#d9d9d9] object-cover md:h-[208px] md:w-[153px]" loading="lazy" /> : <div className="h-[199px] w-[141px] bg-[#d9d9d9] md:h-[208px] md:w-[153px]" />}
      <span className="line-clamp-2 w-full text-base leading-normal text-ink md:text-[15px]">{book.title}</span>
    </button>
  )
}

function SearchGridSkeleton() {
  return (
    <>
      <div className="mt-6 h-[19px] w-20 animate-pulse justify-self-end bg-zinc-100 md:mt-[11px]" />
      <div className="mt-6 grid grid-cols-2 gap-x-2 gap-y-[19px] md:mt-[11px] md:grid-cols-5 md:gap-x-[30px] md:gap-y-[30px]">
        {Array.from({ length: 10 }, (_, index) => (
          <div key={index} className="flex flex-col items-center gap-2">
            <div className="h-[199px] w-[141px] animate-pulse bg-[#d9d9d9] md:h-[208px] md:w-[153px]" />
            <div className="h-4 w-24 animate-pulse rounded bg-zinc-100" />
          </div>
        ))}
      </div>
    </>
  )
}

function minimumDelay(ms: number) {
  return new Promise(resolve => window.setTimeout(resolve, ms))
}

function fallbackSearchBooks(): Book[] {
  if (!import.meta.env.DEV) return []
  return [...figmaResultBooks, ...featuredBooks, figmaResultBooks[0]].map((book, index) => ({
    id: -(index + 1),
    title: book.title,
    authors: '',
    publisher: '',
    published_date: '',
    class_number: '',
    registration_number: '',
    isbn: '',
    thumbnail: book.cover,
    info_link: null,
    shelf_candidates: [],
  }))
}
