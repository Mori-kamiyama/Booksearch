import { describe, expect, it } from 'vitest'
import { candidateCounts } from './candidateCounts'

describe('candidateCounts', () => {
  it('deduplicates repeated book observations per unit while retaining separate cells', () => {
    const common = { book_id: 1, confidence: 0.8, observations: 1 }
    const a = { ...common, shelf_id: 'base-01-c02-r04' }
    const b = { ...common, shelf_id: 'base-01-c03-r04' }
    expect(candidateCounts([a, a, b], 'unit')).toEqual({ 'base-01': 1 })
    expect(candidateCounts([a, a, b], 'cell')).toEqual({ [a.shelf_id]: 1, [b.shelf_id]: 1 })
  })
})
