import { useCallback, useEffect, useState, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiFetch, apiUrl } from '../lib/api'
import { frameMetrics, shouldSendFrame, type FrameSkipReason } from '../lib/liveFrameGate'
import { detectBrowserAprilTags } from '../lib/browserAprilTag'
import { StableTagTracker } from '../lib/stableTagTracker'

interface LiveDetectedTag {
  tag_id: number
  mapped: boolean
  orientation_status: 'ok' | 'mismatch' | 'unchecked'
  placement?: string
}

interface LiveDetectResponse {
  tags: LiveDetectedTag[]
}

interface ShelfTagMap {
  tags?: Record<string, { unit?: string; quadrants?: Record<string, string> }>
}

interface ShelfEvent {
  id: string
  label: string
  time: string
}

export default function ScanPage() {
  const [mode, setMode] = useState<'upload' | 'camera'>('upload')
  const [file, setFile] = useState<File | null>(null)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')
  const [progress, setProgress] = useState(0)

  const videoRef = useRef<HTMLVideoElement>(null)
  const [recording, setRecording] = useState(false)
  const [detecting, setDetecting] = useState(false)
  const [browserDetecting, setBrowserDetecting] = useState(false)
  const [opencvState, setOpencvState] = useState<'loading' | 'ready' | 'fallback'>('loading')
  const [liveStatus, setLiveStatus] = useState('カメラを開始すると、タグを連続で読み取ります。')
  const [detectedTags, setDetectedTags] = useState<LiveDetectedTag[]>([])
  const [currentShelfLabel, setCurrentShelfLabel] = useState('棚を探しています')
  const [shelfEvents, setShelfEvents] = useState<ShelfEvent[]>([])
  const [shelfTagMap, setShelfTagMap] = useState<ShelfTagMap>({})
  const streamRef = useRef<MediaStream | null>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const browserCanvasRef = useRef<HTMLCanvasElement>(null)
  const detectTimerRef = useRef<number | null>(null)
  const browserDetectTimerRef = useRef<number | null>(null)
  const detectingRef = useRef(false)
  const browserDetectingRef = useRef(false)
  const stableTagTrackerRef = useRef(new StableTagTracker(3))
  const scanCanvasRef = useRef<HTMLCanvasElement>(null)
  const captureCanvasRef = useRef<HTMLCanvasElement>(null)
  const scanAnimationRef = useRef<number | null>(null)
  const scanGateRef = useRef<{ previous?: Uint8Array; lastSentAt: number }>({ lastSentAt: 0 })
  const sessionRef = useRef<{ id: string; template: string } | null>(null)
  const frameNumberRef = useRef(0)
  const lastEvaluationRef = useRef(0)
  const pendingUploadsRef = useRef<Set<Promise<void>>>(new Set())
  const [scanStats, setScanStats] = useState({ evaluated: 0, sent: 0, skipped: {} as Partial<Record<FrameSkipReason, number>> })
  const navigate = useNavigate()

  const detectFrame = useCallback(async () => {
    if (detectingRef.current) return
    const video = videoRef.current
    const canvas = canvasRef.current
    if (!video || !canvas || video.videoWidth === 0 || video.videoHeight === 0) return

    detectingRef.current = true
    setDetecting(true)
    try {
      const width = Math.min(960, video.videoWidth)
      const height = Math.round((width / video.videoWidth) * video.videoHeight)
      canvas.width = width
      canvas.height = height
      const context = canvas.getContext('2d')
      if (!context) throw new Error('canvas is not available')
      context.drawImage(video, 0, 0, width, height)
      const blob = await new Promise<Blob | null>(resolve => canvas.toBlob(resolve, 'image/jpeg', 0.82))
      if (!blob) throw new Error('frame capture failed')

      const form = new FormData()
      form.append('image', blob, 'live-scan-frame.jpg')
      const response = await apiFetch('/api/tags/detect', { method: 'POST', body: form })
      if (!response.ok) throw new Error(await response.text())
      const data = (await response.json()) as LiveDetectResponse
      const tags = data.tags ?? []
      setDetectedTags(tags)
      const usable = tags.find(tag => tag.mapped && tag.orientation_status !== 'mismatch')
      if (usable) {
        setLiveStatus(`tag ${usable.tag_id} を認識中。${usable.placement ?? '棚の位置を確認できます。'}`)
      } else if (tags.length > 0) {
        setLiveStatus(`tag ${tags.map(tag => tag.tag_id).join(', ')} を検出しました。向きか配置表を確認してください。`)
      } else {
        setLiveStatus('タグを探しています。棚のタグを画面中央に写してください。')
      }
    } catch (detectionError) {
      setLiveStatus(`ライブ検出に失敗しました: ${detectionError instanceof Error ? detectionError.message : String(detectionError)}`)
    } finally {
      detectingRef.current = false
      setDetecting(false)
    }
  }, [])

  const detectBrowserFrame = useCallback(async () => {
    if (browserDetectingRef.current) return
    const video = videoRef.current
    const canvas = browserCanvasRef.current
    if (!video || !canvas || video.videoWidth === 0 || video.videoHeight === 0) return

    browserDetectingRef.current = true
    setBrowserDetecting(true)
    try {
      const width = Math.min(960, video.videoWidth)
      canvas.width = width
      canvas.height = Math.round((width / video.videoWidth) * video.videoHeight)
      const context = canvas.getContext('2d')
      if (!context) return
      context.drawImage(video, 0, 0, canvas.width, canvas.height)
      const tags = await detectBrowserAprilTags(canvas)
      setOpencvState('ready')
      const event = stableTagTrackerRef.current.update(tags.map(tag => tag.tagId))
      if (!event) return

      const primary = shelfTagMap.tags?.[String(event.tagIds[0])]
      const label = primary?.unit
        ? `${primary.unit}（tag ${event.tagIds.join(', ')}）`
        : `tag ${event.tagIds.join(', ')}`
      const time = new Date().toLocaleTimeString('ja-JP', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
      setCurrentShelfLabel(label)
      setShelfEvents(events => [{ id: `${event.key}-${Date.now()}`, label, time }, ...events].slice(0, 6))
      setLiveStatus(`新しい棚を検知しました。${label}を確認しています…`)

      // Browser detection is the low-latency trigger. Ask the backend for the
      // authoritative orientation/quadrant/shelf assignment on this frame.
      void detectFrame()
    } catch (browserError) {
      setOpencvState('fallback')
      setLiveStatus(`ブラウザ検知を利用できないため、サーバー検知を使用中です。`)
      if (browserError instanceof Error) console.warn(browserError)
    } finally {
      browserDetectingRef.current = false
      setBrowserDetecting(false)
    }
  }, [detectFrame, shelfTagMap])

  const stopCamera = useCallback(() => {
    if (detectTimerRef.current != null) {
      window.clearInterval(detectTimerRef.current)
      detectTimerRef.current = null
    }
    if (browserDetectTimerRef.current != null) {
      window.clearInterval(browserDetectTimerRef.current)
      browserDetectTimerRef.current = null
    }
    streamRef.current?.getTracks().forEach(track => track.stop())
    streamRef.current = null
    if (videoRef.current) videoRef.current.srcObject = null
    if (scanAnimationRef.current != null) cancelAnimationFrame(scanAnimationRef.current)
    scanAnimationRef.current = null
    stableTagTrackerRef.current.reset()
    const abandoned = sessionRef.current
    sessionRef.current = null
    if (abandoned) void apiFetch(`/api/scan/sessions/${abandoned.id}/cancel`, { method: 'POST' })
    setRecording(false)
  }, [])

  const startCamera = useCallback(async () => {
    try {
      setError('')
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } })
      streamRef.current = stream
      if (videoRef.current) {
        videoRef.current.srcObject = stream
        await videoRef.current.play()
      }
      try {
        const mapResponse = await apiFetch('/api/shelves')
        if (mapResponse.ok) setShelfTagMap(await mapResponse.json() as ShelfTagMap)
      } catch {
        setShelfTagMap({})
      }
      setOpencvState('loading')
      setCurrentShelfLabel('棚を探しています')
      setShelfEvents([])
      setLiveStatus('タグを探しています。棚のタグを画面中央に写してください。')
      await detectFrame()
      void detectBrowserFrame()
      detectTimerRef.current = window.setInterval(() => void detectFrame(), 1600)
      browserDetectTimerRef.current = window.setInterval(() => void detectBrowserFrame(), 450)
    } catch {
      setError('カメラにアクセスできませんでした。')
      stopCamera()
    }
  }, [detectBrowserFrame, detectFrame, stopCamera])

  useEffect(() => {
    if (mode !== 'camera') {
      stopCamera()
      return
    }
    void startCamera()
    return stopCamera
  }, [mode, startCamera, stopCamera])

  const stopFrameLoop = () => {
    if (scanAnimationRef.current != null) cancelAnimationFrame(scanAnimationRef.current)
    scanAnimationRef.current = null
  }

  const startRecording = async () => {
    if (!videoRef.current?.srcObject) return
    try {
      const res = await apiFetch('/api/scan/sessions', { method: 'POST' })
      if (!res.ok) throw new Error(await res.text())
      const session = await res.json() as { session_id: string; frame_upload_url_template?: string; frame_upload_url_endpoint?: string }
      sessionRef.current = { id: session.session_id, template: session.frame_upload_url_template ?? session.frame_upload_url_endpoint ?? '' }
      frameNumberRef.current = 0; lastEvaluationRef.current = 0
      scanGateRef.current = { lastSentAt: 0 }
      setScanStats({ evaluated: 0, sent: 0, skipped: {} })
      setRecording(true)
      const loop = (now: number) => {
        if (!sessionRef.current) return
        if (now - lastEvaluationRef.current >= 1000 / 15) {
          lastEvaluationRef.current = now
          const video = videoRef.current; const canvas = scanCanvasRef.current
          if (video && canvas && video.videoWidth) {
            canvas.width = 160; canvas.height = Math.round(video.videoHeight * 160 / video.videoWidth)
            const ctx = canvas.getContext('2d')
            if (ctx) {
              ctx.drawImage(video, 0, 0, canvas.width, canvas.height)
              const evaluated = frameMetrics(ctx.getImageData(0, 0, canvas.width, canvas.height), scanGateRef.current.previous)
              const reason = shouldSendFrame(evaluated.metrics, now, scanGateRef.current)
              scanGateRef.current.previous = evaluated.sample
              if (reason) setScanStats(s => ({ ...s, evaluated: s.evaluated + 1, skipped: { ...s.skipped, [reason]: (s.skipped[reason] ?? 0) + 1 } }))
              else {
                scanGateRef.current.lastSentAt = now
                const frame = `frame_${String(++frameNumberRef.current).padStart(6, '0')}.jpg`
                const capture = captureCanvasRef.current
                if (!capture) return
                const captureWidth = Math.min(1280, video.videoWidth)
                capture.width = captureWidth
                capture.height = Math.round(video.videoHeight * captureWidth / video.videoWidth)
                capture.getContext('2d')?.drawImage(video, 0, 0, capture.width, capture.height)
                capture.toBlob(blob => {
                  if (!blob || !sessionRef.current) return
                  const activeSession = sessionRef.current
                  const upload = (async () => {
                    let url = activeSession.template.replace('{frame_id}', frame)
                    if (!activeSession.template.includes('{frame_id}')) {
                      const init = await apiFetch(activeSession.template, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ filename: frame }) })
                      if (!init.ok) throw new Error(`frame init ${init.status}`)
                      url = (await init.json() as { upload_url: string }).upload_url
                    }
                    const put = await fetch(apiUrl(url), { method: 'PUT', headers: { 'Content-Type': 'image/jpeg' }, body: blob })
                    if (!put.ok) throw new Error(`frame upload ${put.status}`)
                    setScanStats(s => ({ ...s, evaluated: s.evaluated + 1, sent: s.sent + 1 }))
                  })().catch(e => setError(`候補フレームの保存に失敗しました: ${String(e)}`))
                  pendingUploadsRef.current.add(upload)
                  void upload.finally(() => pendingUploadsRef.current.delete(upload))
                }, 'image/jpeg', 0.82)
              }
            }
          }
        }
        scanAnimationRef.current = requestAnimationFrame(loop)
      }
      scanAnimationRef.current = requestAnimationFrame(loop)
    } catch (e) { setError(`ライブスキャンを開始できませんでした: ${String(e)}`) }
  }

  const stopRecording = async () => {
    const session = sessionRef.current; stopFrameLoop(); setRecording(false)
    if (!session) return
    try {
      await Promise.all([...pendingUploadsRef.current])
      const res = await apiFetch(`/api/scan/sessions/${session.id}/complete`, { method: 'POST' })
      if (!res.ok) throw new Error(await res.text())
      const data = await res.json() as { job_id: string }
      sessionRef.current = null
      navigate(`/jobs/${data.job_id}`)
    } catch (e) {
      setRecording(true)
      setError(`スキャン確定に失敗しました。再確定またはキャンセルできます: ${String(e)}`)
    }
  }

  const cancelRecording = async () => {
    const session = sessionRef.current; stopFrameLoop(); setRecording(false); sessionRef.current = null
    if (session) await apiFetch(`/api/scan/sessions/${session.id}/cancel`, { method: 'POST' })
  }

  const submit = async () => {
    if (!file) return
    setUploading(true)
    setError('')
    setProgress(0)
    try {
      const contentType = file.type || 'application/octet-stream'
      // 1. presigned PUT URL を取得
      const initRes = await apiFetch('/api/scan/init', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename: file.name, content_type: contentType }),
      })
      if (!initRes.ok) throw new Error(`init ${initRes.status}: ${await initRes.text()}`)
      const init = (await initRes.json()) as { job_id: string; upload_url: string; content_type: string }

      // 2. S3 へ直接 PUT（XHR で進捗付き）
      await putWithProgress(init.upload_url, file, init.content_type, setProgress)

      // 3. 処理開始を通知
      const startRes = await apiFetch('/api/scan/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_id: init.job_id }),
      })
      if (!startRes.ok) throw new Error(`start ${startRes.status}: ${await startRes.text()}`)

      navigate(`/jobs/${init.job_id}`)
    } catch (e) {
      setError(`アップロードに失敗しました: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setUploading(false)
    }
  }

  return (
    <div>
      <h2 className="text-2xl font-bold text-gray-800 mb-6">棚をスキャン</h2>

      {/* モード切替 */}
      <div className="flex gap-2 mb-6">
        {(['upload', 'camera'] as const).map(m => (
          <button
            key={m}
            onClick={() => { setMode(m); setFile(null) }}
            className={`px-4 py-2 rounded-lg text-sm font-semibold border transition-colors ${
              mode === m ? 'bg-[#1f7a5c] text-white border-[#1f7a5c]' : 'bg-white text-gray-600 border-gray-300 hover:bg-gray-50'
            }`}
          >
            {m === 'upload' ? 'ファイルアップロード' : 'カメラ撮影'}
          </button>
        ))}
      </div>

      {mode === 'upload' && (
        <label className="flex flex-col items-center justify-center border-2 border-dashed border-gray-300 rounded-xl p-12 cursor-pointer hover:border-[#1f7a5c] transition-colors bg-white">
          <span className="text-4xl mb-3">📷</span>
          <span className="text-gray-600 font-medium">
            {file ? file.name : '画像または動画を選択'}
          </span>
          <span className="text-sm text-gray-400 mt-1">JPG / PNG / MP4 / MOV</span>
          <input
            type="file"
            accept="image/*,video/*"
            className="hidden"
            onChange={e => setFile(e.target.files?.[0] ?? null)}
          />
        </label>
      )}

      {mode === 'camera' && (
        <div className="bg-black rounded-xl overflow-hidden mb-4">
          <video ref={videoRef} className="w-full max-h-80 object-contain" muted playsInline />
          <canvas ref={canvasRef} className="hidden" />
          <canvas ref={browserCanvasRef} className="hidden" />
          <canvas ref={scanCanvasRef} className="hidden" />
          <canvas ref={captureCanvasRef} className="hidden" />
          <div className="flex gap-3 p-4 justify-center">
            {!recording ? (
              <button onClick={startRecording} className="px-6 py-2 bg-red-600 text-white rounded-lg font-semibold">
                ● ライブスキャン開始
              </button>
            ) : (
              <button onClick={stopRecording} className="px-6 py-2 bg-gray-700 text-white rounded-lg font-semibold animate-pulse">
                ■ スキャンを確定
              </button>
            )}
          </div>
          {recording && <button onClick={() => void cancelRecording()} className="mx-auto mb-3 block text-xs text-gray-300 underline">キャンセル（OCRしない）</button>}
          <div className="px-4 pb-4 text-center">
            <p className="text-sm text-white">{detecting || browserDetecting ? 'タグを読み取り中…' : liveStatus}</p>
            <div className="mt-2 flex flex-wrap justify-center gap-2 text-xs">
              <span className="rounded-full bg-white/15 px-2 py-1 text-white">現在: {currentShelfLabel}</span>
              <span className={`rounded-full px-2 py-1 ${opencvState === 'ready' ? 'bg-emerald-200 text-emerald-900' : opencvState === 'fallback' ? 'bg-amber-200 text-amber-900' : 'bg-white/15 text-white'}`}>
                Webタグ検知: {opencvState === 'ready' ? '有効' : opencvState === 'fallback' ? 'サーバーへ切替' : '準備中'}
              </span>
            </div>
            {detectedTags.length > 0 && (
              <div className="mt-2 flex flex-wrap justify-center gap-2">
                {detectedTags.map(tag => (
                  <span
                    key={`${tag.tag_id}-${tag.orientation_status}`}
                    className={`rounded-full px-2 py-1 text-xs font-semibold ${tag.orientation_status === 'mismatch' ? 'bg-red-100 text-red-700' : 'bg-green-100 text-green-800'}`}
                  >
                    tag {tag.tag_id}
                  </span>
                ))}
              </div>
            )}
          </div>
          {shelfEvents.length > 0 && (
            <div className="mx-4 mb-3 rounded-lg bg-white/10 p-3 text-left text-xs text-white">
              <p className="mb-2 font-semibold">検知した棚</p>
              <div className="space-y-1.5">
                {shelfEvents.map(event => (
                  <div key={event.id} className="flex items-center justify-between gap-3">
                    <span>✓ {event.label}</span>
                    <span className="text-gray-300">{event.time}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
          <p className="px-4 pb-4 text-center text-xs text-gray-300">評価 {scanStats.evaluated} / 送信 {scanStats.sent} / スキップ: ぶれ {scanStats.skipped.blurred ?? 0}・同一 {scanStats.skipped.unchanged ?? 0}・間隔 {scanStats.skipped.rate_limited ?? 0}</p>
        </div>
      )}

      {error && <p className="text-red-600 text-sm mt-3">{error}</p>}

      <button
        disabled={!file || uploading}
        onClick={submit}
        className="mt-6 w-full py-3 bg-[#1f7a5c] text-white rounded-xl font-bold text-base hover:bg-[#196649] disabled:opacity-40 transition-colors"
      >
        {uploading ? (progress > 0 && progress < 100 ? `アップロード中… ${progress}%` : '送信中…') : '解析する'}
      </button>
      {uploading && progress > 0 && progress < 100 && (
        <div className="mt-3 w-full h-2 bg-gray-200 rounded-full overflow-hidden">
          <div className="h-full bg-[#1f7a5c] transition-all" style={{ width: `${progress}%` }} />
        </div>
      )}

      <p className="text-xs text-gray-400 mt-3 text-center">
        本棚にAprilTagが貼られている場合、棚IDも自動で認識されます。
      </p>
    </div>
  )
}

function putWithProgress(
  url: string,
  file: File,
  contentType: string,
  onProgress: (pct: number) => void,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('PUT', url)
    xhr.setRequestHeader('Content-Type', contentType)
    xhr.upload.onprogress = e => {
      if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100))
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        onProgress(100)
        resolve()
      } else {
        reject(new Error(`S3 PUT ${xhr.status}: ${xhr.responseText.slice(0, 200)}`))
      }
    }
    xhr.onerror = () => reject(new Error('S3 PUT network error'))
    xhr.send(file)
  })
}
