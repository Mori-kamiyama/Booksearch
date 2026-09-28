import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiFetch, getFeaturedBooks, searchBookResults } from './api'
import type { Book } from './types'

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

describe('API stability', () => {
  it('keeps caller cancellation active after response headers', async () => {
    let signal: AbortSignal | undefined
    vi.stubGlobal('fetch', vi.fn(async (_url, init) => {
      signal = init.signal
      return new Response('{}')
    }))
    const controller = new AbortController()
    await apiFetch('/api/test', { signal: controller.signal })
    expect(signal?.aborted).toBe(false)
    controller.abort()
    expect(signal?.aborted).toBe(true)
  })

  it('distinguishes missing totals from zero totals', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(new Response('{"books":[]}'))
      .mockResolvedValueOnce(new Response('{"books":[],"total":0}')))
    expect((await searchBookResults('x')).total).toBeNull()
    expect((await searchBookResults('x')).total).toBe(0)
  })

  it('passes the requested search offset while preserving the total', async () => {
    let requested = ''
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      requested = url
      return new Response('{"books":[{"id":2,"title":"second"}],"total":31}')
    }))
    const result = await searchBookResults('two words', 30, 30)
    expect(requested).toContain('/api/books/search?q=two%20words&limit=30&offset=30')
    expect(result.total).toBe(31)
    expect(result.books[0].id).toBe(2)
  })

  it('rejects unsafe offsets instead of sending an imprecise integer', async () => {
    let requested = ''
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      requested = url
      return new Response('{"books":[],"total":65}')
    }))
    await searchBookResults('alpha', 30, Number.MAX_SAFE_INTEGER + 1)
    expect(requested).toContain('offset=0')
  })

  it('coalesces featured requests and serves cached books', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ books: [{ id: 1, title: 'real' }] })))
    vi.stubGlobal('fetch', fetchMock)
    const [first, second] = await Promise.all([getFeaturedBooks(91), getFeaturedBooks(91)])
    expect(first).toEqual([{ id: 1, title: 'real' }])
    expect(second).toEqual(first)
    expect(await getFeaturedBooks(91)).toEqual(first)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 360_000)
    fetchMock.mockRejectedValueOnce(new Error('offline'))
    expect(await getFeaturedBooks(91)).toEqual(first)
  })

  it('rejects a malformed featured response and keeps a previous cache', async () => {
    const values = new Map([
      ['booksearch:featured:v1:relative:96', JSON.stringify({
        version: 1,
        week: '1900-1',
        savedAt: Date.now(),
        books: [{ id: 96, title: 'previous' }],
      })],
    ])
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => { values.set(key, value) },
      removeItem: (key: string) => { values.delete(key) },
      clear: () => { values.clear() },
      key: (index: number) => [...values.keys()][index] ?? null,
      get length() { return values.size },
    })
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ books: [{ id: 97, title: 12 }] }))))
    vi.resetModules()
    const api = await import('./api')
    await expect(api.getFeaturedBooks(96)).resolves.toEqual([{ id: 96, title: 'previous' }])

    vi.resetModules()
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ error: 'invalid' }))))
    const freshApi = await import('./api')
    await expect(freshApi.getFeaturedBooks(97)).rejects.toThrow('invalid featured response')
  })

  it('hydrates featured books from persistent storage without a request', async () => {
    const values = new Map<string, string>()
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => { values.set(key, value) },
      removeItem: (key: string) => { values.delete(key) },
      clear: () => { values.clear() },
      key: (index: number) => [...values.keys()][index] ?? null,
      get length() { return values.size },
    })
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ books: [{ id: 92, title: 'persisted' }] })))
    vi.stubGlobal('fetch', fetchMock)
    const firstApi = await import('./api')
    await expect(firstApi.getFeaturedBooks(92)).resolves.toEqual([{ id: 92, title: 'persisted' }])
    expect(values.has('booksearch:featured:v1:relative:92')).toBe(true)

    vi.resetModules()
    fetchMock.mockRejectedValue(new Error('offline'))
    const reloadedApi = await import('./api')
    await expect(reloadedApi.getFeaturedBooks(92)).resolves.toEqual([{ id: 92, title: 'persisted' }])
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('serves an old-week cache immediately and publishes a successful refresh', async () => {
    const values = new Map([
      ['booksearch:featured:v1:relative:93', JSON.stringify({
        version: 1,
        week: '1900-1',
        savedAt: Date.now(),
        books: [{ id: 93, title: 'old week' }],
      })],
    ])
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => { values.set(key, value) },
      removeItem: (key: string) => { values.delete(key) },
      clear: () => { values.clear() },
      key: (index: number) => [...values.keys()][index] ?? null,
      get length() { return values.size },
    })
    let resolveResponse: ((response: Response) => void) | undefined
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(resolve => { resolveResponse = resolve })))
    vi.resetModules()
    const api = await import('./api')
    const refreshed: Book[] = []
    const unsubscribe = api.subscribeFeaturedBooks(93, books => refreshed.push(...books))
    await expect(api.getFeaturedBooks(93)).resolves.toEqual([{ id: 93, title: 'old week' }])
    expect(fetch).toHaveBeenCalledTimes(1)
    resolveResponse?.(new Response(JSON.stringify({ books: [{ id: 94, title: 'new week' }] })))
    await vi.waitFor(() => expect(refreshed).toEqual([{ id: 94, title: 'new week' }]))
    unsubscribe()
  })

  it('treats malformed or unavailable storage as a cache miss', async () => {
    vi.stubGlobal('localStorage', {
      getItem: () => { throw new Error('storage denied') },
      setItem: () => { throw new Error('storage denied') },
      removeItem: () => undefined,
      clear: () => undefined,
      key: () => null,
      length: 0,
    })
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ books: [{ id: 95, title: 'network' }] })))
    vi.stubGlobal('fetch', fetchMock)
    vi.resetModules()
    const api = await import('./api')
    await expect(api.getFeaturedBooks(95)).resolves.toEqual([{ id: 95, title: 'network' }])
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('does not hydrate a cache entry with malformed book fields', async () => {
    const values = new Map([
      ['booksearch:featured:v1:relative:98', JSON.stringify({
        version: 1,
        week: '1900-1',
        savedAt: Date.now(),
        books: [{ id: 98, title: 'broken', thumbnail: 42 }],
      })],
    ])
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => { values.set(key, value) },
      removeItem: (key: string) => { values.delete(key) },
      clear: () => { values.clear() },
      key: (index: number) => [...values.keys()][index] ?? null,
      get length() { return values.size },
    })
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ books: [{ id: 99, title: 'network' }] })))
    vi.stubGlobal('fetch', fetchMock)
    vi.resetModules()
    const api = await import('./api')
    await expect(api.getFeaturedBooks(98)).resolves.toEqual([{ id: 99, title: 'network' }])
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})
