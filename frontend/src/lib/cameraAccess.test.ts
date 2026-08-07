import { describe, expect, it } from 'vitest'
import { cameraAccessErrorMessage } from './cameraAccess'

function namedError(name: string) {
  const error = new Error(name)
  error.name = name
  return error
}

describe('camera access error messages', () => {
  it('explains user-agent permission rejection in Japanese', () => {
    expect(cameraAccessErrorMessage(namedError('NotAllowedError'), true)).toContain('サイト設定でカメラを許可')
  })

  it('explains insecure contexts before the browser error', () => {
    expect(cameraAccessErrorMessage(namedError('NotAllowedError'), false)).toContain('HTTPS')
  })

  it('explains missing and busy cameras separately', () => {
    expect(cameraAccessErrorMessage(namedError('NotFoundError'), true)).toContain('見つかりません')
    expect(cameraAccessErrorMessage(namedError('NotReadableError'), true)).toContain('他のアプリ')
  })
})
