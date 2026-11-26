export const defaultFontSize = 14.3
export const fontSizes = [12, 14, defaultFontSize, 16, 18, 20, 22, 24]
const storageKey = 'tongxue.fontSize'

export function parseFontSize(value: string) {
  const size = Number(value.trim())
  return /^\d{1,2}(?:\.\d)?$/.test(value.trim()) && size >= 12 && size <= 24 ? size : null
}

export function readFontSize() {
  try {
    return parseFontSize(localStorage.getItem(storageKey) ?? '') ?? defaultFontSize
  } catch {
    return defaultFontSize
  }
}

export function saveFontSize(size: number) {
  try {
    localStorage.setItem(storageKey, String(size))
  } catch {}
}
