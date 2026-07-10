import { useCallback, useEffect, useState, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiFetch } from '../lib/api'

interface LiveDetectedTag {
  tag_id: number
  mapped: boolean
  orientation_status: 'ok' | 'mismatch' | 'unchecked'
  placement?: string
}

interface LiveDetectResponse {
  tags: LiveDetectedTag[]
}

export default function ScanPage() {
  const [mode, setMode] = useState<'upload' | 'camera'>('upload')
  const [file, setFile] = useState<File | null>(null)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')
  const [progress, setProgress] = useState(0)

  const videoRef = useRef<HTMLVideoElement>(null)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const [recording, setRecording] = useState(false)
  const [detecting, setDetecting] = useState(false)
  const [liveStatus, setLiveStatus] = useState('カメラを開始すると、タグを連続で読み取ります。')
  const [detectedTags, setDetectedTags] = useState<LiveDetectedTag[]>([])
  const streamRef = useRef<MediaStream | null>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const detectTimerRef = useRef<number | null>(null)
  const detectingRef = useRef(false)
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

  const stopCamera = useCallback(() => {
    if (detectTimerRef.current != null) {
      window.clearInterval(detectTimerRef.current)
      detectTimerRef.current = null
    }
    streamRef.current?.getTracks().forEach(track => track.stop())
    streamRef.current = null
    if (videoRef.current) videoRef.current.srcObject = null
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
      setLiveStatus('タグを探しています。棚のタグを画面中央に写してください。')
      await detectFrame()
      detectTimerRef.current = window.setInterval(() => void detectFrame(), 1600)
    } catch {
      setError('カメラにアクセスできませんでした。')
      stopCamera()
    }
  }, [detectFrame, stopCamera])

  useEffect(() => {
    if (mode !== 'camera') {
      stopCamera()
      return
    }
    void startCamera()
    return stopCamera
  }, [mode, startCamera, stopCamera])

  const startRecording = () => {
    const stream = videoRef.current?.srcObject as MediaStream
    if (!stream) return
    chunksRef.current = []
    const recorder = new MediaRecorder(stream)
    recorder.ondataavailable = e => { if (e.data.size > 0) chunksRef.current.push(e.data) }
    recorder.onstop = () => {
      const blob = new Blob(chunksRef.current, { type: 'video/webm' })
      setFile(new File([blob], `scan_${Date.now()}.webm`, { type: 'video/webm' }))
      setRecording(false)
    }
    recorderRef.current = recorder
    recorder.start()
    setRecording(true)
  }

  const stopRecording = () => { recorderRef.current?.stop() }

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
          <div className="flex gap-3 p-4 justify-center">
            {!recording ? (
              <button onClick={startRecording} className="px-6 py-2 bg-red-600 text-white rounded-lg font-semibold">
                ● 録画開始
              </button>
            ) : (
              <button onClick={stopRecording} className="px-6 py-2 bg-gray-700 text-white rounded-lg font-semibold animate-pulse">
                ■ 録画停止
              </button>
            )}
          </div>
          {file && (
            <p className="text-center text-sm text-green-400 pb-4">
              録画完了: {(file.size / 1024).toFixed(0)} KB
            </p>
          )}
          <div className="px-4 pb-4 text-center">
            <p className="text-sm text-white">{detecting ? 'タグを読み取り中…' : liveStatus}</p>
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
