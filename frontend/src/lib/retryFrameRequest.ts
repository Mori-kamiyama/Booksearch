// Only use for idempotent frame initialization, PUT and commit requests.
export async function retryFrameRequest(request: () => Promise<Response>, signal: AbortSignal): Promise<Response> {
  for (let attempt = 0; ; attempt++) {
    signal.throwIfAborted()
    try {
      const response = await request()
      if (![408, 429, 500, 502, 503, 504].includes(response.status) || attempt === 3) return response
      await response.body?.cancel()
    } catch (error) {
      if (signal.aborted || attempt === 3) throw error
    }
    await new Promise<void>((resolve, reject) => {
      const abort = () => { clearTimeout(timer); signal.removeEventListener('abort', abort); reject(signal.reason) }
      const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve() }, 500 * 2 ** attempt)
      signal.addEventListener('abort', abort, { once: true })
      if (signal.aborted) abort()
    })
  }
}
