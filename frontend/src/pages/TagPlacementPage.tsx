import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Camera, CameraOff, ChevronLeft, ChevronRight, MapPin, RotateCcw } from 'lucide-react'
import { apiFetch } from '../lib/api'
import embeddedShelfMap from '../../../data/apriltag_library_map.json'

type Quadrant = 'top_left' | 'top_right' | 'bottom_right' | 'bottom_left'

interface TagConfig {
  expected_angle_deg?: number
  angle_tolerance_deg?: number
  quadrants: Partial<Record<Quadrant, string>>
}

interface ShelfMap {
  map_id?: string
  dictionary?: string
  tags?: Record<string, TagConfig>
}

const canonicalShelfMap = embeddedShelfMap as ShelfMap

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
  diagnostics?: {
    selected?: string
    raw_ids?: number[]
  }
}

const quadrantLabels: Record<Quadrant, string> = {
  top_left: '左上',
  top_right: '右上',
  bottom_right: '右下',
  bottom_left: '左下',
}

const quadrantOrder: Quadrant[] = ['top_left', 'top_right', 'bottom_left', 'bottom_right']

export default function TagPlacementPage() {
  const [selectedTag, setSelectedTag] = useState(Object.keys(canonicalShelfMap.tags ?? {}).sort(compareTagID)[0] ?? '')
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

  const tagIDs = useMemo(() => Object.keys(canonicalShelfMap.tags ?? {}).sort(compareTagID), [])
  const tag = selectedTag ? canonicalShelfMap.tags?.[selectedTag] : undefined
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
      const res = await apiFetch('/api/tags/detect', { method: 'POST', body: form })
      if (!res.ok) throw new Error(await res.text())
      const data = (await res.json()) as DetectResponse
      if (data.map_id && data.map_id !== canonicalShelfMap.map_id) {
        throw new Error(`配置データの版が一致しません: ${data.map_id}`)
      }
      setDetectedTags(data.tags ?? [])

      const usable = (data.tags ?? []).find(t => t.mapped && t.orientation_status !== 'mismatch' && tagIDs.includes(String(t.tag_id)))
      if (usable) {
        setSelectedTag(String(usable.tag_id))
        setDetectStatus(`tag ${usable.tag_id} を認識しました。`)
      } else if ((data.tags ?? []).length > 0) {
        const ids = data.tags.map(t => t.tag_id).join(', ')
        setDetectStatus(`tag ${ids} を見つけましたが、この配置表では未登録か向きが違います。`)
      } else {
        setDetectStatus('タグを探しています。画面中央に大きく写してください。')
      }
    } catch (e) {
      setCameraError(e instanceof Error ? e.message : String(e))
    } finally {
      detectingRef.current = false
      setDetecting(false)
    }
  }, [tagIDs])

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
    const next = (index + delta + tagIDs.length) % tagIDs.length
    setSelectedTag(tagIDs[next])
  }

  return (
    <div className="grid gap-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="text-2xl font-bold text-gray-800">タグ貼り付けガイド</h2>
          <p className="text-sm text-gray-500 mt-1">AprilTagの番号から、棚のどこに貼るか確認できます。</p>
        </div>
        <div className="inline-flex rounded-lg border border-gray-200 bg-white p-1 self-start">
          <button
            type="button"
            onClick={() => moveTag(-1)}
            disabled={tagIDs.length < 2}
            className="h-10 w-10 grid place-items-center rounded-md text-gray-600 hover:bg-gray-100 disabled:opacity-35"
            title="前のタグ"
            aria-label="前のタグ"
          >
            <ChevronLeft size={20} />
          </button>
          <select
            value={selectedTag}
            onChange={e => setSelectedTag(e.target.value)}
            className="h-10 min-w-28 rounded-md px-3 text-center font-bold text-gray-800 outline-none"
            aria-label="タグID"
          >
            {tagIDs.map(id => (
              <option key={id} value={id}>tag {id}</option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => moveTag(1)}
            disabled={tagIDs.length < 2}
            className="h-10 w-10 grid place-items-center rounded-md text-gray-600 hover:bg-gray-100 disabled:opacity-35"
            title="次のタグ"
            aria-label="次のタグ"
          >
            <ChevronRight size={20} />
          </button>
        </div>
      </div>

      {tag ? (
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
          <section className="bg-white border border-gray-200 rounded-lg overflow-hidden">
            <div className="border-b border-gray-100 px-5 py-4 flex items-center justify-between gap-3">
              <div>
                <p className="text-sm text-gray-500">選択中</p>
                <p className="text-4xl font-black text-[#1f7a5c] leading-tight">tag {selectedTag}</p>
              </div>
              <div className="h-14 w-14 rounded-lg border-2 border-[#1f7a5c] grid place-items-center text-lg font-black text-[#1f7a5c]">
                {selectedTag}
              </div>
            </div>

            <div className="p-5">
              <div className="mb-5 rounded-lg bg-[#eff8f4] border border-[#c8eadc] px-4 py-3">
                <div className="flex items-start gap-3">
                  <MapPin className="mt-0.5 text-[#1f7a5c] shrink-0" size={22} />
                  <div>
                    <p className="font-bold text-gray-800">{placement}</p>
                    <p className="text-sm text-gray-600 mt-1">
                      タグの上側が棚の上段側、下側が棚の下段側になる向きで貼ってください。
                    </p>
                  </div>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                {quadrantOrder.map(q => (
                  <div key={q} className="min-h-28 rounded-lg border border-gray-200 bg-gray-50 p-4">
                    <p className="text-xs font-bold text-gray-400">{quadrantLabels[q]}</p>
                    <p className="mt-2 text-lg font-bold text-gray-800 break-words">
                      {tag.quadrants[q] ?? 'なし'}
                    </p>
                  </div>
                ))}
              </div>
            </div>
          </section>

          <aside className="grid gap-4 content-start">
            <section className="bg-white border border-gray-200 rounded-lg p-5">
              <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-2 text-gray-800 font-bold">
                  <Camera size={18} />
                  カメラ認識
                </div>
                {!cameraActive ? (
                  <button
                    type="button"
                    onClick={startCamera}
                    className="h-9 px-3 rounded-md bg-[#1f7a5c] text-white text-sm font-bold hover:bg-[#196649]"
                  >
                    開始
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={stopCamera}
                    className="h-9 px-3 rounded-md border border-gray-300 text-gray-700 text-sm font-bold hover:bg-gray-50"
                  >
                    停止
                  </button>
                )}
              </div>

              <div className="mt-4 overflow-hidden rounded-lg bg-black aspect-video">
                {cameraActive ? (
                  <video ref={videoRef} className="h-full w-full object-cover" muted playsInline />
                ) : (
                  <div className="h-full w-full grid place-items-center text-gray-400">
                    <CameraOff size={28} />
                  </div>
                )}
              </div>
              <canvas ref={canvasRef} className="hidden" />

              <p className="mt-3 text-sm text-gray-600">
                {detecting ? '読み取り中...' : detectStatus}
              </p>
              {cameraError && (
                <p className="mt-2 text-xs text-red-600 break-words">{cameraError}</p>
              )}
              {detectedTags.length > 0 && (
                <div className="mt-3 flex flex-wrap gap-2">
                  {detectedTags.map(t => (
                    <span
                      key={`${t.tag_id}:${t.orientation_status}`}
                      className={`text-xs font-bold px-2 py-1 rounded ${
                        t.orientation_status === 'mismatch'
                          ? 'bg-orange-50 text-orange-700'
                          : 'bg-green-50 text-green-700'
                      }`}
                    >
                      tag {t.tag_id}
                    </span>
                  ))}
                </div>
              )}
            </section>

            <section className="bg-white border border-gray-200 rounded-lg p-5">
              <div className="flex items-center gap-2 text-gray-800 font-bold">
                <RotateCcw size={18} />
                向き
              </div>
              <p className="mt-3 text-sm text-gray-600">
                期待角度 {tag.expected_angle_deg ?? '-'} 度
                {typeof tag.angle_tolerance_deg === 'number' ? ` / 許容 ${tag.angle_tolerance_deg} 度` : ''}
              </p>
            </section>

            <section className="bg-white border border-gray-200 rounded-lg p-5">
              <div className="flex items-center gap-2 text-gray-800 font-bold">
                <Camera size={18} />
                現場チェック
              </div>
              <div className="mt-3 grid grid-cols-3 gap-2">
                {tagIDs.map(id => (
                  <button
                    key={id}
                    type="button"
                    onClick={() => setSelectedTag(id)}
                    className={`h-12 rounded-md border text-sm font-bold transition-colors ${
                      selectedTag === id
                        ? 'border-[#1f7a5c] bg-[#1f7a5c] text-white'
                        : 'border-gray-200 bg-white text-gray-700 hover:bg-gray-50'
                    }`}
                  >
                    {id}
                  </button>
                ))}
              </div>
            </section>
          </aside>
        </div>
      ) : (
        <div className="text-center text-gray-400 py-12">タグ配置データはまだありません。</div>
      )}
    </div>
  )
}

function compareTagID(a: string, b: string) {
  return Number(a) - Number(b)
}

function describePlacement(tag: TagConfig) {
  const shelves = quadrantOrder
    .map(q => tag.quadrants[q])
    .filter((shelf): shelf is string => Boolean(shelf))

  if (shelves.length === 0) return 'このタグに対応する棚は未登録です。'
  if (shelves.length === 1) return `${shelves[0]} の近くに貼る`

  const uniqueShelves = Array.from(new Set(shelves))
  return `${uniqueShelves.join(' / ')} の交点に貼る`
}
