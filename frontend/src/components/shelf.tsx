import { useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { displayColToActualCol, formatShelfLabel, getSlot, getUnit, isEmptyCell, layoutUnits, shelfIdForCell } from '../lib/shelf'

export function ShelfLocationLabel({ shelfId, size }: { shelfId: string; size: 'sm' | 'lg' }) {
  return (
    <p className={size === 'lg' ? 'text-base font-bold text-ink' : 'text-sm font-semibold text-ink'}>
      {formatShelfLabel(shelfId)}
    </p>
  )
}

export function ShelfMapHighlight({
  unitId,
  highlight,
  onCellClick,
}: {
  unitId: string
  highlight: string[]
  onCellClick?: (shelfId: string) => void
}) {
  const unit = getUnit(unitId)
  if (!unit) return null
  const highlights = new Set(highlight)
  const cell = 28
  const gap = 4
  const width = unit.cols * cell + (unit.cols - 1) * gap
  const height = unit.rows * cell + (unit.rows - 1) * gap
  return (
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${unitId} の棚`} className="h-auto w-full max-w-full">
      {Array.from({ length: unit.rows }, (_, rowIndex) => {
        const row = unit.rows - rowIndex
        return Array.from({ length: unit.cols }, (_, colIndex) => {
          const displayCol = colIndex + 1
          const col = displayColToActualCol(unit, displayCol)
          if (isEmptyCell(unit, col, row)) return null
          const shelfId = shelfIdForCell(unit.unit, col, row)
          const cellId = `c${String(displayCol).padStart(2, '0')}-r${String(row).padStart(2, '0')}`
          const active = highlights.has(cellId) || highlights.has(shelfId)
          return (
            <rect
              key={shelfId}
              x={colIndex * (cell + gap)}
              y={rowIndex * (cell + gap)}
              width={cell}
              height={cell}
              rx={5}
              className={active ? 'animate-pulse fill-primary' : 'fill-white'}
              stroke={active ? '#15803d' : '#d4d4d8'}
              strokeWidth={active ? 2 : 1}
              onClick={() => onCellClick?.(shelfId)}
            />
          )
        })
      })}
    </svg>
  )
}

export function LibraryFloorMap({
  unitCounts,
  onUnitClick,
}: {
  unitCounts: Record<string, number>
  onUnitClick: (unitId: string) => void
}) {
  const base = layoutUnits.filter(unit => unit.kind === 'base')
  const side = layoutUnits.filter(unit => unit.kind === 'side')
  return (
    <div className="rounded-xl border border-line bg-white p-4 shadow-sm">
      <div className="overflow-x-auto pb-2">
        <div className="flex min-w-max items-start gap-3">
          {base.map(unit => (
            <UnitButton key={unit.unit} unitId={unit.unit} count={unitCounts[unit.unit] ?? 0} onClick={onUnitClick} />
          ))}
          {side.map(unit => (
            <UnitButton key={unit.unit} unitId={unit.unit} count={unitCounts[unit.unit] ?? 0} onClick={onUnitClick} compact />
          ))}
        </div>
      </div>
      <div className="mt-3">
        <div className="rounded-lg border border-dashed border-line bg-zinc-50 py-2 text-center text-xs font-semibold text-ink-muted">
          入口
        </div>
      </div>
    </div>
  )
}

function UnitButton({
  unitId,
  count,
  compact = false,
  onClick,
}: {
  unitId: string
  count: number
  compact?: boolean
  onClick: (unitId: string) => void
}) {
  return (
    <button
      type="button"
      onClick={() => onClick(unitId)}
      className={`rounded-lg border border-line bg-zinc-50 p-2 text-left hover:border-primary ${compact ? 'w-24' : 'w-56'}`}
    >
      <p className="font-bold text-ink">{unitName(unitId)}</p>
      <div className="mt-2 rounded border border-line bg-white p-1">
        <ShelfMapHighlight unitId={unitId} highlight={[]} />
      </div>
      <p className="mt-2 inline-flex rounded-full bg-primary-soft px-2 py-1 text-xs font-semibold text-primary">{count}冊</p>
    </button>
  )
}

export function ShelfUnitGrid({
  unitId,
  cellCounts,
  onCellClick,
}: {
  unitId: string
  cellCounts: Record<string, number>
  onCellClick: (shelfId: string) => void
}) {
  const unit = getUnit(unitId)
  if (!unit) return null
  return (
    <div className="rounded-xl border border-line bg-white p-4 shadow-sm">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="font-bold text-ink">{unitName(unitId)}</h2>
        <p className="text-xs text-ink-muted">色が濃いほど本が多い区画</p>
      </div>
      <div
        className="grid gap-1"
        style={{ gridTemplateColumns: `repeat(${unit.cols}, minmax(0, 1fr))` }}
      >
        {Array.from({ length: unit.rows }, (_, rowIndex) => {
          const row = unit.rows - rowIndex
          return Array.from({ length: unit.cols }, (_, colIndex) => {
            const displayCol = colIndex + 1
            // Keep the shelf ID/count in the stable displayed coordinate system,
            // while deriving the blank shape from the physically mirrored grid.
            const physicalCol = displayColToActualCol(unit, displayCol)
            const shelfId = shelfIdForCell(unit.unit, displayCol, row)
            const empty = isEmptyCell(unit, physicalCol, row)
            const count = cellCounts[shelfId] ?? 0
            return (
              <button
                key={shelfId}
                type="button"
                disabled={empty}
                onClick={() => onCellClick(shelfId)}
                className={`aspect-square rounded text-[10px] font-semibold ${empty ? 'bg-zinc-100 text-zinc-300' : countTone(count)}`}
                aria-label={formatShelfLabel(shelfId)}
              >
                {empty ? '' : count || ''}
              </button>
            )
          })
        })}
      </div>
    </div>
  )
}

export function FreshnessBadge({ lastSeenAt }: { lastSeenAt?: string }) {
  const label = useMemo(() => freshnessLabel(lastSeenAt), [lastSeenAt])
  return <span className="rounded-full bg-zinc-100 px-2 py-1 text-xs font-semibold text-ink-muted">{label}</span>
}

export function ShelfMiniMap({ shelfId }: { shelfId: string }) {
  const slot = getSlot(shelfId)
  if (!slot) return null
  const cellId = `c${String(slot.col).padStart(2, '0')}-r${String(slot.row).padStart(2, '0')}`
  return <ShelfMapHighlight unitId={slot.unit} highlight={[cellId]} />
}

export function useGoToShelf() {
  const navigate = useNavigate()
  return (shelfId: string) => navigate(`/map/${encodeURIComponent(shelfId)}`)
}

function unitName(unitId: string): string {
  if (unitId.startsWith('base-')) return `入口側から${Number(unitId.slice(-2))}台目`
  return `壁側の棚 ${Number(unitId.slice(-2))}`
}

function countTone(count: number): string {
  if (count >= 6) return 'bg-green-500 text-white'
  if (count >= 3) return 'bg-green-300 text-green-950'
  if (count >= 1) return 'bg-green-100 text-green-800'
  return 'bg-white text-zinc-300 border border-line'
}

function freshnessLabel(value: string | undefined): string {
  if (!value) return '確認日不明'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '確認日不明'
  const days = Math.floor((Date.now() - date.getTime()) / 86400000)
  if (days <= 0) return '今日確認'
  if (days < 14) return `${days}日前`
  return '2週間以上前'
}
