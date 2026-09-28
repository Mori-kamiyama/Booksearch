import { useCallback, useEffect, useRef, useState } from 'react'
import { BookOpen, Menu, ScanLine, Search, X } from 'lucide-react'
import { Link, useNavigate } from 'react-router-dom'
import { searchBooks } from '../lib/api'
import type { Book } from '../lib/types'
import { featuredBooks } from '../data/figmaBooks'
import { BookCard, SearchBar } from '../components/book'
import { EmptyState, ErrorState, Skeleton } from '../components/common'

export default function SearchPage() {
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [books, setBooks] = useState<Book[]>([])
  const [loading, setLoading] = useState(false)
  const [searched, setSearched] = useState(false)
  const [error, setError] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const requestGeneration = useRef(0)

  useEffect(() => () => { requestGeneration.current += 1 }, [])

  const runSearch = useCallback(async (term: string) => {
    const q = term.trim()
    if (!q) return
    const generation = ++requestGeneration.current
    setQuery(q)
    setLoading(true)
    setSearched(true)
    setError(false)
    try {
      const results = await searchBooks(q)
      if (generation !== requestGeneration.current) return
      setBooks(results)
    } catch {
      if (generation !== requestGeneration.current) return
      setBooks([])
      setError(true)
    } finally {
      if (generation === requestGeneration.current) setLoading(false)
    }
  }, [])

  return (
    <div className="relative flex min-h-svh flex-col overflow-hidden bg-white text-ink">
      <header className="absolute inset-x-0 top-0 z-20 flex h-16 items-center justify-end px-5 sm:px-8">
        <button type="button" aria-label="メニューを開く" aria-expanded={menuOpen} onClick={() => setMenuOpen(true)} className="grid size-11 place-items-center rounded-full focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink">
          <Menu className="size-7" />
        </button>
      </header>

      {menuOpen && (
        <div className="fixed inset-0 z-30">
          <button type="button" aria-label="メニューを閉じる" onClick={() => setMenuOpen(false)} className="absolute inset-0 bg-black/30" />
          <nav aria-label="メインメニュー" className="absolute right-0 top-0 flex h-full w-64 max-w-[80vw] flex-col bg-white px-6 shadow-xl">
            <div className="flex h-16 justify-end">
              <button type="button" aria-label="メニューを閉じる" onClick={() => setMenuOpen(false)} className="grid size-11 place-items-center"><X className="size-6" /></button>
            </div>
            <Link to="/" onClick={() => { setMenuOpen(false); setSearched(false) }} className="border-b border-line py-4 text-base sm:text-lg">さがす</Link>
            <Link to="/map" className="border-b border-line py-4 text-base sm:text-lg">図書室マップ</Link>
            <Link to="/scan" className="border-b border-line py-4 text-base sm:text-lg">スキャン</Link>
          </nav>
        </div>
      )}

      {searched ? (
        <main className="mx-auto w-full max-w-3xl px-5 pb-12 pt-20 sm:px-8">
          <button type="button" onClick={() => setSearched(false)} className="mb-6 text-sm text-ink-muted underline underline-offset-4">ホームに戻る</button>
          <SearchBar value={query} onChange={setQuery} onSubmit={() => runSearch(query)} />
          <div className="mt-6">
            {loading && <div className="grid gap-3"><Skeleton variant="card" /><Skeleton variant="card" /><Skeleton variant="card" /></div>}
            {!loading && error && <ErrorState message="検索できませんでした。" onRetry={() => runSearch(query)} />}
            {!loading && !error && books.length === 0 && <EmptyState icon={<BookOpen className="size-5" />} title="見つかりませんでした" hint="別の言葉か、書名の一部で検索してください。" />}
            {!loading && !error && books.length > 0 && <><p className="mb-3 text-sm text-ink-muted">{books.length}件</p><div className="grid gap-3">{books.map(book => <BookCard key={book.id} book={book} onClick={() => navigate(`/books/${book.id}?q=${encodeURIComponent(query.trim())}`)} />)}</div></>}
          </div>
        </main>
      ) : (
        <>
          <div className="mx-auto flex w-full max-w-xl flex-col items-center px-5 pt-24 sm:px-8 sm:pt-[clamp(6rem,13vh,8rem)]">
            <div className="flex items-center gap-3" aria-label="ホンノキ">
              <img src="/figma-icons/leaf.svg" alt="" className="h-7 w-12 sm:h-9 sm:w-16" />
              <img src="/figma-icons/wordmark.svg" alt="ホンノキ" className="h-[30px] w-[137px] sm:h-10 sm:w-[180px]" />
            </div>
            <h1 className="mt-5 text-center text-lg tracking-wide sm:mt-6 sm:text-2xl">どんな<span className="text-[#087f5b]">本</span>でも一瞬で</h1>
            <form onSubmit={event => { event.preventDefault(); void runSearch(query) }} className="mt-6 flex h-14 w-full shrink-0 items-center gap-3 overflow-hidden rounded-full bg-white px-5 shadow-[0_4px_12px_rgba(0,0,0,0.14)] focus-within:outline focus-within:outline-1 focus-within:outline-ink-muted sm:mt-8">
              <Search className="size-5 shrink-0 text-[#087f5b] sm:size-6" aria-hidden="true" />
              <input value={query} onChange={event => setQuery(event.target.value)} aria-label="本を検索" placeholder="書名・著者・テーマを検索" className="h-full min-w-0 flex-1 bg-transparent text-base outline-none placeholder:text-ink-muted" />
              {query && <button type="button" aria-label="検索語を消す" onClick={() => setQuery('')} className="grid size-8 shrink-0 place-items-center rounded-full text-ink-muted"><X className="size-4" /></button>}
            </form>
          </div>

          <section aria-labelledby="featured-heading" className="mt-10 w-full sm:mt-14">
            <h2 id="featured-heading" className="text-center text-base font-normal sm:text-lg">今週のおすすめ</h2>
            <div className="mt-5 flex snap-x gap-5 overflow-x-auto px-5 pb-2 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden sm:justify-center sm:px-8">
              {featuredBooks.map(book => (
                <button key={book.title} type="button" onClick={() => void runSearch(book.title)} className="flex w-[118px] shrink-0 snap-start flex-col items-center gap-2 text-center focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink">
                  <img src={book.cover} alt="" className="h-[148px] w-[108px] object-contain" />
                  <span className="line-clamp-2 text-[13px] leading-snug sm:text-sm">{book.title}</span>
                </button>
              ))}
            </div>
          </section>

          <div className="mt-auto flex flex-col items-center px-5 pb-12 pt-10">
            <button type="button" onClick={() => navigate('/scan')} className="flex min-h-14 min-w-44 items-center justify-center gap-3 rounded-full bg-[#087f5b] px-7 text-lg text-white shadow-[0_10px_24px_rgba(8,127,91,0.18)] focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ink sm:min-h-16 sm:min-w-48 sm:text-xl">
              <ScanLine className="size-7 sm:size-8" strokeWidth={1.8} />スキャン
            </button>
            <p className="mt-3 text-xs sm:text-sm">本棚をスキャンして検索</p>
          </div>
          <a href="https://developers.rakuten.com/" target="_blank" rel="noreferrer" className="absolute bottom-2 left-4 text-[10px] text-ink-faint">Supported by Rakuten Developers</a>
        </>
      )}
    </div>
  )
}
