import { BrowserRouter, Routes, Route, Link, useLocation } from 'react-router-dom'
import SearchPage from './pages/SearchPage'
import ScanPage from './pages/ScanPage'
import JobPage from './pages/JobPage'
import ShelvesPage from './pages/ShelvesPage'

function Nav() {
  const { pathname } = useLocation()
  const link = (to: string, label: string) => (
    <Link
      to={to}
      className={`px-4 py-2 rounded-lg text-sm font-semibold transition-colors ${
        pathname === to
          ? 'bg-[#1f7a5c] text-white'
          : 'text-gray-600 hover:bg-gray-100'
      }`}
    >
      {label}
    </Link>
  )
  return (
    <header className="bg-white border-b border-gray-200 px-6 py-4 flex items-center gap-3">
      <span className="text-xl font-bold text-[#1f7a5c] mr-4">🌳 ホンノキ</span>
      {link('/', '本を探す')}
      {link('/scan', '棚をスキャン')}
      {link('/shelves', '棚候補')}
    </header>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <div className="min-h-screen bg-gray-50">
        <Nav />
        <main className="max-w-5xl mx-auto w-full px-4 py-8">
          <Routes>
            <Route path="/" element={<SearchPage />} />
            <Route path="/scan" element={<ScanPage />} />
            <Route path="/shelves" element={<ShelvesPage />} />
            <Route path="/jobs/:id" element={<JobPage />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  )
}
