import { createHash } from 'node:crypto'
import { readFile } from 'node:fs/promises'

export const HOME_KEY = 'home.html'
export const MANIFEST_PREFIX = 'featured/weeks/'
export const MAX_BOOKS = 5
export const MAX_CANDIDATES = 20
export const MAX_IMAGE_BYTES = 1024 * 1024
export const IMAGE_TIMEOUT_MS = 10_000
export const PUBLISH_TIMEOUT_MS = 100_000
export const ALLOWED_IMAGE_HOSTS = new Set([
  'books.google.com',
  'books.google.co.jp',
  'thumbnail.image.rakuten.co.jp',
])

const IMAGE_TYPES = new Set(['image/png', 'image/jpeg', 'image/webp'])
const NORMALIZED_IMAGE_TYPES = new Set(['image/jpeg', 'image/webp'])
const REDIRECT_STATUSES = new Set([301, 302, 303, 307, 308])
let sharpPromise

export class HomePublisherError extends Error {
  constructor(message, options) {
    super(message, options)
    this.name = 'HomePublisherError'
  }
}

/** Go's featuredWeekKey uses UTC ISO week and a non-padded week number. */
export function weeklyKey(input = new Date()) {
  const date = new Date(input)
  if (Number.isNaN(date.getTime())) throw new HomePublisherError('invalid current time')
  date.setUTCHours(0, 0, 0, 0)
  const day = date.getUTCDay() || 7
  date.setUTCDate(date.getUTCDate() + 4 - day)
  const yearStart = Date.UTC(date.getUTCFullYear(), 0, 1)
  const week = Math.ceil((((date.getTime() - yearStart) / 86400000) + 1) / 7)
  return `${date.getUTCFullYear()}-${week}`
}

export function escapeBootstrapJson(value) {
  return JSON.stringify(value)
    .replace(/</g, '\\u003c')
    .replace(/\u2028/g, '\\u2028')
    .replace(/\u2029/g, '\\u2029')
}

function isNotFound(error) {
  return error?.$metadata?.httpStatusCode === 404
    || error?.statusCode === 404
    || error?.name === 'NoSuchKey'
    || error?.name === 'NotFound'
}

async function bodyToUtf8(body) {
  if (body == null) throw new HomePublisherError('S3 object has no body')
  if (typeof body === 'object' && !Buffer.isBuffer(body) && !(body instanceof Uint8Array) && typeof body.transformToString !== 'function' && !body[Symbol.asyncIterator]) return body
  if (typeof body === 'string') return body
  if (Buffer.isBuffer(body)) return body.toString('utf8')
  if (body instanceof Uint8Array) return Buffer.from(body).toString('utf8')
  if (typeof body.transformToString === 'function') return body.transformToString()
  const chunks = []
  for await (const chunk of body) chunks.push(Buffer.from(chunk))
  return Buffer.concat(chunks).toString('utf8')
}

function parseManifest(raw, week) {
  let manifest
  try {
    manifest = typeof raw === 'string' ? JSON.parse(raw) : raw
  } catch (error) {
    throw new HomePublisherError('featured manifest is not valid JSON', { cause: error })
  }
  if (!manifest || manifest.week !== week || !Array.isArray(manifest.books)) {
    throw new HomePublisherError(`featured manifest is not for ${week}`)
  }
  return manifest
}

function contentType(response) {
  const header = response?.headers?.get?.('content-type')
  return typeof header === 'string' ? header.split(';', 1)[0].trim().toLowerCase() : ''
}

function allowedUrl(raw) {
  let url
  try {
    url = new URL(raw)
  } catch {
    throw new HomePublisherError('cover URL is not valid')
  }
  if (url.protocol !== 'https:' || url.username || url.password || url.port || !ALLOWED_IMAGE_HOSTS.has(url.hostname.toLowerCase())) {
    throw new HomePublisherError(`cover URL host is not allowed: ${url.hostname}`)
  }
  return url
}

async function readBytes(response, maxBytes) {
  const declaredLength = Number(response?.headers?.get?.('content-length'))
  if (Number.isFinite(declaredLength) && declaredLength > maxBytes) {
    throw new HomePublisherError('cover image exceeds 1 MiB')
  }
  if (response?.body?.getReader) {
    const reader = response.body.getReader()
    const chunks = []
    let length = 0
    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        const chunk = Buffer.from(value)
        length += chunk.length
        if (length > maxBytes) throw new HomePublisherError('cover image exceeds 1 MiB')
        chunks.push(chunk)
      }
    } finally {
      reader.releaseLock?.()
    }
    return Buffer.concat(chunks, length)
  }
  if (typeof response?.arrayBuffer === 'function') {
    const bytes = Buffer.from(await response.arrayBuffer())
    if (bytes.length > maxBytes) throw new HomePublisherError('cover image exceeds 1 MiB')
    return bytes
  }
  if (response && Symbol.asyncIterator in Object(response)) {
    const chunks = []
    let length = 0
    for await (const chunk of response) {
      const bytes = Buffer.from(chunk)
      length += bytes.length
      if (length > maxBytes) throw new HomePublisherError('cover image exceeds 1 MiB')
      chunks.push(bytes)
    }
    return Buffer.concat(chunks, length)
  }
  throw new HomePublisherError('cover response has no readable body')
}

function matchesSignature(bytes, type) {
  if (type === 'image/png') return bytes.length >= 8 && Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]).equals(bytes.subarray(0, 8))
  if (type === 'image/jpeg') return bytes.length >= 3 && bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff
  return bytes.length >= 12 && bytes.subarray(0, 4).toString('ascii') === 'RIFF' && bytes.subarray(8, 12).toString('ascii') === 'WEBP'
}

async function defaultDecodeImage(bytes) {
  sharpPromise ??= import('sharp').then(module => module.default ?? module)
  const sharp = await sharpPromise
  const normalized = await sharp(bytes, { failOn: 'error' })
    .rotate()
    .resize({ width: 256, height: 384, fit: 'inside', withoutEnlargement: true })
    .jpeg({ quality: 85 })
    .toBuffer()
  return { bytes: normalized, contentType: 'image/jpeg' }
}

function timeoutError() {
  return new HomePublisherError('cover fetch timed out')
}

export async function fetchImageDataUri(rawUrl, {
  fetchImpl = globalThis.fetch,
  decodeImage = defaultDecodeImage,
  deadlineAt = Date.now() + PUBLISH_TIMEOUT_MS,
  imageTimeoutMs = IMAGE_TIMEOUT_MS,
  now = () => Date.now(),
  maxRedirects = 3,
} = {}) {
  if (typeof fetchImpl !== 'function') throw new HomePublisherError('fetch is unavailable')
  let url = allowedUrl(rawUrl)
  for (let redirects = 0; ; redirects += 1) {
    const remaining = deadlineAt - now()
    if (remaining <= 0) throw new HomePublisherError('home publish timed out')
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), Math.min(imageTimeoutMs, remaining))
    let response
    try {
      response = await fetchImpl(url, { redirect: 'manual', signal: controller.signal })
    } catch (error) {
      clearTimeout(timeout)
      if (controller.signal.aborted) throw timeoutError()
      throw error
    }
    if (REDIRECT_STATUSES.has(response.status)) {
      clearTimeout(timeout)
      if (redirects >= maxRedirects) throw new HomePublisherError('cover has too many redirects')
      const location = response.headers?.get?.('location')
      if (!location) throw new HomePublisherError('cover redirect has no location')
      url = allowedUrl(new URL(location, url).toString())
      continue
    }
    try {
      if (!response.ok) throw new HomePublisherError(`cover fetch failed with HTTP ${response.status}`)
      const type = contentType(response)
      if (!IMAGE_TYPES.has(type)) throw new HomePublisherError(`unsupported cover content type: ${type || 'missing'}`)
      const bytes = await readBytes(response, MAX_IMAGE_BYTES)
      if (!matchesSignature(bytes, type)) throw new HomePublisherError('cover content does not match its content type')
      const decoded = await decodeImage(bytes, type)
      const decodedBytes = Buffer.isBuffer(decoded) || decoded instanceof Uint8Array ? decoded : decoded?.bytes
      const normalizedBytes = decodedBytes instanceof Uint8Array ? Buffer.from(decodedBytes) : decodedBytes
      const normalizedType = Buffer.isBuffer(decoded) || decoded instanceof Uint8Array ? type : decoded?.contentType
      if (!Buffer.isBuffer(normalizedBytes) || !NORMALIZED_IMAGE_TYPES.has(normalizedType)) {
        throw new HomePublisherError('cover decoder returned an invalid image')
      }
      if (normalizedBytes.length > MAX_IMAGE_BYTES || !matchesSignature(normalizedBytes, normalizedType)) {
        throw new HomePublisherError('normalized cover image is invalid')
      }
      return `data:${normalizedType};base64,${normalizedBytes.toString('base64')}`
    } catch (error) {
      if (controller.signal.aborted) throw timeoutError()
      throw error
    } finally {
      clearTimeout(timeout)
    }
  }
}

function positiveId(value) {
  return Number.isInteger(value) && value > 0
}

export async function selectFeaturedBooks(manifest, {
  fetchImpl = globalThis.fetch,
  decodeImage = defaultDecodeImage,
  deadlineAt = Date.now() + PUBLISH_TIMEOUT_MS,
  imageTimeoutMs = IMAGE_TIMEOUT_MS,
  now = () => Date.now(),
} = {}) {
  const selected = []
  const seen = new Set()
  for (const book of manifest.books.slice(0, MAX_CANDIDATES)) {
    if (!book || !positiveId(book.id) || typeof book.title !== 'string' || seen.has(book.id)) continue
    seen.add(book.id)
    try {
      if (deadlineAt - now() <= 0) throw new HomePublisherError('home publish timed out')
      const thumbnail = await fetchImageDataUri(book.thumbnail, { fetchImpl, decodeImage, deadlineAt, imageTimeoutMs, now })
      selected.push({ ...book, thumbnail })
      if (selected.length === MAX_BOOKS) break
    } catch (error) {
      if (deadlineAt - now() <= 0) throw error
    }
  }
  if (selected.length !== MAX_BOOKS) throw new HomePublisherError(`only ${selected.length} featured covers succeeded`)
  return selected
}

function renderDocument(clientIndexHtml, renderedMarkup, snapshot) {
  const rootPattern = /<div\s+id=(['"])root\1\s*>\s*<\/div>/i
  if (!rootPattern.test(clientIndexHtml)) throw new HomePublisherError('client-index.html has no empty root')
  const root = `<div id="root" data-prerendered="home">${renderedMarkup}</div>`
  const bootstrap = `<script type="application/json" id="featured-bootstrap">${escapeBootstrapJson(snapshot)}</script>`
  let html = clientIndexHtml.replace(rootPattern, () => root)
  if (/<script[^>]+id=(['"])featured-bootstrap\1[^>]*>[\s\S]*?<\/script>/i.test(html)) {
    html = html.replace(/<script[^>]+id=(['"])featured-bootstrap\1[^>]*>[\s\S]*?<\/script>/i, () => bootstrap)
  } else if (/<\/body>/i.test(html)) {
    html = html.replace(/<\/body>/i, () => `${bootstrap}</body>`)
  } else {
    html += bootstrap
  }
  return html
}

export async function publishHome({
  frontendBucket,
  dataBucket,
  apiFunctionName,
  storage,
  invokeRefresh,
  clientIndexHtml,
  renderHome,
  fetchImpl = globalThis.fetch,
  decodeImage = defaultDecodeImage,
  now = () => Date.now(),
  force = false,
  imageTimeoutMs = IMAGE_TIMEOUT_MS,
  publishTimeoutMs = PUBLISH_TIMEOUT_MS,
} = {}) {
  if (!frontendBucket || !dataBucket || !apiFunctionName) throw new HomePublisherError('publisher environment is incomplete')
  if (!storage || typeof storage.headHome !== 'function' || typeof storage.getManifest !== 'function' || typeof storage.putHome !== 'function') {
    throw new HomePublisherError('publisher storage adapter is incomplete')
  }
  if (typeof clientIndexHtml !== 'string' || typeof renderHome !== 'function') throw new HomePublisherError('home render assets are unavailable')
  const week = weeklyKey(new Date(now()))
  const clientIndexHash = createHash('sha256').update(clientIndexHtml).digest('hex')
  let expectedETag
  {
    try {
      const existing = await storage.headHome({ bucket: frontendBucket, key: HOME_KEY })
      expectedETag = existing?.ETag
      const metadata = existing?.Metadata ?? existing?.metadata ?? {}
      if (!force && (metadata['featured-week'] ?? metadata['featured_week']) === week
        && (metadata['client-index-sha256'] ?? metadata['client_index_sha256']) === clientIndexHash) {
        return { status: 'skipped', week, clientIndexHash }
      }
    } catch (error) {
      if (!isNotFound(error)) throw error
    }
  }

  const deadlineAt = now() + publishTimeoutMs
  const manifestKey = `${MANIFEST_PREFIX}${week}.json`
  let rawManifest
  try {
    rawManifest = await storage.getManifest({ bucket: dataBucket, key: manifestKey })
  } catch (error) {
    if (!isNotFound(error)) throw error
    await invokeRefresh({ functionName: apiFunctionName, payload: { source: 'booksearch.featured.refresh' } })
    rawManifest = await storage.getManifest({ bucket: dataBucket, key: manifestKey })
  }
  const manifestBody = rawManifest && typeof rawManifest === 'object' && 'Body' in rawManifest
    ? rawManifest.Body
    : rawManifest
  const manifest = parseManifest(await bodyToUtf8(manifestBody), week)
  const books = await selectFeaturedBooks(manifest, { fetchImpl, decodeImage, deadlineAt, imageTimeoutMs, now })
  const snapshot = { week, books }
  const renderedMarkup = await renderHome(snapshot)
  if (typeof renderedMarkup !== 'string') throw new HomePublisherError('home render did not return HTML')
  if (now() > deadlineAt) throw new HomePublisherError('home publish timed out')
  const html = renderDocument(clientIndexHtml, renderedMarkup, snapshot)
  await storage.putHome({
    bucket: frontendBucket,
    key: HOME_KEY,
    body: html,
    contentType: 'text/html',
    cacheControl: 'public,max-age=300',
    metadata: { 'featured-week': week, 'client-index-sha256': clientIndexHash },
    expectedETag,
  })
  return { status: 'published', week, clientIndexHash, books: books.length }
}

async function createAwsPublisher() {
  const [{ S3Client, GetObjectCommand, HeadObjectCommand, PutObjectCommand }, { LambdaClient, InvokeCommand }] = await Promise.all([
    import('@aws-sdk/client-s3'),
    import('@aws-sdk/client-lambda'),
  ])
  const s3 = new S3Client({})
  const lambda = new LambdaClient({})
  const storage = {
    async headHome({ bucket, key }) {
      return s3.send(new HeadObjectCommand({ Bucket: bucket, Key: key }))
    },
    async getManifest({ bucket, key }) {
      const output = await s3.send(new GetObjectCommand({ Bucket: bucket, Key: key }))
      return output.Body
    },
    async putHome({ bucket, key, body, contentType, cacheControl, metadata, expectedETag }) {
      return s3.send(new PutObjectCommand({ Bucket: bucket, Key: key, Body: body, ContentType: contentType, CacheControl: cacheControl, Metadata: metadata,
        ...(expectedETag ? { IfMatch: expectedETag } : { IfNoneMatch: '*' }),
      }))
    },
  }
  const invokeRefresh = async ({ functionName, payload }) => {
    const output = await lambda.send(new InvokeCommand({
      FunctionName: functionName,
      InvocationType: 'RequestResponse',
      Payload: Buffer.from(JSON.stringify(payload)),
    }))
    if (output.FunctionError) throw new HomePublisherError(`featured refresh failed: ${output.FunctionError}`)
    return output
  }
  const clientIndexHtml = await readFile(new URL('./client-index.html', import.meta.url), 'utf8')
  const renderModule = await import('./render/entry-home.js')
  const renderHome = renderModule.renderHome ?? renderModule.default?.renderHome ?? renderModule.default
  return { storage, invokeRefresh, clientIndexHtml, renderHome }
}

function eventIsForced(event) {
  return event?.force === true || event?.detail?.force === true
}

export async function handler(event = {}) {
  const { FRONTEND_BUCKET: frontendBucket, DATA_BUCKET: dataBucket, API_FUNCTION_NAME: apiFunctionName } = process.env
  const dependencies = await createAwsPublisher()
  return publishHome({ frontendBucket, dataBucket, apiFunctionName, force: eventIsForced(event), ...dependencies })
}
