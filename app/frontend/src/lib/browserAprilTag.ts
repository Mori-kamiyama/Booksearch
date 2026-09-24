export interface BrowserAprilTag {
  tagId: number
  center?: [number, number]
}

type OpenCV = any

let cvPromise: Promise<OpenCV> | null = null

async function loadOpenCV(): Promise<OpenCV> {
  if (!cvPromise) {
    cvPromise = (async () => {
      const imported = await import('@techstark/opencv-js')
      const module = (imported as any).default ?? imported
      if (module instanceof Promise) return module
      if (module.Mat) return module
      await new Promise<void>(resolve => {
        module.onRuntimeInitialized = () => resolve()
      })
      return module
    })()
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
  const refine = new cv.aruco_RefineParameters()
  const detector = new cv.aruco_ArucoDetector(dictionary, parameters, refine)
  const corners = new cv.MatVector()
  const ids = new cv.Mat()

  try {
    cv.cvtColor(source, gray, cv.COLOR_RGBA2GRAY)
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
      marker.delete()
    }
    return tags
  } finally {
    source.delete()
    gray.delete()
    corners.delete()
    ids.delete()
    dictionary.delete?.()
    parameters.delete?.()
    refine.delete?.()
    detector.delete?.()
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
