import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { Component, lazy, Suspense, useEffect, type ErrorInfo, type ReactNode } from 'react'
import SearchPage from './pages/SearchPage'
import { NotFoundPage, SiteFooter, SiteHeader } from './components/common'

const SearchResultsPage = lazy(() => import('./pages/SearchResultsPage'))
const ScanPage = lazy(() => import('./pages/ScanPage'))
const JobPage = lazy(() => import('./pages/JobPage'))
const ShelvesPage = lazy(() => import('./pages/ShelvesPage'))
const TagPlacementPage = lazy(() => import('./pages/TagPlacementPage'))
const BookDetailPage = lazy(() => import('./pages/BookDetailPage'))
const IndexPage = lazy(() => import('./pages/IndexPage'))
const MapPage = lazy(() => import('./pages/MapPage'))
const ShelfDetailPage = lazy(() => import('./pages/ShelfDetailPage'))

export default function App() {
  return (
    <BrowserRouter>
      <AppErrorBoundary>
        <AppShell />
      </AppErrorBoundary>
    </BrowserRouter>
  )
}

interface AppErrorBoundaryState {
  hasError: boolean
}

class AppErrorBoundary extends Component<{ children: ReactNode }, AppErrorBoundaryState> {
  state: AppErrorBoundaryState = { hasError: false }

  static getDerivedStateFromError(): AppErrorBoundaryState {
    return { hasError: true }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Unhandled application error', error, info)
  }

  render() {
    if (this.state.hasError) {
      return (
        <main className="grid min-h-screen place-items-center bg-white px-7 text-center">
          <div>
            <h1 className="text-xl font-bold text-ink">ページを表示できませんでした</h1>
            <p className="mt-2 text-sm text-ink-muted">ホームへ戻って、もう一度お試しください。</p>
            <a href="/" className="mt-5 inline-flex min-h-11 items-center justify-center rounded-lg bg-primary px-4 text-sm font-bold text-white">
              ホームへ戻る
            </a>
          </div>
        </main>
      )
    }
    return this.props.children
  }
}

function AppShell() {
  const { pathname } = useLocation()
  const isScanFlow = pathname === '/scan' || pathname.startsWith('/jobs/')
  const usesFigmaLayout = pathname === '/' || pathname === '/search' || pathname === '/index' || pathname.startsWith('/books/') || isScanFlow
  const isHome = pathname === '/'
  const needsHeaderPadding = !isScanFlow && !isHome

  useEffect(() => {
    document.title = documentTitleForPath(pathname)
  }, [pathname])

  return (
    <div className={`min-h-screen flex flex-col ${usesFigmaLayout ? 'bg-white' : 'bg-surface'}`}>
      {!isScanFlow && <SiteHeader />}
      <main className={`flex-grow ${usesFigmaLayout ? 'w-full' : 'mx-auto w-full max-w-5xl px-4 pb-6'} ${needsHeaderPadding ? (usesFigmaLayout ? 'pt-[72px] md:pt-[88px]' : 'pt-[96px] md:pt-[120px]') : ''}`}>
        <Suspense fallback={<RouteLoading />}>
          <Routes>
            <Route path="/" element={<SearchPage />} />
            <Route path="/search" element={<SearchResultsPage />} />
            <Route path="/books/:id" element={<BookDetailPage />} />
            <Route path="/index" element={<IndexPage />} />
            <Route path="/map" element={<MapPage />} />
            <Route path="/map/:shelfId" element={<ShelfDetailPage />} />
            <Route path="/scan" element={<ScanPage />} />
            <Route path="/jobs/:id" element={<JobPage />} />
            <Route path="/admin/shelves" element={<ShelvesPage />} />
            <Route path="/admin/tags" element={<TagPlacementPage />} />
            <Route path="/shelves" element={<Navigate to="/admin/shelves" replace />} />
            <Route path="/tag-placement" element={<Navigate to="/admin/tags" replace />} />
            <Route path="*" element={<NotFoundPage />} />
          </Routes>
        </Suspense>
      </main>
      {!isScanFlow && <SiteFooter />}
    </div>
  )
}

function RouteLoading() {
  return (
    <div role="status" aria-live="polite" className="grid min-h-[50vh] place-items-center px-7 py-16 text-sm text-ink-muted">
      ページを読み込んでいます…
    </div>
  )
}

function documentTitleForPath(pathname: string): string {
  if (pathname === '/') return 'ホンノキ｜図書室の本を探す'
  if (pathname === '/search') return '検索結果｜ホンノキ'
  if (pathname.startsWith('/books/')) return '本の詳細｜ホンノキ'
  if (pathname === '/index') return '索引｜ホンノキ'
  if (pathname === '/map') return '図書室マップ｜ホンノキ'
  if (pathname.startsWith('/map/')) return '棚区画｜ホンノキ'
  if (pathname === '/scan') return '本棚をスキャン｜ホンノキ'
  if (pathname.startsWith('/jobs/')) return 'スキャン結果｜ホンノキ'
  if (pathname === '/admin/shelves' || pathname === '/shelves') return '棚の管理｜ホンノキ'
  if (pathname === '/admin/tags' || pathname === '/tag-placement') return 'タグ配置｜ホンノキ'
  return 'ホンノキ'
}
