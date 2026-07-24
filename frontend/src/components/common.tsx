import { AlertCircle, ArrowLeft, BookOpen, Menu, Map, Search, Upload, X } from 'lucide-react'
import { Link, NavLink, useLocation, useNavigate } from 'react-router-dom'
import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import type { ReactNode } from 'react'

export function BrandMark({ className = '' }: { className?: string }) {
  return (
    <span className={`inline-flex items-center gap-3 ${className}`}>
      <img src="/figma-icons/leaf.svg" alt="" className="h-[26.5px] w-[48px]" />
      <img src="/figma-icons/wordmark.svg" alt="ホンノキ" className="h-[30px] w-[137px]" />
    </span>
  )
}

export function PageHeader({ title, back = false }: { title: string; back?: boolean }) {
  const navigate = useNavigate()
  return (
    <div className="mb-5 flex min-h-11 items-center gap-3">
      {back && (
        <button
          type="button"
          aria-label="戻る"
          onClick={() => navigate(-1)}
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

export function BottomTabs() {
  const tabs = [
    { to: '/', label: 'さがす', icon: Search },
    { to: '/map', label: 'マップ', icon: Map },
    { to: '/scan', label: 'スキャン', icon: Upload },
  ]
  return (
    <nav className="fixed inset-x-0 bottom-0 z-20 border-t border-line bg-white/95 px-3 pb-[max(0.5rem,env(safe-area-inset-bottom))] pt-2 backdrop-blur md:hidden">
      <div className="mx-auto grid max-w-xl grid-cols-3 gap-1">
        {tabs.map(tab => {
          const Icon = tab.icon
          return (
            <NavLink
              key={tab.to}
              to={tab.to}
              end={tab.to === '/'}
              className={({ isActive }) => `flex min-h-11 flex-col items-center justify-center gap-0.5 rounded-lg text-xs font-semibold ${isActive ? 'bg-primary-soft text-primary' : 'text-ink-muted'}`}
            >
              <Icon className="size-5" />
              {tab.label}
            </NavLink>
          )
        })}
      </div>
    </nav>
  )
}

export function SiteHeader() {
  const location = useLocation()
  const navigate = useNavigate()
  const isHome = location.pathname === '/'
  const [menuOpen, setMenuOpen] = useState(false)

  useEffect(() => {
    setMenuOpen(false)
  }, [location.pathname])

  return (
    <header className="sticky top-0 z-10 bg-white/95 backdrop-blur">
      <div className="mx-auto flex h-[72px] w-full items-center justify-between px-7 md:h-[88px] md:px-[39px]">
        <div className="size-11 md:w-auto">
          {!isHome && (
            <>
              <button type="button" aria-label="戻る" onClick={() => navigate(-1)} className="tap-soft grid size-11 place-items-center text-ink md:hidden">
                <ArrowLeft className="size-6" />
              </button>
              <Link to="/" className="hidden md:block"><BrandMark /></Link>
            </>
          )}
        </div>
        <button type="button" aria-label="メニューを開く" aria-expanded={menuOpen} onClick={() => setMenuOpen(true)} className="tap-soft grid size-11 place-items-center rounded-full text-ink hover:bg-zinc-100 md:fixed md:right-[calc(32px-(100vw-100%))] md:top-[22px] md:z-20 md:w-[31px]">
          <Menu className="size-6 md:h-6 md:w-[31px]" />
        </button>
      </div>
      {menuOpen && <MenuDrawer onClose={() => setMenuOpen(false)} />}
    </header>
  )
}

function MenuDrawer({ onClose }: { onClose: () => void }) {
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [onClose])

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
      <nav aria-label="メインメニュー" className="absolute right-0 top-0 flex h-full w-72 max-w-[80vw] flex-col bg-white px-7 pb-8 shadow-xl">
        <div className="flex h-[72px] items-center justify-end">
          <button type="button" aria-label="メニューを閉じる" onClick={onClose} className="tap-soft grid size-11 place-items-center rounded-full text-ink hover:bg-zinc-100">
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
