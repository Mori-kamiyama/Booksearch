export function cameraAccessErrorMessage(
  error: unknown,
  secureContext = typeof window === 'undefined' || window.isSecureContext,
): string {
  if (!secureContext) {
    return 'カメラはHTTPS接続でのみ利用できます。HTTPSのページを開いて、もう一度お試しください。'
  }
  const name = error instanceof Error ? error.name : ''
  switch (name) {
    case 'NotAllowedError':
    case 'PermissionDeniedError':
      return 'カメラの使用が許可されていません。ブラウザのサイト設定でカメラを許可して、もう一度押してください。'
    case 'NotFoundError':
    case 'DevicesNotFoundError':
      return '利用できるカメラが見つかりませんでした。端末のカメラを確認してください。'
    case 'NotReadableError':
    case 'TrackStartError':
      return 'カメラを開始できませんでした。他のアプリでカメラを使用していないか確認してください。'
    case 'AbortError':
      return 'カメラの開始が中断されました。もう一度ボタンを押してください。'
    case 'SecurityError':
      return 'このページではカメラを利用できません。ブラウザまたはサイトの権限設定を確認してください。'
    default:
      return 'カメラにアクセスできませんでした。ブラウザのカメラ権限を確認してください。'
  }
}
