/** 本地缓存：与 pypano 的 getCache 保持同样的 JSON 存储格式。 */
export function getCache<T>(key: string): T | null {
  const raw = localStorage.getItem(key)
  if (!raw) return null
  try {
    return JSON.parse(raw) as T
  } catch {
    // 允许直接在 localStorage 里填纯文本的 API Key
    return raw as unknown as T
  }
}

export function setCache<T>(key: string, value: T): void {
  localStorage.setItem(key, JSON.stringify(value))
}

export function removeCache(key: string): void {
  localStorage.removeItem(key)
}
