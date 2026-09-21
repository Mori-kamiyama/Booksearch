import { Link, useParams } from 'react-router-dom'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ScanLine } from 'lucide-react'
import { getShelfCandidates } from '../lib/api'
import type { ShelfCandidate } from '../lib/types'
import { getSlot } from '../lib/shelf'
import { BookCard, shelfBookFromCandidate } from '../components/book'
import { EmptyState, ErrorState, PageHeader, Skeleton } from '../components/common'
import { FreshnessBadge, ShelfLocationLabel, ShelfMiniMap } from '../components/shelf'

export default function ShelfDetailPage() {
  const { shelfId } = useParams<{ shelfId: string }>()
  const shelf = getSlot(shelfId)
  const validShelfId = shelf?.shelf_id ?? ''
  const [candidates, setCandidates] = useState<ShelfCandidate[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const mountedRef = useRef(false)
  const requestIdRef = useRef(0)

  const load = useCallback(async (requestId: number) => {
    const isCurrent = () => mountedRef.current && requestIdRef.current === requestId
    if (!isCurrent()) return
    setLoading(true)
    setError(false)
    try {
      const loadedCandidates = await getShelfCandidates()
      if (!isCurrent()) return
      setCandidates(loadedCandidates)
    } catch {
      if (!isCurrent()) return
      setCandidates([])
      setError(true)
    } finally {
      if (isCurrent()) setLoading(false)
    }
  }, [])

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  useEffect(() => {
    const requestId = ++requestIdRef.current
    if (!shelf) {
      setCandidates([])
      setLoading(false)
      setError(false)
      return
    }
    load(requestId)
  }, [load, shelf])

  const books = useMemo(
    () => candidates
      .filter(candidate => candidate.shelf_id === validShelfId)
      .sort((a, b) => b.confidence - a.confidence),
    [candidates, validShelfId],
  )
  const lastSeenAt = books.map(book => book.last_seen_at ?? book.updated_at)
    .filter((date): date is string => !!date && Number.isFinite(Date.parse(date)))
    .sort((a, b) => Date.parse(b) - Date.parse(a))[0]

  return (
    <div>
      <PageHeader title="棚区画" back />
      {!shelf ? (
        <EmptyState
          title="棚区画が見つかりません"
          hint="指定された棚区画は存在しません。図書室マップから選択してください。"
          action={(
            <Link
              to="/map"
              className="inline-flex min-h-11 items-center justify-center rounded-lg bg-primary px-4 text-sm font-bold text-white"
            >
              図書室マップを見る
            </Link>
          )}
        />
      ) : loading ? (
        <Skeleton variant="card" />
      ) : error ? (
        <ErrorState message="棚の本を読み込めませんでした。" onRetry={() => load(++requestIdRef.current)} />
      ) : (
        <div className="grid gap-4">
          <section className="rounded-xl border border-line bg-white p-4 shadow-sm">
            <div className="flex items-start justify-between gap-3">
              <ShelfLocationLabel shelfId={validShelfId} size="lg" />
              <div className="text-right"><p className="mb-1 text-xs text-ink-muted">本の最新観測（棚全体の確認日ではありません）</p><FreshnessBadge lastSeenAt={lastSeenAt} /></div>
            </div>
            <div className="mt-4">
              <ShelfMiniMap shelfId={validShelfId} />
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
            to={`/scan?shelf=${encodeURIComponent(validShelfId)}`}
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
