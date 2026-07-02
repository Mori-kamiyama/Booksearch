import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const appRoot = dirname(fileURLToPath(import.meta.url))

export default defineConfig({
  root: appRoot,
  plugins: [react(), tailwindcss()],
  build: {
    outDir: resolve(appRoot, 'dist'),
    emptyOutDir: true,
  },
})
