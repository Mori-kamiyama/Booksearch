import { useCallback, useEffect, useState, useRef } from 'react'
import { ArrowUpLeft } from 'lucide-react'
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
  const [mode, setMode] = useState<'upload' | 'camera'>('camera')
  const [file, setFile] = useState<File | null>(null)
  const [filePreview, setFilePreview] = useState('')
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')
  const [progress, setProgress] = useState(0)

  const videoRef = useRef<HTMLVideoElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [recording, setRecording] = useState(false)
  const [cameraReady, setCameraReady] = useState(false)
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

  useEffect(() => {
    if (!file?.type.startsWith('image/')) {
      setFilePreview('')
      return
    }
    const preview = URL.createObjectURL(file)
    setFilePreview(preview)
    return () => URL.revokeObjectURL(preview)
  }, [file])

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
    setCameraReady(false)
  }, [])

  const startCamera = useCallback(async () => {
    try {
      setError('')
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } })
      streamRef.current = stream
      if (videoRef.current) {
        videoRef.current.srcObject = stream
        await videoRef.current.play()
        setCameraReady(true)
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

  const chooseFile = (selected: File | null) => {
    setFile(selected)
    if (selected) setMode('upload')
  }

  const primaryAction = () => {
    if (mode === 'upload') {
      void submit()
    } else if (recording) {
      void stopRecording()
    } else {
      void startRecording()
    }
  }

  const statusLines = shelfEvents.length > 0
    ? [`新しい棚を検知しました!`, '本を解析しています…']
    : mode === 'upload'
      ? [file?.name ?? '画像または動画を選択', file ? 'このファイルを解析します' : '右下のボタンから選択できます']
      : import.meta.env.DEV
        ? ['新しい棚を検知しました！', '本を解析しています…']
        : [detecting || browserDetecting ? '棚を検知しています…' : currentShelfLabel, liveStatus]
  const showDevCameraPreview = import.meta.env.DEV && mode === 'camera' && (!cameraReady || Boolean(error))

  return (
    <div className="min-h-screen bg-[#1e1e1e] md:grid md:place-items-center">
      <div className="relative mx-auto h-[100dvh] min-h-[674px] w-full max-w-[402px] overflow-hidden bg-[#1e1e1e] p-4 md:h-[874px]">
        <div className="relative h-full w-full overflow-hidden bg-[#292929]">
          {mode === 'camera' ? (
            <>
              {showDevCameraPreview && <img src="/dev-scan-shelf.jpg" alt="" className="absolute inset-0 size-full object-cover" />}
              <video ref={videoRef} className={`absolute inset-0 size-full object-cover ${showDevCameraPreview ? 'opacity-0' : ''}`} muted playsInline />
            </>
          ) : filePreview ? (
            <img src={filePreview} alt="選択した本棚" className="absolute inset-0 size-full object-cover" />
          ) : (
            <div className="absolute inset-0 grid place-items-center bg-[radial-gradient(circle_at_center,#414141,#1e1e1e)] px-10 text-center text-sm text-white/60">
              {file ? file.name : '本棚を画面に収めてください'}
            </div>
          )}

          <div className="absolute inset-0 bg-gradient-to-b from-black/20 via-transparent to-black/25" />

          <button type="button" onClick={() => navigate(-1)} aria-label="戻る" className="tap-soft absolute left-7 top-6 z-10 grid size-10 place-items-center rounded-full bg-white text-[#1e1e1e]">
            <ArrowUpLeft className="size-6" />
          </button>

          <div className="absolute bottom-[204px] left-1/2 flex h-[63px] w-[300px] -translate-x-1/2 flex-col justify-center overflow-hidden rounded-xl bg-[#1e1e1e] px-4 text-base leading-[19px] text-white shadow-[20px_8px_0_rgba(30,30,30,0.83)]">
            <p>{statusLines[0]}</p>
            <p className="line-clamp-1 text-white/90">{statusLines[1]}</p>
          </div>

          {error && !showDevCameraPreview && (
            <div className="absolute left-1/2 top-[104px] z-10 w-[300px] -translate-x-1/2 rounded-xl bg-red-700/90 px-4 py-3 text-sm text-white">
              {error}
            </div>
          )}

          {uploading && (
            <div className="absolute inset-x-7 bottom-[164px] z-10 overflow-hidden rounded-full bg-white/35">
              <div className="h-2 bg-[#087f5b] transition-all" style={{ width: `${Math.max(8, progress)}%` }} />
            </div>
          )}

          <div className="absolute bottom-16 left-1/2 flex w-[300px] -translate-x-1/2 items-center justify-end gap-12">
            <button
              type="button"
              onClick={primaryAction}
              disabled={(mode === 'upload' && !file) || uploading}
              aria-label={mode === 'upload' ? '選択したファイルを解析' : recording ? 'スキャンを終了' : 'ライブスキャンを開始'}
              className={`tap-card grid size-[100px] place-items-center rounded-full transition disabled:opacity-50 ${recording ? 'bg-[#1e1e1e]' : 'border-[5px] border-[#1e1e1e] bg-white'}`}
            >
              {recording && <span className="size-11 rounded-[12px] bg-[#ff3b30]" />}
              {mode === 'upload' && !recording && <span className="text-sm font-semibold text-[#1e1e1e]">解析</span>}
            </button>

            {!recording && (
              <button type="button" onClick={() => fileInputRef.current?.click()} aria-label="画像または動画を選択" className="tap-card h-[61px] w-[58px] rounded-xl bg-white shadow-sm" />
            )}
          </div>

          {recording && (
            <button type="button" onClick={() => void cancelRecording()} className="tap-soft absolute bottom-6 left-1/2 -translate-x-1/2 text-xs text-white/75 underline">
              キャンセル
            </button>
          )}

          <input ref={fileInputRef} type="file" accept="image/*,video/*" className="hidden" onChange={event => chooseFile(event.target.files?.[0] ?? null)} />
          <canvas ref={canvasRef} className="hidden" />
          <canvas ref={browserCanvasRef} className="hidden" />
          <canvas ref={scanCanvasRef} className="hidden" />
          <canvas ref={captureCanvasRef} className="hidden" />

          <div className="sr-only" aria-live="polite">
            Webタグ検知: {opencvState}。評価 {scanStats.evaluated}、送信 {scanStats.sent}。検出タグ {detectedTags.map(tag => tag.tag_id).join(', ')}
          </div>
        </div>
      </div>
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
