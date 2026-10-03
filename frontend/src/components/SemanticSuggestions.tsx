import { useEffect, useRef, useState } from 'react'
import { getSemanticAvailability, searchSemanticBooks } from '../lib/api'
import type { Book } from '../lib/types'
import { SearchResultCard } from './SearchResultCard'

// The parent keys this component by search/navigation identity. Old searches
// abort on unmount and cannot overwrite the next query's candidates.
export function SemanticSuggestions({ query, filters, exclude, automatic, onOpen, onPointerDown }: {
  query: string; filters: string; exclude: number[]; automatic: boolean
  onOpen: (book: Book) => void; onPointerDown: () => void
}) {
  const [available, setAvailable] = useState(false)
  const [requested, setRequested] = useState(automatic)
  const [attempt, setAttempt] = useState(0)
  const [status, setStatus] = useState<'idle' | 'loading' | 'done' | 'error'>('idle')
  const [books, setBooks] = useState<Book[]>([])
  const sequence = useRef(0)
  const excluded = exclude.join(',')

  useEffect(() => {
    const controller = new AbortController()
    getSemanticAvailability(controller.signal).then(value => {
      if (!controller.signal.aborted) setAvailable(value)
    }).catch(() => {})
    return () => controller.abort()
  }, [])

  useEffect(() => {
    if (!available || !requested) return
    const controller = new AbortController()
    const current = ++sequence.current
    let active = true
    const timer = setTimeout(() => controller.abort(), 9_000)
    setStatus('loading')
    setBooks([])
    const ids = excluded ? excluded.split(',').map(Number) : []
    searchSemanticBooks(query, filters, ids, controller.signal).then(results => {
      if (!active || current !== sequence.current) return
      const seen = new Set(ids)
      setBooks(results.filter(book => {
        if (seen.has(book.id)) return false
        seen.add(book.id)
        return true
      }).slice(0, 5))
      setStatus('done')
    }).catch(() => {
      if (active && current === sequence.current) setStatus('error')
    }).finally(() => clearTimeout(timer))
    return () => { active = false; clearTimeout(timer); controller.abort() }
  }, [available, requested, query, filters, excluded, attempt])

  if (!available) return null
  if (!requested) return (
    <div className="mt-8 flex justify-center pb-6">
      <button type="button" className="min-h-11 rounded-lg border border-line px-4 text-sm text-primary" onClick={() => setRequested(true)}>関連する本も探す</button>
    </div>
  )
  return (
    <section aria-label="意味の近い本" className="mt-8 border-t border-line pt-6 pb-8">
      <h2 className="text-base font-semibold text-ink">意味の近い本</h2>
      <p className="mt-2 text-xs leading-5 text-ink-muted">書名や紹介文の意味から選んだ候補です。検索語を含まない本もあります。</p>
      <div role="status" className="mt-3 text-sm text-ink-muted">
        {status === 'loading' && '意味の近い本を探しています…'}
        {status === 'done' && books.length === 0 && '表示できる候補はありませんでした。'}
        {status === 'error' && '関連する本を検索できませんでした。'}
      </div>
      {status === 'error' && <button type="button" className="mt-2 min-h-11 rounded-lg border border-line px-4 text-sm text-primary" onClick={() => setAttempt(value => value + 1)}>関連する本を再検索</button>}
      {books.length > 0 && <div className="mt-4 grid grid-cols-2 gap-x-2 gap-y-[19px] md:grid-cols-4 md:gap-x-[30px] md:gap-y-[30px] lg:grid-cols-5">
        {books.map(book => <SearchResultCard key={book.id} book={book} onPointerDown={onPointerDown} onOpen={() => onOpen(book)} />)}
      </div>}
    </section>
  )
}
