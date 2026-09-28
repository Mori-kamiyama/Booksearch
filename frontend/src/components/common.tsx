import { AlertCircle, ArrowLeft, BookOpen, Menu, Map, Search, Upload, X } from 'lucide-react'
import { Link, NavLink, useLocation, useNavigate } from 'react-router-dom'
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import type { KeyboardEvent, ReactNode } from 'react'

export function BrandMark({ className = '' }: { className?: string }) {
  return (
    <span className={`inline-flex items-center gap-3 ${className}`}>
      <img src="/figma-icons/leaf.svg" alt="" className="h-[26.5px] w-[48px]" />
      <img src="/figma-icons/wordmark.svg" alt="ホンノキ" className="h-[30px] w-[137px]" />
    </span>
  )
}

export function RakutenCredit({ className = '' }: { className?: string }) {
  return (
    <div className={className}>
      <a href="https://developers.rakuten.com/" target="_blank" rel="noreferrer">Supported by Rakuten Developers</a>
    </div>
  )
}

export function PageHeader({ title, back = false }: { title: string; back?: boolean }) {
  const navigate = useNavigate()
  const goBack = () => {
    if (typeof window.history.state?.idx === 'number' && window.history.state.idx > 0) navigate(-1)
    else navigate('/')
  }
  return (
    <div className="mb-5 flex min-h-11 items-center gap-3">
      {back && (
        <button
          type="button"
          aria-label="戻る"
          onClick={goBack}
          className="grid size-11 shrink-0 place-items-center rounded-lg border border-line bg-white text-ink-muted"
        >
          <ArrowLeft className="size-5" />
        </button>
      )}
      <h1 className="text-xl font-bold text-ink">{title}</h1>
    </div>
  )
}

export function EmptyState({
  icon,
  title,
  hint,
  action,
}: {
  icon?: ReactNode
  title: string
  hint: string
  action?: ReactNode
}) {
  return (
    <div className="rounded-xl border border-line bg-white p-6 text-center shadow-sm">
      <div className="mx-auto mb-3 grid size-11 place-items-center rounded-full bg-zinc-100 text-ink-muted">
        {icon ?? <BookOpen className="size-5" />}
      </div>
      <p className="font-semibold text-ink">{title}</p>
      <p className="mt-1 text-sm text-ink-muted">{hint}</p>
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}

export function NotFoundPage() {
  return (
    <div className="mx-auto w-full max-w-xl">
      <PageHeader title="ページが見つかりません" />
      <EmptyState
        title="お探しのページは見つかりませんでした"
        hint="URLを確認するか、ホームからもう一度お探しください。"
        action={(
          <Link to="/" className="inline-flex min-h-11 items-center justify-center rounded-lg bg-primary px-4 text-sm font-bold text-white">
            ホームへ戻る
          </Link>
        )}
      />
    </div>
  )
}

export function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="rounded-xl border border-red-100 bg-red-50 p-5 text-center text-red-700">
      <AlertCircle className="mx-auto mb-2 size-6" />
      <p className="text-sm font-semibold">{message}</p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-4 min-h-11 rounded-lg bg-red-600 px-4 text-sm font-semibold text-white"
      >
        再試行
      </button>
    </div>
  )
}

export function Skeleton({ variant }: { variant: 'card' | 'grid' | 'hero' }) {
  if (variant === 'grid') {
    return <div className="grid grid-cols-4 gap-3">{Array.from({ length: 8 }, (_, i) => <div key={i} className="h-24 animate-pulse rounded-xl bg-zinc-200" />)}</div>
  }
  if (variant === 'hero') {
    return <div className="h-64 animate-pulse rounded-xl bg-zinc-200" />
  }
  return (
    <div className="flex gap-3 rounded-xl border border-line bg-white p-4">
      <div className="h-20 w-14 animate-pulse rounded bg-zinc-200" />
      <div className="flex-1 space-y-3 py-1">
        <div className="h-4 w-4/5 animate-pulse rounded bg-zinc-200" />
        <div className="h-3 w-2/5 animate-pulse rounded bg-zinc-200" />
        <div className="h-7 w-32 animate-pulse rounded-full bg-zinc-200" />
      </div>
    </div>
  )
}

export function SiteFooter() {
  const location = useLocation()
  const isHome = location.pathname === '/'
  const isScanFlow = location.pathname === '/scan' || location.pathname.startsWith('/jobs/')

  // スキャン画面やホーム画面ではフッターを表示しない（画面全体のレイアウトを保つため）
  if (isScanFlow || isHome) return null

  return (
    <footer className="mt-[50px] w-full border-t border-line/40 bg-zinc-50/60 py-8 text-ink-muted backdrop-blur-xl">
      <div className="mx-auto max-w-5xl px-7 md:px-[39px]">
        <div className="flex flex-col items-center justify-between gap-6 md:flex-row md:gap-4">
          <div className="flex flex-col items-center gap-1 md:items-start">
            <BrandMark className="opacity-80 scale-90 origin-left" />
            <p className="text-xs text-ink-muted/70 mt-1">
              どんな本でも一瞬で
            </p>
          </div>
          <div className="flex flex-wrap justify-center gap-x-6 gap-y-2 text-sm font-medium">
            <Link to="/" className="hover:text-primary transition-colors">さがす</Link>
            <Link to="/index" className="hover:text-primary transition-colors">索引</Link>
            <Link to="/scan" className="hover:text-primary transition-colors">スキャン</Link>
          </div>
        </div>
        <div className="mt-8 border-t border-line/20 pt-6 text-center text-xs text-ink-muted/50">
          <RakutenCredit className="mb-2 underline-offset-2 hover:underline" />
          <p>© {new Date().getFullYear()} ホンノキ. All rights reserved.</p>
        </div>
      </div>
    </footer>
  )
}

export function SiteHeader() {
  const location = useLocation()
  const navigate = useNavigate()
  const isHome = location.pathname === '/'
  const [menuOpen, setMenuOpen] = useState(false)
  const menuButtonRef = useRef<HTMLButtonElement>(null)
  const wasOpenRef = useRef(false)
  const goBack = () => {
    if (typeof window.history.state?.idx === 'number' && window.history.state.idx > 0) navigate(-1)
    else navigate('/')
  }

  useEffect(() => {
    setMenuOpen(false)
  }, [location.pathname])

  useEffect(() => {
    if (!menuOpen && wasOpenRef.current) menuButtonRef.current?.focus()
    wasOpenRef.current = menuOpen
  }, [menuOpen])

  return (
    <header className={`fixed left-0 top-0 w-full z-20 transition-all ${isHome ? 'bg-transparent backdrop-blur-none border-b border-transparent' : 'bg-white/60 backdrop-blur-xl border-b border-line/40'}`}>
      <div className="mx-auto flex h-[72px] w-full items-center justify-between px-7 md:h-[88px] md:px-[39px]">
        <div className="size-11 md:w-auto h-auto">
          {!isHome && (
            <>
              <button type="button" aria-label="戻る" onClick={goBack} className="tap-soft grid size-11 place-items-center text-ink md:hidden">
                <ArrowLeft className="size-6" />
              </button>
              <Link to="/" className="hidden md:block">
                <BrandMark />
              </Link>
            </>
          )}
        </div>
        <button ref={menuButtonRef} type="button" aria-label="メニューを開く" aria-expanded={menuOpen} onClick={() => setMenuOpen(true)} className="tap-soft grid size-11 place-items-center rounded-full text-ink hover:bg-zinc-100 md:fixed md:right-[calc(32px-(100vw-100%))] md:top-[22px] md:z-20">
          <Menu className="size-6 md:h-6 md:w-[31px]" />
        </button>
      </div>
      {menuOpen && <MenuDrawer onClose={() => setMenuOpen(false)} />}
    </header>
  )
}

function MenuDrawer({ onClose }: { onClose: () => void }) {
  const navRef = useRef<HTMLElement>(null)
  const closeButtonRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    closeButtonRef.current?.focus()
  }, [onClose])

  const handleKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault()
      onClose()
      return
    }
    if (event.key !== 'Tab') return
    const focusable = navRef.current?.querySelectorAll<HTMLElement>('a[href], button:not([disabled])')
    if (!focusable || focusable.length === 0) return
    const first = focusable[0]
    const last = focusable[focusable.length - 1]
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first.focus()
    }
  }

  const items = [
    { to: '/', label: 'さがす' },
    { to: '/index', label: '索引' },
    { to: '/scan', label: 'スキャン' },
  ]
  const adminItems = [
    { to: '/map', label: '図書室マップ' },
    { to: '/admin/shelves', label: '棚の管理' },
    { to: '/admin/tags', label: 'タグ配置' },
  ]
  const itemClass = ({ isActive }: { isActive: boolean }) =>
    `flex min-h-12 items-center border-b border-line text-xl ${isActive ? 'text-[#087f5b]' : 'text-ink'}`

  // ヘッダーの backdrop-blur が fixed の基準を変えるため body 直下に描画する
  return createPortal(
    <div className="fixed inset-0 z-30">
      <button type="button" aria-label="メニューを閉じる" onClick={onClose} className="absolute inset-0 cursor-default bg-black/30" />
      <nav ref={navRef} aria-label="メインメニュー" onKeyDown={handleKeyDown} className="absolute right-0 top-0 flex h-full w-72 max-w-[80vw] flex-col bg-white px-7 pb-8 shadow-xl md:px-8">
        <div className="flex h-[72px] items-center justify-end md:h-[88px]">
          <button ref={closeButtonRef} type="button" aria-label="メニューを閉じる" onClick={onClose} className="tap-soft grid size-11 place-items-center rounded-full text-ink hover:bg-zinc-100">
            <X className="size-6" />
          </button>
        </div>
        <div className="flex flex-col">
          {items.map(item => (
            <NavLink key={item.to} to={item.to} end={item.to === '/'} className={itemClass}>
              {item.label}
            </NavLink>
          ))}
        </div>
        <div className="mt-auto flex flex-col gap-1 border-t border-line pt-4">
          {adminItems.map(item => (
            <NavLink key={item.to} to={item.to} className={({ isActive }) => `flex min-h-10 items-center text-sm ${isActive ? 'text-[#087f5b]' : 'text-ink-muted'}`}>
              {item.label}
            </NavLink>
          ))}
        </div>
      </nav>
    </div>,
    document.body,
  )
}

function TopLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <NavLink
      to={to}
      end={to === '/'}
      className={({ isActive }) => `rounded-lg px-3 py-2 text-sm font-semibold ${isActive ? 'bg-primary text-white' : 'text-ink-muted hover:bg-zinc-100'}`}
    >
      {children}
    </NavLink>
  )
}
