import { Link, useParams } from 'react-router-dom'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { ScanLine } from 'lucide-react'
import { getShelfCandidates } from '../lib/api'
import type { ShelfCandidate } from '../lib/types'
import { BookCard, shelfBookFromCandidate } from '../components/book'
import { EmptyState, ErrorState, PageHeader, Skeleton } from '../components/common'
import { FreshnessBadge, ShelfLocationLabel, ShelfMiniMap } from '../components/shelf'

export default function ShelfDetailPage() {
  const { shelfId } = useParams<{ shelfId: string }>()
  const decodedShelfId = shelfId ? decodeURIComponent(shelfId) : ''
  const [candidates, setCandidates] = useState<ShelfCandidate[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError(false)
    try {
      setCandidates(await getShelfCandidates())
    } catch {
      setCandidates([])
      setError(true)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const books = useMemo(
    () => candidates
      .filter(candidate => candidate.shelf_id === decodedShelfId)
      .sort((a, b) => b.confidence - a.confidence),
    [candidates, decodedShelfId],
  )
  const lastSeenAt = books[0]?.last_seen_at ?? books[0]?.updated_at

  return (
    <div>
      <PageHeader title="棚区画" back />
      {loading ? (
        <Skeleton variant="card" />
      ) : error ? (
        <ErrorState message="棚の本を読み込めませんでした。" onRetry={load} />
      ) : (
        <div className="grid gap-4">
          <section className="rounded-xl border border-line bg-white p-4 shadow-sm">
            <div className="flex items-start justify-between gap-3">
              <ShelfLocationLabel shelfId={decodedShelfId} size="lg" />
              <FreshnessBadge lastSeenAt={lastSeenAt} />
            </div>
            <div className="mt-4">
              <ShelfMiniMap shelfId={decodedShelfId} />
            </div>
          </section>

          {books.length === 0 ? (
            <EmptyState title="この区画の本は未登録です" hint="スキャンで見つかると、ここに一覧が表示されます。" />
          ) : (
            <div className="grid gap-3">
              {books.map(candidate => (
                <BookCard key={`${candidate.book_id}:${candidate.shelf_id}`} book={shelfBookFromCandidate(candidate)} />
              ))}
            </div>
          )}

          <Link
            to={`/scan?shelf=${encodeURIComponent(decodedShelfId)}`}
            className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg bg-primary px-4 text-sm font-bold text-white"
          >
            <ScanLine className="size-4" />
            この棚をスキャンして更新
          </Link>
        </div>
      )}
    </div>
  )
}
