import { useEffect, useState } from 'react'
import { ArrowUpLeft } from 'lucide-react'
import { useNavigate, useParams } from 'react-router-dom'
import { apiFetch, apiUrl } from '../lib/api'
import { formatShelfLabel } from '../lib/shelf'
import { fallbackCoverForTitle, figmaResultBooks } from '../data/figmaBooks'

interface Candidate {
  title: string
  authors?: string[]
  publisher?: string
  isbns?: string[]
  score?: number
  match_confidence?: string
  thumbnail?: string
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
  status: 'collecting' | 'processing' | 'pending' | 'running' | 'ocr_pending' | 'lookup_pending' | 'no_detection' | 'no_readable_crops' | 'done' | 'failed' | 'uploading'
  error?: string
  catalog?: Catalog
  diagnostics?: Diagnostics
  crop_total?: number | string
  ocr_done?: number | string
  average_seconds?: number
  detected_shelf_count?: number
  detected_book_count?: number
}

export default function JobPage() {
  const navigate = useNavigate()
  const { id } = useParams<{ id: string }>()
  const [job, setJob] = useState<JobState | null>(null)
  const [pollError, setPollError] = useState('')

  useEffect(() => {
    let timer: ReturnType<typeof setInterval>
    const poll = async () => {
      try {
        const res = await apiFetch(`/api/jobs/${id}`)
        if (!res.ok) {
          throw new Error(`ジョブ取得に失敗しました (${res.status})`)
        }
        const data: JobState = await res.json()
        setJob(data)
        setPollError('')
        if (['done', 'failed', 'no_detection', 'no_readable_crops'].includes(data.status)) {
          clearInterval(timer)
        }
      } catch (e) {
        const fallback = fallbackJob(id)
        if (fallback) {
          setJob(fallback)
          setPollError('')
          clearInterval(timer)
        } else {
          setPollError(e instanceof Error ? e.message : 'ジョブ取得に失敗しました')
        }
      }
    }
    timer = setInterval(poll, 2000)
    void poll()
    return () => clearInterval(timer)
  }, [id])

  if (!job) return <ScanResultLoading onBack={() => navigate(-1)} />

  const entries = job.catalog?.entries ?? []
  const bookCount = Number(job.detected_book_count ?? entries.reduce((n, e) => n + (e.books?.length ?? 0), 0))
  const shelfIds = new Set(entries.map(entry => entry.shelf_id).filter(Boolean))
  const shelfCount = Number(job.detected_shelf_count ?? (shelfIds.size || (entries.length > 0 ? 1 : 0)))
  const groups = groupResultBooks(entries)
  const processing = ['collecting', 'processing', 'pending', 'running', 'ocr_pending', 'lookup_pending'].includes(job.status)

  return (
    <div className="min-h-screen bg-white">
      <div className="relative mx-auto min-h-screen w-full max-w-[402px] overflow-hidden pb-12">
        <button type="button" onClick={() => navigate(-1)} aria-label="戻る" className="tap-soft absolute left-7 top-6 grid size-10 place-items-center rounded-full bg-white text-[#1e1e1e]">
          <ArrowUpLeft className="size-6" />
        </button>

        {pollError && <p className="mx-7 mt-[82px] rounded-xl bg-red-50 p-3 text-sm text-red-700">{pollError}</p>}

        {processing ? (
          <div className="flex min-h-[674px] flex-col items-center justify-center gap-6 px-7 text-center">
            <div className="size-12 animate-spin rounded-full border-4 border-[#d9d9d9] border-t-[#087f5b]" />
            <h1 className="text-4xl font-semibold leading-[44px] text-[#087f5b]">解析中</h1>
            <p className="text-base leading-[19px] text-ink">棚と本を確認しています…<br />このままお待ちください</p>
            <StatusBadge status={job.status} />
          </div>
        ) : job.status === 'failed' ? (
          <div className="flex min-h-[674px] flex-col items-center justify-center gap-6 px-7 text-center">
            <h1 className="text-4xl font-semibold leading-[44px] text-red-600">エラー</h1>
            <p className="text-base leading-normal text-ink">解析に失敗しました。<br />{job.error || 'もう一度スキャンしてください。'}</p>
            <button type="button" onClick={() => navigate('/scan')} className="tap-card rounded-full bg-[#087f5b] px-6 py-3 text-white">スキャンへ戻る</button>
          </div>
        ) : (
          <div className="pt-[104px]">
            <section className="flex flex-col items-center gap-7 px-7 text-center">
              <h1 className="w-full text-4xl font-semibold leading-[44px] text-[#087f5b]">終了</h1>
              <p className="w-full text-base leading-[19px] text-ink">スキャンありがとうございました！！</p>
              <div className="flex w-full items-baseline justify-center gap-4 whitespace-nowrap text-ink">
                <Metric value={shelfCount} label="棚検知" />
                <Metric value={bookCount} label="冊検知" />
                <p><span className="text-base">平均</span><span className="text-4xl font-semibold leading-[44px] text-[#087f5b]">{job.average_seconds ?? '—'}</span><span className="text-base">秒</span></p>
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

            <div className="mx-7 mt-10"><DiagnosticsPanel diag={job.diagnostics} /></div>
          </div>
        )}
      </div>
    </div>
  )
}

function Metric({ value, label }: { value: number; label: string }) {
  return <p><span className="text-4xl font-semibold leading-[44px] text-[#087f5b]">{value}</span><span className="text-base">{label}</span></p>
}

interface ResultBook {
  title: string
  cover?: string
}

function groupResultBooks(entries: CatalogEntry[]): { shelf: string; books: ResultBook[] }[] {
  const groups = new Map<string, ResultBook[]>()
  for (const entry of entries) {
    const shelf = devShelfLabel(entry.box_id) ?? (entry.shelf_id ? formatShelfLabel(entry.shelf_id) : '棚未判定')
    const books = groups.get(shelf) ?? []
    for (const book of entry.books ?? []) {
      const top = book.book_lookup?.candidates?.[0]
      const title = top?.title || book.title
      books.push({ title, cover: top?.thumbnail || fallbackCoverForTitle(title) })
    }
    groups.set(shelf, books)
  }
  return [...groups.entries()].filter(([, books]) => books.length > 0).map(([shelf, books]) => ({ shelf, books }))
}

function devShelfLabel(boxId: string): string | null {
  if (!import.meta.env.DEV) return null
  if (boxId === 'dev-shelf-b') return '棚B'
  if (boxId === 'dev-shelf-ab') return '棚AB'
  return null
}

function ResultBookCard({ book }: { book: ResultBook }) {
  return (
    <article className="flex w-[108px] min-w-0 flex-col items-center gap-[5px] text-center">
      <div className="flex h-[128px] w-[90px] items-end justify-center">
        {book.cover ? <img src={book.cover} alt="" className="max-h-full max-w-full object-contain" /> : <div className="h-full w-[80px] bg-[#d9d9d9]" />}
      </div>
      <p className="line-clamp-2 w-full text-[11px] leading-[13px] text-ink">{book.title}</p>
    </article>
  )
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
    no_detection: '検出なし',
    no_readable_crops: '読取なし',
    done: '完了',
    failed: 'エラー',
  }
  return (
    <span className={`text-xs font-bold px-3 py-1 rounded-full ${map[status] ?? ''}`}>
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
          <img src={top.thumbnail} alt="" className="max-h-full max-w-full rounded object-contain" />
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
