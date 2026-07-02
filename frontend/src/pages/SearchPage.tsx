import { useState, useCallback } from 'react'
import { Check, ExternalLink, MapPin, RotateCcw, X } from 'lucide-react'
import { apiFetch } from '../lib/api'

interface Book {
  id: number
  title: string
  authors: string
  publisher: string
  published_date: string
  class_number: string
  registration_number: string
  isbn: string
  thumbnail: string | null
  info_link: string | null
  shelf_ids?: string[]
  shelf_candidates?: ShelfCandidate[]
}

interface ShelfCandidate {
  shelf_id: string
  confidence: number
  observations: number
}

type LocationReview =
  | { status: 'confirmed'; shelfID: string }
  | { status: 'rejected'; shelfID?: string }
  | { status: 'corrected'; shelfID: string }

export default function SearchPage() {
  const [query, setQuery] = useState('')
  const [books, setBooks] = useState<Book[]>([])
  const [loading, setLoading] = useState(false)
  const [searched, setSearched] = useState(false)
  const [reviews, setReviews] = useState<Record<number, LocationReview>>({})

  const search = useCallback(async (q: string) => {
    if (!q.trim()) return
    setLoading(true)
    setSearched(true)
    try {
      const res = await apiFetch(`/api/books/search?q=${encodeURIComponent(q)}&limit=30`)
      const data = await res.json()
      setBooks(data.books ?? [])
    } catch {
      setBooks([])
    } finally {
      setLoading(false)
    }
  }, [])

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    search(query)
  }

  return (
    <div>
      <h2 className="text-2xl font-bold text-gray-800 mb-6">本を探す</h2>

      <form onSubmit={onSubmit} className="flex gap-3 mb-8">
        <input
          type="text"
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder="タイトル・著者・ISBNで検索"
          className="flex-1 border border-gray-300 rounded-lg px-4 py-3 text-base focus:outline-none focus:ring-2 focus:ring-[#1f7a5c] bg-white"
        />
        <button
          type="submit"
          disabled={loading}
          className="px-6 py-3 bg-[#1f7a5c] text-white rounded-lg font-semibold hover:bg-[#196649] disabled:opacity-50 transition-colors"
        >
          {loading ? '検索中…' : '検索'}
        </button>
      </form>

      {loading && (
        <div className="text-center text-gray-500 py-12">検索中…</div>
      )}

      {!loading && searched && books.length === 0 && (
        <div className="text-center text-gray-400 py-12">
          「{query}」に一致する本が見つかりませんでした。
        </div>
      )}

      {!loading && books.length > 0 && (
        <>
          <p className="text-sm text-gray-500 mb-4">{books.length} 件</p>
          <div className="grid gap-4">
            {books.map(book => (
              <BookCard
                key={book.id}
                book={book}
                review={reviews[book.id]}
                onReview={review => setReviews(prev => ({ ...prev, [book.id]: review }))}
                onClearReview={() => setReviews(prev => {
                  const next = { ...prev }
                  delete next[book.id]
                  return next
                })}
              />
            ))}
          </div>
        </>
      )}
    </div>
  )
}

function BookCard({
  book,
  review,
  onReview,
  onClearReview,
}: {
  book: Book
  review?: LocationReview
  onReview: (review: LocationReview) => void
  onClearReview: () => void
}) {
  const shelfCandidates = [...(book.shelf_candidates ?? [])].sort((a, b) => b.confidence - a.confidence)
  const bestShelf = shelfCandidates[0]

  return (
    <div className="bg-white border border-gray-200 rounded-lg p-4 hover:shadow-sm transition-shadow">
      <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_320px]">
        <div className="flex gap-4 items-start min-w-0">
          {book.thumbnail ? (
            <img
              src={book.thumbnail}
              alt=""
              className="w-16 h-24 object-cover rounded border border-gray-100 shrink-0"
            />
          ) : (
            <div className="w-16 h-24 bg-gray-100 rounded border border-gray-200 shrink-0 flex items-center justify-center text-gray-400 text-xs text-center">
              No<br />cover
            </div>
          )}
          <div className="flex-1 min-w-0">
            <p className="font-bold text-gray-800 text-base leading-tight mb-1">{book.title}</p>
            {book.authors && <p className="text-sm text-gray-600">{book.authors}</p>}
            {book.publisher && <p className="text-sm text-gray-400">{book.publisher}</p>}
            <div className="flex flex-wrap gap-2 mt-3">
              {book.isbn && (
                <span className="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded">ISBN: {book.isbn}</span>
              )}
              {book.class_number && (
                <span className="text-xs bg-sky-50 text-sky-700 px-2 py-0.5 rounded">分類: {book.class_number}</span>
              )}
              {book.registration_number && (
                <span className="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded">登録番号: {book.registration_number}</span>
              )}
            </div>
            {book.info_link && (
              <a
                href={book.info_link}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-xs text-[#1f7a5c] hover:underline mt-3"
              >
                詳細 <ExternalLink className="w-3 h-3" />
              </a>
            )}
          </div>
        </div>

        <LocationReviewPanel
          bestShelf={bestShelf}
          candidates={shelfCandidates}
          review={review}
          onReview={onReview}
          onClearReview={onClearReview}
        />
      </div>
    </div>
  )
}

function LocationReviewPanel({
  bestShelf,
  candidates,
  review,
  onReview,
  onClearReview,
}: {
  bestShelf?: ShelfCandidate
  candidates: ShelfCandidate[]
  review?: LocationReview
  onReview: (review: LocationReview) => void
  onClearReview: () => void
}) {
  const [correction, setCorrection] = useState('')
  const shownShelf = review?.status === 'corrected' ? review.shelfID : bestShelf?.shelf_id
  const confidence = bestShelf ? Math.round(bestShelf.confidence * 100) : 0
  const confidenceTone =
    confidence >= 75 ? 'bg-emerald-500' :
      confidence >= 45 ? 'bg-amber-500' :
        'bg-rose-500'
  const statusLabel = review ? reviewLabel(review) : bestShelf ? '確認待ち' : '未登録'
  const statusTone = review
    ? review.status === 'rejected'
      ? 'bg-rose-50 text-rose-700 border-rose-100'
      : 'bg-emerald-50 text-emerald-700 border-emerald-100'
    : 'bg-slate-50 text-slate-600 border-slate-100'

  const saveCorrection = () => {
    const shelfID = correction.trim()
    if (!shelfID) return
    onReview({ status: 'corrected', shelfID })
  }

  return (
    <section className="border border-gray-200 rounded-lg bg-slate-50/60 p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-xs font-semibold text-gray-500">
            <MapPin className="w-4 h-4 text-[#1f7a5c]" />
            <span>この本はここにありそう</span>
          </div>
          <div className="mt-2 flex items-center gap-2 min-w-0">
            <p className="text-xl font-bold text-gray-900 truncate">
              {shownShelf ?? '場所未登録'}
            </p>
            <span className={`text-xs border px-2 py-0.5 rounded-full shrink-0 ${statusTone}`}>
              {statusLabel}
            </span>
          </div>
        </div>
        {review && (
          <button
            type="button"
            title="未確認に戻す"
            aria-label="未確認に戻す"
            onClick={onClearReview}
            className="h-8 w-8 rounded border border-gray-200 bg-white text-gray-500 hover:text-gray-800 hover:bg-gray-50 grid place-items-center shrink-0"
          >
            <RotateCcw className="w-4 h-4" />
          </button>
        )}
      </div>

      {bestShelf ? (
        <>
          <div className="mt-3">
            <div className="h-2 rounded-full bg-gray-200 overflow-hidden">
              <div
                className={`h-full ${confidenceTone}`}
                style={{ width: `${Math.max(4, confidence)}%` }}
              />
            </div>
            <div className="mt-1 flex items-center justify-between text-xs text-gray-500">
              <span>{confidence}%</span>
              <span>{bestShelf.observations}回検出</span>
            </div>
          </div>

          {candidates.length > 1 && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {candidates.slice(1, 4).map(c => (
                <button
                  key={c.shelf_id}
                  type="button"
                  onClick={() => {
                    setCorrection(c.shelf_id)
                    onReview({ status: 'corrected', shelfID: c.shelf_id })
                  }}
                  className="text-xs bg-white border border-gray-200 text-gray-600 px-2 py-1 rounded hover:border-[#1f7a5c] hover:text-[#1f7a5c]"
                >
                  {c.shelf_id} {Math.round(c.confidence * 100)}%
                </button>
              ))}
            </div>
          )}

          <div className="mt-3 grid grid-cols-2 gap-2">
            <button
              type="button"
              onClick={() => onReview({ status: 'confirmed', shelfID: bestShelf.shelf_id })}
              className="inline-flex items-center justify-center gap-1.5 rounded bg-[#1f7a5c] px-3 py-2 text-sm font-semibold text-white hover:bg-[#196649]"
            >
              <Check className="w-4 h-4" />
              合っている
            </button>
            <button
              type="button"
              onClick={() => onReview({ status: 'rejected', shelfID: bestShelf.shelf_id })}
              className="inline-flex items-center justify-center gap-1.5 rounded border border-rose-200 bg-white px-3 py-2 text-sm font-semibold text-rose-700 hover:bg-rose-50"
            >
              <X className="w-4 h-4" />
              違う
            </button>
          </div>
        </>
      ) : (
        <div className="mt-3 rounded border border-dashed border-gray-300 bg-white px-3 py-2 text-sm text-gray-500">
          まだ場所データがありません。
        </div>
      )}

      {review?.status === 'rejected' && (
        <div className="mt-3 flex gap-2">
          <input
            type="text"
            value={correction}
            onChange={e => setCorrection(e.target.value)}
            placeholder="正しい棚ID"
            className="min-w-0 flex-1 rounded border border-gray-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[#1f7a5c]"
          />
          <button
            type="button"
            onClick={saveCorrection}
            disabled={!correction.trim()}
            className="rounded bg-gray-900 px-3 py-2 text-sm font-semibold text-white disabled:opacity-40"
          >
            記録
          </button>
        </div>
      )}
    </section>
  )
}

function reviewLabel(review: LocationReview): string {
  switch (review.status) {
    case 'confirmed':
      return '確認済み'
    case 'rejected':
      return '修正待ち'
    case 'corrected':
      return '修正済み'
  }
}
