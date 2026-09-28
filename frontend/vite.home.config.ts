import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss(), {
    name: 'home-render-esm-package',
    generateBundle() {
      this.emitFile({ type: 'asset', fileName: 'package.json', source: '{"type":"module"}\n' })
    },
  }],
  ssr: { noExternal: true },
  build: {
    ssr: 'src/entry-home.tsx',
    outDir: '.home-render',
    emptyOutDir: true,
    rollupOptions: {
      output: {
        entryFileNames: 'entry-home.js',
      },
    },
  },
})
