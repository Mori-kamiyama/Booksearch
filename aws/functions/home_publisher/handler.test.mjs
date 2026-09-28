import assert from 'node:assert/strict'
import { test } from 'node:test'
import sharp from 'sharp'
import {
  HomePublisherError,
  fetchImageDataUri,
  publishHome,
  weeklyKey,
} from './handler.mjs'

const PNG = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00])
const VALID_JPEG = await sharp({ create: { width: 2, height: 2, channels: 3, background: { r: 30, g: 80, b: 120 } } }).jpeg().toBuffer()
const CLIENT_INDEX = '<!doctype html><html><body><div id="root"></div></body></html>'
const identityDecode = async () => ({ bytes: VALID_JPEG, contentType: 'image/jpeg' })

function response(bytes = PNG, type = 'image/png', status = 200, extraHeaders = {}) {
  return {
    status,
    ok: status >= 200 && status < 300,
    headers: new Headers({ 'content-type': type, 'content-length': String(bytes.length), ...extraHeaders }),
    arrayBuffer: async () => bytes,
  }
}

function books(count = 5) {
  return Array.from({ length: count }, (_, index) => ({
    id: index + 1,
    title: index === 0 ? 'A $& < B' : `Book ${index + 1}`,
    authors: '',
    publisher: '',
    published_date: '',
    class_number: '',
    registration_number: '',
    isbn: '',
    thumbnail: `https://books.google.com/books/content?id=${index + 1}`,
    info_link: null,
  }))
}

function manifestFor(week, count = 5) {
  return { week, books: books(count) }
}

function fakeStorage(manifest, { head, onPut, onManifest } = {}) {
  return {
    async headHome() {
      if (head instanceof Error) throw head
      return head ?? (() => { const error = new Error('not found'); error.name = 'NotFound'; throw error })()
    },
    async getManifest() {
      onManifest?.()
      return manifest
    },
    async putHome(input) {
      onPut?.(input)
    },
  }
}

function imageFetch() {
  return async () => response()
}

test('weeklyKey follows UTC ISO week without zero padding', () => {
  assert.equal(weeklyKey(new Date('2026-09-22T00:00:00+09:00')), '2026-39')
  assert.equal(weeklyKey(new Date('2021-01-01T12:00:00Z')), '2020-53')
})

test('forced publication still carries the previous ETag and surfaces concurrent writes', async () => {
  const week = '2026-39'
  let attempted = false
  await assert.rejects(publishHome({
    frontendBucket: 'frontend', dataBucket: 'data', apiFunctionName: 'api', force: true,
    storage: fakeStorage(manifestFor(week), {
      head: { ETag: 'previous-version' },
      onPut: input => {
        attempted = true
        assert.equal(input.expectedETag, 'previous-version')
        throw Object.assign(new Error('concurrent update'), { name: 'PreconditionFailed' })
      },
    }),
    invokeRefresh: async () => {}, clientIndexHtml: CLIENT_INDEX,
    renderHome: () => '<main>Books</main>', fetchImpl: imageFetch(), decodeImage: identityDecode,
    now: () => Date.parse('2026-09-22T00:00:00Z'),
  }), /concurrent update/)
  assert.equal(attempted, true)
})

test('publishes five covers, embeds safe bootstrap JSON, and carries metadata', async () => {
  const week = weeklyKey(new Date('2026-09-22T00:00:00Z'))
  const puts = []
  const result = await publishHome({
    frontendBucket: 'frontend',
    dataBucket: 'data',
    apiFunctionName: 'api',
    storage: fakeStorage(manifestFor(week), { onPut: input => puts.push(input) }),
    invokeRefresh: async () => { throw new Error('refresh should not run') },
    clientIndexHtml: CLIENT_INDEX,
    renderHome: snapshot => `<main>${snapshot.books[0].title}</main>`,
    fetchImpl: imageFetch(),
    decodeImage: identityDecode,
    now: () => Date.parse('2026-09-22T00:00:00Z'),
  })
  assert.equal(result.status, 'published')
  assert.equal(puts.length, 1)
  assert.equal(puts[0].contentType, 'text/html')
  assert.equal(puts[0].cacheControl, 'public,max-age=300')
  assert.match(puts[0].body, /data-prerendered="home"/)
  assert.match(puts[0].body, /data:image\/jpeg;base64,\/9j\//)
  assert.match(puts[0].body, /<main>A \$& < B<\/main>/)
  assert.match(puts[0].body, /\\u003c/) // rendered markup remains literal; bootstrap is escaped
  assert.deepEqual(puts[0].metadata, { 'featured-week': week, 'client-index-sha256': result.clientIndexHash })
})

test('same week and client hash skips without fetching the manifest', async () => {
  let manifestReads = 0
  let puts = 0
  const week = '2026-39'
  const result = await publishHome({
    frontendBucket: 'frontend',
    dataBucket: 'data',
    apiFunctionName: 'api',
    storage: fakeStorage(manifestFor(week), {
      head: { Metadata: { 'featured-week': week, 'client-index-sha256': 'hash' } },
      onManifest: () => { manifestReads += 1 },
      onPut: () => { puts += 1 },
    }),
    invokeRefresh: async () => { throw new Error('refresh should not run') },
    clientIndexHtml: CLIENT_INDEX,
    renderHome: () => '<main />',
    fetchImpl: imageFetch(),
    decodeImage: identityDecode,
    now: () => Date.parse('2026-09-22T00:00:00Z'),
  })
  // The real hash is unknown to the fixture, so this assertion is repeated with the computed value below.
  assert.equal(result.status, 'published')
  assert.equal(manifestReads, 1)
  assert.equal(puts, 1)

  let skippedManifestReads = 0
  const skipped = await publishHome({
    frontendBucket: 'frontend',
    dataBucket: 'data',
    apiFunctionName: 'api',
    storage: fakeStorage(manifestFor(week), {
      head: { Metadata: { 'featured-week': week, 'client-index-sha256': result.clientIndexHash } },
      onManifest: () => { skippedManifestReads += 1 },
    }),
    invokeRefresh: async () => { throw new Error('refresh should not run') },
    clientIndexHtml: CLIENT_INDEX,
    renderHome: () => '<main />',
    decodeImage: identityDecode,
    now: () => Date.parse('2026-09-22T00:00:00Z'),
  })
  assert.equal(skipped.status, 'skipped')
  assert.equal(skippedManifestReads, 0)
})

test('missing manifest refreshes once, while fewer than five covers leaves old home untouched', async () => {
  const week = '2026-39'
  let reads = 0
  let refreshes = 0
  let puts = 0
  const missing = new Error('missing')
  missing.name = 'NoSuchKey'
  const storage = {
    async headHome() { throw Object.assign(new Error('missing home'), { name: 'NotFound' }) },
    async getManifest() {
      reads += 1
      if (reads === 1) throw missing
      return manifestFor(week, 4)
    },
    async putHome() { puts += 1 },
  }
  await assert.rejects(() => publishHome({
    frontendBucket: 'frontend', dataBucket: 'data', apiFunctionName: 'api', storage,
    invokeRefresh: async ({ payload }) => { refreshes += 1; assert.deepEqual(payload, { source: 'booksearch.featured.refresh' }) },
    clientIndexHtml: CLIENT_INDEX, renderHome: () => '<main />', fetchImpl: imageFetch(),
    decodeImage: identityDecode,
    now: () => Date.parse('2026-09-22T00:00:00Z'),
  }), HomePublisherError)
  assert.equal(reads, 2)
  assert.equal(refreshes, 1)
  assert.equal(puts, 0)
})

test('cover redirects stay on the allowlist and stop at the redirect limit', async () => {
  let calls = 0
  const data = await fetchImageDataUri('https://books.google.com/start', {
    fetchImpl: async url => {
      calls += 1
      if (calls < 3) return response(Buffer.alloc(0), 'text/plain', 302, { location: `https://books.google.co.jp/step${calls}` })
      return response(PNG)
    },
    decodeImage: identityDecode,
  })
  assert.match(data, /^data:image\/jpeg;base64,\/9j\//)
  await assert.rejects(() => fetchImageDataUri('https://books.google.com/start', {
    maxRedirects: 3,
    fetchImpl: async (_url, options) => {
      options.signal.throwIfAborted?.()
      return response(Buffer.alloc(0), 'text/plain', 302, { location: 'https://books.google.com/next' })
    },
  }), /too many redirects/)
  await assert.rejects(() => fetchImageDataUri('https://books.google.com/start', {
    fetchImpl: async () => response(PNG, 'image/svg+xml'),
  }), /unsupported cover content type/)
})

test('cover timeout does not publish', async () => {
  let puts = 0
  const week = '2026-39'
  await assert.rejects(() => publishHome({
    frontendBucket: 'frontend', dataBucket: 'data', apiFunctionName: 'api',
    storage: fakeStorage(manifestFor(week), { onPut: () => { puts += 1 } }),
    invokeRefresh: async () => {}, clientIndexHtml: CLIENT_INDEX, renderHome: () => '<main />',
    decodeImage: identityDecode,
    imageTimeoutMs: 1,
    fetchImpl: async (_url, { signal }) => await new Promise((resolve, reject) => {
      signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true })
    }),
    now: () => Date.parse('2026-09-22T00:00:00Z'),
  }), /timed out|succeeded/)
  assert.equal(puts, 0)
})

test('default decoder rejects signature-only payloads and emits a real normalized JPEG', async () => {
  const valid = await fetchImageDataUri('https://books.google.com/valid', {
    fetchImpl: async () => response(VALID_JPEG, 'image/jpeg'),
  })
  assert.match(valid, /^data:image\/jpeg;base64,\/9j\//)
  await assert.rejects(() => fetchImageDataUri('https://books.google.com/broken', {
    fetchImpl: async () => response(PNG),
  }), /Input buffer contains unsupported image format|corrupt|decoder/i)
})
