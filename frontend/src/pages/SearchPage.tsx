import { useEffect, useState } from 'react'
import { ScanLine } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { SearchBar } from '../components/book'
import { BrandMark } from '../components/common'
import { fallbackCoverForTitle, featuredBooks } from '../data/figmaBooks'
import { getFeaturedBooks } from '../lib/api'
import type { Book } from '../lib/types'

export default function SearchPage() {
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [featured, setFeatured] = useState<Book[]>([])
  const [featuredLoading, setFeaturedLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    getFeaturedBooks(5)
      .then(books => { if (!cancelled) setFeatured(featuredBooksForDisplay(books)) })
      .catch(() => { if (!cancelled) setFeatured(fallbackFeaturedBooks()) })
      .finally(() => { if (!cancelled) setFeaturedLoading(false) })
    return () => { cancelled = true }
  }, [])

  const runSearch = () => {
    const q = query.trim()
    if (q) navigate(`/search?q=${encodeURIComponent(q)}`)
  }

  return (
    <div className="relative h-[calc(100svh-72px)] min-h-[620px] overflow-hidden bg-white md:h-auto md:min-h-[calc(100vh-88px)] md:overflow-x-hidden md:overflow-y-visible">
      <img
        src="/figma-icons/book-corner-tl.svg"
        alt=""
        aria-hidden="true"
        className="pointer-events-none absolute left-0 top-0 -z-0 h-[164px] w-[168px] select-none opacity-95 md:-left-[98px] md:-top-[133px] md:h-[376px] md:w-[384px]"
      />
      <img
        src="/figma-icons/book-corner-br.svg"
        alt=""
        aria-hidden="true"
        className="pointer-events-none absolute -bottom-[122px] -right-[72px] -z-0 h-[246px] w-[234px] select-none opacity-95 md:-bottom-[63px] md:-right-[46px] md:h-[331px] md:w-[315px]"
      />
      <div className="relative z-10 mx-auto flex h-full w-full max-w-[402px] flex-col justify-start gap-8 pt-[180px] md:min-h-[744px] md:max-w-none md:gap-[92px] md:pb-0 md:pt-0">
        <div className="flex flex-col items-center gap-6 px-7 md:mt-[147px] md:w-[444px] md:self-center md:gap-10 md:px-0">
          <div className="hidden md:block">
            <BrandMark />
          </div>
          <h1 className="w-full text-center text-2xl font-normal leading-[29px] tracking-[0.05em] text-ink">
            どんな<span className="text-[#087f5b]">本</span>でも一瞬で
          </h1>
          <SearchBar value={query} onChange={setQuery} onSubmit={runSearch} />
        </div>

        {(featuredLoading || featured.length > 0) && (
          <div className="flex flex-col items-center gap-4 md:gap-8">
            <p className="w-full text-center text-base leading-[19px] text-ink">今週のおすすめ</p>
            <div className="flex w-full snap-x snap-mandatory scroll-px-7 gap-8 overflow-x-auto px-7 [-webkit-overflow-scrolling:touch] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden md:w-auto md:snap-none md:justify-center md:gap-[22px] md:overflow-visible md:px-0 md:scroll-px-0">
              {featuredLoading
                ? Array.from({ length: 5 }, (_, index) => <FeaturedBookSkeleton key={index} />)
                : featured.map(book => (
                    <FeaturedBookCard key={book.id} book={book} onClick={() => navigate(`/books/${book.id}`)} className="snap-start" />
                  ))}
            </div>
          </div>
        )}

        <div className="relative z-10 flex flex-col items-center gap-3 md:fixed md:bottom-[67px] md:right-8 md:z-10">
          <button type="button" onClick={() => navigate('/scan')} className="tap-card flex h-[71px] w-[196px] items-center justify-center gap-4 rounded-full bg-[#087f5b] px-6 py-4 text-2xl text-white shadow-[0_10px_24px_rgba(8,127,91,0.22)] transition hover:bg-[#076b4d] md:size-[90px] md:bg-[#363636] md:p-0 md:hover:bg-[#222]" aria-label="本棚をスキャン">
            <ScanLine className="size-9 md:size-12" strokeWidth={1.8} />
            <span className="md:hidden">スキャン</span>
          </button>
          <p className="text-center text-xs leading-[15px] text-ink md:hidden">本棚をスキャンして検索</p>
        </div>
      </div>
    </div>
  )
}

function FeaturedBookCard({ book, onClick, className = '' }: { book: Book; onClick: () => void; className?: string }) {
  const cover = book.thumbnail || fallbackCoverForTitle(book.title)
  return (
    <button
      type="button"
      onClick={onClick}
      className={`tap-card flex w-[165px] shrink-0 flex-col items-center gap-2 rounded-lg text-center md:w-[114px] md:gap-[6px] ${className}`}
    >
      {cover ? (
        <img src={cover} alt="" className="h-[210px] w-[149px] object-cover md:h-[155px] md:w-[114px]" loading="lazy" />
      ) : (
        <div className="h-[210px] w-[149px] bg-[#d9d9d9] md:h-[155px] md:w-[114px]" />
      )}
      <p className="line-clamp-2 w-full text-center text-base leading-[19px] text-ink md:text-[11px] md:leading-[13px]">{book.title}</p>
    </button>
  )
}

function FeaturedBookSkeleton() {
  return (
    <div className="flex w-[165px] shrink-0 flex-col items-center gap-2 md:w-[114px] md:gap-[6px]">
      <div className="h-[210px] w-[149px] animate-pulse bg-[#d9d9d9] md:h-[155px] md:w-[114px]" />
      <div className="h-4 w-20 animate-pulse rounded bg-zinc-100" />
    </div>
  )
}

function fallbackFeaturedBooks(): Book[] {
  if (!import.meta.env.DEV) return []
  return featuredBooks.map((book, index) => ({
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

function featuredBooksForDisplay(books: Book[]): Book[] {
  if (!import.meta.env.DEV) return books
  const withCovers = books.filter(book => fallbackCoverForTitle(book.title))
  return withCovers.length >= 3 ? books : fallbackFeaturedBooks()
}
