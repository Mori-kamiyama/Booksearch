import { describe, expect, it } from 'vitest'
import { displayPositionForShelf, getUnit, isDisplayCellEmpty, shelfDensityLevel, shelfIdForDisplayCell } from './shelf'

describe('shelf display coordinates', () => {
  it('maps display cells to mirrored data shelf IDs for mirrored base units', () => {
    const unit = getUnit('base-01')
    expect(unit).toBeDefined()
    expect(shelfIdForDisplayCell(unit!, 6, 3)).toBe('base-01-c08-r03')
  })

  it('derives empty cells from the mirrored physical grid', () => {
    const unit = getUnit('base-01')
    expect(unit).toBeDefined()
    expect(isDisplayCellEmpty(unit!, 6, 3)).toBe(false)
    expect(isDisplayCellEmpty(unit!, 8, 4)).toBe(true)
  })

  it('does not mirror shelf IDs or empties for non-mirrored base units', () => {
    const unit = getUnit('base-04')
    expect(unit).toBeDefined()
    expect(shelfIdForDisplayCell(unit!, 8, 4)).toBe('base-04-c08-r04')
    expect(isDisplayCellEmpty(unit!, 8, 4)).toBe(false)
    expect(isDisplayCellEmpty(unit!, 11, 4)).toBe(true)
  })

  it('places a canonical shelf ID in the same physical cell in every map component', () => {
    const position = displayPositionForShelf('base-01-c01-r02')
    expect(position?.displayCol).toBe(13)
    expect(position?.displayCellId).toBe('c13-r02')
    expect(position?.shelfId).toBe('base-01-c01-r02')
  })
})

describe('shelf density colors', () => {
  it('uses gentle tiers based on the number of books', () => {
    expect(shelfDensityLevel(0)).toBe('empty')
    expect(shelfDensityLevel(1)).toBe('low')
    expect(shelfDensityLevel(6)).toBe('mid')
    expect(shelfDensityLevel(13)).toBe('high')
  })
})
