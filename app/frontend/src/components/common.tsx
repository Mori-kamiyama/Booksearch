import { AlertCircle, ArrowLeft, BookOpen, Map, Search, Upload } from 'lucide-react'
import { Link, NavLink, useNavigate } from 'react-router-dom'
import type { ReactNode } from 'react'

export function PageHeader({ title, back = false }: { title: string; back?: boolean }) {
  const navigate = useNavigate()
  return (
    <div className="mb-4 flex min-h-10 items-center gap-3 sm:mb-5 sm:min-h-11">
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
      <h1 className="text-base font-bold text-ink sm:text-xl">{title}</h1>
    </div>
  )
}

export function Breadcrumbs({ items }: { items: { label: string; to?: string }[] }) {
  return (
    <nav aria-label="パンくずリスト" className="mb-4 flex flex-wrap items-center gap-2 text-sm text-ink-muted">
      {items.map((item, index) => (
        <span key={`${item.label}-${index}`} className="inline-flex items-center gap-2">
          {index > 0 && <span aria-hidden="true">&gt;</span>}
          {item.to ? <Link to={item.to} className="underline-offset-2 hover:underline">{item.label}</Link> : <span aria-current="page" className="text-ink">{item.label}</span>}
        </span>
      ))}
    </nav>
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
    <div className="rounded-xl border border-line bg-white px-4 py-5 text-center shadow-sm sm:p-6">
      <div className="mx-auto mb-3 grid size-10 place-items-center rounded-full bg-zinc-100 text-ink-muted sm:size-11">
        {icon ?? <BookOpen className="size-5" />}
      </div>
      <p className="text-sm font-semibold text-ink sm:text-base">{title}</p>
      <p className="mt-1 text-[13px] text-ink-muted sm:text-sm">{hint}</p>
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
    <nav className="fixed inset-x-0 bottom-0 z-20 border-t border-line bg-white/95 px-5 pb-[max(0.5rem,env(safe-area-inset-bottom))] pt-2 backdrop-blur md:hidden">
      <div className="mx-auto grid max-w-xl grid-cols-3 gap-2">
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
  return (
    <header className="sticky top-0 z-10 border-b border-line bg-white/95 backdrop-blur">
      <div className="mx-auto flex h-12 max-w-5xl items-center justify-between px-5 sm:h-14 sm:px-6">
        <Link to="/" className="text-sm font-bold text-primary sm:text-lg">ホンノキ</Link>
        <nav className="hidden items-center gap-1 md:flex">
          <TopLink to="/">さがす</TopLink>
          <TopLink to="/map">マップ</TopLink>
          <TopLink to="/scan">スキャン</TopLink>
          <TopLink to="/admin/shelves">管理</TopLink>
        </nav>
      </div>
    </header>
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
