import { describe, expect, it } from 'vitest'
import { semanticQueryAllowed } from './semanticSearch'

describe('semantic query eligibility', () => {
  it('preserves identifiers and explicit original-query searches', () => {
    for (const query of ['', '???', '978-4-123456-78-9', '９７８４１２３４５６７８９', 'ISBN: 9784123456789', '123456789X']) {
      expect(semanticQueryAllowed(query)).toBe(false)
    }
    expect(semanticQueryAllowed('ユーザビリティ', true)).toBe(false)
  })
  it('allows subject searches and rejects overly long input', () => {
    for (const query of ['ユーザビリティ', 'C++', 'Python データ分析']) expect(semanticQueryAllowed(query)).toBe(true)
    expect(semanticQueryAllowed('あ'.repeat(201))).toBe(false)
  })
})
