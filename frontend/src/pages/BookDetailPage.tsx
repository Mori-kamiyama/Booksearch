import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { SearchBar } from '../components/book'
import { EmptyState, ErrorState } from '../components/common'
import { fallbackCoverForTitle, featuredBooks, figmaResultBooks } from '../data/figmaBooks'
import { getBook, getFeaturedBooks } from '../lib/api'
import type { Book } from '../lib/types'

const fallbackSummary = 'データがありません。'

export default function BookDetailPage() {
  const navigate = useNavigate()
  const { id } = useParams<{ id: string }>()
  const [params] = useSearchParams()
  const sourceQuery = params.get('q')?.trim() ?? ''
  const [query, setQuery] = useState(sourceQuery)
  const [book, setBook] = useState<Book | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [recommendations, setRecommendations] = useState<Book[]>([])

  const load = useCallback(async () => {
    if (!id) return
    setLoading(true)
    setError(false)
    try {
      setBook(await getBook(id))
    } catch {
      const fallback = fallbackDetailBook(id)
      setBook(fallback)
      setError(!fallback)
    } finally {
      setLoading(false)
    }
  }, [id])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    let cancelled = false
    getFeaturedBooks(7)
      .then(books => {
        if (!cancelled) setRecommendations(recommendationsForDisplay(books.filter(b => String(b.id) !== id).slice(0, 6)))
      })
      .catch(() => { if (!cancelled) setRecommendations(fallbackRecommendationBooks()) })
    return () => { cancelled = true }
  }, [id])

  const topCandidate = useMemo(() => [...(book?.shelf_candidates ?? [])].sort((a, b) => b.confidence - a.confidence)[0], [book])
  const runSearch = () => {
    const q = query.trim()
    if (q) navigate(`/search?q=${encodeURIComponent(q)}`)
  }

  if (loading) return <DetailSkeleton />
  if (error) return <div className="mx-auto mt-12 max-w-md px-7"><ErrorState message="本の情報を読み込めませんでした。" onRetry={load} /></div>
  if (!book) return <div className="mx-auto mt-12 max-w-md px-7"><EmptyState title="本が見つかりません" hint="検索からもう一度探してください。" /></div>

  const cover = book.thumbnail || fallbackCoverForTitle(book.title)
  const summary = book.summary || book.description || fallbackSummary
  const readingHours = Math.max(1, Math.round((book.reading_time_minutes ?? 180) / 60))

  return (
    <div className="bg-white">
      <div className="md:hidden">
        <div className="flex flex-col items-center gap-4 pt-[49px]">
          <BookIdentity book={book} cover={cover} readingHours={readingHours} mobile />
          <div className="h-px w-64 bg-[#087f5b]" />
          <Summary text={summary} />
          <MapSection hasLocation={Boolean(topCandidate)} />
          <Recommendations books={recommendations} onOpen={bookId => navigate(`/books/${bookId}`)} />
        </div>
      </div>

      <div className="hidden w-[886px] md:ml-[calc((100vw-886px)/2)] md:block">
        <div className="mt-[54px]"><SearchBar value={query} onChange={setQuery} onSubmit={runSearch} /></div>
        <div className="mt-[39px] grid grid-cols-[265px_1fr] items-start gap-[44px]">
          <BookIdentity book={book} cover={cover} readingHours={readingHours} />
          <div>
            <Summary text={summary} desktop />
            <div className="mt-[22px]"><MapSection hasLocation={Boolean(topCandidate)} desktop /></div>
            <div className="mt-[39px]"><Recommendations books={recommendations} onOpen={bookId => navigate(`/books/${bookId}`)} desktop /></div>
          </div>
        </div>
      </div>
    </div>
  )
}

function BookIdentity({ book, cover, readingHours, mobile = false }: { book: Book; cover?: string; readingHours: number; mobile?: boolean }) {
  return (
    <section className={`flex flex-col items-center ${mobile ? 'gap-3 px-7' : 'gap-[17px]'}`}>
      {cover ? <img src={cover} alt="" className={mobile ? 'h-[197px] max-w-[139px] object-cover' : 'h-[359px] w-[265px] object-cover'} /> : <div className={mobile ? 'h-[197px] w-[139px] bg-[#d9d9d9]' : 'h-[359px] w-[265px] bg-[#d9d9d9]'} />}
      <h1 className={`${mobile ? 'text-[32px] leading-[38px]' : 'text-4xl leading-[44px]'} w-full text-center font-normal text-ink`}>{book.title}</h1>
      <p className="text-center text-base text-ink">推定読了時間 {readingHours}時間</p>
    </section>
  )
}

function Summary({ text, desktop = false }: { text: string; desktop?: boolean }) {
  return (
    <section className={desktop ? '' : 'w-full px-7'}>
      <h2 className="text-base font-semibold leading-[19px] text-ink">内容要約</h2>
      <p className="mt-3 text-xs leading-[1.45] text-ink md:mt-[11px] md:leading-[1.5]">{text}</p>
    </section>
  )
}

function MapSection({ hasLocation, desktop = false }: { hasLocation: boolean; desktop?: boolean }) {
  return (
    <section className={desktop ? '' : 'w-full px-7'}>
      <h2 className="text-base font-semibold leading-[19px] text-ink">MAP</h2>
      <div className="mt-3 flex flex-col items-center gap-1 md:mt-7 md:gap-[22px]">
        <LibraryMap hasLocation={hasLocation} />
        <div className="grid h-[23px] w-[136px] grid-cols-2 overflow-hidden rounded-full border border-[#087f5b] text-center text-xs leading-[21px] md:relative md:left-[5px] md:border-[#363636]">
          <span className="bg-[#087f5b] text-white md:bg-[#363636]">全体</span>
          <span className="text-ink">詳細</span>
        </div>
      </div>
    </section>
  )
}

function LibraryMap({ hasLocation }: { hasLocation: boolean }) {
  const bars = [0, 93, 119, 212, 238, 331]
  const feet = [119, 192, 238, 311]
  return (
    <div className="relative h-[151px] w-[298px] max-w-full md:h-[180px] md:w-[355px]" aria-label={hasLocation ? '本がある棚を緑色で表示' : '図書室の棚マップ'}>
      <div className="absolute left-0 top-0 h-[180px] w-[355px] origin-top-left scale-[0.84] md:scale-100">
        {bars.map((left, index) => <div key={left} className={`absolute top-0 h-[154px] w-6 ${hasLocation && index === bars.length - 1 ? 'bg-[#087f5b] md:bg-[#363636]' : 'bg-[#d9d9d9]'}`} style={{ left }} />)}
        {feet.map(left => <div key={left} className="absolute top-[156px] h-6 w-[43px] bg-[#d9d9d9]" style={{ left }} />)}
        {hasLocation && (
          <div className="absolute left-[274px] top-[14px] h-[54px] w-[69px] bg-[#d9d9d9] px-2 pt-4 text-center text-base font-semibold">
            ここ！
            <span className="absolute -bottom-3 right-0 size-0 border-l-[30px] border-t-[12px] border-l-transparent border-t-[#d9d9d9]" />
          </div>
        )}
      </div>
    </div>
  )
}

function Recommendations({ books, onOpen, desktop = false }: { books: Book[]; onOpen: (bookId: number) => void; desktop?: boolean }) {
  if (books.length === 0) return null
  return (
    <section className={desktop ? '' : 'w-full px-7'}>
      <h2 className="text-base font-semibold leading-[19px] text-ink">この本を読むあなたに</h2>
      <div
        className={
          desktop
            ? 'mt-[22px] flex items-start justify-start gap-5'
            : '-mx-7 mt-4 flex snap-x snap-mandatory scroll-px-7 items-start gap-8 overflow-x-auto px-7 [-webkit-overflow-scrolling:touch] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden'
        }
      >
        {books.map(book => {
          const cover = book.thumbnail || fallbackCoverForTitle(book.title)
          return (
            <button
              key={book.id}
              type="button"
              onClick={() => onOpen(book.id)}
              className={desktop ? 'tap-card flex w-[102px] shrink-0 flex-col items-center gap-[5px] rounded-lg text-center' : 'tap-card flex w-[118px] shrink-0 snap-start flex-col items-center gap-[5px] rounded-lg text-center'}
            >
              {cover ? (
                <img src={cover} alt="" className={desktop ? 'h-[139px] w-[102px] object-cover' : 'h-[155px] max-w-[114px] object-cover'} loading="lazy" />
              ) : (
                <div className={desktop ? 'h-[139px] w-[102px] bg-[#d9d9d9]' : 'h-[155px] w-[114px] bg-[#d9d9d9]'} />
              )}
              <p className={desktop ? 'line-clamp-2 w-full text-center text-[10px]' : 'line-clamp-2 w-full text-center text-[11px] leading-[13px]'}>{book.title}</p>
            </button>
          )
        })}
      </div>
    </section>
  )
}

function DetailSkeleton() {
  return (
    <div className="mx-auto flex max-w-[402px] flex-col items-center gap-4 px-7 md:ml-[calc((100vw-886px)/2)] md:mr-0 md:mt-[147px] md:max-w-[886px]">
      <div className="h-64 w-[182px] animate-pulse bg-[#d9d9d9] md:h-[359px] md:w-[265px]" />
      <div className="h-10 w-48 animate-pulse rounded bg-zinc-100" />
      <div className="h-5 w-32 animate-pulse rounded bg-zinc-100" />
    </div>
  )
}

function fallbackDetailBook(id: string | undefined): Book | null {
  if (!import.meta.env.DEV) return null
  const index = Math.max(0, Math.abs(Number(id) || 1) - 1)
  const book = [...figmaResultBooks, ...featuredBooks][index % (figmaResultBooks.length + featuredBooks.length)]
  if (!book) return null
  return {
    id: Number(id) || -1,
    title: book.title,
    authors: '',
    publisher: '',
    published_date: '',
    class_number: '',
    registration_number: '',
    isbn: '',
    thumbnail: book.cover,
    info_link: null,
    summary: fallbackSummary,
    reading_time_minutes: 180,
    shelf_candidates: [{
      book_id: Number(id) || -1,
      title: book.title,
      shelf_id: 'base-04-c01-r01',
      confidence: 0.95,
      observations: 1,
    }],
  }
}

function recommendationsForDisplay(books: Book[]): Book[] {
  if (!import.meta.env.DEV) return books
  const withCovers = books.filter(book => fallbackCoverForTitle(book.title))
  return withCovers.length >= 3 ? books : fallbackRecommendationBooks()
}

function fallbackRecommendationBooks(): Book[] {
  if (!import.meta.env.DEV) return []
  return featuredBooks.map((book, index) => ({
    id: -(100 + index),
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
