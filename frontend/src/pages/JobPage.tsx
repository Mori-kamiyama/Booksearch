import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { apiFetch, apiUrl } from '../lib/api'

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
  status: 'pending' | 'running' | 'ocr_pending' | 'lookup_pending' | 'no_detection' | 'no_readable_crops' | 'done' | 'failed' | 'uploading'
  error?: string
  catalog?: Catalog
  diagnostics?: Diagnostics
  crop_total?: number | string
  ocr_done?: number | string
}

export default function JobPage() {
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
        setPollError(e instanceof Error ? e.message : 'ジョブ取得に失敗しました')
      }
    }
    poll()
    timer = setInterval(poll, 2000)
    return () => clearInterval(timer)
  }, [id])

  if (!job) return <div className="text-center py-12 text-gray-400">読み込み中…</div>

  const entries = job.catalog?.entries ?? []
  const bookCount = entries.reduce((n, e) => n + (e.books?.length ?? 0), 0)
  const matchCount = entries.reduce(
    (n, e) => n + (e.books?.filter(b => (b.book_lookup?.candidates?.length ?? 0) > 0).length ?? 0),
    0
  )

  return (
    <div>
      <div className="flex items-center gap-4 mb-6">
        <Link to="/scan" className="text-sm text-gray-500 hover:text-[#1f7a5c]">← スキャンに戻る</Link>
        <h2 className="text-2xl font-bold text-gray-800">スキャン結果</h2>
        <StatusBadge status={job.status} />
      </div>

      {pollError && (
        <div className="mb-6 bg-red-50 border border-red-200 rounded-xl p-4 text-sm text-red-700">
          {pollError}。画面を再読み込みするか、スキャンをやり直してください。
        </div>
      )}

      {['pending', 'running', 'ocr_pending', 'lookup_pending'].includes(job.status) ? (
        <div className="text-center py-20 text-gray-500">
          <div className="text-4xl mb-4 animate-spin">⚙️</div>
          <p>解析中です。しばらくお待ちください…</p>
        </div>
      ) : job.status === 'failed' ? (
        <div className="bg-red-50 border border-red-200 rounded-xl p-6 text-red-700">
          解析に失敗しました: {job.error}
        </div>
      ) : (
        <>
          {/* サマリー */}
          <div className="grid grid-cols-3 gap-4 mb-6">
            {[
              { label: '検出 box', value: entries.length },
              { label: 'OCR タイトル', value: bookCount },
              { label: 'DB 照合', value: matchCount },
            ].map(m => (
              <div key={m.label} className="bg-white border border-gray-200 rounded-xl p-5 text-center">
                <p className="text-3xl font-bold text-[#1f7a5c]">{m.value}</p>
                <p className="text-sm text-gray-500 mt-1">{m.label}</p>
              </div>
            ))}
          </div>

          <DiagnosticsPanel diag={job.diagnostics} />


          {/* エントリ一覧 */}
          <div className="grid gap-6">
            {entries.map(entry => (
              <EntryCard key={entry.box_id} entry={entry} />
            ))}
          </div>
        </>
      )}
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

function EntryCard({ entry }: { entry: CatalogEntry }) {
  const cropSrc = cropImageUrl(entry.crop_image)

  return (
    <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
      <div className="flex items-center gap-3 px-5 py-3 border-b border-gray-100 bg-gray-50">
        <span className="text-sm font-mono text-gray-600">{entry.box_id}</span>
        {entry.shelf_id && (
          <span className="text-xs bg-green-100 text-green-800 px-2 py-0.5 rounded-full font-bold">
            棚: {entry.shelf_id}
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
      {top?.thumbnail ? (
        <img src={top.thumbnail} alt="" className="w-10 h-14 object-cover rounded shrink-0" />
      ) : (
        <div className="w-10 h-14 bg-gray-100 rounded shrink-0" />
      )}
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
