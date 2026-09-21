import type { ShelfCandidate } from './types'
import { getSlot } from './shelf'

// Count a catalog book once per physical unit/cell even if observations repeat.
export function candidateCounts(candidates: ShelfCandidate[], scope: 'unit' | 'cell'): Record<string, number> {
  const groups = new Map<string, Set<string>>()
  for (const candidate of candidates) {
    const slot = getSlot(candidate.shelf_id)
    if (!slot) continue
    const key = scope === 'unit' ? slot.unit : candidate.shelf_id
    const identity = candidate.book_id != null ? `id:${candidate.book_id}` : `title:${candidate.title ?? ''}`
    if (identity === 'title:') continue
    if (!groups.has(key)) groups.set(key, new Set())
    groups.get(key)!.add(identity)
  }
  return Object.fromEntries([...groups].map(([key, ids]) => [key, ids.size]))
}
