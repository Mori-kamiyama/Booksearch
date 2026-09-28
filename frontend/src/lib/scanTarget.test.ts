import { describe, expect, it } from 'vitest'
import { parseScanNavigationState, scanTargetMatchState, type ScanTargetBook } from './scanTarget'

const target: ScanTargetBook = { id: 7, title: '  本棚の本  ', shelfId: 'base-01-c02-r04' }

describe('scanTargetMatchState', () => {
  it('confirms only the target library id with an automatic match', () => {
    expect(scanTargetMatchState(target, [
      { key: 'id:7', title: '本棚の本', definitive: true },
    ])).toBe('confirmed')
  })

  it('keeps title-only matches as candidates requiring confirmation', () => {
    expect(scanTargetMatchState(target, [
      { key: 'title:本棚の本', title: '本 棚 の 本', definitive: true },
    ])).toBe('candidate')
    expect(scanTargetMatchState(target, [
      { key: 'id:7', title: '本棚の本', definitive: false },
    ])).toBe('candidate')
  })

  it('does not treat a surrounding book as the target', () => {
    expect(scanTargetMatchState(target, [
      { key: 'id:8', title: '周囲の本', definitive: true },
    ])).toBe('searching')
  })
})

describe('parseScanNavigationState', () => {
  it('accepts a valid internal target navigation state', () => {
    expect(parseScanNavigationState({
      targetBook: { id: 7, title: ' 対象本 ', shelfId: 'base-01' },
      returnTo: '/books/7?q=対象本',
    })).toEqual({
      targetBook: { id: 7, title: '対象本', shelfId: 'base-01' },
      returnTo: '/books/7?q=対象本',
    })
  })

  it.each([
    null,
    { targetBook: { id: 7, title: '対象本' }, returnTo: '/\\example.com' },
    { targetBook: { id: 0, title: '対象本' }, returnTo: '/books/7' },
    { targetBook: { id: 7, title: '対象本' }, returnTo: 'https://example.com' },
  ])('rejects malformed navigation state %#', value => {
    expect(parseScanNavigationState(value)).toBeNull()
  })
})
