import { Fragment, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { displayCell, displayPositionForShelf, formatShelfLabel, getSlot, getUnit, layoutUnits } from '../lib/shelf'

export function ShelfLocationLabel({ shelfId, size }: { shelfId: string; size: 'sm' | 'lg' }) {
  return (
    <p className={size === 'lg' ? 'text-base font-bold text-ink' : 'text-sm font-semibold text-ink'}>
      {formatShelfLabel(shelfId)}
    </p>
  )
}

type FloorMapLocation = {
  unitId: string
  kind: 'bar' | 'foot'
  index: number
}

// The physical map is numbered from right to left. The tall upper shapes are
// base shelves, and the small lower blocks are separate side shelves backed by
// the 3 x 7 `side-*` units in library_layout.json. The two leftmost vertical
// locations are reserved for future base shelves 5 and 6.
const floorMapLocations: FloorMapLocation[] = [
  { unitId: 'base-01', kind: 'bar', index: 5 },
  { unitId: 'base-02', kind: 'bar', index: 4 },
  { unitId: 'base-03', kind: 'bar', index: 3 },
  { unitId: 'base-04', kind: 'bar', index: 2 },
  { unitId: 'side-01', kind: 'foot', index: 3 },
  { unitId: 'side-02', kind: 'foot', index: 2 },
  { unitId: 'side-03', kind: 'foot', index: 1 },
  { unitId: 'side-04', kind: 'foot', index: 0 },
]

const barPositions = [0, 93, 119, 212, 238, 331]
const footPositions = [119, 192, 238, 311]

/**
 * Compact physical floor map shared by the index and book-detail pages.
 * A shelf ID takes precedence over a selected unit so the exact book location
 * can be called out without callers having to resolve layout data themselves.
 */
export function LibraryMap({
  selectedUnit,
  shelfId,
  onUnitClick,
  size = 'default',
}: {
  selectedUnit?: string
  shelfId?: string
  onUnitClick?: (unitId: string) => void
  size?: 'default' | 'lg'
}) {
  const shelfUnit = getSlot(shelfId)?.unit
  const activeUnit = shelfUnit ?? selectedUnit
  const marker = shelfUnit ? floorMapLocations.find(location => location.unitId === shelfUnit) : undefined
  const locationFor = (kind: FloorMapLocation['kind'], index: number) => floorMapLocations.find(location => location.kind === kind && location.index === index)
  const renderUnit = (location: FloorMapLocation | undefined, className: string, style: { left: number }) => {
    const active = location?.unitId === activeUnit
    const classes = `${className} ${active ? 'bg-[#087f5b]' : 'bg-[#d9d9d9]'} ${location && onUnitClick ? 'tap-soft cursor-pointer transition hover:bg-[#c4c4c4]' : ''}`
    if (!location || !onUnitClick) return <div className={classes} style={style} />
    return (
      <button
        type="button"
        onClick={() => onUnitClick(location.unitId)}
        aria-label={unitName(location.unitId)}
        aria-pressed={active}
        className={classes}
        style={style}
      />
    )
  }

  const markerLeft = marker
    ? marker.kind === 'bar'
      ? Math.max(0, Math.min(286, barPositions[marker.index] - 24))
      : Math.max(0, Math.min(286, footPositions[marker.index] - 13))
    : 0
  const markerTop = marker?.kind === 'foot' ? 88 : 14

  const frameClass = size === 'lg'
    ? 'relative h-[151px] w-[298px] max-w-full md:h-[216px] md:w-[426px]'
    : 'relative h-[151px] w-[298px] max-w-full md:h-[180px] md:w-[355px]'
  const scaleClass = size === 'lg' ? 'scale-[0.84] md:scale-[1.2]' : 'scale-[0.84] md:scale-100'

  return (
    <div className={frameClass} role={onUnitClick ? 'group' : 'img'} aria-label={shelfUnit ? '本がある棚を緑色で表示' : '図書室の棚マップ'}>
      <div className={`absolute left-0 top-0 h-[180px] w-[355px] origin-top-left ${scaleClass}`}>
        {barPositions.map((left, index) => (
          <Fragment key={left}>{renderUnit(locationFor('bar', index), 'absolute top-0 h-[154px] w-6', { left })}</Fragment>
        ))}
        {footPositions.map((left, index) => (
          <Fragment key={left}>{renderUnit(locationFor('foot', index), 'absolute top-[156px] h-6 w-[43px]', { left })}</Fragment>
        ))}
        {marker && (
          <div className="absolute h-[54px] w-[69px] bg-[#d9d9d9] px-2 pt-4 text-center text-base font-semibold text-ink" style={{ left: markerLeft, top: markerTop }}>
            ここ！
            <span className="absolute -bottom-3 left-1/2 size-0 -translate-x-1/2 border-x-[12px] border-t-[12px] border-x-transparent border-t-[#d9d9d9]" />
          </div>
        )}
      </div>
    </div>
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
          const position = displayCell(unit, displayCol, row)
          const x = colIndex * (cell + gap)
          const y = rowIndex * (cell + gap)
          if (position.empty) {
            return (
              <g key={position.shelfId} aria-label="構造上の空き区画">
                <rect x={x} y={y} width={cell} height={cell} rx={5} fill="#f4f4f5" stroke="#d4d4d8" />
                <path d={`M ${x + 6} ${y + 6} L ${x + cell - 6} ${y + cell - 6} M ${x + cell - 6} ${y + 6} L ${x + 6} ${y + cell - 6}`} stroke="#d4d4d8" strokeWidth={1.5} />
              </g>
            )
          }
          const active = highlights.has(position.displayCellId) || highlights.has(position.shelfId)
          return (
            <rect
              key={position.shelfId}
              x={x}
              y={y}
              width={cell}
              height={cell}
              rx={5}
              className={active ? 'animate-pulse fill-primary' : 'fill-white'}
              stroke={active ? '#15803d' : '#d4d4d8'}
              strokeWidth={active ? 2 : 1}
              onClick={() => onCellClick?.(position.shelfId)}
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
            const position = displayCell(unit, displayCol, row)
            const count = cellCounts[position.shelfId] ?? 0
            return (
              <button
                key={position.shelfId}
                type="button"
                disabled={position.empty}
                onClick={() => onCellClick(position.shelfId)}
                className={`aspect-square rounded text-[10px] font-semibold ${position.empty ? 'bg-zinc-100 text-zinc-300' : countTone(count)}`}
                aria-label={formatShelfLabel(position.shelfId)}
              >
                {position.empty ? '' : count || ''}
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
  const position = displayPositionForShelf(shelfId)
  if (!position) return null
  return <ShelfMapHighlight unitId={position.unit.unit} highlight={[position.shelfId]} />
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
