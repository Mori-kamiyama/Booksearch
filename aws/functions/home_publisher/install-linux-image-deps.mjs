// npm can silently omit optional native packages when cross-building on macOS.
// Install the two exact Linux/arm64 archives from the lockfile, checking integrity.
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { readFile, mkdir, mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'

const target = process.argv[2]
if (!target) throw new Error('Expected artifact directory')
const lock = JSON.parse(await readFile(new URL('./package-lock.json', import.meta.url), 'utf8'))
for (const name of ['@img/sharp-linux-arm64', '@img/sharp-libvips-linux-arm64']) {
  const pinned = lock.packages[`node_modules/${name}`]
  if (!pinned?.version || !pinned.integrity?.startsWith('sha512-')) throw new Error(`Missing lock entry: ${name}`)
  const directory = path.join(target, 'node_modules', name)
  try {
    const installed = JSON.parse(await readFile(path.join(directory, 'package.json'), 'utf8'))
    if (installed.version === pinned.version) continue
  } catch { /* Install missing optional dependency below. */ }
  const temporary = await mkdtemp(path.join(tmpdir(), 'booksearch-image-dep-'))
  try {
    const packed = JSON.parse(execFileSync('npm', ['pack', `${name}@${pinned.version}`, '--json', '--pack-destination', temporary], { encoding: 'utf8', timeout: 300_000 }))
    const archive = path.join(temporary, path.basename(packed[0].filename))
    const bytes = await readFile(archive)
    const integrity = `sha512-${createHash('sha512').update(bytes).digest('base64')}`
    if (integrity !== pinned.integrity) throw new Error(`Integrity mismatch: ${name}`)
    await mkdir(directory, { recursive: true })
    execFileSync('tar', ['-xzf', archive, '-C', directory, '--strip-components=1'])
  } finally { await rm(temporary, { recursive: true, force: true }) }
}
