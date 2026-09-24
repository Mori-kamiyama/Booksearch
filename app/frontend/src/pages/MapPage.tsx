import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getShelfCandidates } from '../lib/api'
import type { ShelfCandidate } from '../lib/types'
import { getSlot } from '../lib/shelf'
import { EmptyState, ErrorState, PageHeader, Skeleton } from '../components/common'
import { LibraryFloorMap, ShelfUnitGrid } from '../components/shelf'

export default function MapPage() {
  const navigate = useNavigate()
  const [candidates, setCandidates] = useState<ShelfCandidate[]>([])
  const [selectedUnit, setSelectedUnit] = useState('base-01')
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

  const unitCounts = useMemo(() => {
    const counts: Record<string, number> = {}
    for (const candidate of candidates) {
      const slot = getSlot(candidate.shelf_id)
      if (slot) counts[slot.unit] = (counts[slot.unit] ?? 0) + 1
    }
    return counts
  }, [candidates])

  const cellCounts = useMemo(() => {
    const counts: Record<string, number> = {}
    for (const candidate of candidates) {
      counts[candidate.shelf_id] = (counts[candidate.shelf_id] ?? 0) + 1
    }
    return counts
  }, [candidates])

  return (
    <div>
      <PageHeader title="図書室マップ" />
      {loading ? (
        <Skeleton variant="grid" />
      ) : error ? (
        <ErrorState message="マップを読み込めませんでした。" onRetry={load} />
      ) : candidates.length === 0 ? (
        <EmptyState title="まだスキャンされていません" hint="棚スキャンが完了すると、ここに本のある区画が表示されます。" />
      ) : (
        <div className="grid gap-4">
          <LibraryFloorMap unitCounts={unitCounts} onUnitClick={setSelectedUnit} />
          <ShelfUnitGrid
            unitId={selectedUnit}
            cellCounts={cellCounts}
            onCellClick={shelfId => navigate(`/map/${encodeURIComponent(shelfId)}`)}
          />
        </div>
      )}
    </div>
  )
}
