import { useCallback, useEffect, useMemo, useState, useRef } from 'react'
import { ArrowLeft, BookMarked, Camera, ImagePlus } from 'lucide-react'
import { useLocation, useNavigate } from 'react-router-dom'
import { apiFetch, apiUrl } from '../lib/api'
import { frameMetrics, shouldSendFrame, type FrameGateState, type FrameSkipReason } from '../lib/liveFrameGate'
import { detectBrowserAprilTags, isBrowserAprilTagReady } from '../lib/browserAprilTag'
import { StableTagTracker } from '../lib/stableTagTracker'
import { cameraAccessErrorMessage } from '../lib/cameraAccess'
import { parseScanNavigationState, scanTargetMatchState, type ScanTargetMatchState } from '../lib/scanTarget'
import embeddedShelfMap from '../../../data/apriltag_library_map.json'
import { fallbackCoverForTitle } from '../data/figmaBooks'

interface LiveDetectedTag {
  tag_id: number
  mapped: boolean
  orientation_status: 'ok' | 'mismatch' | 'unchecked'
  placement?: string
}

interface LiveDetectResponse {
  map_id?: string
  tags: LiveDetectedTag[]
}

interface ShelfTagMap {
  map_id?: string
  tags?: Record<string, { unit?: string; quadrants?: Record<string, string> }>
}

interface ShelfEvent {
  id: string
  label: string
  time: string
}

interface TrackedTag {
  tagId: number
  label: string
  firstSeenAt: number
  lastSeenAt: number
}

interface LiveCatalogCandidate {
  title?: string
  thumbnail?: string
  library_db_id?: number
  match_confidence?: string
}

interface LiveCatalog {
  entries?: Array<{
    shelf_id?: string | null
    books?: Array<{
      title?: string
      book_lookup?: { candidates?: LiveCatalogCandidate[] }
    }>
  }>
}

interface LiveJobState {
  job_id: string
  status: string
  accepted_frames?: number
  processed_frames?: number
  crop_total?: number
  ocr_total?: number
  ocr_done?: number
  catalog?: LiveCatalog
}

interface ShelfAnnouncement {
  id: number
  title: string
  detail: string
}

interface BookPop {
  popId: number
  key: string
  title: string
  cover?: string
  matchLabel: string
}

const MAX_SCAN_UPLOAD_BYTES = 100 * 1024 * 1024
const SUPPORTED_SCAN_EXTENSIONS = new Set(['.jpg', '.jpeg', '.png', '.webp', '.mp4', '.mov', '.webm'])

interface ActiveSession {
  id: string
  template: string
  controller: AbortController
  uploadFailure?: string
}

export default function ScanPage() {
  const location = useLocation()
  const scanNavigation = useMemo(() => parseScanNavigationState(location.state), [location.state])
  const targetBook = scanNavigation?.targetBook ?? null
  const [mode, setMode] = useState<'upload' | 'camera'>('camera')
  const [file, setFile] = useState<File | null>(null)
  const [filePreview, setFilePreview] = useState('')
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')
  const [progress, setProgress] = useState(0)

  const videoRef = useRef<HTMLVideoElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [recording, setRecording] = useState(false)
  const [startingRecording, setStartingRecording] = useState(false)
  const [cameraStarting, setCameraStarting] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [finalizeRetryAvailable, setFinalizeRetryAvailable] = useState(false)
  const [liveJobId, setLiveJobId] = useState('')
  const [completedJobId, setCompletedJobId] = useState('')
  const [liveJob, setLiveJob] = useState<LiveJobState | null>(null)
  const [lastQualityIssue, setLastQualityIssue] = useState<{ reason: 'blurred' | 'glare'; at: number } | null>(null)
  const [shelfAnnouncement, setShelfAnnouncement] = useState<ShelfAnnouncement | null>(null)
  const [bookPops, setBookPops] = useState<BookPop[]>([])
  const [cameraReady, setCameraReady] = useState(false)
  const [detecting, setDetecting] = useState(false)
  const [browserDetecting, setBrowserDetecting] = useState(false)
  const [opencvState, setOpencvState] = useState<'loading' | 'ready' | 'fallback'>('loading')
  const [liveStatus, setLiveStatus] = useState('カメラを開始すると、タグを連続で読み取ります。')
  const [detectedTags, setDetectedTags] = useState<LiveDetectedTag[]>([])
  const [currentShelfLabel, setCurrentShelfLabel] = useState('棚を探しています')
  const [shelfEvents, setShelfEvents] = useState<ShelfEvent[]>([])
  const [trackedTags, setTrackedTags] = useState<TrackedTag[]>([])
  const [activeTagIds, setActiveTagIds] = useState<number[]>([])
  const [shelfTagMap, setShelfTagMap] = useState<ShelfTagMap>(embeddedShelfMap as ShelfTagMap)
  const shelfTagMapRef = useRef<ShelfTagMap>({})
  const streamRef = useRef<MediaStream | null>(null)
  const cameraRequestRef = useRef(0)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const browserCanvasRef = useRef<HTMLCanvasElement>(null)
  const detectTimerRef = useRef<number | null>(null)
  const browserDetectTimerRef = useRef<number | null>(null)
  const detectingRef = useRef(false)
  const browserDetectingRef = useRef(false)
  const browserDetectionActiveRef = useRef(false)
  const trackingEnabledRef = useRef(false)
  const stableTagTrackerRef = useRef(new StableTagTracker(2))
  const announcedTagSetsRef = useRef(new Set<string>())
  const announcedBookKeysRef = useRef(new Set<string>())
  const bookPopIdRef = useRef(0)
  const scanCanvasRef = useRef<HTMLCanvasElement>(null)
  const captureCanvasRef = useRef<HTMLCanvasElement>(null)
  const scanAnimationRef = useRef<number | null>(null)
  const scanGateRef = useRef<FrameGateState>({ lastSentAt: 0 })
  const sessionRef = useRef<ActiveSession | null>(null)
  const sessionGenerationRef = useRef(0)
  const recordingStartInFlightRef = useRef(false)
  const recordingStartAbortRef = useRef<AbortController | null>(null)
  const uploadAbortRef = useRef<AbortController | null>(null)
  const uploadGenerationRef = useRef(0)
  const frameNumberRef = useRef(0)
  const lastEvaluationRef = useRef(0)
  const pendingFrameEncodesRef = useRef<Set<Promise<void>>>(new Set())
  const pendingUploadsRef = useRef<Set<Promise<void>>>(new Set())
  const [scanStats, setScanStats] = useState({ evaluated: 0, sent: 0, skipped: {} as Partial<Record<FrameSkipReason, number>> })
  const navigate = useNavigate()
  const navigateToJob = (jobId: string) => {
    const path = `/jobs/${jobId}`
    if (scanNavigation) navigate(path, { state: scanNavigation })
    else navigate(path)
  }
  const goBack = () => {
    uploadGenerationRef.current += 1
    uploadAbortRef.current?.abort()
    uploadAbortRef.current = null
    if (scanNavigation?.returnTo) {
      navigate(scanNavigation.returnTo)
      return
    }
    if (window.history.state?.idx > 0) navigate(-1)
    else navigate('/')
  }

  useEffect(() => {
    if (!shelfAnnouncement) return
    const timer = window.setTimeout(() => setShelfAnnouncement(current => current?.id === shelfAnnouncement.id ? null : current), 2800)
    return () => window.clearTimeout(timer)
  }, [shelfAnnouncement])

  useEffect(() => {
    if (!liveJobId) return
    let cancelled = false
    let timer: number | null = null
    const poll = async () => {
      let finished = false
      try {
        const response = await apiFetch(`/api/jobs/${liveJobId}`)
        if (response.ok) {
          const data = await response.json() as LiveJobState
          if (cancelled) return
          setLiveJob(data)
          finished = ['done', 'failed', 'no_detection', 'no_readable_crops'].includes(data.status)
        }
      } catch {
        // The local backend creates its batch job only at completion. AWS uses
        // the session ID immediately, so a local 404 while recording is normal.
      } finally {
        if (!cancelled && !finished) timer = window.setTimeout(() => void poll(), 650)
      }
    }
    void poll()
    return () => {
      cancelled = true
      if (timer != null) window.clearTimeout(timer)
    }
  }, [liveJobId])

  useEffect(() => {
    if (!file?.type.startsWith('image/')) {
      setFilePreview('')
      return
    }
    const preview = URL.createObjectURL(file)
    setFilePreview(preview)
    return () => URL.revokeObjectURL(preview)
  }, [file])

  useEffect(() => {
    shelfTagMapRef.current = shelfTagMap
  }, [shelfTagMap])

  const trackDetectedTags = useCallback((tagIds: number[]) => {
    const normalized = [...new Set(tagIds)].sort((a, b) => a - b)
    setActiveTagIds(normalized)

    if (normalized.length === 0) {
      stableTagTrackerRef.current.update([])
      return
    }
    // The frame gate shares the animation-loop clock, so record the sighting
    // with performance.now() rather than Date.now().
    scanGateRef.current.lastTagSeenAt = performance.now()

    const now = Date.now()
    const activePrimary = shelfTagMapRef.current.tags?.[String(normalized[0])]
    const activeLabel = activePrimary?.unit
      ? `${activePrimary.unit}（tag ${normalized.join(', ')}）`
      : `tag ${normalized.join(', ')}`
    setCurrentShelfLabel(activeLabel)
    setTrackedTags(previous => {
      const byId = new Map(previous.map(tag => [tag.tagId, tag]))
      for (const tagId of normalized) {
        const existing = byId.get(tagId)
        const unit = shelfTagMapRef.current.tags?.[String(tagId)]?.unit
        byId.set(tagId, {
          tagId,
          label: unit ?? `tag ${tagId}`,
          firstSeenAt: existing?.firstSeenAt ?? now,
          lastSeenAt: now,
        })
      }
      return [...byId.values()].sort((a, b) => b.lastSeenAt - a.lastSeenAt)
    })

    const event = stableTagTrackerRef.current.update(normalized)
    if (!event) return

    const primary = shelfTagMapRef.current.tags?.[String(event.tagIds[0])]
    const label = primary?.unit
      ? `${primary.unit}（tag ${event.tagIds.join(', ')}）`
      : `tag ${event.tagIds.join(', ')}`
    const isNewShelf = !announcedTagSetsRef.current.has(event.key)
    setCurrentShelfLabel(label)

    if (isNewShelf) {
      announcedTagSetsRef.current.add(event.key)
      const time = new Date().toLocaleTimeString('ja-JP', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
      setShelfEvents(events => [{ id: event.key, label, time }, ...events].slice(0, 6))
      setLiveStatus(`新しい棚のタグを確認しました。${label}の本を読み取っています。`)
      setShelfAnnouncement({
        id: now,
        title: '新しい棚を検知しました！',
        detail: `${label}のタグを確認しました。本の読み取り結果を処理しています。`,
      })
    } else {
      setLiveStatus(`認識済みの棚を追跡中です。${label}`)
    }
  }, [])

  const detectFrame = useCallback(async () => {
    const sessionId = sessionRef.current?.id
    if (!trackingEnabledRef.current || !sessionId || document.hidden) return
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
      if (!trackingEnabledRef.current || sessionRef.current?.id !== sessionId) return
      if (data.map_id && data.map_id !== shelfTagMapRef.current.map_id) {
        throw new Error(`配置データの版が一致しません: ${data.map_id}`)
      }
      const tags = data.tags ?? []
      setDetectedTags(tags)
      const usable = tags.find(tag => tag.mapped && tag.orientation_status !== 'mismatch')
      if (trackingEnabledRef.current && sessionRef.current?.id === sessionId && !browserDetectionActiveRef.current) {
        if (usable) {
          setLiveStatus(`tag ${usable.tag_id} を認識中。${usable.placement ?? '棚の位置を確認できます。'}`)
        } else if (tags.length > 0) {
          setLiveStatus(`tag ${tags.map(tag => tag.tag_id).join(', ')} を検出しました。向きか配置表を確認してください。`)
        } else {
          setLiveStatus('タグを探しています。棚のタグを画面中央に写してください。')
        }
        trackDetectedTags(tags
          .filter(tag => tag.mapped && tag.orientation_status !== 'mismatch')
          .map(tag => tag.tag_id))
      }
    } catch (detectionError) {
      if (trackingEnabledRef.current && sessionRef.current?.id === sessionId && !browserDetectionActiveRef.current) {
        setLiveStatus(`ライブ検出に失敗しました: ${detectionError instanceof Error ? detectionError.message : String(detectionError)}`)
      }
    } finally {
      detectingRef.current = false
      setDetecting(false)
    }
  }, [trackDetectedTags])

  const detectBrowserFrame = useCallback(async () => {
    const sessionId = sessionRef.current?.id
    if (!trackingEnabledRef.current || !sessionId || document.hidden) return
    if (browserDetectingRef.current) return
    const video = videoRef.current
    const canvas = browserCanvasRef.current
    if (!video || !canvas || video.videoWidth === 0 || video.videoHeight === 0) return

    browserDetectingRef.current = true
    setBrowserDetecting(true)
    try {
      const width = Math.min(1280, video.videoWidth)
      canvas.width = width
      canvas.height = Math.round((width / video.videoWidth) * video.videoHeight)
      const context = canvas.getContext('2d')
      if (!context) return
      context.drawImage(video, 0, 0, canvas.width, canvas.height)
      const tags = await detectBrowserAprilTags(canvas)
      if (!trackingEnabledRef.current || sessionRef.current?.id !== sessionId) return
      setOpencvState('ready')
      browserDetectionActiveRef.current = true
      if (tags.length > 0) {
        setLiveStatus(`tag ${tags.map(tag => tag.tagId).join(', ')} を追跡しています…`)
      } else {
        setLiveStatus('次の棚を探しています。カメラをゆっくり動かしてください。')
      }
      trackDetectedTags(tags.map(tag => tag.tagId))

      // Browser detection is usable on its own. The backend remains an
      // optional authoritative orientation/quadrant/shelf assignment pass.
    } catch (browserError) {
      if (!trackingEnabledRef.current || sessionRef.current?.id !== sessionId) return
      setOpencvState('fallback')
      browserDetectionActiveRef.current = false
      const detail = browserError instanceof Error ? browserError.message : String(browserError)
      setLiveStatus(`ブラウザ検知を利用できないため、サーバー検知を使用中です。${detail}`)
      console.warn('browser AprilTag detection failed', browserError)
    } finally {
      browserDetectingRef.current = false
      setBrowserDetecting(false)
    }
  }, [detectFrame, trackDetectedTags])

  const stopTagDetection = useCallback(() => {
    trackingEnabledRef.current = false
    if (detectTimerRef.current != null) {
      window.clearInterval(detectTimerRef.current)
      detectTimerRef.current = null
    }
    if (browserDetectTimerRef.current != null) {
      window.clearInterval(browserDetectTimerRef.current)
      browserDetectTimerRef.current = null
    }
    setActiveTagIds([])
    browserDetectionActiveRef.current = false
  }, [])

  const stopCamera = useCallback(() => {
    cameraRequestRef.current += 1
    sessionGenerationRef.current += 1
    recordingStartAbortRef.current?.abort()
    recordingStartAbortRef.current = null
    recordingStartInFlightRef.current = false
    uploadGenerationRef.current += 1
    uploadAbortRef.current?.abort()
    uploadAbortRef.current = null
    stopTagDetection()
    streamRef.current?.getTracks().forEach(track => track.stop())
    streamRef.current = null
    if (videoRef.current) videoRef.current.srcObject = null
    if (scanAnimationRef.current != null) cancelAnimationFrame(scanAnimationRef.current)
    scanAnimationRef.current = null
    stableTagTrackerRef.current.reset()
    const abandoned = sessionRef.current
    sessionRef.current = null
    abandoned?.controller.abort()
    if (abandoned) {
      void apiFetch(`/api/scan/sessions/${abandoned.id}/cancel`, { method: 'POST' }).catch(() => undefined)
    }
    setRecording(false)
    setStartingRecording(false)
    setFinalizeRetryAvailable(false)
    setStopping(false)
    setUploading(false)
    setCameraReady(false)
  }, [stopTagDetection])

  const startCamera = useCallback(async (): Promise<boolean> => {
    const requestId = cameraRequestRef.current + 1
    cameraRequestRef.current = requestId
    setCameraStarting(true)
    try {
      setError('')
      setLiveStatus('カメラを起動しています…')
      if (!navigator.mediaDevices?.getUserMedia) {
        throw new Error('このブラウザではカメラを利用できません。')
      }
      const stream = await navigator.mediaDevices.getUserMedia({
        video: {
          facingMode: { ideal: 'environment' },
          width: { ideal: 1920 },
          height: { ideal: 1080 },
        },
        audio: false,
      })
      if (requestId !== cameraRequestRef.current) {
        stream.getTracks().forEach(track => track.stop())
        return false
      }

      streamRef.current = stream
      if (videoRef.current) {
        videoRef.current.srcObject = stream
        await videoRef.current.play()
        if (requestId !== cameraRequestRef.current) return false
        setCameraReady(true)
      }
      setCurrentShelfLabel('スキャン待機中')
      setLiveStatus('中央のボタンを押すと、棚の連続読み取りを開始します。')
      return true
    } catch (cameraError) {
      if (requestId !== cameraRequestRef.current) return false
      setError(cameraAccessErrorMessage(cameraError))
      stopCamera()
      return false
    } finally {
      if (requestId === cameraRequestRef.current) setCameraStarting(false)
    }
  }, [stopCamera])

  useEffect(() => {
    if (mode !== 'camera') {
      stopCamera()
      return
    }

    setCurrentShelfLabel('カメラを開始してください')
    setLiveStatus('中央のボタンを押すと、カメラ許可を確認してスキャンを開始します。')
    return stopCamera
  }, [mode, stopCamera])

  const stopFrameLoop = () => {
    if (scanAnimationRef.current != null) cancelAnimationFrame(scanAnimationRef.current)
    scanAnimationRef.current = null
  }

  const isCurrentSession = (session: ActiveSession): boolean => (
    sessionRef.current?.id === session.id && !session.controller.signal.aborted
  )

  const startRecording = async () => {
    if (!videoRef.current?.srcObject || recording || stopping || recordingStartInFlightRef.current) return
    const generation = ++sessionGenerationRef.current
    const requestAbort = new AbortController()
    recordingStartAbortRef.current = requestAbort
    recordingStartInFlightRef.current = true
    setStartingRecording(true)
    try {
      const res = await apiFetch('/api/scan/sessions', { method: 'POST', signal: requestAbort.signal })
      if (!res.ok) throw new Error(await res.text())
      const session = await res.json() as { session_id: string; frame_upload_url_template?: string; frame_upload_url_endpoint?: string }
      if (generation !== sessionGenerationRef.current || requestAbort.signal.aborted || !videoRef.current?.srcObject) {
        void apiFetch(`/api/scan/sessions/${session.session_id}/cancel`, { method: 'POST' }).catch(() => undefined)
        return
      }
      const activeSession: ActiveSession = {
        id: session.session_id,
        template: session.frame_upload_url_template ?? session.frame_upload_url_endpoint ?? '',
        controller: new AbortController(),
      }
      sessionRef.current = activeSession
      setLiveJobId(session.session_id)
      setCompletedJobId('')
      setLiveJob(null)
      setStopping(false)
      setFinalizeRetryAvailable(false)
      setLastQualityIssue(null)
      frameNumberRef.current = 0; lastEvaluationRef.current = 0
      scanGateRef.current = { lastSentAt: 0 }
      setScanStats({ evaluated: 0, sent: 0, skipped: {} })
      stableTagTrackerRef.current.reset()
      announcedTagSetsRef.current.clear()
      announcedBookKeysRef.current.clear()
      setBookPops([])
      setTrackedTags([])
      setActiveTagIds([])
      setShelfEvents([])
      setCurrentShelfLabel('棚を探しています')
      setLiveStatus('タグを探しています。カメラをゆっくり動かしてください。')
      setOpencvState('loading')
      trackingEnabledRef.current = true
      setRecording(true)
      void isBrowserAprilTagReady().catch(() => setOpencvState('fallback'))
      // Recording, browser/WASM tag detection, and server fallback run as
      // independent loops after the user explicitly starts the scan.
      void detectFrame()
      void detectBrowserFrame()
      detectTimerRef.current = window.setInterval(() => void detectFrame(), 1600)
      browserDetectTimerRef.current = window.setInterval(() => void detectBrowserFrame(), 650)
      const loop = (now: number) => {
        if (!isCurrentSession(activeSession)) return
        if (document.hidden) {
          scanAnimationRef.current = requestAnimationFrame(loop)
          return
        }
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
              if (reason) {
                setScanStats(s => ({ ...s, evaluated: s.evaluated + 1, skipped: { ...s.skipped, [reason]: (s.skipped[reason] ?? 0) + 1 } }))
                if (reason === 'blurred' || reason === 'glare') setLastQualityIssue({ reason, at: Date.now() })
              }
              else {
                // Keep the comparison baseline at the last frame accepted for
                // upload. Recording a rate-limited no-tag frame here would
                // make an unchanged shelf stay skipped forever.
                scanGateRef.current.previous = evaluated.sample
                scanGateRef.current.lastSentAt = now
                const frame = `frame_${String(++frameNumberRef.current).padStart(6, '0')}.jpg`
                const capture = captureCanvasRef.current
                if (!capture) return
                const captureWidth = Math.min(1280, video.videoWidth)
                capture.width = captureWidth
                capture.height = Math.round(video.videoHeight * captureWidth / video.videoWidth)
                capture.getContext('2d')?.drawImage(video, 0, 0, capture.width, capture.height)
                const frameEncoding = new Promise<void>(resolve => {
                  capture.toBlob(blob => {
                    if (!blob || !isCurrentSession(activeSession)) {
                      if (!blob && isCurrentSession(activeSession)) activeSession.uploadFailure = '画像を生成できませんでした'
                      resolve()
                      return
                    }
                    const upload = (async () => {
                      if (!isCurrentSession(activeSession)) return
                      let url = activeSession.template.replace('{frame_id}', frame)
                      let frameKey = frame
                      if (!activeSession.template.includes('{frame_id}')) {
                        const init = await apiFetch(activeSession.template, {
                          method: 'POST',
                          headers: { 'Content-Type': 'application/json' },
                          body: JSON.stringify({ filename: frame }),
                          signal: activeSession.controller.signal,
                        })
                        if (!init.ok) throw new Error(`frame init ${init.status}`)
                        const initialized = await init.json() as { upload_url: string; frame_key?: string }
                        url = initialized.upload_url
                        frameKey = initialized.frame_key ?? frame
                      }
                      if (!isCurrentSession(activeSession)) return
                      const putController = new AbortController()
                      const abortPut = () => putController.abort()
                      const putTimeout = window.setTimeout(() => putController.abort(), 30_000)
                      activeSession.controller.signal.addEventListener('abort', abortPut, { once: true })
                      let put: Response
                      try {
                        put = await fetch(apiUrl(url), {
                          method: 'PUT',
                          headers: { 'Content-Type': 'image/jpeg' },
                          body: blob,
                          signal: putController.signal,
                        })
                      } finally {
                        window.clearTimeout(putTimeout)
                        activeSession.controller.signal.removeEventListener('abort', abortPut)
                      }
                      if (!put.ok) throw new Error(`frame upload ${put.status}`)
                      if (!isCurrentSession(activeSession)) return
                      const commit = await apiFetch(`/api/scan/sessions/${activeSession.id}/commit-frame`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ frame_key: frameKey }),
                        signal: activeSession.controller.signal,
                      })
                      if (!commit.ok) throw new Error(`frame commit ${commit.status}`)
                      if (isCurrentSession(activeSession)) setScanStats(s => ({ ...s, evaluated: s.evaluated + 1, sent: s.sent + 1 }))
                    })()
                    pendingUploadsRef.current.add(upload)
                    void upload.then(
                      () => pendingUploadsRef.current.delete(upload),
                      e => {
                        pendingUploadsRef.current.delete(upload)
                        activeSession.uploadFailure = String(e)
                        if (isCurrentSession(activeSession)) setError(`候補フレームの保存に失敗しました: ${String(e)}`)
                      },
                    )
                    resolve()
                  }, 'image/jpeg', 0.82)
                })
                pendingFrameEncodesRef.current.add(frameEncoding)
                void frameEncoding.then(() => pendingFrameEncodesRef.current.delete(frameEncoding))
              }
            }
          }
        }
        scanAnimationRef.current = requestAnimationFrame(loop)
      }
      scanAnimationRef.current = requestAnimationFrame(loop)
    } catch (e) {
      if (!requestAbort.signal.aborted && generation === sessionGenerationRef.current) {
        setError(`ライブスキャンを開始できませんでした: ${String(e)}`)
      }
    } finally {
      if (generation === sessionGenerationRef.current) {
        recordingStartInFlightRef.current = false
        recordingStartAbortRef.current = null
        setStartingRecording(false)
      }
    }
  }

  const stopRecording = async () => {
    const session = sessionRef.current
    if (!session || stopping || startingRecording) return
    stopFrameLoop()
    stopTagDetection()
    setRecording(false)
    setStopping(true)
    try {
      // A canvas.toBlob callback can run after requestAnimationFrame was
      // cancelled. Wait for those callbacks before completing the session so
      // their uploads cannot arrive after finalize.
      await Promise.all([...pendingFrameEncodesRef.current])
      await Promise.all([...pendingUploadsRef.current])
      if (!isCurrentSession(session)) return
      if (session.uploadFailure) throw new Error(`候補フレームの保存に失敗しています: ${session.uploadFailure}`)
      const res = await apiFetch(`/api/scan/sessions/${session.id}/complete`, { method: 'POST' })
      if (!res.ok) throw new Error(await res.text())
      const data = await res.json() as { job_id: string }
      if (!isCurrentSession(session)) return
      sessionRef.current = null
      session.controller.abort()
      sessionGenerationRef.current += 1
      setLiveJobId(data.job_id)
      setCompletedJobId(data.job_id)
      setFinalizeRetryAvailable(false)
      setLiveStatus('録画を終了しました。リザルトから認識結果を確認できます。')
      setStopping(false)
      navigateToJob(data.job_id)
    } catch (e) {
      if (isCurrentSession(session)) {
        setRecording(false)
        setFinalizeRetryAvailable(true)
        setError(session.uploadFailure
          ? '保存できなかったフレームがあります。キャンセルして撮り直してください。'
          : `スキャン確定に失敗しました。もう一度確定するか、キャンセルしてください: ${String(e)}`)
      }
    } finally {
      if (isCurrentSession(session)) setStopping(false)
    }
  }

  const cancelRecording = async () => {
    const session = sessionRef.current
    sessionGenerationRef.current += 1
    recordingStartAbortRef.current?.abort()
    recordingStartAbortRef.current = null
    recordingStartInFlightRef.current = false
    stopFrameLoop()
    stopTagDetection()
    setRecording(false)
    setStartingRecording(false)
    setStopping(false)
    setFinalizeRetryAvailable(false)
    setError('')
    sessionRef.current = null
    session?.controller.abort()
    stableTagTrackerRef.current.reset()
    announcedTagSetsRef.current.clear()
    announcedBookKeysRef.current.clear()
    setBookPops([])
    setTrackedTags([])
    setShelfEvents([])
    setCurrentShelfLabel('スキャン待機中')
    setLiveStatus('中央のボタンを押すと、棚の連続読み取りを開始します。')
    setLiveJobId('')
    setLiveJob(null)
    setCompletedJobId('')
    if (session) {
      try {
        await apiFetch(`/api/scan/sessions/${session.id}/cancel`, { method: 'POST' })
      } catch {
        // Cancellation is best effort after local state has already been reset.
      }
    }
  }

  const submit = async () => {
    if (!file) return
    const generation = ++uploadGenerationRef.current
    const controller = new AbortController()
    uploadAbortRef.current = controller
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
        signal: controller.signal,
      })
      if (!initRes.ok) throw new Error(`init ${initRes.status}: ${await initRes.text()}`)
      const init = (await initRes.json()) as { job_id: string; upload_url: string; content_type: string }

      // 2. S3 へ直接 PUT（XHR で進捗付き）
      await putWithProgress(init.upload_url, file, init.content_type, setProgress, controller.signal)

      // 3. 処理開始を通知
      const startRes = await apiFetch('/api/scan/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_id: init.job_id }),
        signal: controller.signal,
      })
      if (!startRes.ok) throw new Error(`start ${startRes.status}: ${await startRes.text()}`)

      if (generation !== uploadGenerationRef.current || controller.signal.aborted) return
      navigateToJob(init.job_id)
    } catch (e) {
      if (!controller.signal.aborted && generation === uploadGenerationRef.current) {
        setError(`アップロードに失敗しました: ${e instanceof Error ? e.message : String(e)}`)
      }
    } finally {
      if (generation === uploadGenerationRef.current) {
        uploadAbortRef.current = null
        setUploading(false)
      }
    }
  }

  const chooseFile = (selected: File | null) => {
    if (uploading || stopping || cameraStarting || startingRecording) return
    if (!selected) return
    const validationError = validateScanFile(selected)
    if (validationError) {
      setFile(null)
      setFilePreview('')
      setError(validationError)
      setProgress(0)
      return
    }
    setError('')
    setProgress(0)
    setFile(selected)
    setMode('upload')
  }

  const returnToCamera = () => {
    if (uploading || stopping || cameraStarting || startingRecording) return
    setFile(null)
    setFilePreview('')
    setError('')
    setProgress(0)
    setMode('camera')
  }

  const primaryAction = async () => {
    if (mode === 'upload') {
      void submit()
    } else if (completedJobId) {
      navigateToJob(completedJobId)
    } else if (finalizeRetryAvailable) {
      void stopRecording()
    } else if (recording) {
      void stopRecording()
    } else if (!cameraReady) {
      const started = await startCamera()
      if (started) await startRecording()
    } else {
      void startRecording()
    }
  }

  const liveBooks = useMemo(() => {
    const books: Array<{ key: string; title: string; cover?: string; shelf?: string | null; matchLabel: string; definitive: boolean }> = []
    const byKey = new Map<string, typeof books[number]>()
    for (const entry of liveJob?.catalog?.entries ?? []) {
      for (const book of entry.books ?? []) {
        const candidate = book.book_lookup?.candidates?.[0]
        const title = candidate?.title || book.title
        if (!title) continue
        const libraryDbId = positiveLibraryDbId(candidate?.library_db_id) ? candidate?.library_db_id : null
        const definitive = candidate?.match_confidence === 'auto' && libraryDbId != null
        const key = libraryDbId != null
          ? `id:${libraryDbId}`
          : `title:${title.trim().toLocaleLowerCase('ja-JP')}`
        const next = {
          key,
          title,
          cover: candidate?.thumbnail || fallbackCoverForTitle(title),
          shelf: entry.shelf_id,
          matchLabel: definitive ? '自動照合' : candidate ? '照合候補・要確認' : '未照合',
          definitive,
        }
        const previous = byKey.get(key)
        if (!previous || (!previous.definitive && definitive)) byKey.set(key, next)
      }
    }
    return [...byKey.values()]
  }, [liveJob])

  const definitiveLiveBookCount = liveBooks.filter(book => book.definitive).length
  const targetMatchState = useMemo<ScanTargetMatchState>(() => scanTargetMatchState(targetBook, liveBooks), [liveBooks, targetBook])
  const targetMatchLabel = targetMatchState === 'confirmed'
    ? '対象本を自動照合しました'
    : targetMatchState === 'candidate'
      ? '対象本の候補を認識しました（要確認）'
      : '対象本を探しています'

  // Each newly identified book pops up once with its cover and title, so the
  // operator can see what the scan actually recognized while still filming.
  useEffect(() => {
    const fresh = liveBooks.filter(book => !announcedBookKeysRef.current.has(book.key))
    if (fresh.length === 0) return
    for (const book of fresh) announcedBookKeysRef.current.add(book.key)
    const pops = fresh.slice(-3).map(book => ({ ...book, popId: ++bookPopIdRef.current }))
    setBookPops(current => [...current, ...pops].slice(-3))
    // The timer is intentionally not cleared on re-run: a later detection must
    // not keep an already visible pop on screen forever.
    window.setTimeout(() => {
      const expired = new Set(pops.map(pop => pop.popId))
      setBookPops(current => current.filter(pop => !expired.has(pop.popId)))
    }, 3400)
  }, [liveBooks])

  const processedFrames = Number(liveJob?.processed_frames ?? 0)
  const cropTotal = Number(liveJob?.crop_total ?? 0)
  const ocrTotal = Number(liveJob?.ocr_total ?? 0)
  const ocrDone = Number(liveJob?.ocr_done ?? 0)
  const recentQualityIssue = lastQualityIssue && Date.now() - lastQualityIssue.at < 1800 ? lastQualityIssue.reason : null
  const statusLines = mode === 'upload'
    ? [file?.name ?? '画像または動画を選択', file ? 'このファイルを解析します' : '右下のボタンから選択できます']
    : shelfAnnouncement
      ? [shelfAnnouncement.title, shelfAnnouncement.detail]
      : completedJobId
        ? ['録画を終了しました', 'リザルトから認識結果を確認できます。']
        : recentQualityIssue === 'blurred'
          ? ['画像がぶれています', '1秒止めてください。']
          : recentQualityIssue === 'glare'
            ? ['光が反射しています', '角度を変えてください。']
            : cropTotal > 0 && ocrDone < ocrTotal
              ? ['本を検出しました', 'タイトルを照合中…']
              : processedFrames >= 3 && cropTotal === 0
                ? ['背表紙が小さいようです', '少し近づいてください。']
                : activeTagIds.length > 0
                  ? [currentShelfLabel, '本棚を確認しています…']
                  : recording && trackedTags.length > 0
                    ? ['次の棚を探しています', `${trackedTags.length}個のタグを検出しています。映像を解析中です。`]
                    : recording
                      ? ['棚のタグを画面に入れてください', 'タグが見えると棚を特定できます。']
                      : [detecting || browserDetecting ? '棚を検知しています…' : currentShelfLabel, liveStatus]
  const visibleTrackedTags = trackedTags.slice(0, 4)

  return (
    <div className="min-h-screen bg-[#1e1e1e] md:grid md:place-items-center">
      <div className="relative h-[100dvh] min-h-[100dvh] min-[640px]:min-h-[674px] w-full overflow-hidden bg-[#1e1e1e] p-0">
        <div className="relative h-full w-full overflow-hidden bg-[#292929]">
          {mode === 'camera' ? (
            <>
              <video
                ref={videoRef}
                // object-contain, not cover: what the operator frames must be
                // exactly what gets uploaded and analyzed.
                className="absolute inset-0 size-full object-contain"
                autoPlay
                muted
                playsInline
                onLoadedData={() => setCameraReady(true)}
              />
              {!cameraReady && !error && (
                <div className="absolute inset-0 grid place-items-center bg-black/45 text-center text-sm text-white/85">
                  <p>{cameraStarting ? 'カメラを起動しています…' : '中央のボタンを押してカメラを開始してください'}</p>
                </div>
              )}
            </>
          ) : filePreview ? (
            <img src={filePreview} alt="選択した本棚" className="absolute inset-0 size-full object-cover" />
          ) : (
            <div className="absolute inset-0 grid place-items-center bg-[radial-gradient(circle_at_center,#414141,#1e1e1e)] px-10 text-center text-sm text-white/60">
              {file ? file.name : '本棚を画面に収めてください'}
            </div>
          )}

          <div className="absolute inset-0 bg-gradient-to-b from-black/20 via-transparent to-black/25" />

          <button type="button" onClick={goBack} aria-label="戻る" className="tap-soft absolute left-7 top-6 z-10 grid size-10 place-items-center rounded-full bg-white text-[#1e1e1e]">
            <ArrowLeft className="size-6" />
          </button>

          {mode === 'upload' && (
            <button
              type="button"
              onClick={returnToCamera}
              disabled={uploading || stopping || cameraStarting || startingRecording}
              className="tap-soft absolute right-7 top-6 z-10 inline-flex min-h-10 items-center gap-2 rounded-full bg-white px-3 text-xs font-semibold text-[#1e1e1e] shadow-sm disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Camera className="size-4" />
              カメラに戻る（選択解除）
            </button>
          )}

          <div
            key={shelfAnnouncement?.id ?? `guide-${statusLines[0]}`}
            className={`absolute bottom-[204px] left-1/2 z-20 flex min-h-[63px] w-[calc(100%-56px)] max-w-[560px] -translate-x-1/2 flex-col justify-center rounded-xl bg-[#1e1e1e] px-4 py-3 text-base leading-[19px] text-white shadow-[20px_8px_0_rgba(30,30,30,0.83)] ${shelfAnnouncement ? 'scan-status-popup' : ''}`}
            aria-live={shelfAnnouncement ? 'polite' : 'off'}
          >
            <p>{statusLines[0]}</p>
            <p className="text-white/90">{statusLines[1]}</p>
          </div>

          {error && (
            <div className="absolute left-1/2 top-[104px] z-10 w-[300px] -translate-x-1/2 rounded-xl bg-red-700/90 px-4 py-3 text-sm text-white">
              {error}
            </div>
          )}

          {uploading && (
            <div className="absolute inset-x-7 bottom-[164px] z-10 overflow-hidden rounded-full bg-white/35">
              <div className="h-2 bg-[#087f5b] transition-all" style={{ width: `${Math.max(8, progress)}%` }} />
            </div>
          )}

          {mode === 'camera' && liveBooks.length > 0 && (
            <p className="absolute right-7 top-6 z-10 rounded-full bg-black/60 px-3 py-2 text-xs font-semibold text-white backdrop-blur-md">
              {definitiveLiveBookCount > 0
                ? `自動照合 ${definitiveLiveBookCount}冊`
                : `照合候補 ${liveBooks.length}冊`}
            </p>
          )}

          {targetBook && (
            <div
              role="status"
              aria-live="polite"
              data-testid="scan-target-status"
              className="absolute right-7 top-[62px] z-10 max-w-[250px] rounded-xl bg-white/95 px-3 py-2 text-xs text-[#1e1e1e] shadow-lg backdrop-blur-md"
            >
              <p className="font-semibold">探している本</p>
              <p className="mt-0.5 line-clamp-2">{targetBook.title}</p>
              {targetBook.shelfId && <p className="mt-0.5 text-[10px] text-[#087f5b]">棚候補: {targetBook.shelfId}</p>}
              <p className={`mt-1 font-semibold ${targetMatchState === 'confirmed' ? 'text-[#087f5b]' : 'text-ink-muted'}`}>
                {targetMatchLabel}
              </p>
            </div>
          )}

          {mode === 'camera' && bookPops.length > 0 && (
            <div className="pointer-events-none absolute inset-x-7 bottom-[286px] z-20 mx-auto flex max-w-[560px] flex-col items-end gap-2" aria-label="認識した本">
              {bookPops.map(pop => (
                <article key={pop.popId} className="scan-book-pop flex w-[196px] items-center gap-3 rounded-2xl bg-white/95 p-2 shadow-xl">
                  <div className="grid h-[72px] w-[52px] shrink-0 place-items-center overflow-hidden rounded-md bg-[#e9e9e9]">
                    {pop.cover
                      ? <img src={pop.cover} alt="" className="max-h-full max-w-full object-contain" />
                      : <BookMarked className="size-6 text-[#087f5b]" />}
                  </div>
                  <div className="min-w-0">
                    <p className="line-clamp-3 text-xs leading-4 text-[#1e1e1e]">{pop.title}</p>
                    <p className="mt-1 text-[10px] leading-3 text-[#087f5b]">{pop.matchLabel}</p>
                  </div>
                </article>
              ))}
            </div>
          )}

          {mode === 'camera' && trackedTags.length > 0 && bookPops.length === 0 && (
            <div className="absolute inset-x-7 bottom-[286px] z-10 mx-auto max-w-[560px]" aria-live="off">
              <div className="mb-2 flex items-center justify-between text-xs font-medium text-white drop-shadow">
                <span>認識済みタグ</span>
                <span>{trackedTags.length}件</span>
              </div>
              <div className="flex flex-wrap gap-2">
                {visibleTrackedTags.map(tag => {
                  const active = activeTagIds.includes(tag.tagId)
                  return (
                    <span
                      key={tag.tagId}
                      className={`max-w-[180px] truncate rounded-full border px-3 py-1.5 text-xs font-semibold shadow-sm backdrop-blur-md transition-colors ${active ? 'border-[#7ee2bd] bg-[#087f5b] text-white' : 'border-white/45 bg-black/55 text-white/90'}`}
                    >
                      {active ? '追跡中' : '認識済み'} · {tag.label}
                    </span>
                  )
                })}
                {trackedTags.length > visibleTrackedTags.length && (
                  <span className="rounded-full border border-white/35 bg-black/55 px-3 py-1.5 text-xs font-semibold text-white/90 backdrop-blur-md">
                    +{trackedTags.length - visibleTrackedTags.length}
                  </span>
                )}
              </div>
            </div>
          )}

          <div className={`absolute bottom-16 left-1/2 flex -translate-x-1/2 items-center ${recording || stopping || completedJobId ? 'w-auto justify-center' : 'w-[300px] justify-end gap-12'}`}>
            <button
              type="button"
              onClick={() => void primaryAction()}
              disabled={(mode === 'upload' && !file) || uploading || stopping || cameraStarting || startingRecording}
              aria-label={completedJobId ? 'リザルトを見る' : mode === 'upload' ? '選択したファイルを解析' : finalizeRetryAvailable ? 'スキャンを確定して再試行' : recording ? 'スキャンを終了' : startingRecording ? 'スキャンを開始しています' : cameraReady ? 'ライブスキャンを開始' : 'カメラを開始してライブスキャンを開始'}
              className={`tap-card grid place-items-center disabled:opacity-50 ${completedJobId ? 'h-16 min-w-[196px] rounded-full bg-[#087f5b] px-8 text-xl font-semibold text-white shadow-xl' : 'size-[100px]'}`}
            >
              {completedJobId ? 'リザルト' : (
                <span
                  className={`grid place-items-center transition-all duration-200 ease-out ${recording || stopping ? 'size-16 rounded-[20px] bg-[#ff3b30] shadow-lg' : 'size-[90px] rounded-full bg-white'}`}
                >
                  {finalizeRetryAvailable ? (
                    <span className="text-sm font-semibold text-[#1e1e1e]">再確定</span>
                  ) : mode === 'upload' && !recording ? (
                    <span className="text-sm font-semibold text-[#1e1e1e]">解析</span>
                  ) : recording || stopping ? (
                    <span className="block size-6 rounded-[5px] bg-white" aria-label="スキャンを終了" />
                  ) : null}
                </span>
              )}
            </button>

            {!recording && !completedJobId && (
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={uploading || stopping || cameraStarting || startingRecording}
                aria-label="画像または動画を選択"
                className="tap-card flex h-[61px] w-[58px] flex-col items-center justify-center gap-1 rounded-xl bg-white text-[#1e1e1e] shadow-sm disabled:cursor-not-allowed disabled:opacity-50"
              >
                <ImagePlus className="size-5" />
                <span className="text-[10px] font-semibold leading-none">選択</span>
              </button>
            )}
          </div>

          {(recording || finalizeRetryAvailable) && !stopping && (
            <button type="button" onClick={() => void cancelRecording()} className="tap-soft absolute bottom-6 left-1/2 -translate-x-1/2 text-xs text-white/75 underline">
              キャンセル
            </button>
          )}

          <input
            ref={fileInputRef}
            type="file"
            accept=".jpg,.jpeg,.png,.webp,.mp4,.mov,.webm"
            className="hidden"
            onChange={event => {
              chooseFile(event.target.files?.[0] ?? null)
              event.currentTarget.value = ''
            }}
          />
          <canvas ref={canvasRef} className="hidden" />
          <canvas ref={browserCanvasRef} className="hidden" />
          <canvas ref={scanCanvasRef} className="hidden" />
          <canvas ref={captureCanvasRef} className="hidden" />

          <div className="sr-only" aria-live="off">
            Webタグ検知: {opencvState}。評価 {scanStats.evaluated}、送信 {scanStats.sent}。検出タグ {detectedTags.map(tag => tag.tag_id).join(', ')}
          </div>
        </div>
      </div>
    </div>
  )
}

function positiveLibraryDbId(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value > 0
}

function validateScanFile(file: File): string | null {
  if (file.size > MAX_SCAN_UPLOAD_BYTES) {
    return 'ファイルが大きすぎます。100MB以下の画像または動画を選択してください。'
  }
  const extension = file.name.slice(file.name.lastIndexOf('.')).toLowerCase()
  const supportedType = file.type.startsWith('image/') || file.type.startsWith('video/')
  if (!supportedType && !SUPPORTED_SCAN_EXTENSIONS.has(extension)) {
    return '対応していない形式です。JPG、PNG、WebP、MP4、MOV、WebMを選択してください。'
  }
  if (!SUPPORTED_SCAN_EXTENSIONS.has(extension) && file.type !== '') {
    return '対応していない形式です。JPG、PNG、WebP、MP4、MOV、WebMを選択してください。'
  }
  return null
}

function putWithProgress(
  url: string,
  file: File,
  contentType: string,
  onProgress: (pct: number) => void,
  signal?: AbortSignal,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    const abort = () => xhr.abort()
    const cleanup = () => signal?.removeEventListener('abort', abort)
    if (signal?.aborted) {
      reject(new DOMException('Upload aborted', 'AbortError'))
      return
    }
    xhr.open('PUT', url)
    xhr.setRequestHeader('Content-Type', contentType)
    xhr.timeout = 30_000
    xhr.upload.onprogress = e => {
      if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100))
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        onProgress(100)
        cleanup()
        resolve()
      } else {
        cleanup()
        reject(new Error(`S3 PUT ${xhr.status}: ${xhr.responseText.slice(0, 200)}`))
      }
    }
    xhr.onerror = () => { cleanup(); reject(new Error('S3 PUT network error')) }
    xhr.ontimeout = () => { cleanup(); reject(new Error('S3 PUT timeout')) }
    xhr.onabort = () => { cleanup(); reject(new DOMException('Upload aborted', 'AbortError')) }
    signal?.addEventListener('abort', abort, { once: true })
    xhr.send(file)
  })
}
