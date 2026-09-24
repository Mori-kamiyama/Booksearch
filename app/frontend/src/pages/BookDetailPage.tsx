import { Link, useParams } from 'react-router-dom'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Map } from 'lucide-react'
import { getBook } from '../lib/api'
import type { Book } from '../lib/types'
import { AltShelfList, BookHero, BookLocationPanel, ConfidenceMeter, LowConfidenceBanner } from '../components/book'
import { EmptyState, ErrorState, PageHeader, Skeleton } from '../components/common'

export default function BookDetailPage() {
  const { id } = useParams<{ id: string }>()
  const [book, setBook] = useState<Book | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)

  const load = useCallback(async () => {
    if (!id) return
    setLoading(true)
    setError(false)
    try {
      setBook(await getBook(id))
    } catch {
      setBook(null)
      setError(true)
    } finally {
      setLoading(false)
    }
  }, [id])

  useEffect(() => {
    load()
  }, [load])

  const candidates = useMemo(() => [...(book?.shelf_candidates ?? [])].sort((a, b) => b.confidence - a.confidence), [book])
  const top = candidates[0]

  if (loading) return <Skeleton variant="hero" />
  if (error) return <ErrorState message="本の情報を読み込めませんでした。" onRetry={load} />
  if (!book) return <EmptyState title="本が見つかりません" hint="検索からもう一度探してください。" />

  return (
    <div>
      <PageHeader title="本の場所" back />
      <div className="grid gap-4">
        <BookHero book={book} />
        {top ? (
          <>
            <LowConfidenceBanner candidate={top} />
            <BookLocationPanel candidate={top} />
            <ConfidenceMeter candidate={top} />
            <AltShelfList candidates={candidates} />
            {/* Phase 2: LocationFeedback will live here after the feedback API exists. */}
          </>
        ) : (
          <EmptyState
            icon={<Map className="size-5" />}
            title="場所は未登録です"
            hint="スキャンで見つかると、ここに棚の場所が表示されます。"
            action={<Link to="/map" className="inline-flex min-h-11 items-center rounded-lg bg-primary px-4 text-sm font-bold text-white">マップを見る</Link>}
          />
        )}
      </div>
    </div>
  )
}
