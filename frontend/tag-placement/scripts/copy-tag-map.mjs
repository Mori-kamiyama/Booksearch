import { copyFileSync, mkdirSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const appRoot = resolve(here, '..')
const repoRoot = resolve(appRoot, '..', '..')

mkdirSync(resolve(appRoot, 'public'), { recursive: true })
copyFileSync(
  resolve(repoRoot, 'data', 'apriltag_library_map.json'),
  resolve(appRoot, 'public', 'apriltag_library_map.json'),
)
copyFileSync(
  resolve(repoRoot, 'data', 'apriltag_library_map.json'),
  resolve(repoRoot, 'api', 'tags', 'apriltag_library_map.json'),
)
