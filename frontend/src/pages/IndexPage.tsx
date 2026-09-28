import { candidateCounts } from '../lib/candidateCounts'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { ErrorState } from '../components/common'
import { CoverImage } from '../components/book'
import { LibraryMap } from '../components/shelf'
import { apiUrl, getIndexBooks, getShelfCandidates } from '../lib/api'
import type { ShelfCandidate } from '../lib/types'
import { formatShelfLabel, getSlot, getUnit, isDisplayCellEmpty, shelfDensityLevel, shelfIdForDisplayCell } from '../lib/shelf'
import { fallbackCoverForTitle, figmaResultBooks } from '../data/figmaBooks'
import { groupBooksForIndex } from '../lib/indexGrouping'

type ViewMode = 'map' | 'list'

interface IndexBook {
  id: number
  title: string
  title_reading?: string
  cover?: string
}

export default function IndexPage() {
  const [params, setParams] = useSearchParams()
  const [view, setView] = useState<ViewMode>(() => viewModeFromParam(params.get('view')))
  const [candidates, setCandidates] = useState<ShelfCandidate[]>([])
  const [indexBooks, setIndexBooks] = useState<IndexBook[]>([])
  const [candidatesLoading, setCandidatesLoading] = useState(true)
  const [indexBooksLoading, setIndexBooksLoading] = useState(true)
  const [candidatesError, setCandidatesError] = useState(false)
  const [indexBooksError, setIndexBooksError] = useState(false)
  const mountedRef = useRef(false)
  const candidatesRequestIdRef = useRef(0)
  const indexBooksRequestIdRef = useRef(0)

  const loadCandidates = useCallback(async () => {
    const requestId = ++candidatesRequestIdRef.current
    const isCurrent = () => mountedRef.current && candidatesRequestIdRef.current === requestId
    if (!isCurrent()) return
    setCandidatesLoading(true)
    setCandidatesError(false)
    try {
      const loadedCandidates = await withDevTimeout(getShelfCandidates())
      if (!isCurrent()) return
      setCandidates(indexCandidatesForDisplay(loadedCandidates))
    } catch (loadError) {
      if (!isCurrent()) return
      const fallback = shouldUseDevFallback(loadError) ? fallbackIndexCandidates() : []
      if (fallback.length > 0) setCandidates(fallback)
      setCandidatesError(fallback.length === 0)
    } finally {
      if (isCurrent()) setCandidatesLoading(false)
    }
  }, [])

  const loadIndexBooks = useCallback(async () => {
    const requestId = ++indexBooksRequestIdRef.current
    const isCurrent = () => mountedRef.current && indexBooksRequestIdRef.current === requestId
    if (!isCurrent()) return
    setIndexBooksLoading(true)
    setIndexBooksError(false)
    try {
      const loadedBooks = await withDevTimeout(getIndexBooks())
      if (!isCurrent()) return
      setIndexBooks(loadedBooks.map(book => ({
        id: book.id,
        title: book.title,
        title_reading: book.title_reading,
        cover: book.thumbnail || fallbackCoverForTitle(book.title),
      })))
    } catch (loadError) {
      if (!isCurrent()) return
      const fallback = shouldUseDevFallback(loadError) ? fallbackIndexCandidates() : []
      if (fallback.length > 0) setIndexBooks(uniqueBooks(fallback))
      setIndexBooksError(fallback.length === 0)
    } finally {
      if (isCurrent()) setIndexBooksLoading(false)
    }
  }, [])

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])
  useEffect(() => { loadCandidates() }, [loadCandidates])
  useEffect(() => { loadIndexBooks() }, [loadIndexBooks])
  useEffect(() => { setView(viewModeFromParam(params.get('view'))) }, [params])

  const changeView = (next: ViewMode) => {
    setView(next)
    setParams(next === 'map' ? {} : { view: next }, { replace: true })
  }

  return (
    <div className="min-h-[calc(100vh-72px)] bg-white md:min-h-[calc(100vh-88px)]">
      <div className="mx-auto flex w-full max-w-[402px] flex-col items-center gap-3 px-7 pb-16 pt-[42px] md:max-w-[760px] md:gap-8 md:pt-[54px] lg:max-w-[886px]">
        <h1 className="w-full text-center text-4xl font-normal leading-normal text-ink">索引</h1>
        <ViewToggle value={view} onChange={changeView} />
        {view === 'map' && candidatesError && candidates.length > 0 && (
          <div className="w-full"><ErrorState message="棚の索引を更新できませんでした。" onRetry={loadCandidates} /></div>
        )}
        {view === 'list' && indexBooksError && indexBooks.length > 0 && (
          <div className="w-full"><ErrorState message="蔵書の索引を更新できませんでした。" onRetry={loadIndexBooks} /></div>
        )}
        {view === 'map' ? (
          candidatesLoading && candidates.length === 0 ? <IndexSkeleton />
            : candidatesError && candidates.length === 0 ? <div className="w-full"><ErrorState message="棚の索引を読み込めませんでした。" onRetry={loadCandidates} /></div>
              : <MapView candidates={candidates} />
        ) : (
          indexBooksLoading && indexBooks.length === 0 ? <IndexSkeleton />
            : indexBooksError && indexBooks.length === 0 ? <div className="w-full"><ErrorState message="蔵書の索引を読み込めませんでした。" onRetry={loadIndexBooks} /></div>
              : <ListView books={indexBooks} />
        )}
      </div>
    </div>
  )
}

function viewModeFromParam(value: string | null): ViewMode {
  return value === 'list' ? 'list' : 'map'
}

function ViewToggle({ value, onChange }: { value: ViewMode; onChange: (view: ViewMode) => void }) {
  return (
    <div className="grid h-[28px] w-[156px] shrink-0 grid-cols-2 overflow-hidden rounded-full border border-[#087f5b] text-center text-sm leading-[26px]" role="group" aria-label="索引の表示切替">
      <button type="button" aria-pressed={value === 'map'} onClick={() => onChange('map')} className={`tap-soft rounded-full ${value === 'map' ? 'bg-[#087f5b] text-white' : 'text-ink'}`}>
        Map
      </button>
      <button type="button" aria-pressed={value === 'list'} onClick={() => onChange('list')} className={`tap-soft rounded-full ${value === 'list' ? 'bg-[#087f5b] text-white' : 'text-ink'}`}>
        リスト
      </button>
    </div>
  )
}

function MapView({ candidates }: { candidates: ShelfCandidate[] }) {
  const [selectedUnit, setSelectedUnit] = useState('base-01')
  const [selectedShelf, setSelectedShelf] = useState<string | null>(null)
  const initializedFromCandidates = useRef(false)

  // Candidates arrive asynchronously, so initialize once after the first
  // successful load without overwriting a location the user subsequently picks.
  useEffect(() => {
    if (initializedFromCandidates.current) return
    const candidate = candidates.find(item => getSlot(item.shelf_id))
    if (!candidate) return
    const unitId = getSlot(candidate.shelf_id)?.unit
    if (!unitId) return
    setSelectedUnit(unitId)
    setSelectedShelf(candidate.shelf_id)
    initializedFromCandidates.current = true
  }, [candidates])

  const shelfBooks = useMemo(() => {
    const scoped = candidates.filter(candidate => selectedShelf
      ? candidate.shelf_id === selectedShelf
      : getSlot(candidate.shelf_id)?.unit === selectedUnit)
    return uniqueBooks(scoped)
  }, [candidates, selectedUnit, selectedShelf])

  const cellCounts = useMemo(() => candidateCounts(candidates, 'cell'), [candidates])

  const unitCounts = useMemo(() => candidateCounts(candidates, 'unit'), [candidates])

  const selectUnit = (unitId: string) => {
    setSelectedUnit(unitId)
    setSelectedShelf(null)
  }

  return (
    <>
      <section className="flex w-full flex-col gap-4 md:gap-5">
        <h2 className="w-full text-base font-semibold leading-[19px] text-ink">MAP</h2>
        <div className="flex flex-col items-center gap-5 md:gap-6">
          <LibraryMap selectedUnit={selectedUnit} unitCounts={unitCounts} selectionTone="charcoal" onUnitClick={selectUnit} size="lg" />
          <div className="flex w-full max-w-[560px] flex-col items-center gap-2">
            <p className="w-full text-xs font-semibold text-ink-muted md:text-sm">棚の区画を選択</p>
            <UnitCellGrid unitId={selectedUnit} cellCounts={cellCounts} selectedShelf={selectedShelf} onSelectShelf={setSelectedShelf} />
            <p className="w-full text-[11px] text-ink-faint md:text-xs">本が多い区画ほど緑が少し濃くなります</p>
          </div>
        </div>
      </section>

      <section className="flex w-full flex-col gap-4">
        <h2 className="w-full text-base font-semibold leading-[19px] text-ink">この棚の本</h2>
        {shelfBooks.length === 0 ? (
          <p className="w-full text-center text-sm text-ink-faint">
            {selectedShelf ? 'この区画の本はまだ登録されていません。' : 'この棚の本はまだ登録されていません。'}
          </p>
        ) : (
          <BookGrid books={shelfBooks} />
        )}
      </section>
    </>
  )
}



function UnitCellGrid({ unitId, cellCounts, selectedShelf, onSelectShelf }: {
  unitId: string
  cellCounts: Record<string, number>
  selectedShelf: string | null
  onSelectShelf: (shelfId: string | null) => void
}) {
  const unit = getUnit(unitId)
  if (!unit) return null
  const cellSize = 44
  const gap = 4
  const gridWidth = unit.cols * cellSize + (unit.cols - 1) * gap
  return (
    <div className="w-full overflow-x-auto pb-1">
      <div
        className="grid gap-1"
        style={{
          gridTemplateColumns: `repeat(${unit.cols}, ${cellSize}px)`,
          width: `${gridWidth}px`,
        }}
      >
        {Array.from({ length: unit.rows }, (_, rowIndex) => {
          const row = unit.rows - rowIndex
          return Array.from({ length: unit.cols }, (_, colIndex) => {
            const displayCol = colIndex + 1
            const shelfId = shelfIdForDisplayCell(unit, displayCol, row)
            if (isDisplayCellEmpty(unit, displayCol, row)) {
              return <div key={shelfId} className="aspect-square" />
            }
            const active = selectedShelf === shelfId
            const count = cellCounts[shelfId] ?? 0
            return (
              <button
                key={shelfId}
                type="button"
                onClick={() => onSelectShelf(active ? null : shelfId)}
                aria-label={`${formatShelfLabel(shelfId)} ${count}冊`}
                aria-pressed={active}
                style={active ? { backgroundColor: '#363636' } : undefined}
                className={`tap-soft aspect-square transition ${active ? 'bg-[#363636] ring-2 ring-[#8a8a8a] ring-offset-1' : indexCellTone(count)}`}
              />
            )
          })
        })}
      </div>
    </div>
  )
}

function indexCellTone(count: number): string {
  const level = shelfDensityLevel(count)
  if (level === 'high') return 'bg-[#9bcfbd] hover:brightness-95'
  if (level === 'mid') return 'bg-[#c8e5da] hover:brightness-95'
  if (level === 'low') return 'bg-[#e7f3ef] hover:brightness-95'
  return 'bg-[#d9d9d9] hover:bg-[#cfcfcf]'
}



function ListView({ books }: { books: IndexBook[] }) {
  const [visibleCount, setVisibleCount] = useState(120)
  const groups = useMemo(() => {
    const sorted = groupBooksForIndex(books)
    let remaining = visibleCount
    return sorted.flatMap(group => {
      const visible = group.books.slice(0, Math.max(0, remaining))
      remaining -= visible.length
      return visible.length ? [{ ...group, books: visible }] : []
    })
  }, [books, visibleCount])

  return (
    <section className="flex w-full flex-col gap-4">
      <div className="flex w-full items-baseline justify-between gap-4">
        <h2 className="text-base font-semibold leading-[19px] text-ink">リスト</h2>
        <p className="text-xs text-ink-muted">全{books.length.toLocaleString('ja-JP')}冊</p>
      </div>
      {groups.length === 0 && <p className="w-full text-center text-sm text-ink-faint">蔵書が登録されていません。</p>}
      {groups.map(group => (
        <div key={group.label} className="flex w-full flex-col gap-4">
          <p className="w-full text-4xl font-normal leading-normal text-[#087f5b]">{group.label}</p>
          <BookGrid books={group.books} />
        </div>
      ))}
      {visibleCount < books.length && <button type="button" className="min-h-11 rounded-lg border border-line p-3 text-primary" onClick={() => setVisibleCount(count => count + 120)}>さらに表示（{Math.min(visibleCount, books.length)} / {books.length}冊）</button>}
    </section>
  )
}

function BookGrid({ books }: { books: IndexBook[] }) {
  const navigate = useNavigate()
  return (
    <div className="grid w-full grid-cols-3 gap-x-[5px] gap-y-[13px] md:grid-cols-4 md:gap-x-8 md:gap-y-7">
      {books.map(book => (
        <button key={book.id} type="button" onClick={() => navigate(`/books/${book.id}`)} className="tap-card flex min-w-0 flex-col items-center gap-[5px] rounded-lg md:gap-[7px]">
          <div className="flex h-[160px] w-[93px] max-w-full items-end justify-center md:h-[150px] md:w-[112px]">
            <CoverImage
              src={book.cover}
              alt=""
              className="max-h-full max-w-full bg-[#d9d9d9] object-contain"
              fallbackClassName="grid h-[131px] w-[93px] place-items-center bg-[#d9d9d9] md:h-[150px] md:w-[112px]"
            />
          </div>
          <span className="line-clamp-2 w-full text-center text-[10px] leading-normal text-ink md:text-[11px] md:leading-[13px]">{book.title}</span>
        </button>
      ))}
    </div>
  )
}

function IndexSkeleton() {
  return (
    <div className="grid w-full grid-cols-3 gap-x-[5px] gap-y-[13px] md:grid-cols-4 md:gap-x-8 md:gap-y-7">
      {Array.from({ length: 8 }, (_, index) => (
        <div key={index} className="flex flex-col items-center gap-[5px] md:gap-[7px]">
          <div className="h-[131px] w-[93px] animate-pulse bg-[#d9d9d9] md:h-[150px] md:w-[112px]" />
          <div className="h-3 w-16 animate-pulse rounded bg-zinc-100" />
        </div>
      ))}
    </div>
  )
}

function uniqueBooks(candidates: ShelfCandidate[]): IndexBook[] {
  const best = new Map<number, ShelfCandidate>()
  for (const candidate of candidates) {
    if (candidate.book_id == null || !candidate.title) continue
    const current = best.get(candidate.book_id)
    if (!current || candidate.confidence > current.confidence) best.set(candidate.book_id, candidate)
  }
  return [...best.entries()].map(([id, candidate]) => ({
    id,
    title: candidate.title!,
    title_reading: candidate.title_reading,
    cover: candidate.thumbnail || (candidate.crop_url ? apiUrl(candidate.crop_url) : fallbackCoverForTitle(candidate.title!)),
  }))
}



function fallbackIndexCandidates(): ShelfCandidate[] {
  if (!import.meta.env.DEV) return []
  return figmaResultBooks.map((book, index) => ({
    book_id: -(index + 1),
    title: book.title,
    shelf_id: 'base-04-c08-r04',
    confidence: 0.9,
    observations: 1,
  }))
}

function indexCandidatesForDisplay(candidates: ShelfCandidate[]): ShelfCandidate[] {
  if (!import.meta.env.DEV) return candidates
  return candidates.length > 0 ? candidates : fallbackIndexCandidates()
}

function withDevTimeout<T>(promise: Promise<T>): Promise<T> {
  if (!import.meta.env.DEV) return promise
  return Promise.race([
    promise,
    new Promise<T>((_, reject) => window.setTimeout(() => reject(new Error('dev fallback timeout')), 1200)),
  ])
}

function shouldUseDevFallback(error: unknown): boolean {
  if (!import.meta.env.DEV) return false
  return error instanceof TypeError || (error instanceof Error && error.message === 'dev fallback timeout')
}
