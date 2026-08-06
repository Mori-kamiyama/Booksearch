export interface BrowserAprilTag {
  tagId: number
  center?: [number, number]
}

type OpenCV = any

function safeDelete(resource: OpenCV): void {
  try {
    resource?.delete?.()
  } catch {
    // Some mobile WASM builds expose a destructor as a null function. Cleanup
    // must not turn an otherwise successful tag detection into a fallback.
  }
}

let cvPromise: Promise<OpenCV> | null = null

async function loadOpenCV(): Promise<OpenCV> {
  if (!cvPromise) {
    cvPromise = (async () => {
      const imported = await import('@techstark/opencv-js')
      const module = (imported as any).default ?? imported
      const cv = module instanceof Promise ? await module : module
      if (cv.Mat) return cv
      await new Promise<void>((resolve, reject) => {
        cv.onRuntimeInitialized = () => resolve()
        cv.onAbort = (reason: unknown) => reject(new Error(`OpenCV.js aborted: ${String(reason)}`))
      })
      return cv
    })().catch(error => {
      // A failed WASM initialization must not poison all later retries.
      cvPromise = null
      throw error
    })
  }
  return cvPromise
}

/**
 * Fast browser-side AprilTag pass. This is intentionally only a tag-ID
 * detector; the backend remains authoritative for orientation and shelf
 * quadrant assignment.
 */
export async function detectBrowserAprilTags(canvas: HTMLCanvasElement): Promise<BrowserAprilTag[]> {
  const cv = await loadOpenCV()
  const source = cv.imread(canvas)
  const gray = new cv.Mat()
  const dictionary = cv.getPredefinedDictionary(cv.DICT_APRILTAG_36h11)
  const parameters = new cv.aruco_DetectorParameters()
  // The AprilTag-specific refinement path calls a null WASM function on some
  // mobile builds. SUBPIX is slower than NONE but broadly supported.
  if ('cornerRefinementMethod' in parameters && cv.CORNER_REFINE_SUBPIX != null) {
    parameters.cornerRefinementMethod = cv.CORNER_REFINE_SUBPIX
  }
  // Embind does not carry over the C++ default arguments. Both constructors
  // require the full argument list in this OpenCV.js build.
  const refine = new cv.aruco_RefineParameters(10, 3, true)
  const detector = new cv.aruco_ArucoDetector(dictionary, parameters, refine)
  const corners = new cv.MatVector()
  const ids = new cv.Mat()

  try {
    const channels = typeof source.channels === 'function' ? source.channels() : 4
    if (channels === 4) cv.cvtColor(source, gray, cv.COLOR_RGBA2GRAY)
    else if (channels === 3) cv.cvtColor(source, gray, cv.COLOR_RGB2GRAY)
    else source.copyTo(gray)
    detector.detectMarkers(gray, corners, ids)

    const values = ids.data32S ?? ids.data32F ?? []
    const tags: BrowserAprilTag[] = []
    for (let index = 0; index < values.length; index += 1) {
      const marker = corners.get(index)
      const points = marker.data32F ?? marker.data64F ?? []
      let center: [number, number] | undefined
      if (points.length >= 8) {
        center = [
          (points[0] + points[2] + points[4] + points[6]) / 4,
          (points[1] + points[3] + points[5] + points[7]) / 4,
        ]
      }
      tags.push({ tagId: Number(values[index]), center })
      safeDelete(marker)
    }
    return tags
  } finally {
    safeDelete(detector)
    safeDelete(refine)
    safeDelete(parameters)
    safeDelete(dictionary)
    safeDelete(ids)
    safeDelete(corners)
    safeDelete(gray)
    safeDelete(source)
  }
}

export async function isBrowserAprilTagReady(): Promise<boolean> {
  try {
    await loadOpenCV()
    return true
  } catch {
    return false
  }
}
