import { ExternalLink, X } from 'lucide-react'
import type { FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import type { Book, ShelfCandidate } from '../lib/types'
import { confidenceLevel, formatShelfLabel, getSlot, getUnitId, shortShelfLabel, splitShelfId } from '../lib/shelf'
import { ShelfLocationLabel, ShelfMapHighlight } from './shelf'

// FigmaのMaterial Symbols「search」(filled)を移植。lucideのSearchは線画で意匠が異なるため使わない。
function SearchIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" className={className} aria-hidden="true">
      <path
        d="M19.6 21L13.3 14.7C12.8 15.1 12.225 15.4167 11.575 15.65C10.925 15.8833 10.2333 16 9.5 16C7.68333 16 6.14583 15.3708 4.8875 14.1125C3.62917 12.8542 3 11.3167 3 9.5C3 7.68333 3.62917 6.14583 4.8875 4.8875C6.14583 3.62917 7.68333 3 9.5 3C11.3167 3 12.8542 3.62917 14.1125 4.8875C15.3708 6.14583 16 7.68333 16 9.5C16 10.2333 15.8833 10.925 15.65 11.575C15.4167 12.225 15.1 12.8 14.7 13.3L21 19.6L19.6 21ZM9.5 14C10.75 14 11.8125 13.5625 12.6875 12.6875C13.5625 11.8125 14 10.75 14 9.5C14 8.25 13.5625 7.1875 12.6875 6.3125C11.8125 5.4375 10.75 5 9.5 5C8.25 5 7.1875 5.4375 6.3125 6.3125C5.4375 7.1875 5 8.25 5 9.5C5 10.75 5.4375 11.8125 6.3125 12.6875C7.1875 13.5625 8.25 14 9.5 14Z"
        fill="currentColor"
      />
    </svg>
  )
}

export function SearchBar({
  value,
  onChange,
  onSubmit,
  autoFocus = false,
}: {
  value: string
  onChange: (value: string) => void
  onSubmit: () => void
  autoFocus?: boolean
}) {
  const submit = (event: FormEvent) => {
    event.preventDefault()
    onSubmit()
  }
  return (
    <form
      onSubmit={submit}
      className="flex w-full items-center gap-2 rounded-[24px] bg-white px-[14px] py-3 shadow-[0_3px_2.9px_rgba(0,0,0,0.1)] focus-within:ring-2 focus-within:ring-primary-soft"
    >
      <SearchIcon className="size-6 shrink-0 text-[#087f5b]" />
      <input
        value={value}
        onChange={event => onChange(event.target.value)}
        autoFocus={autoFocus}
        placeholder="蟹工船"
        className="min-w-0 flex-1 border-0 bg-transparent p-0 text-base text-ink outline-none placeholder:text-black/50"
      />
      {value && (
        <button
          type="button"
          aria-label="検索語を消す"
          onClick={() => onChange('')}
          className="grid size-8 shrink-0 place-items-center rounded-full text-ink-muted hover:bg-zinc-100"
        >
          <X className="size-4" />
        </button>
      )}
    </form>
  )
}

export function BookCard({ book, onClick }: { book: Book; onClick?: () => void }) {
  const navigate = useNavigate()
  const top = [...(book.shelf_candidates ?? [])].sort((a, b) => b.confidence - a.confidence)[0]
  const open = onClick ?? (() => navigate(`/books/${book.id}`))
  return (
    <button
      type="button"
      onClick={open}
      className="flex w-full gap-3 rounded-xl border border-line bg-white p-3 text-left shadow-sm transition hover:border-primary-soft"
    >
      <BookCover book={book} size="sm" />
      <div className="min-w-0 flex-1">
        <p className="line-clamp-2 font-semibold leading-snug text-ink">{book.title}</p>
        {book.authors && <p className="mt-1 truncate text-sm text-ink-muted">{book.authors}</p>}
        {book.publisher && <p className="truncate text-xs text-ink-faint">{book.publisher}</p>}
        <div className="mt-3">
          {top ? <ShelfChip shelfId={top.shelf_id} confidence={top.confidence} /> : <span className="rounded-full bg-zinc-100 px-3 py-1 text-xs font-semibold text-ink-muted">場所未登録</span>}
        </div>
      </div>
    </button>
  )
}

export function BookHero({ book }: { book: Book }) {
  return (
    <section className="flex gap-4 rounded-xl border border-line bg-white p-4 shadow-sm">
      <BookCover book={book} size="lg" />
      <div className="min-w-0 flex-1">
        <h2 className="text-lg font-bold leading-tight text-ink">{book.title}</h2>
        {book.authors && <p className="mt-2 text-sm text-ink-muted">{book.authors}</p>}
        <div className="mt-3 grid gap-1 text-xs text-ink-muted">
          {book.publisher && <p>{book.publisher}</p>}
          {book.published_date && <p>{book.published_date}</p>}
          {book.isbn && <p>ISBN {book.isbn}</p>}
          {book.class_number && <p>分類 {book.class_number}</p>}
        </div>
        {book.info_link && (
          <a href={book.info_link} target="_blank" rel="noreferrer" className="mt-3 inline-flex items-center gap-1 text-xs font-semibold text-primary">
            書誌情報 <ExternalLink className="size-3" />
          </a>
        )}
      </div>
    </section>
  )
}

function BookCover({ book, size }: { book: Book; size: 'sm' | 'lg' }) {
  const cls = size === 'lg' ? 'h-32 w-24' : 'h-20 w-14'
  if (book.thumbnail) {
    return <img src={book.thumbnail} alt="" className={`${cls} shrink-0 rounded-lg border border-line object-cover bg-zinc-100`} loading="lazy" />
  }
  return <div className={`${cls} grid shrink-0 place-items-center rounded-lg border border-line bg-zinc-100 text-center text-xs text-ink-faint`}>No<br />cover</div>
}

export function ShelfChip({ shelfId, confidence }: { shelfId: string; confidence: number }) {
  const level = confidenceLevel(confidence)
  const tone = level === 'high' ? 'bg-confidence-high' : level === 'mid' ? 'bg-confidence-mid' : 'bg-confidence-low'
  return (
    <span className="inline-flex max-w-full items-center gap-2 rounded-full bg-primary-soft px-3 py-1 text-xs font-semibold text-primary">
      <span className={`size-2 rounded-full ${tone}`} />
      <span className="truncate">{shortShelfLabel(shelfId)}</span>
    </span>
  )
}

export function ConfidenceMeter({ candidate }: { candidate: ShelfCandidate }) {
  const level = confidenceLevel(candidate.confidence)
  const label = level === 'high' ? 'ほぼ確実' : level === 'mid' ? 'たぶんここ' : '情報が古いかも'
  const tone = level === 'high' ? 'text-confidence-high' : level === 'mid' ? 'text-confidence-mid' : 'text-confidence-low'
  return (
    <div className="rounded-xl border border-line bg-white p-4">
      <p className={`font-bold ${tone}`}>{label}</p>
      <p className="mt-1 text-xs text-ink-muted">
        {candidate.observations}回の観測{candidate.last_seen_at || candidate.updated_at ? ` ・ 最終確認 ${formatDate(candidate.last_seen_at ?? candidate.updated_at)}` : ''}
      </p>
    </div>
  )
}

export function AltShelfList({ candidates }: { candidates: ShelfCandidate[] }) {
  const others = candidates.slice(1)
  if (others.length === 0) return null
  return (
    <section className="rounded-xl border border-line bg-white p-4">
      <h3 className="font-semibold text-ink">ほかの候補</h3>
      <div className="mt-3 grid gap-2">
        {others.map(candidate => (
          <div key={candidate.shelf_id} className="flex items-center justify-between gap-3 rounded-lg bg-zinc-50 p-3">
            <ShelfLocationLabel shelfId={candidate.shelf_id} size="sm" />
            <ShelfChip shelfId={candidate.shelf_id} confidence={candidate.confidence} />
          </div>
        ))}
      </div>
    </section>
  )
}

export function BookLocationPanel({ candidate }: { candidate: ShelfCandidate }) {
  const unitId = getUnitId(candidate.shelf_id)
  const split = splitShelfId(candidate.shelf_id)
  return (
    <section className="rounded-xl border border-line bg-white p-4 shadow-sm">
      <div className="mb-3">
        <p className="text-xs font-semibold text-ink-muted">本の場所</p>
        <ShelfLocationLabel shelfId={candidate.shelf_id} size="lg" />
      </div>
      {unitId && split && (
        <ShelfMapHighlight unitId={unitId} highlight={[split.cellId]} />
      )}
      <p className="mt-3 text-xs text-ink-muted">図の色がついた区画を見てください。</p>
    </section>
  )
}

export function LowConfidenceBanner({ candidate }: { candidate: ShelfCandidate }) {
  if (confidenceLevel(candidate.confidence) !== 'low') return null
  return (
    <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm font-semibold text-amber-800">
      この場所情報は古い可能性があります。見つからないときは周辺の棚も見てください。
    </div>
  )
}

export function shelfBookFromCandidate(candidate: ShelfCandidate): Book {
  return {
    id: candidate.book_id ?? 0,
    title: candidate.title ?? 'タイトル未登録',
    authors: '',
    publisher: '',
    published_date: '',
    class_number: '',
    registration_number: '',
    isbn: '',
    thumbnail: null,
    info_link: null,
    shelf_candidates: [candidate],
  }
}

function formatDate(value: string | undefined): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return `${date.getMonth() + 1}/${date.getDate()}`
}
