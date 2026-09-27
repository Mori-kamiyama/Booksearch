import { afterEach, expect, it, vi } from 'vitest'
import { retryFrameRequest } from './retryFrameRequest'
afterEach(() => vi.useRealTimers())
it('recovers from network loss and 503 with bounded retries', async () => {
  vi.useFakeTimers()
  const request = vi.fn().mockRejectedValueOnce(new TypeError('network')).mockResolvedValueOnce(new Response('', { status: 503 })).mockResolvedValue(new Response('ok'))
  const result = retryFrameRequest(request, new AbortController().signal)
  await vi.runAllTimersAsync()
  expect((await result).status).toBe(200)
  expect(request).toHaveBeenCalledTimes(3)
})
it('does not retry permanent errors and stops after four attempts', async () => {
  vi.useFakeTimers()
  const permanent = vi.fn().mockResolvedValue(new Response('', { status: 409 }))
  expect((await retryFrameRequest(permanent, new AbortController().signal)).status).toBe(409)
  expect(permanent).toHaveBeenCalledTimes(1)
  const busy = vi.fn().mockImplementation(async () => new Response('', { status: 503 }))
  const result = retryFrameRequest(busy, new AbortController().signal)
  await vi.runAllTimersAsync()
  expect((await result).status).toBe(503)
  expect(busy).toHaveBeenCalledTimes(4)
})
it('canceling a backoff prevents any further request', async () => {
  vi.useFakeTimers()
  const controller = new AbortController()
  const request = vi.fn().mockImplementation(async () => new Response('', { status: 503 }))
  const result = retryFrameRequest(request, controller.signal)
  const rejection = expect(result).rejects.toBeDefined()
  await vi.advanceTimersByTimeAsync(1)
  controller.abort()
  await rejection
  await vi.runAllTimersAsync()
  expect(request).toHaveBeenCalledTimes(1)
})
