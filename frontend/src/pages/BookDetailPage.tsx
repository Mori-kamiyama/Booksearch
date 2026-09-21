import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Clock, ExternalLink } from 'lucide-react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { CoverImage, SearchBar } from '../components/book'
import { EmptyState, ErrorState } from '../components/common'
import { LibraryMap, ShelfLocationLabel } from '../components/shelf'
import { fallbackCoverForTitle } from '../data/figmaBooks'
import { getBook } from '../lib/api'
import { getRelatedBooks, type RelatedBook } from '../lib/relatedBooks'
import type { Book, ShelfCandidate } from '../lib/types'

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
  const [recommendations, setRecommendations] = useState<RelatedBook[]>([])
  const [recommendationsFailed, setRecommendationsFailed] = useState(false)
  const [recommendationsLoading, setRecommendationsLoading] = useState(true)

  const requestRef = useRef(0)
  const load = useCallback(async () => {
    const request = ++requestRef.current
    if (!id) return
    setLoading(true)
    setError(false)
    try {
      const result = await getBook(id)
      if (request === requestRef.current) setBook(result)
    } catch {
      if (request === requestRef.current) { setBook(null); setError(true) }
    } finally {
      if (request === requestRef.current) setLoading(false)
    }
  }, [id])

  useEffect(() => { load(); return () => { requestRef.current += 1 } }, [load])
  useEffect(() => { setQuery(sourceQuery) }, [sourceQuery])

  useEffect(() => {
    let cancelled = false
    setRecommendations([])
    setRecommendationsFailed(false)
    setRecommendationsLoading(true)
    if (id) getRelatedBooks(id)
      .then(books => { if (!cancelled) setRecommendations(books) })
      .catch(() => { if (!cancelled) setRecommendationsFailed(true) })
      .finally(() => { if (!cancelled) setRecommendationsLoading(false) })
    return () => { cancelled = true }
  }, [id])

  const topCandidate = useMemo(() => [...(book?.shelf_candidates ?? [])].sort((a, b) => b.confidence - a.confidence)[0], [book])
  const returnTo = book
    ? `/books/${book.id}${params.toString() ? `?${params.toString()}` : ''}`
    : '/'
  const runSearch = () => {
    const q = query.trim()
    if (q) navigate(`/search?q=${encodeURIComponent(q)}`)
  }

  if (loading) return <DetailSkeleton />
  if (error) return <div className="mx-auto mt-12 max-w-md px-7"><ErrorState message="本の情報を読み込めませんでした。" onRetry={load} /></div>
  if (!book) return <div className="mx-auto mt-12 max-w-md px-7"><EmptyState title="本が見つかりません" hint="検索からもう一度探してください。" /></div>

  const cover = book.thumbnail || fallbackCoverForTitle(book.title)
  const summary = book.summary || book.description || fallbackSummary
  const readingHours = book.reading_time_minutes && book.reading_time_minutes > 0 ? Math.max(1, Math.round(book.reading_time_minutes / 60)) : null

  return (
    <div className="bg-white pb-24 md:pb-0">
      <div className="mx-auto w-full max-w-[402px] px-7 pt-[49px] md:max-w-none md:px-0 md:pt-0">
        <div className="hidden md:mx-auto md:mb-[39px] md:mt-[54px] md:block md:w-full md:max-w-[920px] md:px-7">
          <SearchBar value={query} onChange={setQuery} onSubmit={runSearch} />
        </div>

        <div className="md:mx-auto md:w-full md:max-w-[920px] md:px-7">
          <nav aria-label="パンくず" className="mb-7 text-sm text-ink-muted md:mb-[39px]">
            <Link to="/" className="text-primary hover:underline">TOP</Link>
            <span className="mx-2">&gt;</span>
            <span className="line-clamp-1 align-bottom text-ink-muted">{book.title}</span>
          </nav>

          <div className="mb-6 flex items-center justify-between gap-3 rounded-xl bg-primary-soft px-4 py-3 text-sm">
            <div>
              <p className="text-xs text-ink-muted">{topCandidate ? 'この本の棚候補' : 'この本の位置は未登録です'}</p>
              {topCandidate && <ShelfLocationLabel shelfId={topCandidate.shelf_id} size="sm" />}
            </div>
            <a href="#shelf-location" className="shrink-0 py-2 font-semibold text-primary underline">{topCandidate ? '場所を確認' : '探し方を見る'}</a>
          </div>

          <section className="grid gap-8 md:grid-cols-[minmax(0,300px)_minmax(0,1fr)] md:gap-[44px]">
            <BookCoverPanel book={book} cover={cover} />
            <BookInfoPanel book={book} readingHours={readingHours} />
          </section>
        </div>
      </div>

      <MapSection
        candidate={topCandidate}
        onOpenMap={() => topCandidate && navigate(`/map/${encodeURIComponent(topCandidate.shelf_id)}`)}
        onScanForBook={() => navigate('/scan', {
          state: {
            targetBook: {
              id: book.id,
              title: book.title,
              shelfId: topCandidate?.shelf_id ?? null,
            },
            returnTo,
          },
        })}
      />

      <AiSummarySection text={summary} />

      <div className="mx-auto w-full max-w-[402px] px-7 pt-10 md:max-w-[886px] md:px-0 md:pt-12">
        <Recommendations loading={recommendationsLoading} failed={recommendationsFailed} books={recommendations} onOpen={bookId => navigate(`/books/${bookId}`)} />
      </div>
    </div>
  )
}

function BookCoverPanel({ book, cover }: { book: Book; cover?: string }) {
  return (
    <section className="flex flex-col items-center md:items-start">
      <div className="relative flex h-[350px] w-full max-w-[240px] items-end justify-center md:h-[405px] md:max-w-[300px]">
        <CoverImage
          src={cover}
          alt=""
          className="h-full w-auto max-w-full object-contain"
          fallbackClassName="grid h-full w-full place-items-center bg-[#d9d9d9] text-ink-muted"
        />
      </div>
      <p className="mt-3 line-clamp-2 max-w-[280px] text-center text-xs text-ink-muted md:hidden">{book.title}</p>
    </section>
  )
}

function BookInfoPanel({ book, readingHours }: { book: Book; readingHours: number | null }) {
  return (
    <section className="md:pt-2">
      <h1 className="text-2xl font-normal leading-[1.35] text-ink md:text-[32px]">{book.title}</h1>
      <dl className="mt-4 grid gap-1 text-sm leading-6 text-ink md:text-base">
        <MetaRow label="著者" value={book.authors} accent />
        <MetaRow label="出版社" value={book.publisher} />
        <MetaRow label="出版日" value={book.published_date} />
        <MetaRow label="分類" value={book.class_number} />
        <MetaRow label="登録番号" value={book.registration_number} />
        <MetaRow label="ISBN" value={book.isbn} />
      </dl>

      <div className="mt-5 flex flex-wrap items-center gap-x-5 gap-y-2 text-sm">
        {readingHours !== null && <span className="inline-flex items-center gap-1 text-ink-muted">
          <Clock className="size-4" />
          推定読了 {readingHours}時間
        </span>}
        <a href={detailUrlForBook(book)} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-semibold text-primary hover:underline">
          外部サイトで本の情報を見る
          <ExternalLink className="size-4" />
        </a>
      </div>

    </section>
  )
}

function detailUrlForBook(book: Book): string {
  if (book.info_link && /^https?:\/\//i.test(book.info_link)) return book.info_link
  const query = book.isbn || [book.title, book.authors].filter(Boolean).join(' ')
  return `https://www.google.com/search?tbm=bks&q=${encodeURIComponent(query)}`
}

function MetaRow({ label, value, accent = false }: { label: string; value?: string | null; accent?: boolean }) {
  if (!value) return null
  return (
    <div className="grid grid-cols-[80px_1fr] gap-3">
      <dt className="text-ink-muted whitespace-nowrap">{label}：</dt>
      <dd className={accent ? 'text-primary font-semibold' : 'text-ink'}>{value}</dd>
    </div>
  )
}

function MapSection({ candidate, onOpenMap, onScanForBook }: { candidate?: ShelfCandidate; onOpenMap: () => void; onScanForBook: () => void }) {
  return (
    <section id="shelf-location" className="mx-auto mt-12 w-full max-w-[402px] scroll-mt-24 px-7 md:mt-16 md:max-w-[886px] md:px-0">
      <h2 className="text-base font-semibold leading-[19px] text-ink">棚の位置候補</h2>
      {candidate ? (
        <div className="mt-4 flex flex-col items-center gap-4 md:mt-6 md:grid md:grid-cols-[1fr_355px] md:items-center md:gap-[44px]">
          <div className="order-2 text-center md:order-1 md:text-left">
            <ShelfLocationLabel shelfId={candidate.shelf_id} size="lg" />
            <p className="mt-1 text-sm text-ink-muted">この本はここにありそう</p>
            <p className="mt-2 text-xs text-ink-muted">観測 {candidate.observations}回 / 位置は推定です</p>
            <button type="button" onClick={onOpenMap} className="tap-soft mt-4 text-sm font-semibold text-primary hover:underline">
              マップで見る
            </button>
          </div>
          <LibraryMap shelfId={candidate.shelf_id} />
        </div>
      ) : (
        <p className="mt-4 text-sm text-ink-muted">まだ棚の位置が登録されていません。スキャンするとここに表示されます。</p>
      )}
      <div className="mt-6 rounded-xl border border-primary-soft bg-primary-soft p-4">
        <p className="text-sm font-semibold text-ink">本棚を見ながら、この本を探す</p>
        <p className="mt-1 text-xs leading-5 text-ink-muted">周囲の本も一緒に認識し、対象本は候補と自動照合を分けて表示します。</p>
        <button type="button" onClick={onScanForBook} className="tap-card mt-3 rounded-full bg-primary px-4 py-2 text-sm font-semibold text-white">
          スキャンしながら探す
        </button>
      </div>
    </section>
  )
}



function AiSummarySection({ text }: { text: string }) {
  return (
    <section className="mx-auto mt-12 w-full max-w-[402px] px-7 md:mt-16 md:max-w-[886px] md:px-0">
      <h2 className="text-base font-semibold leading-[19px] text-ink">本の紹介</h2>
      <div className="mt-4 text-sm leading-7 text-ink md:mt-5 md:text-base">
        <p className="font-semibold">登録されている内容紹介</p>
        <p className="mt-2 text-ink-muted">{text}</p>
      </div>
    </section>
  )
}

function Recommendations({ books, onOpen, failed, loading }: { books: RelatedBook[]; onOpen: (bookId: number) => void; failed: boolean; loading: boolean }) {
  return (
    <section>
      <h2 className="text-lg font-bold leading-[1.4] text-ink">関連する本</h2>
      {books.length === 0 && <p className="mt-3 text-sm text-ink-muted">{loading ? '関連する本を読み込んでいます…' : failed ? '関連する本を取得できませんでした。' : '関連する本はまだ用意されていません。'}</p>}
      <div className="-mx-7 mt-5 flex snap-x snap-mandatory scroll-px-7 items-start gap-6 overflow-x-auto px-7 pb-2 [-webkit-overflow-scrolling:touch] [scrollbar-width:none] md:mx-0 md:snap-none md:gap-5 md:overflow-x-auto md:px-0 [&::-webkit-scrollbar]:hidden">
        {books.map(book => {
          const cover = book.thumbnail || fallbackCoverForTitle(book.title)
          return (
            <button
              key={book.id}
              type="button"
              onClick={() => onOpen(book.id)}
              className="tap-card flex w-[118px] shrink-0 snap-start flex-col items-center gap-[7px] rounded-lg text-center md:w-[112px]"
            >
              <div className="flex h-[155px] w-[114px] items-end justify-center md:h-[150px] md:w-[112px]">
                <CoverImage
                  src={cover}
                  alt=""
                  className="max-h-full max-w-full object-contain"
                  fallbackClassName="grid h-full w-full place-items-center bg-[#d9d9d9] text-ink-muted"
                />
              </div>
              <p className="line-clamp-2 w-full text-center text-[11px] leading-[13px] text-ink">{book.title}</p>
              {book.reasons.map(reason => <p key={reason} className="text-xs text-ink-muted">{reason}</p>)}
            </button>
          )
        })}
      </div>
    </section>
  )
}

function DetailSkeleton() {
  return (
    <div className="mx-auto grid max-w-[402px] gap-8 px-7 pt-12 md:max-w-[920px] md:grid-cols-[minmax(0,300px)_minmax(0,1fr)] md:px-0">
      <div className="mx-auto h-[350px] w-[240px] animate-pulse bg-[#d9d9d9] md:h-[405px] md:w-[300px]" />
      <div className="space-y-4">
        <div className="h-10 w-4/5 animate-pulse rounded bg-zinc-100" />
        <div className="h-5 w-64 animate-pulse rounded bg-zinc-100" />
        <div className="h-5 w-52 animate-pulse rounded bg-zinc-100" />
        <div className="mt-8 h-48 w-full max-w-[540px] animate-pulse rounded-xl bg-zinc-100" />
      </div>
    </div>
  )
}
