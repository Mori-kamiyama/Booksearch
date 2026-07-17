import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import SearchPage from './pages/SearchPage'
import ScanPage from './pages/ScanPage'
import JobPage from './pages/JobPage'
import ShelvesPage from './pages/ShelvesPage'
import TagPlacementPage from './pages/TagPlacementPage'
import BookDetailPage from './pages/BookDetailPage'
import MapPage from './pages/MapPage'
import ShelfDetailPage from './pages/ShelfDetailPage'
import { BottomTabs, SiteHeader } from './components/common'

export default function App() {
  return (
    <BrowserRouter>
      <div className="min-h-screen bg-surface pb-20 md:pb-0">
        <SiteHeader />
        <main className="mx-auto w-full max-w-5xl px-4 py-6">
          <Routes>
            <Route path="/" element={<SearchPage />} />
            <Route path="/books/:id" element={<BookDetailPage />} />
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
        <BottomTabs />
      </div>
    </BrowserRouter>
  )
}
