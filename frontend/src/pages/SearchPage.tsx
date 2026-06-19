import { useState, useCallback } from 'react'
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

export default function SearchPage() {
  const [query, setQuery] = useState('')
  const [books, setBooks] = useState<Book[]>([])
  const [loading, setLoading] = useState(false)
  const [searched, setSearched] = useState(false)

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
              <BookCard key={book.id} book={book} />
            ))}
          </div>
        </>
      )}
    </div>
  )
}

function BookCard({ book }: { book: Book }) {
  const shelfCandidates = [...(book.shelf_candidates ?? [])].sort((a, b) => b.confidence - a.confidence)
  const bestShelf = shelfCandidates[0]

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-4 flex gap-4 items-start hover:shadow-md transition-shadow">
      {book.thumbnail ? (
        <img
          src={book.thumbnail}
          alt=""
          className="w-14 h-20 object-cover rounded border border-gray-100 shrink-0"
        />
      ) : (
        <div className="w-14 h-20 bg-gray-100 rounded border border-gray-200 shrink-0 flex items-center justify-center text-gray-400 text-xs text-center">
          No<br />cover
        </div>
      )}
      <div className="flex-1 min-w-0">
        <p className="font-bold text-gray-800 text-base leading-tight mb-1">{book.title}</p>
        {book.authors && <p className="text-sm text-gray-600">{book.authors}</p>}
        {book.publisher && <p className="text-sm text-gray-400">{book.publisher}</p>}
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <span className="text-xs font-semibold text-gray-500">場所</span>
          {bestShelf ? (
            <>
              <span className="text-sm bg-green-50 text-green-800 px-2.5 py-1 rounded font-bold">
                {bestShelf.shelf_id}
              </span>
              <span className="text-xs text-gray-500">
                {Math.round(bestShelf.confidence * 100)}% / {bestShelf.observations}回
              </span>
              {shelfCandidates.slice(1).map(c => (
                <span key={c.shelf_id} className="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded">
                  {c.shelf_id} {Math.round(c.confidence * 100)}%
                </span>
              ))}
            </>
          ) : (
            <span className="text-sm text-gray-400">場所未登録</span>
          )}
        </div>
        <div className="flex flex-wrap gap-2 mt-2">
          {book.isbn && (
            <span className="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded">ISBN: {book.isbn}</span>
          )}
          {book.class_number && (
            <span className="text-xs bg-blue-50 text-blue-700 px-2 py-0.5 rounded">分類: {book.class_number}</span>
          )}
        </div>
      </div>
      {book.info_link && (
        <a
          href={book.info_link}
          target="_blank"
          rel="noopener noreferrer"
          className="text-xs text-[#1f7a5c] hover:underline shrink-0"
        >
          詳細 →
        </a>
      )}
    </div>
  )
}
