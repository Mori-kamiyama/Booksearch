import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { ErrorState } from '../components/common'
import { getShelfCandidates } from '../lib/api'
import type { ShelfCandidate } from '../lib/types'
import { displayColToActualCol, formatShelfLabel, getSlot, getUnit, isEmptyCell, layoutUnits, shelfIdForCell } from '../lib/shelf'
import { fallbackCoverForTitle, figmaResultBooks } from '../data/figmaBooks'

type ViewMode = 'map' | 'list'

interface IndexBook {
  id: number
  title: string
  cover?: string
}

export default function IndexPage() {
  const [params, setParams] = useSearchParams()
  const [view, setView] = useState<ViewMode>(() => viewModeFromParam(params.get('view')))
  const [candidates, setCandidates] = useState<ShelfCandidate[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError(false)
    try {
      const loaded = await withDevTimeout(getShelfCandidates())
      setCandidates(indexCandidatesForDisplay(loaded))
    } catch {
      const fallback = fallbackIndexCandidates()
      setCandidates(fallback)
      setError(fallback.length === 0)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])
  useEffect(() => { setView(viewModeFromParam(params.get('view'))) }, [params])

  const changeView = (next: ViewMode) => {
    setView(next)
    setParams(next === 'map' ? {} : { view: next }, { replace: true })
  }

  return (
    <div className="min-h-[calc(100vh-72px)] bg-white md:min-h-[calc(100vh-88px)]">
      <div className="mx-auto flex w-full max-w-[402px] flex-col items-center gap-3 px-7 pb-16 pt-[42px] md:max-w-[560px] md:gap-7 md:pt-0">
        <h1 className="w-full text-center text-4xl font-normal leading-normal text-ink">索引</h1>
        <ViewToggle value={view} onChange={changeView} />
        {loading && <IndexSkeleton />}
        {!loading && error && <div className="w-full"><ErrorState message="索引を読み込めませんでした。" onRetry={load} /></div>}
        {!loading && !error && (view === 'map' ? <MapView candidates={candidates} /> : <ListView candidates={candidates} />)}
      </div>
    </div>
  )
}

function viewModeFromParam(value: string | null): ViewMode {
  return value === 'list' ? 'list' : 'map'
}

function ViewToggle({ value, onChange }: { value: ViewMode; onChange: (view: ViewMode) => void }) {
  return (
    <div className="grid h-[23px] w-[136px] shrink-0 grid-cols-2 overflow-hidden rounded-full border border-[#087f5b] text-center text-xs leading-[21px]" role="tablist" aria-label="索引の表示切替">
      <button type="button" role="tab" aria-selected={value === 'map'} onClick={() => onChange('map')} className={`tap-soft rounded-full ${value === 'map' ? 'bg-[#087f5b] text-white' : 'text-ink'}`}>
        Map
      </button>
      <button type="button" role="tab" aria-selected={value === 'list'} onClick={() => onChange('list')} className={`tap-soft rounded-full ${value === 'list' ? 'bg-[#087f5b] text-white' : 'text-ink'}`}>
        リスト
      </button>
    </div>
  )
}

function MapView({ candidates }: { candidates: ShelfCandidate[] }) {
  const [selectedUnit, setSelectedUnit] = useState(() => {
    const withBooks = layoutUnits.find(unit => candidates.some(candidate => getSlot(candidate.shelf_id)?.unit === unit.unit))
    return withBooks?.unit ?? layoutUnits[0]?.unit ?? 'base-01'
  })
  const [selectedShelf, setSelectedShelf] = useState<string | null>(() => candidates[0]?.shelf_id ?? null)

  const shelfBooks = useMemo(() => {
    const scoped = candidates.filter(candidate => selectedShelf
      ? candidate.shelf_id === selectedShelf
      : getSlot(candidate.shelf_id)?.unit === selectedUnit)
    return uniqueBooks(scoped)
  }, [candidates, selectedUnit, selectedShelf])

  const selectUnit = (unitId: string) => {
    setSelectedUnit(unitId)
    setSelectedShelf(null)
  }

  return (
    <>
      <section className="flex w-full flex-col items-center gap-3">
        <h2 className="w-full text-base font-semibold leading-[19px] text-ink">MAP</h2>
        <FloorMap selectedUnit={selectedUnit} onSelectUnit={selectUnit} />
        <UnitCellGrid unitId={selectedUnit} selectedShelf={selectedShelf} onSelectShelf={setSelectedShelf} />
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

function FloorMap({ selectedUnit, onSelectUnit }: { selectedUnit: string; onSelectUnit: (unitId: string) => void }) {
  const base = layoutUnits.filter(unit => unit.kind === 'base')
  const side = layoutUnits.filter(unit => unit.kind === 'side')
  return (
    <div className="flex w-[282px] max-w-full items-stretch justify-between" aria-label="図書室のフロアマップ">
      {base.map(unit => (
        <div key={unit.unit} className="flex flex-col items-center gap-[2px]">
          <button
            type="button"
            onClick={() => onSelectUnit(unit.unit)}
            aria-label={unitLabel(unit.unit)}
            aria-pressed={selectedUnit === unit.unit}
            className={`tap-soft h-[126px] w-5 transition ${selectedUnit === unit.unit ? 'bg-[#087f5b]' : 'bg-[#d9d9d9] hover:bg-[#c4c4c4]'}`}
          />
          <div className="h-5 w-9 bg-[#d9d9d9]" />
        </div>
      ))}
      <div className="flex flex-col justify-between">
        {side.map(unit => (
          <button
            key={unit.unit}
            type="button"
            onClick={() => onSelectUnit(unit.unit)}
            aria-label={unitLabel(unit.unit)}
            aria-pressed={selectedUnit === unit.unit}
            className={`tap-soft h-5 w-9 transition ${selectedUnit === unit.unit ? 'bg-[#087f5b]' : 'bg-[#d9d9d9] hover:bg-[#c4c4c4]'}`}
          />
        ))}
      </div>
    </div>
  )
}

function UnitCellGrid({ unitId, selectedShelf, onSelectShelf }: {
  unitId: string
  selectedShelf: string | null
  onSelectShelf: (shelfId: string | null) => void
}) {
  const unit = getUnit(unitId)
  if (!unit) return null
  return (
    <div className="grid w-[282px] max-w-full gap-[3px]" style={{ gridTemplateColumns: `repeat(${unit.cols}, minmax(0, 1fr))` }}>
      {Array.from({ length: unit.rows }, (_, rowIndex) => {
        const row = unit.rows - rowIndex
        return Array.from({ length: unit.cols }, (_, colIndex) => {
          const displayCol = colIndex + 1
          // DB上のshelf_idは物理座標なので、ShelfMapHighlightと同じく物理座標でIDを組む
          const physicalCol = displayColToActualCol(unit, displayCol)
          const shelfId = shelfIdForCell(unit.unit, physicalCol, row)
          if (isEmptyCell(unit, physicalCol, row)) {
            return <div key={shelfId} className="aspect-square" />
          }
          const active = selectedShelf === shelfId
          return (
            <button
              key={shelfId}
              type="button"
              onClick={() => onSelectShelf(active ? null : shelfId)}
              aria-label={formatShelfLabel(shelfId)}
              aria-pressed={active}
              className={`tap-soft aspect-square transition ${active ? 'bg-[#087f5b]' : 'bg-[#d9d9d9] hover:bg-[#c4c4c4]'}`}
            />
          )
        })
      })}
    </div>
  )
}

const KANA_ROWS: Array<[string, string]> = [
  ['あ', 'あいうえおぁぃぅぇぉ'],
  ['か', 'かきくけこがぎぐげご'],
  ['さ', 'さしすせそざじずぜぞ'],
  ['た', 'たちつてとだぢづでどっ'],
  ['な', 'なにぬねの'],
  ['は', 'はひふへほばびぶべぼぱぴぷぺぽ'],
  ['ま', 'まみむめも'],
  ['や', 'やゆよゃゅょ'],
  ['ら', 'らりるれろ'],
  ['わ', 'わをんゎ'],
]
const GROUP_ORDER = [...KANA_ROWS.map(([label]) => label), '英数', 'その他']

function ListView({ candidates }: { candidates: ShelfCandidate[] }) {
  const groups = useMemo(() => {
    const collator = new Intl.Collator('ja')
    const books = uniqueBooks(candidates)
    if (isDevFallbackCandidates(candidates)) return [{ label: 'あ', books }]
    const sortedBooks = [...books].sort((a, b) => collator.compare(a.title, b.title))
    const grouped = new Map<string, IndexBook[]>()
    for (const book of sortedBooks) {
      const key = kanaRowForTitle(book.title)
      const list = grouped.get(key) ?? []
      list.push(book)
      grouped.set(key, list)
    }
    return GROUP_ORDER.filter(label => grouped.has(label)).map(label => ({ label, books: grouped.get(label)! }))
  }, [candidates])

  return (
    <section className="flex w-full flex-col gap-4">
      <h2 className="w-full text-base font-semibold leading-[19px] text-ink">リスト</h2>
      {groups.length === 0 && <p className="w-full text-center text-sm text-ink-faint">まだ本が登録されていません。棚をスキャンすると一覧に追加されます。</p>}
      {groups.map(group => (
        <div key={group.label} className="flex w-full flex-col gap-4">
          <p className="w-full text-4xl font-normal leading-normal text-[#087f5b]">{group.label}</p>
          <BookGrid books={group.books} />
        </div>
      ))}
    </section>
  )
}

function isDevFallbackCandidates(candidates: ShelfCandidate[]): boolean {
  return import.meta.env.DEV && candidates.length > 0 && candidates.every(candidate => Number(candidate.book_id) < 0)
}

function BookGrid({ books }: { books: IndexBook[] }) {
  const navigate = useNavigate()
  return (
    <div className="grid w-full grid-cols-3 gap-x-[5px] gap-y-[13px]">
      {books.map(book => (
        <button key={book.id} type="button" onClick={() => navigate(`/books/${book.id}`)} className="tap-card flex min-w-0 flex-col items-center gap-[5px] rounded-lg">
          {book.cover
            ? <img src={book.cover} alt="" className="h-[131px] w-[93px] bg-[#d9d9d9] object-cover" loading="lazy" />
            : <div className="h-[131px] w-[93px] bg-[#d9d9d9]" />}
          <span className="line-clamp-2 w-full text-center text-[10px] leading-normal text-ink">{book.title}</span>
        </button>
      ))}
    </div>
  )
}

function IndexSkeleton() {
  return (
    <div className="grid w-full grid-cols-3 gap-x-[5px] gap-y-[13px]">
      {Array.from({ length: 6 }, (_, index) => (
        <div key={index} className="flex flex-col items-center gap-[5px]">
          <div className="h-[131px] w-[93px] animate-pulse bg-[#d9d9d9]" />
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
    cover: fallbackCoverForTitle(candidate.title!),
  }))
}

function kanaRowForTitle(title: string): string {
  const ch = title.trim().charAt(0)
  if (!ch) return 'その他'
  const code = ch.codePointAt(0) ?? 0
  // カタカナはひらがなに寄せてから行を判定する
  const hira = code >= 0x30a1 && code <= 0x30f6 ? String.fromCodePoint(code - 0x60) : ch
  for (const [label, chars] of KANA_ROWS) {
    if (chars.includes(hira)) return label
  }
  if (/[A-Za-z0-9０-９Ａ-Ｚａ-ｚ]/.test(ch)) return '英数'
  return 'その他'
}

function unitLabel(unitId: string): string {
  if (unitId.startsWith('base-')) return `入口側から${Number(unitId.slice(-2))}台目の棚`
  return `壁側の棚${Number(unitId.slice(-2))}`
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
  const coverable = candidates.filter(candidate => candidate.title && fallbackCoverForTitle(candidate.title)).length
  return candidates.length > 0 && coverable >= 3 ? candidates : fallbackIndexCandidates()
}

function withDevTimeout<T>(promise: Promise<T>): Promise<T> {
  if (!import.meta.env.DEV) return promise
  return Promise.race([
    promise,
    new Promise<T>((_, reject) => window.setTimeout(() => reject(new Error('dev fallback timeout')), 1200)),
  ])
}
