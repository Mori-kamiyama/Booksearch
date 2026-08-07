import { describe, expect, it } from 'vitest'
import { StableTagTracker } from './stableTagTracker'

describe('StableTagTracker', () => {
  it('emits only after the same tag set is seen repeatedly', () => {
    const tracker = new StableTagTracker(3)
    expect(tracker.update([29])).toBeNull()
    expect(tracker.update([29])).toBeNull()
    expect(tracker.update([29])).toEqual({ key: '29', tagIds: [29] })
    expect(tracker.update([29])).toBeNull()
    expect(tracker.update([31])).toBeNull()
    expect(tracker.update([31])).toBeNull()
    expect(tracker.update([31])).toEqual({ key: '31', tagIds: [31] })
  })

  it('does not depend on tag ordering', () => {
    const tracker = new StableTagTracker(2)
    expect(tracker.update([31, 29])).toBeNull()
    expect(tracker.update([29, 31])).toEqual({ key: '29,31', tagIds: [29, 31] })
  })

  it('emits again when the camera returns through a different stable shelf', () => {
    const tracker = new StableTagTracker(2)
    expect(tracker.update([29])).toBeNull()
    expect(tracker.update([29])).toEqual({ key: '29', tagIds: [29] })
    expect(tracker.update([31])).toBeNull()
    expect(tracker.update([31])).toEqual({ key: '31', tagIds: [31] })
    expect(tracker.update([29])).toBeNull()
    expect(tracker.update([29])).toEqual({ key: '29', tagIds: [29] })
  })
})
