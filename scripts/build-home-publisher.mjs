import { execFileSync } from 'node:child_process'
import { cp, rm } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'

const frontend = new URL('../frontend/', import.meta.url)
const publisher = new URL('../aws/functions/home_publisher/', import.meta.url)
for (const script of ['build', 'build:home-render']) {
  execFileSync('npm', ['run', script], { cwd: fileURLToPath(frontend), stdio: 'inherit' })
}
await rm(new URL('render/', publisher), { recursive: true, force: true })
await cp(new URL('.home-render/', frontend), new URL('render/', publisher), { recursive: true })
await cp(new URL('dist/index.html', frontend), new URL('client-index.html', publisher))
