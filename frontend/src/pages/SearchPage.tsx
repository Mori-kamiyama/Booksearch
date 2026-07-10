import { useCallback, useState } from 'react'
import { BookOpen } from 'lucide-react'
import { searchBooks } from '../lib/api'
import type { Book } from '../lib/types'
import { BookCard, SearchBar } from '../components/book'
import { EmptyState, ErrorState, PageHeader, Skeleton } from '../components/common'

export default function SearchPage() {
  const [query, setQuery] = useState('')
  const [books, setBooks] = useState<Book[]>([])
  const [loading, setLoading] = useState(false)
  const [searched, setSearched] = useState(false)
  const [error, setError] = useState(false)

  const runSearch = useCallback(async () => {
    const q = query.trim()
    if (!q) return
    setLoading(true)
    setSearched(true)
    setError(false)
    try {
      setBooks(await searchBooks(q))
    } catch {
      setBooks([])
      setError(true)
    } finally {
      setLoading(false)
    }
  }, [query])

  return (
    <div>
      <PageHeader title="図書室の本、どこにある？" />
      <SearchBar value={query} onChange={setQuery} onSubmit={runSearch} autoFocus />

      <div className="mt-6">
        {loading && (
          <div className="grid gap-3">
            <Skeleton variant="card" />
            <Skeleton variant="card" />
            <Skeleton variant="card" />
          </div>
        )}

        {!loading && error && <ErrorState message="検索できませんでした。" onRetry={runSearch} />}

        {!loading && !error && !searched && (
          <EmptyState
            icon={<BookOpen className="size-5" />}
            title="書名の一部だけでも探せます"
            hint="見つけたい本を検索すると、棚の見取り図まで案内します。"
          />
        )}

        {!loading && !error && searched && books.length === 0 && (
          <EmptyState
            title="見つかりませんでした"
            hint="別の言葉で試すか、書名の一部だけで検索してください。"
          />
        )}

        {!loading && !error && books.length > 0 && (
          <>
            <p className="mb-3 text-sm text-ink-muted">{books.length}件</p>
            <div className="grid gap-3">
              {books.map(book => <BookCard key={book.id} book={book} />)}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
