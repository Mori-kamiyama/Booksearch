import { useEffect, useState } from 'react'
import { ScanLine } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { CoverImage, SearchBar } from '../components/book'
import { fallbackCoverForTitle } from '../data/figmaBooks'
import { getFeaturedBooks } from '../lib/api'
import type { Book } from '../lib/types'
import { BrandMark, RakutenCredit } from '../components/common'

export default function SearchPage() {
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [featured, setFeatured] = useState<Book[]>([])
  const [featuredLoading, setFeaturedLoading] = useState(true)

  const [featuredError, setFeaturedError] = useState(false)
  const [retry, setRetry] = useState(0)

  useEffect(() => {
    let cancelled = false
    setFeaturedLoading(true)
    setFeaturedError(false)
    getFeaturedBooks(5)
      .then(books => { if (!cancelled) setFeatured(books) })
      .catch(() => { if (!cancelled) setFeaturedError(true) })
      .finally(() => { if (!cancelled) setFeaturedLoading(false) })
    return () => { cancelled = true }
  }, [retry])

  const runSearch = () => {
    const q = query.trim()
    if (q) navigate(`/search?q=${encodeURIComponent(q)}`)
  }

  return (
    <div className="relative min-h-svh overflow-x-clip bg-white">
      <img
        src="/figma-icons/book-corner-tl.svg"
        alt=""
        aria-hidden="true"
        className="pointer-events-none absolute left-0 top-0 -z-0 hidden h-[164px] w-[168px] select-none opacity-95 md:block md:-left-[98px] md:-top-[133px] md:h-[376px] md:w-[384px]"
      />
      <img
        src="/figma-icons/book-corner-br.svg"
        alt=""
        aria-hidden="true"
        className="pointer-events-none absolute -bottom-[122px] -right-[72px] -z-0 hidden h-[246px] w-[234px] select-none opacity-95 md:block md:-bottom-[63px] md:-right-[46px] md:h-[331px] md:w-[315px]"
      />
      <div className="relative z-10 mx-auto flex min-h-svh w-full max-w-[402px] flex-col justify-center gap-8 pb-6 pt-[72px] md:max-w-none md:justify-start md:gap-16 md:pb-16 md:pt-0">
        <div className="flex w-full flex-col items-center gap-6 md:mt-[25vh] md:w-[444px] md:self-center md:gap-10">
          <BrandMark />
          <h1 className="w-full text-center text-2xl font-normal leading-[29px] tracking-[0.05em] text-ink">
            どんな<span className="text-[#087f5b]">本</span>でも一瞬で
          </h1>
          <SearchBar value={query} onChange={setQuery} onSubmit={runSearch} />
        </div>

        {featuredError && <div role="status" className="text-center text-sm text-ink-muted">おすすめを読み込めませんでした。<button type="button" className="ml-2 min-h-11 text-primary underline" onClick={() => setRetry(value => value + 1)}>再試行</button></div>}
        {!featuredLoading && !featuredError && featured.length === 0 && (
          <div className="flex flex-col items-center gap-1 text-center text-sm text-ink-muted" role="status">
            <p className="text-base text-ink">今週のおすすめ</p>
            <p>おすすめは準備中です。</p>
          </div>
        )}
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

        <div className="relative z-20 flex flex-col items-center gap-3 self-center md:fixed md:bottom-[67px] md:right-8 md:z-10">
          <button type="button" onClick={() => navigate('/scan')} className="tap-card flex h-[71px] w-[196px] items-center justify-center gap-4 rounded-full bg-[#087f5b] px-6 py-4 text-2xl text-white shadow-[0_10px_24px_rgba(8,127,91,0.22)] transition hover:bg-[#076b4d] md:size-[90px] md:bg-[#363636] md:p-0 md:hover:bg-[#222]" aria-label="本棚をスキャン">
            <ScanLine className="size-9 text-white md:size-12" strokeWidth={1.8} />
            <span className="md:hidden">スキャン</span>
          </button>
          <p className="text-center text-xs leading-[15px] text-ink md:hidden">本棚をスキャンして検索</p>
        </div>
      </div>
      <RakutenCredit className="absolute bottom-1 left-2 z-20 text-[9px] text-ink-muted/60 underline-offset-2 hover:underline md:bottom-3 md:left-1/2 md:-translate-x-1/2 md:text-[10px]" />
    </div>
  )
}

function FeaturedBookCard({ book, onClick, className = '' }: { book: Book; onClick: () => void; className?: string }) {
  const cover = book.thumbnail || fallbackCoverForTitle(book.title)
  return (
    <button
      type="button"
      onClick={onClick}
      className={`tap-card flex w-[122px] shrink-0 flex-col items-center gap-2 rounded-lg text-center md:w-[114px] md:gap-[6px] ${className}`}
    >
      <div className="flex h-[150px] w-[108px] shrink-0 items-end justify-center overflow-hidden md:h-[155px] md:w-[114px]">
        {cover ? (
          <CoverImage src={cover} alt="" className="block max-h-full max-w-full object-contain" fallbackClassName="grid h-full w-full place-items-center bg-[#d9d9d9]" />
        ) : (
          <CoverImage fallbackClassName="grid h-full w-full place-items-center bg-[#d9d9d9]" />
        )}
      </div>
      <p className="line-clamp-2 min-h-[34px] w-full break-words px-1 text-center text-sm leading-[17px] text-ink md:min-h-[26px] md:px-0 md:text-[11px] md:leading-[13px]">{book.title}</p>
    </button>
  )
}

function FeaturedBookSkeleton() {
  return (
    <div className="flex w-[122px] shrink-0 flex-col items-center gap-2 md:w-[114px] md:gap-[6px]">
      <div className="h-[150px] w-[108px] animate-pulse bg-[#d9d9d9] md:h-[155px] md:w-[114px]" />
      <div className="h-4 w-20 animate-pulse rounded bg-zinc-100" />
    </div>
  )
}
