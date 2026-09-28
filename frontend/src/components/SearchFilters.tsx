import { useId, useRef, useState } from 'react'
import { SlidersHorizontal, X } from 'lucide-react'
import { isGenre, isTheme, isGenreId, type DiscoveryIndex } from '../lib/discoveryIndex'

type Draft = { genre: string; topic: string; min_pages: string; max_pages: string; level: string }
const empty: Draft = { genre: '', topic: '', min_pages: '', max_pages: '', level: '' }

export function SearchFilters({ filters, discovery, onApply, summary }: {
  filters: string
  summary?: string
  discovery: DiscoveryIndex | null
  onApply: (filters: string) => void
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const heading = useId()
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState<Draft>(empty)
  const [error, setError] = useState('')
  const [showAllThemes, setShowAllThemes] = useState(false)
  const themesId = useId()
  const current = new URLSearchParams(filters)
  const topic = current.get('topic') ?? ''
  const selection: Draft = {
    genre: current.get('genre') ?? (isGenreId(topic) ? topic : ''),
    topic: isGenreId(topic) ? '' : topic,
    min_pages: current.get('min_pages') ?? '', max_pages: current.get('max_pages') ?? '', level: current.get('level') ?? '',
  }
  const active = Object.entries(selection).filter(([, value]) => value)
  const genres = discovery?.topics.filter(isGenre) ?? []
  const themes = discovery?.topics.filter(isTheme) ?? []
  const availableThemes = themes.filter(topic => !draft.genre || !topic.genres || topic.genres.includes(draft.genre))
  const initialThemes = availableThemes.slice(0, 6)
  const visibleThemes = showAllThemes ? availableThemes : availableThemes.filter(topic => initialThemes.includes(topic) || topic.id === draft.topic)
  const hiddenThemes = availableThemes.length - visibleThemes.length
  const titleFor = (key: string, value: string) => {
    if (key === 'genre' || key === 'topic') return discovery?.topics.find(topic => topic.id === value)?.label ?? value
    if (key === 'min_pages') return `${value}ページ以上`
    if (key === 'max_pages') return `${value}ページ以下`
    return ({ beginner: '入門', intermediate: '中級', advanced: '専門' })[value] ?? value
  }
  const encode = (value: Draft) => new URLSearchParams(Object.entries(value).filter(([, text]) => text)).toString()
  const close = () => { dialog.current?.close(); setOpen(false); trigger.current?.focus() }
  const change = (key: keyof Draft, value: string) => { setDraft(previous => ({ ...previous, [key]: value })); setError('') }

  return (
    <div className="mt-3 flex flex-wrap items-center gap-2 md:mt-5">
      <button ref={trigger} type="button" aria-haspopup="dialog" aria-expanded={open}
        onClick={() => { setDraft(selection); setShowAllThemes(false); setError(''); dialog.current?.showModal(); setOpen(true) }}
        className={`inline-flex min-h-11 items-center gap-2 rounded-full border px-4 text-sm transition ${active.length ? 'border-primary bg-primary-soft text-primary' : 'border-line bg-white text-ink hover:bg-zinc-50'}`}>
        <SlidersHorizontal className="size-4" />フィルター
        {active.length > 0 && <span className="grid size-5 place-items-center rounded-full bg-primary text-xs text-white">{active.length}</span>}
      </button>
      {summary && <p className="ml-auto text-right text-xs leading-5 text-ink-muted md:text-sm">{summary}</p>}
      {active.map(([key, value]) => <button key={key} type="button" aria-label={`${titleFor(key, value)}を解除`}
        onClick={() => onApply(encode({ ...selection, [key]: '' }))}
        className="inline-flex min-h-9 max-w-full items-center gap-2 rounded-full bg-zinc-100 px-3 text-xs text-ink">
        <span className="truncate">{titleFor(key, value)}</span><X className="size-3 shrink-0" />
      </button>)}
      <dialog ref={dialog} aria-labelledby={heading}
        onCancel={event => { event.preventDefault(); close() }}
        onClose={() => { setOpen(false); trigger.current?.focus() }}
        onClick={event => { if (event.target === event.currentTarget) { const r = event.currentTarget.getBoundingClientRect(); if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) close() } }}
        className="fixed inset-0 m-auto max-h-[85dvh] w-[calc(100%_-_2rem)] max-w-[460px] overflow-hidden rounded-3xl border border-line bg-white p-0 text-ink shadow-2xl backdrop:bg-black/20">
        <form className="flex max-h-[85dvh] flex-col" onSubmit={event => {
          event.preventDefault()
          if (draft.min_pages && draft.max_pages && Number(draft.min_pages) > Number(draft.max_pages)) { setError('ページ数の下限は上限以下にしてください。'); return }
          onApply(encode(draft)); close()
        }}>
          <div className="flex shrink-0 items-center justify-between border-b border-line px-6 py-4">
            <h2 id={heading} className="text-base font-semibold">フィルター</h2>
            <button type="button" autoFocus aria-label="フィルターを閉じる" onClick={close} className="grid size-10 place-items-center rounded-full hover:bg-zinc-100"><X className="size-5" /></button>
          </div>
          <div className="min-h-0 space-y-5 overflow-y-auto overscroll-contain px-6 py-5 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
            <label className="flex flex-col gap-2 text-sm font-medium">ジャンル
              <select value={draft.genre} onChange={event => {
                const genre = event.target.value
                setShowAllThemes(false)
                const selected = themes.find(topic => topic.id === draft.topic)
                setDraft(previous => ({ ...previous, genre, topic: genre && selected?.genres && !selected.genres.includes(genre) ? '' : previous.topic }))
              }} className="min-h-11 w-full rounded-xl border border-line bg-white px-3 font-normal">
                <option value="">すべてのジャンル</option>
                {draft.genre && !genres.some(topic => topic.id === draft.genre) && <option value={draft.genre}>{draft.genre}</option>}
                {genres.map(topic => <option key={topic.id} value={topic.id}>{topic.label}</option>)}
              </select>
            </label>
            <fieldset>
              <legend className="mb-2 text-sm font-medium">テーマ</legend>
              <p className="mb-3 text-xs text-ink-muted">学びたい内容や、気になるテーマで絞り込めます。</p>
              <div id={themesId} className="flex flex-wrap gap-2">
                <button type="button" aria-pressed={!draft.topic} onClick={() => change('topic', '')} className={`min-h-10 rounded-full border px-3 text-sm ${!draft.topic ? 'border-primary bg-primary-soft text-primary' : 'border-line'}`}>指定なし</button>
                {visibleThemes.map(topic => <button type="button" key={topic.id} aria-pressed={draft.topic === topic.id} onClick={() => change('topic', topic.id)}
                  className={`min-h-10 rounded-full border px-3 text-sm ${draft.topic === topic.id ? 'border-primary bg-primary-soft text-primary' : 'border-line hover:bg-zinc-50'}`}>{topic.label}</button>)}
                {draft.topic && !availableThemes.some(topic => topic.id === draft.topic) && <button type="button" aria-pressed="true" onClick={() => change('topic', '')} className="min-h-10 rounded-full border border-primary bg-primary-soft px-3 text-sm text-primary">{titleFor('topic', draft.topic)} ×</button>}
              </div>
              {(hiddenThemes > 0 || showAllThemes) && <button type="button" aria-expanded={showAllThemes} aria-controls={themesId}
                onClick={() => setShowAllThemes(value => !value)} className="mt-2 min-h-10 text-sm text-primary">
                {showAllThemes ? '表示を減らす' : `もっと表示する（あと${hiddenThemes}件）`}
              </button>}
              {!discovery && <p role="status" className="mt-2 text-xs text-ink-muted">ジャンル・テーマの候補を準備しています。</p>}
              {discovery && availableThemes.length === 0 && <p className="mt-2 text-xs text-ink-muted">このジャンルのテーマは準備中です。ジャンルだけでも検索できます。</p>}
            </fieldset>
            <details open={Boolean(draft.min_pages || draft.max_pages || draft.level) || undefined}>
              <summary className="min-h-10 cursor-pointer text-sm text-ink-muted">ページ数・レベル{discovery && !discovery.coverage.page_count && !discovery.coverage.level ? '（準備中）' : ''}</summary>
              <div className="mt-2 grid grid-cols-2 gap-3 text-sm">
                <label className="flex min-w-0 flex-col gap-2">ページ数（下限）<input type="number" min="1" max="100000" value={draft.min_pages} onChange={event => change('min_pages', event.target.value)} disabled={!discovery?.coverage.page_count && !selection.min_pages} className="min-h-11 w-full rounded-xl border border-line px-3 disabled:bg-zinc-100" /></label>
                <label className="flex min-w-0 flex-col gap-2">ページ数（上限）<input type="number" min="1" max="100000" value={draft.max_pages} onChange={event => change('max_pages', event.target.value)} disabled={!discovery?.coverage.page_count && !selection.max_pages} className="min-h-11 w-full rounded-xl border border-line px-3 disabled:bg-zinc-100" /></label>
                <label className="col-span-2 flex flex-col gap-2">レベル<select value={draft.level} onChange={event => change('level', event.target.value)} disabled={!discovery?.coverage.level && !selection.level} className="min-h-11 rounded-xl border border-line bg-white px-3 disabled:bg-zinc-100"><option value="">指定なし</option><option value="beginner">入門</option><option value="intermediate">中級</option><option value="advanced">専門</option></select></label>
              </div>
            </details>
            {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
          </div>
          <div className="flex shrink-0 justify-between gap-3 border-t border-line bg-white px-6 py-4">
            <button type="button" onClick={() => { setDraft(empty); setShowAllThemes(false); setError('') }} className="min-h-11 px-2 text-sm text-ink-muted">リセット</button>
            <button type="submit" className="min-h-11 rounded-full bg-primary px-6 text-sm font-medium text-white">適用する</button>
          </div>
        </form>
      </dialog>
    </div>
  )
}
