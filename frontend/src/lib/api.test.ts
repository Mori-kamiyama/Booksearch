import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiFetch, getFeaturedBooks, searchBookResults } from './api'

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

  it('coalesces featured requests, keeps only real IDs, and serves cached books', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ books: [{ id: 1, title: 'real' }, { id: -1, title: 'fake' }] })))
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
})
