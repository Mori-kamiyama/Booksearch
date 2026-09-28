import { CoverImage } from '../components/book'
import { useEffect, useMemo, useState } from 'react'
import { ArrowUpLeft } from 'lucide-react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { apiFetch, apiUrl } from '../lib/api'
import { formatShelfLabel } from '../lib/shelf'
import { fallbackCoverForTitle, figmaResultBooks } from '../data/figmaBooks'
import { parseScanNavigationState, scanTargetMatchState, type ScanTargetMatchState } from '../lib/scanTarget'

interface Candidate {
  title: string
  authors?: string[]
  publisher?: string
  isbns?: string[]
  score?: number
  match_confidence?: string
  thumbnail?: string
  library_db_id?: number
}

interface BookEntry {
  title: string
  book_lookup?: {
    candidates?: Candidate[]
  }
}

interface CatalogEntry {
  box_id: string
  shelf_id: string | null
  crop_image: string
  detector_confidence: number
  ocr_error?: string
  books?: BookEntry[]
}

interface Catalog {
  source?: string
  entries?: CatalogEntry[]
}

interface Diagnostics {
  apriltag?: {
    tried?: { dict: string; count: number }[]
    selected?: string
    raw_ids?: number[]
    error?: string
  }
  skip_reasons?: Record<string, number>
  readable_count?: number
  total_crops?: number
}

interface JobState {
  job_id: string
  // Keep this open at the API boundary: the backend may add a status before
  // this client is updated. Unknown values are rendered as an explicit state.
  status: string
  error?: string
  catalog?: Catalog
  diagnostics?: Diagnostics
  // Live scans write per-frame diagnostics under a different key.
  latest_diagnostics?: Diagnostics
  crop_total?: number | string
  failed_frames?: number
  ocr_done?: number | string
  average_seconds?: number
  detected_shelf_count?: number
  detected_book_count?: number
}

export default function JobPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const scanNavigation = useMemo(() => parseScanNavigationState(location.state), [location.state])
  const goBack = () => { if ((window.history.state?.idx ?? 0) > 0) navigate(-1); else navigate('/') }
  const { id } = useParams<{ id: string }>()
  const [job, setJob] = useState<JobState | null>(null)
  const [pollError, setPollError] = useState<{ id: string | undefined; message: string } | null>(null)
  const [retryCount, setRetryCount] = useState(0)

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const controller = new AbortController()

    setJob(null)
    setPollError(null)

    const poll = async () => {
      try {
        const res = await apiFetch(`/api/jobs/${id}`, { signal: controller.signal })
        if (!res.ok) {
          throw new Error(`ジョブ取得に失敗しました (${res.status})`)
        }
        const data: JobState = await res.json()
        if (cancelled) return
        setJob(data)
        setPollError(null)
        if (!isTerminalStatus(data.status)) {
          timer = setTimeout(() => void poll(), 2000)
        }
      } catch (e) {
        if (cancelled || controller.signal.aborted) return
        // DEV's fixture is useful when the API is unavailable, but an HTTP
        // error is an actual job error and must remain visible to the user.
        const fallback = e instanceof TypeError ? fallbackJob(id) : null
        if (fallback) {
          setJob(fallback)
          setPollError(null)
        } else {
          setPollError({
            id,
            message: e instanceof Error ? e.message : 'ジョブ取得に失敗しました',
          })
          // Keep retrying transient failures, but only schedule the next
          // request after this one has settled.
          timer = setTimeout(() => void poll(), 2000)
        }
      }
    }
    void poll()
    return () => {
      cancelled = true
      controller.abort()
      if (timer !== undefined) clearTimeout(timer)
    }
  }, [id, retryCount])

  const currentJob = job?.job_id === id ? job : null
  const currentPollError = pollError && pollError.id === id ? pollError.message : ''

  if (!currentJob && currentPollError) {
    return (
      <JobLoadError
        error={currentPollError}
        onBack={goBack}
        onRetry={() => {
          setJob(null)
          setPollError(null)
          setRetryCount(count => count + 1)
        }}
      />
    )
  }

  if (!currentJob) return <ScanResultLoading onBack={goBack} />

  const entries = currentJob.catalog?.entries ?? []
  const groups = groupResultBooks(entries)
  // detected_book_count counts every OCR hit, so the same spine seen in ten
  // frames reads as ten books. The metric follows the deduplicated list.
  const bookCount = groups.reduce((n, group) => n + group.books.length, 0)
  const shelfIds = new Set(entries.map(entry => entry.shelf_id).filter(Boolean))
  const shelfCount = Number(currentJob.detected_shelf_count ?? shelfIds.size)
  const processing = isProcessingStatus(currentJob.status)
  const uploading = currentJob.status === 'uploading'
  const targetMatchState = scanNavigation
    ? processing
      ? 'searching'
      : scanTargetMatchState(scanNavigation.targetBook, groups.flatMap(group => group.books.map(book => ({
        key: resultBookKey({ title: book.title, library_db_id: book.libraryDbId }, book.title),
        title: book.title,
        definitive: book.matchLabel === '自動照合',
      }))))
    : null

  return (
    <div className="min-h-screen bg-white">
      <div className="relative mx-auto min-h-screen w-full max-w-[402px] overflow-hidden pb-12">
        <button type="button" onClick={goBack} aria-label="戻る" className="tap-soft absolute left-7 top-6 grid size-10 place-items-center rounded-full bg-white text-[#1e1e1e]">
          <ArrowUpLeft className="size-6" />
        </button>

        {currentPollError && <p className="mx-7 mt-[82px] rounded-xl bg-red-50 p-3 text-sm text-red-700">{currentPollError}</p>}

        {Number(currentJob.failed_frames) > 0 && (
          <p role="status" className="mx-7 mt-[82px] rounded-xl bg-orange-50 p-3 text-sm text-orange-800">
            一部の画像（{currentJob.failed_frames}件）を解析できませんでした。結果に含まれていない本は、もう一度撮影してください。
          </p>
        )}

        {scanNavigation && targetMatchState && (
          <TargetBookResultStatus target={scanNavigation.targetBook} matchState={targetMatchState} processing={processing} completed={currentJob.status === 'done'} returnTo={scanNavigation.returnTo} />
        )}

        {processing ? (
          <div className="flex min-h-[674px] flex-col items-center justify-center gap-6 px-7 text-center">
            <div className="size-12 animate-spin rounded-full border-4 border-[#d9d9d9] border-t-[#087f5b]" />
            <h1 className="text-4xl font-semibold leading-[44px] text-[#087f5b]">{uploading ? 'アップロード中' : '解析中'}</h1>
            <p className="text-base leading-[19px] text-ink">{uploading ? <>画像をアップロードしています…<br />このままお待ちください</> : <>棚と本を確認しています…<br />このままお待ちください</>}</p>
            <StatusBadge status={currentJob.status} />
          </div>
        ) : currentJob.status === 'failed' ? (
          <div className="flex min-h-[674px] flex-col items-center justify-center gap-6 px-7 text-center">
            <h1 className="text-4xl font-semibold leading-[44px] text-red-600">エラー</h1>
            <p className="text-base leading-normal text-ink">解析に失敗しました。<br />{currentJob.error || 'もう一度スキャンしてください。'}</p>
            <button type="button" onClick={() => navigate('/scan')} className="tap-card rounded-full bg-[#087f5b] px-6 py-3 text-white">スキャンへ戻る</button>
          </div>
        ) : currentJob.status === 'no_detection' ? (
          <NoResultState
            status={currentJob.status}
            title="本を検出できませんでした"
            message="撮影画像から本の候補が見つかりませんでした。撮影距離や向きを変えてもう一度お試しください。"
            diagnostics={currentJob.diagnostics ?? currentJob.latest_diagnostics}
            onScan={() => navigate('/scan')}
          />
        ) : currentJob.status === 'no_readable_crops' ? (
          <NoResultState
            status={currentJob.status}
            title="読み取れる画像がありませんでした"
            message="本の候補は見つかりましたが、読み取りに使える画像がありませんでした。明るさや撮影距離を変えてもう一度お試しください。"
            diagnostics={currentJob.diagnostics ?? currentJob.latest_diagnostics}
            onScan={() => navigate('/scan')}
          />
        ) : currentJob.status === 'done' ? (
          <div className="pt-[104px]">
            <section className="flex flex-col items-center gap-7 px-7 text-center">
              <h1 className="w-full text-4xl font-semibold leading-[44px] text-[#087f5b]">終了</h1>
              <p className="w-full text-base leading-[19px] text-ink">スキャンありがとうございました！！</p>
              <div className="flex w-full flex-wrap items-baseline justify-center gap-4 whitespace-nowrap text-ink">
                <Metric value={shelfCount} label="棚検知" />
                <Metric value={bookCount} label="件の認識候補" />
                <p><span className="text-base">平均</span><span className="text-4xl font-semibold leading-[44px] text-[#087f5b]">{currentJob.average_seconds ?? '—'}</span><span className="text-base">秒</span></p>
              </div>
            </section>

            <div className="mx-auto my-7 h-px w-64 bg-[#087f5b]" />

            {groups.length > 0 ? (
              <div className="flex flex-col gap-7">
                {groups.map(group => (
                  <section key={group.shelf} className="px-7">
                    <h2 className="mb-4 text-base font-semibold leading-[19px] text-ink">{group.shelf}</h2>
                    <div className="grid grid-cols-3 gap-x-[5px] gap-y-4">
                      {group.books.map((book, index) => <ResultBookCard key={`${book.title}-${index}`} book={book} />)}
                    </div>
                  </section>
                ))}
              </div>
            ) : (
              <p className="px-7 text-center text-sm text-ink-muted">本を検出できませんでした。撮影距離を変えてもう一度お試しください。</p>
            )}

            <div className="mx-7 mt-10"><DiagnosticsPanel diag={currentJob.diagnostics ?? currentJob.latest_diagnostics} /></div>
            <div className="mt-10 flex justify-center gap-3 px-7">
              <Link to="/" className="tap-card rounded-full border border-[#087f5b] px-5 py-3 text-sm font-semibold text-[#087f5b]">本を検索する</Link>
              <Link to="/scan" className="tap-card rounded-full bg-[#087f5b] px-5 py-3 text-sm font-semibold text-white">もう一度スキャン</Link>
            </div>
          </div>
        ) : (
          <UnknownStatusState status={currentJob.status} onBack={goBack} />
        )}
      </div>
    </div>
  )
}

function TargetBookResultStatus({
  target,
  matchState,
  processing,
  completed,
  returnTo,
}: {
  target: { id: number; title: string }
  matchState: ScanTargetMatchState
  processing: boolean
  completed: boolean
  returnTo: string
}) {
  const label = processing
    ? '対象本を探索中'
    : !completed ? '探索結果を確定できませんでした'
    : matchState === 'confirmed'
      ? '対象本を自動照合しました'
      : matchState === 'candidate'
        ? '対象本の候補があります（要確認）'
        : '対象本は未発見でした'
  return (
    <section data-testid="target-result-status" className="mx-7 mt-[82px] rounded-xl border border-primary-soft bg-primary-soft p-4">
      <p className="text-sm text-ink">対象本: {target.title}</p>
      <p className="mt-1 font-semibold text-[#087f5b]">{label}</p>
      <Link to={returnTo} className="mt-3 inline-flex text-sm font-semibold text-[#087f5b] underline underline-offset-2">
        対象本の詳細へ戻る
      </Link>
    </section>
  )
}

function isProcessingStatus(status: string): boolean {
  return ['collecting', 'processing', 'pending', 'running', 'ocr_pending', 'lookup_pending', 'uploading'].includes(status)
}

function isTerminalStatus(status: string): boolean {
  return ['done', 'failed', 'no_detection', 'no_readable_crops'].includes(status)
}

function JobLoadError({ error, onBack, onRetry }: { error: string; onBack: () => void; onRetry: () => void }) {
  return (
    <div className="relative mx-auto min-h-screen w-full max-w-[402px] bg-white">
      <button type="button" onClick={onBack} aria-label="戻る" className="tap-soft absolute left-7 top-6 grid size-10 place-items-center rounded-full bg-white"><ArrowUpLeft className="size-6" /></button>
      <div className="flex min-h-[674px] flex-col items-center justify-center gap-5 px-7 text-center">
        <h1 className="text-3xl font-semibold text-red-600">結果を読み込めませんでした</h1>
        <p className="text-base leading-normal text-ink">{error}</p>
        <button type="button" onClick={onRetry} className="tap-card rounded-full bg-[#087f5b] px-6 py-3 text-white">再試行</button>
      </div>
    </div>
  )
}

function NoResultState({
  status,
  title,
  message,
  diagnostics,
  onScan,
}: {
  status: string
  title: string
  message: string
  diagnostics?: Diagnostics
  onScan: () => void
}) {
  return (
    <div className="flex min-h-[674px] flex-col items-center gap-5 px-7 pt-[150px] text-center">
      <StatusBadge status={status} />
      <h1 className="text-3xl font-semibold leading-[40px] text-[#087f5b]">{title}</h1>
      <p className="text-base leading-normal text-ink">{message}</p>
      <button type="button" onClick={onScan} className="tap-card rounded-full bg-[#087f5b] px-6 py-3 text-white">スキャンへ戻る</button>
      <div className="w-full text-left"><DiagnosticsPanel diag={diagnostics} /></div>
    </div>
  )
}

function UnknownStatusState({ status, onBack }: { status: string; onBack: () => void }) {
  return (
    <div className="flex min-h-[674px] flex-col items-center justify-center gap-5 px-7 text-center">
      <StatusBadge status={status} />
      <h1 className="text-3xl font-semibold leading-[40px] text-orange-600">解析状態を確認できません</h1>
      <p className="text-base leading-normal text-ink">未対応の状態「{status}」が返されました。時間をおいて再度確認してください。</p>
      <button type="button" onClick={onBack} className="tap-card rounded-full bg-[#087f5b] px-6 py-3 text-white">戻る</button>
    </div>
  )
}

function Metric({ value, label }: { value: number; label: string }) {
  return <p><span className="text-4xl font-semibold leading-[44px] text-[#087f5b]">{value}</span><span className="text-base">{label}</span></p>
}

interface ResultBook {
  title: string
  cover?: string
  libraryDbId?: number
  matchConfidence?: string
  matchLabel: string
}

function groupResultBooks(entries: CatalogEntry[]): { shelf: string; books: ResultBook[] }[] {
  const groups = new Map<string, Map<string, ResultBook>>()
  for (const entry of entries) {
    const shelf = devShelfLabel(entry.box_id) ?? (entry.shelf_id ? formatShelfLabel(entry.shelf_id) : '棚未判定')
    const books = groups.get(shelf) ?? new Map<string, ResultBook>()
    for (const book of entry.books ?? []) {
      const top = book.book_lookup?.candidates?.[0]
      const title = top?.title || book.title
      if (!title) continue
      // A live scan sees the same spine across many frames, so each book is
      // shown once per shelf instead of once per crop.
      const libraryDbId = top?.library_db_id
      const key = resultBookKey(top, title)
      const candidate = {
        title,
        cover: top?.thumbnail || fallbackCoverForTitle(title),
        libraryDbId,
        matchConfidence: top?.match_confidence,
        matchLabel: resultMatchLabel(top),
      }
      const existing = books.get(key)
      if (!existing || resultBookRank(candidate) > resultBookRank(existing)) {
        books.set(key, candidate)
      }
    }
    groups.set(shelf, books)
  }
  return [...groups.entries()]
    .filter(([, books]) => books.size > 0)
    .map(([shelf, books]) => ({ shelf, books: [...books.values()] }))
}

function normalizeResultTitle(title: string): string {
  return title.trim().toLocaleLowerCase('ja-JP')
}

function hasPositiveLibraryDbId(candidate: Candidate | undefined): candidate is Candidate & { library_db_id: number } {
  return Number.isInteger(candidate?.library_db_id) && (candidate?.library_db_id ?? 0) > 0
}

function isConfidentLibraryMatch(candidate: Candidate | undefined): candidate is Candidate & { library_db_id: number } {
  return candidate?.match_confidence === 'auto' && hasPositiveLibraryDbId(candidate)
}

function resultBookKey(candidate: Candidate | undefined, title: string): string {
  return hasPositiveLibraryDbId(candidate)
    ? `id:${candidate.library_db_id}`
    : `title:${normalizeResultTitle(title)}`
}

function resultMatchLabel(candidate: Candidate | undefined): string {
  return isConfidentLibraryMatch(candidate) ? '自動照合' : candidate ? '照合候補・要確認' : '未照合'
}

function resultBookRank(book: ResultBook): number {
  if (book.matchConfidence === 'auto' && Number.isInteger(book.libraryDbId) && (book.libraryDbId ?? 0) > 0) return 3
  if (Number.isInteger(book.libraryDbId) && (book.libraryDbId ?? 0) > 0) return 2
  if (book.matchConfidence) return 1
  return 0
}

function devShelfLabel(boxId: string): string | null {
  if (!import.meta.env.DEV) return null
  if (boxId === 'dev-shelf-b') return '棚B'
  if (boxId === 'dev-shelf-ab') return '棚AB'
  return null
}

function ResultBookCard({ book }: { book: ResultBook }) {
  const className = 'flex w-[108px] max-w-full min-w-0 flex-col items-center gap-[5px] text-center'
  const content = (
    <>
      <div className="flex h-[128px] w-[90px] items-end justify-center">
        {book.cover ? <CoverImage src={book.cover} className="max-h-full max-w-full object-contain" fallbackClassName="grid h-full w-full place-items-center bg-zinc-100" /> : <div className="h-full w-[80px] bg-[#d9d9d9]" />}
      </div>
      <p className="line-clamp-2 w-full text-[11px] leading-[13px] text-ink">{book.title}</p>
      <span className={`text-[10px] leading-[12px] ${book.matchLabel === '自動照合' ? 'text-[#087f5b]' : 'text-ink-muted'}`}>{book.matchLabel}</span>
    </>
  )
  if (book.matchLabel === '自動照合' && Number.isInteger(book.libraryDbId) && (book.libraryDbId ?? 0) > 0) {
    return <Link to={`/books/${book.libraryDbId}`} aria-label={book.title} className={`${className} text-inherit no-underline`}>{content}</Link>
  }
  return <article className={className}>{content}</article>
}

function ScanResultLoading({ onBack }: { onBack: () => void }) {
  return (
    <div className="relative mx-auto min-h-screen w-full max-w-[402px] bg-white">
      <button type="button" onClick={onBack} aria-label="戻る" className="tap-soft absolute left-7 top-6 grid size-10 place-items-center rounded-full bg-white"><ArrowUpLeft className="size-6" /></button>
      <div className="flex min-h-[674px] flex-col items-center justify-center gap-6">
        <div className="size-12 animate-spin rounded-full border-4 border-[#d9d9d9] border-t-[#087f5b]" />
        <p className="text-base text-ink">結果を読み込んでいます…</p>
      </div>
    </div>
  )
}

function DiagnosticsPanel({ diag }: { diag?: Diagnostics }) {
  if (!diag) return null
  const tag = diag.apriltag
  const skip = diag.skip_reasons ?? {}
  const skipEntries = Object.entries(skip)
  const hasContent =
    (tag?.raw_ids?.length ?? 0) > 0 ||
    (tag?.tried?.length ?? 0) > 0 ||
    skipEntries.length > 0
  if (!hasContent) return null

  return (
    <details className="mb-6 bg-white border border-gray-200 rounded-xl">
      <summary className="cursor-pointer select-none px-5 py-3 font-semibold text-gray-700 hover:bg-gray-50">
        🔧 診断情報
      </summary>
      <div className="px-5 py-4 border-t border-gray-100 space-y-4 text-sm">
        <div>
          <p className="font-semibold text-gray-700 mb-1">AprilTag 検出</p>
          {tag?.error ? (
            <p className="text-red-600">エラー: {tag.error}</p>
          ) : (tag?.raw_ids?.length ?? 0) > 0 ? (
            <p className="text-gray-600">
              辞書 <code className="bg-gray-100 px-1 rounded">{tag?.selected}</code> で
              tag ID = [{tag?.raw_ids?.join(', ')}] を検出
            </p>
          ) : (
            <p className="text-gray-500">タグ検出なし。試行: {(tag?.tried ?? []).map(t => `${t.dict}=${t.count}`).join(' / ') || '—'}</p>
          )}
        </div>
        {skipEntries.length > 0 && (
          <div>
            <p className="font-semibold text-gray-700 mb-1">crop スキップ理由（{diag.readable_count}/{diag.total_crops} が読取対象）</p>
            <div className="flex flex-wrap gap-2">
              {skipEntries.map(([reason, count]) => (
                <span key={reason} className="text-xs bg-orange-50 text-orange-700 px-2 py-0.5 rounded">
                  {reasonLabel(reason)}: {count}
                </span>
              ))}
            </div>
            {('too_small' in skip) && (
              <p className="text-xs text-gray-500 mt-2">
                ヒント: 撮影画像が小さい/被写体が遠いと「too_small」が出ます。より高解像度で撮影するか、被写体に近づいてください。
              </p>
            )}
          </div>
        )}
      </div>
    </details>
  )
}

function reasonLabel(r: string): string {
  switch (r) {
    case 'too_small': return 'サイズ小'
    case 'blurry': return 'ピンボケ'
    case 'edge_wide': return '横長で端切れ'
    case 'edge_tall': return '縦長で端切れ'
    default: return r
  }
}

function cropImageUrl(raw: string | undefined): string | null {
  if (!raw) return null
  // AWS catalog: "s3://bucket/crops/{job}/{crop}.jpg" → /api/crops/{job}/{crop}.jpg
  const s3Match = raw.match(/^s3:\/\/[^/]+\/crops\/(.+)$/)
  if (s3Match) return apiUrl('/api/crops/' + s3Match[1])
  // local backend: /outputs/jobs/... → /static/jobs/...
  if (raw.includes('/outputs/')) {
    return apiUrl('/static/' + raw.replace(/^.*?\/outputs\//, 'jobs/'))
  }
  if (raw.startsWith('/')) return apiUrl(raw)
  return raw
}

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    collecting: 'bg-blue-100 text-blue-800',
    processing: 'bg-blue-100 text-blue-800',
    pending: 'bg-yellow-100 text-yellow-800',
    running: 'bg-blue-100 text-blue-800',
    ocr_pending: 'bg-blue-100 text-blue-800',
    lookup_pending: 'bg-blue-100 text-blue-800',
    uploading: 'bg-blue-100 text-blue-800',
    no_detection: 'bg-gray-100 text-gray-700',
    no_readable_crops: 'bg-gray-100 text-gray-700',
    done: 'bg-green-100 text-green-800',
    failed: 'bg-red-100 text-red-800',
  }
  const label: Record<string, string> = {
    collecting: '録画中',
    processing: '最終処理中',
    pending: '待機中',
    running: '処理中',
    ocr_pending: 'OCR中',
    lookup_pending: 'DB照合中',
    uploading: 'アップロード中',
    no_detection: '検出なし',
    no_readable_crops: '読取なし',
    done: '完了',
    failed: 'エラー',
  }
  return (
    <span className={`text-xs font-bold px-3 py-1 rounded-full ${map[status] ?? 'bg-orange-100 text-orange-800'}`}>
      {label[status] ?? status}
    </span>
  )
}

function fallbackJob(id: string | undefined): JobState | null {
  if (!import.meta.env.DEV) return null
  const bookEntries = figmaResultBooks.slice(0, 6).map((book, index) => ({
    title: book.title,
    book_lookup: {
      candidates: [{
        title: book.title,
        thumbnail: book.cover,
        score: 1 - index * 0.03,
        match_confidence: 'auto',
      }],
    },
  }))
  return {
    job_id: id ?? 'dev',
    status: 'done',
    average_seconds: 30,
    detected_shelf_count: 29,
    detected_book_count: 103,
    catalog: {
      entries: [
        {
          box_id: 'dev-shelf-b',
          shelf_id: 'base-01-c01-r01',
          crop_image: '',
          detector_confidence: 0.98,
          books: bookEntries.slice(3, 6),
        },
        {
          box_id: 'dev-shelf-ab',
          shelf_id: 'base-02-c01-r01',
          crop_image: '',
          detector_confidence: 0.98,
          books: bookEntries.slice(0, 3),
        },
      ],
    },
  }
}

function EntryCard({ entry }: { entry: CatalogEntry }) {
  const cropSrc = cropImageUrl(entry.crop_image)

  return (
    <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
      <div className="flex items-center gap-3 px-5 py-3 border-b border-gray-100 bg-gray-50">
        <span className="text-sm font-mono text-gray-600">{entry.box_id}</span>
        {entry.shelf_id && (
          <span className="text-xs bg-green-100 text-green-800 px-2 py-0.5 rounded-full font-bold">
            棚: {formatShelfLabel(entry.shelf_id)}
          </span>
        )}
        <span className="text-xs text-gray-400 ml-auto">
          信頼度: {(entry.detector_confidence * 100).toFixed(0)}%
        </span>
      </div>

      <div className="flex gap-6 p-5">
        {cropSrc && (
          <img
            src={cropSrc}
            alt="crop"
            className="w-32 shrink-0 rounded-lg border border-gray-200 object-cover self-start"
          />
        )}
        <div className="flex-1 min-w-0">
          {entry.ocr_error ? (
            <p className="text-sm text-orange-600">スキップ: {entry.ocr_error}</p>
          ) : (entry.books ?? []).length === 0 ? (
            <p className="text-sm text-gray-400">タイトルを検出できませんでした。</p>
          ) : (
            <div className="grid gap-3">
              {(entry.books ?? []).map((book, i) => (
                <BookRow key={i} book={book} />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function BookRow({ book }: { book: BookEntry }) {
  const top = book.book_lookup?.candidates?.[0]
  const conf = top?.match_confidence
  return (
    <div className="flex gap-3 items-start border border-gray-100 rounded-lg p-3">
      <div className="flex h-14 w-10 shrink-0 items-end justify-center">
        {top?.thumbnail ? (
          <CoverImage src={top.thumbnail} className="max-h-full max-w-full rounded object-contain" />
        ) : (
          <div className="h-full w-full rounded bg-gray-100" />
        )}
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-sm font-semibold text-gray-800 leading-tight">
          {top?.title ?? book.title}
        </p>
        {top?.authors && (
          <p className="text-xs text-gray-500 mt-0.5">{top.authors.join('、')}</p>
        )}
        <div className="flex gap-2 mt-1">
          <span className="text-xs text-gray-400">OCR: {book.title}</span>
          {conf && (
            <span className={`text-xs px-1.5 py-0.5 rounded ${conf === 'auto' ? 'bg-green-50 text-green-700' : 'bg-yellow-50 text-yellow-700'}`}>
              {conf === 'auto' ? '自動照合' : '要確認'}
            </span>
          )}
          {top?.score != null && (
            <span className="text-xs text-gray-400">score {top.score.toFixed(2)}</span>
          )}
        </div>
      </div>
    </div>
  )
}
