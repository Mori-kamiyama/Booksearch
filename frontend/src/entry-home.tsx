import { renderToString } from 'react-dom/server'
import { StaticRouter } from 'react-router-dom'
import { AppContent } from './App'
import type { FeaturedSnapshot } from './lib/types'

export function renderHome(snapshot: FeaturedSnapshot): string {
  return renderToString(
    <StaticRouter location="/">
      <AppContent initialFeatured={snapshot} />
    </StaticRouter>,
  )
}
