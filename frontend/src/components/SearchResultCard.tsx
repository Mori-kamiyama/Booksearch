import { CoverImage } from './book'
import { fallbackCoverForTitle } from '../data/figmaBooks'
import type { Book } from '../lib/types'

export function SearchResultCard({ book, onOpen, onPointerDown }: { book: Book; onOpen: () => void; onPointerDown: () => void }) {
  const cover = book.thumbnail || fallbackCoverForTitle(book.title)
  return (
    <button type="button" aria-labelledby={`search-title-${book.id}`} aria-describedby={`search-meta-${book.id}`} onPointerDown={onPointerDown} onClick={onOpen} className="tap-card flex min-w-0 flex-col items-center gap-2 rounded-lg text-center">
      <div className="flex h-[199px] w-[141px] max-w-full items-end justify-center md:h-[208px] md:w-[153px]">
        {cover ? <CoverImage src={cover} className="max-h-full max-w-full bg-[#d9d9d9] object-contain" fallbackClassName="grid h-full w-full place-items-center bg-[#d9d9d9]" /> : <div className="h-full w-full bg-[#d9d9d9]" />}
      </div>
      <span id={`search-title-${book.id}`} className="line-clamp-2 w-full text-sm leading-normal text-ink md:text-[15px]">{book.title}</span>
      <span id={`search-meta-${book.id}`} className="flex w-full flex-col gap-1 text-xs text-ink-muted">
        {book.authors && <span className="line-clamp-1">{book.authors}</span>}
        {book.published_date?.match(/^\d{4}/)?.[0] && <span>{book.published_date.match(/^\d{4}/)?.[0]}</span>}
      </span>
    </button>
  )
}
