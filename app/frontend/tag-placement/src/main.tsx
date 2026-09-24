import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { Camera, CameraOff, ChevronLeft, ChevronRight, MapPin, RotateCcw } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import './style.css'

type Quadrant = 'top_left' | 'top_right' | 'bottom_right' | 'bottom_left'

interface TagConfig {
  unit?: string
  physical_intersection?: {
    between_display_cols?: number[]
    between_rows?: number[]
  }
  expected_angle_deg?: number
  angle_tolerance_deg?: number
  quadrants: Partial<Record<Quadrant, string>>
}

interface ShelfMap {
  map_id?: string
  dictionary?: string
  tags?: Record<string, TagConfig>
}

interface DetectedTag {
  tag_id: number
  mapped: boolean
  orientation_status: 'ok' | 'mismatch' | 'unchecked'
  angle_deg: number
  angle_delta_deg: number | null
  placement: string
}

interface DetectResponse {
  map_id?: string
  tags: DetectedTag[]
}

const quadrantLabels: Record<Quadrant, string> = {
  top_left: '左上',
  top_right: '右上',
  bottom_right: '右下',
  bottom_left: '左下',
}

const quadrantOrder: Quadrant[] = ['top_left', 'top_right', 'bottom_left', 'bottom_right']

function App() {
  const [shelfMap, setShelfMap] = useState<ShelfMap | null>(null)
  const [selectedTag, setSelectedTag] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [cameraActive, setCameraActive] = useState(false)
  const [detecting, setDetecting] = useState(false)
  const [cameraError, setCameraError] = useState('')
  const [detectStatus, setDetectStatus] = useState('カメラを開始すると自動でタグを読み取ります。')
  const [detectedTags, setDetectedTags] = useState<DetectedTag[]>([])

  const videoRef = useRef<HTMLVideoElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const intervalRef = useRef<number | null>(null)
  const detectingRef = useRef(false)

  useEffect(() => {
    fetch('/apriltag_library_map.json')
      .then(async res => {
        if (!res.ok) throw new Error(`map ${res.status}`)
        return (await res.json()) as ShelfMap
      })
      .then(data => {
        setShelfMap(data)
        setSelectedTag(Object.keys(data.tags ?? {}).sort(compareTagID)[0] ?? '')
      })
      .catch(() => setError('タグ配置データを読み込めませんでした。'))
      .finally(() => setLoading(false))
  }, [])

  const tagIDs = useMemo(() => Object.keys(shelfMap?.tags ?? {}).sort(compareTagID), [shelfMap])
  const tag = selectedTag ? shelfMap?.tags?.[selectedTag] : undefined
  const placement = tag ? describePlacement(tag) : ''

  const detectFrame = useCallback(async () => {
    if (detectingRef.current) return
    const video = videoRef.current
    const canvas = canvasRef.current
    if (!video || !canvas || video.videoWidth === 0 || video.videoHeight === 0) return

    detectingRef.current = true
    setDetecting(true)
    setCameraError('')
    try {
      const width = Math.min(960, video.videoWidth)
      const height = Math.round((width / video.videoWidth) * video.videoHeight)
      canvas.width = width
      canvas.height = height
      const ctx = canvas.getContext('2d')
      if (!ctx) throw new Error('canvas is not available')
      ctx.drawImage(video, 0, 0, width, height)

      const blob = await new Promise<Blob | null>(resolve => canvas.toBlob(resolve, 'image/jpeg', 0.86))
      if (!blob) throw new Error('frame capture failed')

      const form = new FormData()
      form.append('image', blob, 'camera-frame.jpg')
      const res = await fetch('/api/tags/detect', { method: 'POST', body: form })
      if (!res.ok) throw new Error(await res.text())
      const data = (await res.json()) as DetectResponse
      if (data.map_id && data.map_id !== shelfMap?.map_id) {
        throw new Error(`配置データの版が一致しません: ${data.map_id}`)
      }
      setDetectedTags(data.tags ?? [])

      const usable = (data.tags ?? []).find(t => t.mapped && t.orientation_status !== 'mismatch' && tagIDs.includes(String(t.tag_id)))
      if (usable) {
        setSelectedTag(String(usable.tag_id))
        setDetectStatus(`tag ${usable.tag_id} を認識しました。`)
      } else if ((data.tags ?? []).length > 0) {
        const ids = data.tags.map(t => t.tag_id).join(', ')
        setDetectStatus(`tag ${ids} を見つけましたが、未登録か向きが違います。`)
      } else {
        setDetectStatus('タグを探しています。画面中央に大きく写してください。')
      }
    } catch (e) {
      setCameraError(e instanceof Error ? e.message : String(e))
    } finally {
      detectingRef.current = false
      setDetecting(false)
    }
  }, [shelfMap?.map_id, tagIDs])

  const stopCamera = useCallback(() => {
    if (intervalRef.current != null) {
      window.clearInterval(intervalRef.current)
      intervalRef.current = null
    }
    streamRef.current?.getTracks().forEach(track => track.stop())
    streamRef.current = null
    if (videoRef.current) videoRef.current.srcObject = null
    setCameraActive(false)
    setDetecting(false)
  }, [])

  const startCamera = useCallback(async () => {
    try {
      setCameraError('')
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'environment', width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: false,
      })
      streamRef.current = stream
      if (videoRef.current) {
        videoRef.current.srcObject = stream
        await videoRef.current.play()
      }
      setCameraActive(true)
      setDetectStatus('タグを探しています。画面中央に大きく写してください。')
      await detectFrame()
      intervalRef.current = window.setInterval(() => void detectFrame(), 1600)
    } catch {
      setCameraError('カメラにアクセスできませんでした。ブラウザの権限を確認してください。')
      stopCamera()
    }
  }, [detectFrame, stopCamera])

  useEffect(() => stopCamera, [stopCamera])

  const moveTag = (delta: number) => {
    if (tagIDs.length === 0) return
    const index = Math.max(0, tagIDs.indexOf(selectedTag))
    setSelectedTag(tagIDs[(index + delta + tagIDs.length) % tagIDs.length])
  }

  return (
    <main>
      <header className="topbar">
        <div>
          <p className="brand">ホンノキ</p>
          <h1>タグ貼り付けガイド</h1>
        </div>
        <div className="tagPicker">
          <button type="button" onClick={() => moveTag(-1)} disabled={tagIDs.length < 2} aria-label="前のタグ">
            <ChevronLeft size={20} />
          </button>
          <select value={selectedTag} onChange={e => setSelectedTag(e.target.value)} aria-label="タグID">
            {tagIDs.map(id => (
              <option key={id} value={id}>tag {id}</option>
            ))}
          </select>
          <button type="button" onClick={() => moveTag(1)} disabled={tagIDs.length < 2} aria-label="次のタグ">
            <ChevronRight size={20} />
          </button>
        </div>
      </header>

      {loading ? (
        <p className="empty">読み込み中...</p>
      ) : error ? (
        <p className="error">{error}</p>
      ) : tag ? (
        <div className="layout">
          <section className="placementPanel">
            <div className="selected">
              <div>
                <p className="label">選択中</p>
                <p className="tagTitle">tag {selectedTag}</p>
              </div>
              <div className="tagBadge">{selectedTag}</div>
            </div>

            <div className="placement">
              <MapPin size={22} />
              <div>
                <p>{placement}</p>
                <span>タグの上側が棚の上段側、下側が棚の下段側になる向きで貼ってください。</span>
              </div>
            </div>

            <div className="quadrants">
              {quadrantOrder.map(q => (
                <div key={q}>
                  <span>{quadrantLabels[q]}</span>
                  <strong>{tag.quadrants[q] ?? 'なし'}</strong>
                </div>
              ))}
            </div>
          </section>

          <aside>
            <section className="sidePanel">
              <div className="panelHead">
                <span><Camera size={18} />カメラ認識</span>
                {!cameraActive ? (
                  <button type="button" className="primary" onClick={startCamera}>開始</button>
                ) : (
                  <button type="button" onClick={stopCamera}>停止</button>
                )}
              </div>
              <div className="videoBox">
                {cameraActive ? (
                  <video ref={videoRef} muted playsInline />
                ) : (
                  <CameraOff size={30} />
                )}
              </div>
              <canvas ref={canvasRef} hidden />
              <p className="status">{detecting ? '読み取り中...' : detectStatus}</p>
              {cameraError && <p className="error small">{cameraError}</p>}
              {detectedTags.length > 0 && (
                <div className="detectedTags">
                  {detectedTags.map(t => (
                    <span key={`${t.tag_id}:${t.orientation_status}`} className={t.orientation_status === 'mismatch' ? 'warn' : ''}>
                      tag {t.tag_id}
                    </span>
                  ))}
                </div>
              )}
            </section>

            <section className="sidePanel">
              <div className="sideTitle"><RotateCcw size={18} />向き</div>
              <p className="status">
                期待角度 {tag.expected_angle_deg ?? '-'} 度
                {typeof tag.angle_tolerance_deg === 'number' ? ` / 許容 ${tag.angle_tolerance_deg} 度` : ''}
              </p>
            </section>
          </aside>
        </div>
      ) : (
        <p className="empty">タグ配置データはまだありません。</p>
      )}
    </main>
  )
}

function compareTagID(a: string, b: string) {
  return Number(a) - Number(b)
}

function describePlacement(tag: TagConfig) {
  const shelves = quadrantOrder
    .map(q => tag.quadrants[q])
    .filter((shelf): shelf is string => Boolean(shelf))

  const uniqueShelves = Array.from(new Set(shelves))
  if (uniqueShelves.length === 0) return 'このタグに対応する棚は未登録です。'
  if (uniqueShelves.length === 1) return `${uniqueShelves[0]} の近くに貼る`
  return `${uniqueShelves.join(' / ')} の交点に貼る`
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
