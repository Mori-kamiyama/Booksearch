import { useState, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiFetch } from '../lib/api'

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
  const navigate = useNavigate()

  const startCamera = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } })
      if (videoRef.current) {
        videoRef.current.srcObject = stream
        videoRef.current.play()
      }
    } catch {
      setError('カメラにアクセスできませんでした。')
    }
  }

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
            onClick={() => { setMode(m); setFile(null); if (m === 'camera') startCamera() }}
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
