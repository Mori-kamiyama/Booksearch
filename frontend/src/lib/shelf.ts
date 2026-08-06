import layout from '../data/library_layout.json'
import type { LayoutSlot, LayoutUnit } from './types'

interface LayoutData {
  units: LayoutUnit[]
  slots: LayoutSlot[]
}

const data = layout as LayoutData
const slotsById = new Map(
  data.slots
    .filter((slot): slot is LayoutSlot & { shelf_id: string } => Boolean(slot.shelf_id))
    .map(slot => [slot.shelf_id, slot]),
)
const unitsById = new Map(data.units.map(unit => [unit.unit, unit]))

export const layoutUnits = data.units
export const layoutSlots = data.slots

export function getSlot(shelfId: string | undefined): LayoutSlot | undefined {
  return shelfId ? slotsById.get(shelfId) : undefined
}

export function getUnit(unitId: string | undefined): LayoutUnit | undefined {
  return unitId ? unitsById.get(unitId) : undefined
}

export function displayColToActualCol(unit: LayoutUnit, displayCol: number): number {
  if (!unit.mirrored) return displayCol
  return unit.cols + 1 - displayCol
}

export function actualColToDisplayCol(unit: LayoutUnit, actualCol: number): number {
  if (!unit.mirrored) return actualCol
  return unit.cols + 1 - actualCol
}

export function getUnitId(shelfId: string): string | undefined {
  const match = shelfId.match(/^(base-\d{2}|side-\d{2})-c\d{2}-r\d{2}$/)
  return match?.[1]
}

export function formatShelfLabel(shelfId: string | undefined): string {
  return getSlot(shelfId)?.label_ja ?? '場所情報なし'
}

export function shortShelfLabel(shelfId: string | undefined): string {
  const slot = getSlot(shelfId)
  if (!slot) return '場所未登録'
  const unit = slot.kind === 'base'
    ? `${slot.unit_index_from_entrance ?? ''}番棚`
    : `壁側${slot.unit_index ?? ''}`
  return `${unit}・${slot.col}列・${slot.row}段`
}

export function confidenceLevel(confidence: number): 'high' | 'mid' | 'low' {
  if (confidence >= 0.5) return 'high'
  if (confidence >= 0.2) return 'mid'
  return 'low'
}

export function splitShelfId(shelfId: string): { unitId: string; cellId: string; col: number; row: number } | undefined {
  const position = displayPositionForShelf(shelfId)
  if (!position) return undefined
  return {
    unitId: position.unit.unit,
    cellId: position.displayCellId,
    col: position.displayCol,
    row: position.row,
  }
}

export function shelfIdForCell(unitId: string, col: number, row: number): string {
  return `${unitId}-c${String(col).padStart(2, '0')}-r${String(row).padStart(2, '0')}`
}

export function isEmptyCell(unit: LayoutUnit, col: number, row: number): boolean {
  return unit.empty_rule?.regions.some(region => region.cols.includes(col) && region.rows.includes(row)) ?? false
}

export interface DisplayCell {
  unit: LayoutUnit
  displayCol: number
  canonicalCol: number
  row: number
  displayCellId: string
  shelfId: string
  empty: boolean
}

export function displayCell(unit: LayoutUnit, displayCol: number, row: number): DisplayCell {
  const canonicalCol = displayColToActualCol(unit, displayCol)
  return {
    unit,
    displayCol,
    canonicalCol,
    row,
    displayCellId: `c${String(displayCol).padStart(2, '0')}-r${String(row).padStart(2, '0')}`,
    shelfId: shelfIdForCell(unit.unit, canonicalCol, row),
    empty: isEmptyCell(unit, canonicalCol, row),
  }
}

export function displayPositionForShelf(shelfId: string): DisplayCell | undefined {
  const slot = getSlot(shelfId)
  if (!slot) return undefined
  const unit = getUnit(slot.unit)
  if (!unit) return undefined
  return displayCell(unit, actualColToDisplayCol(unit, slot.col), slot.row)
}

export function shelfIdForDisplayCell(unit: LayoutUnit, displayCol: number, row: number): string {
  return displayCell(unit, displayCol, row).shelfId
}

export function isDisplayCellEmpty(unit: LayoutUnit, displayCol: number, row: number): boolean {
  return displayCell(unit, displayCol, row).empty
}
