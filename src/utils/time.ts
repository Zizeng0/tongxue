export function formatElapsed(seconds: number) {
  const total = Math.max(0, Math.floor(seconds))
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor(total % 3600 / 60)
  const remaining = total % 60
  return hours ? `${hours}时${minutes}分${remaining}秒` : minutes ? `${minutes}分${remaining}秒` : `${remaining}秒`
}
