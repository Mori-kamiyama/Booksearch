export interface ScanTargetBook {
  id: number
  title: string
  shelfId: string | null
}

export interface ScanNavigationState {
  targetBook: ScanTargetBook
  returnTo: string
}

export interface ScanBookMatch {
  key: string
  title: string
  definitive: boolean
}

export type ScanTargetMatchState = 'searching' | 'candidate' | 'confirmed'

export function normalizeScanTargetTitle(title: string): string {
  return title.normalize('NFKC').replace(/\s+/g, '').toLocaleLowerCase('ja-JP')
}

export function parseScanNavigationState(value: unknown): ScanNavigationState | null {
  if (!value || typeof value !== 'object') return null
  const state = value as { targetBook?: unknown; returnTo?: unknown }
  if (!state.targetBook || typeof state.targetBook !== 'object' || typeof state.returnTo !== 'string') return null
  const target = state.targetBook as { id?: unknown; title?: unknown; shelfId?: unknown }
  if (!Number.isSafeInteger(target.id) || (target.id as number) <= 0 || typeof target.title !== 'string' || !target.title.trim()) return null
  if (!state.returnTo.startsWith('/') || state.returnTo.startsWith('//') || /[\\\u0000-\u001f]/.test(state.returnTo)) return null
  return {
    targetBook: {
      id: target.id as number,
      title: target.title.trim(),
      shelfId: typeof target.shelfId === 'string' && target.shelfId ? target.shelfId : null,
    },
    returnTo: state.returnTo,
  }
}

export function scanTargetMatchState(target: ScanTargetBook | null, books: ScanBookMatch[]): ScanTargetMatchState {
  if (!target) return 'searching'
  const targetIdKey = `id:${target.id}`
  const targetTitle = normalizeScanTargetTitle(target.title)
  const matches = books.filter(book => book.key === targetIdKey || normalizeScanTargetTitle(book.title) === targetTitle)
  if (matches.some(book => book.key === targetIdKey && book.definitive)) return 'confirmed'
  return matches.length > 0 ? 'candidate' : 'searching'
}
