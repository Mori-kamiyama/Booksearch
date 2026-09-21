import { candidateCounts } from '../lib/candidateCounts'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getShelfCandidates } from '../lib/api'
import type { ShelfCandidate } from '../lib/types'
import { EmptyState, ErrorState, PageHeader, Skeleton } from '../components/common'
import { LibraryFloorMap, ShelfUnitGrid } from '../components/shelf'

export default function MapPage() {
  const navigate = useNavigate()
  const [candidates, setCandidates] = useState<ShelfCandidate[]>([])
  const [selectedUnit, setSelectedUnit] = useState('base-01')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)

  const requestRef = useRef(0)
  const load = useCallback(async () => {
    const request = ++requestRef.current
    setLoading(true)
    setError(false)
    try {
      const results = await getShelfCandidates()
      if (request === requestRef.current) setCandidates(results)
    } catch {
      if (request === requestRef.current) { setCandidates([]); setError(true) }
    } finally {
      if (request === requestRef.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
    return () => { requestRef.current += 1 }
  }, [load])

  const unitCounts = useMemo(() => candidateCounts(candidates, 'unit'), [candidates])

  const cellCounts = useMemo(() => candidateCounts(candidates, 'cell'), [candidates])

  return (
    <div>
      <PageHeader title="図書室マップ" />
      {loading ? (
        <Skeleton variant="grid" />
      ) : error ? (
        <ErrorState message="マップを読み込めませんでした。" onRetry={load} />
      ) : candidates.length === 0 ? (
        <EmptyState title="棚の位置情報がまだありません" hint="棚スキャンが完了すると、ここに本のある区画が表示されます。" />
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
