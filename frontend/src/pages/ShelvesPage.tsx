import { useEffect, useState } from 'react'
import { apiFetch } from '../lib/api'

interface ShelfCandidate {
  book_id: number
  shelf_id: string
  confidence: number
  observations: number
  avg_score: number
  title?: string
  updated_at?: string
}

export default function ShelvesPage() {
  const [candidates, setCandidates] = useState<ShelfCandidate[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    apiFetch('/api/shelf-candidates')
      .then(res => res.json())
      .then(data => setCandidates(data.candidates ?? []))
      .catch(() => setCandidates([]))
      .finally(() => setLoading(false))
  }, [])

  const grouped = candidates.reduce<Record<string, ShelfCandidate[]>>((acc, c) => {
    ;(acc[c.shelf_id] ??= []).push(c)
    return acc
  }, {})

  return (
    <div>
      <h2 className="text-2xl font-bold text-gray-800 mb-6">本の場所候補</h2>
      {loading ? (
        <div className="text-center text-gray-500 py-12">読み込み中…</div>
      ) : Object.keys(grouped).length === 0 ? (
        <div className="text-center text-gray-400 py-12">場所データはまだありません。</div>
      ) : (
        <div className="grid gap-6">
          {Object.entries(grouped).sort().map(([shelfID, rows]) => (
            <section key={shelfID} className="bg-white border border-gray-200 rounded-xl overflow-hidden">
              <div className="px-5 py-3 bg-gray-50 border-b border-gray-100 font-bold text-gray-800">
                {shelfID}
              </div>
              <div className="divide-y divide-gray-100">
                {rows
                  .sort((a, b) => b.confidence - a.confidence)
                  .map(row => (
                    <div key={`${row.book_id}:${row.shelf_id}`} className="px-5 py-3 flex items-center gap-4">
                      <div className="flex-1 min-w-0">
                        <p className="font-semibold text-gray-800 truncate">{row.title || `book_id ${row.book_id}`}</p>
                        <p className="text-xs text-gray-500">
                          book_id {row.book_id} / avg {row.avg_score.toFixed(2)} / {row.observations}回
                        </p>
                      </div>
                      <span className="text-sm font-bold text-[#1f7a5c]">
                        {Math.round(row.confidence * 100)}%
                      </span>
                    </div>
                  ))}
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  )
}
