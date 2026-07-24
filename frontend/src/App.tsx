import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import SearchPage from './pages/SearchPage'
import SearchResultsPage from './pages/SearchResultsPage'
import ScanPage from './pages/ScanPage'
import JobPage from './pages/JobPage'
import ShelvesPage from './pages/ShelvesPage'
import TagPlacementPage from './pages/TagPlacementPage'
import BookDetailPage from './pages/BookDetailPage'
import IndexPage from './pages/IndexPage'
import MapPage from './pages/MapPage'
import ShelfDetailPage from './pages/ShelfDetailPage'
import { BottomTabs, SiteHeader } from './components/common'

export default function App() {
  return (
    <BrowserRouter>
      <AppShell />
    </BrowserRouter>
  )
}

function AppShell() {
  const { pathname } = useLocation()
  const isScanFlow = pathname === '/scan' || pathname.startsWith('/jobs/')
  const usesFigmaLayout = pathname === '/' || pathname === '/search' || pathname === '/index' || pathname.startsWith('/books/') || isScanFlow

  return (
    <div className={`min-h-screen ${usesFigmaLayout ? 'bg-white' : 'bg-surface pb-20 md:pb-0'}`}>
      {!isScanFlow && <SiteHeader />}
      <main className={usesFigmaLayout ? 'w-full' : 'mx-auto w-full max-w-5xl px-4 py-6'}>
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
        </Routes>
      </main>
      {!usesFigmaLayout && <BottomTabs />}
    </div>
  )
}
