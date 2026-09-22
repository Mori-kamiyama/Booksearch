import { StrictMode } from 'react';
import { createRoot, hydrateRoot } from 'react-dom/client';
import './index.css';
import App from './App';
import { readFeaturedSnapshot } from './lib/featuredSnapshot';

const root = document.getElementById('root');
if (!root) throw new Error('root element is missing');

const snapshot = readFeaturedSnapshot(document);
const app = (
  <StrictMode>
    <App initialFeatured={snapshot} />
  </StrictMode>
);

if (root.dataset.prerendered === 'home' && snapshot) {
  hydrateRoot(root, app);
} else {
  createRoot(root).render(app);
}
