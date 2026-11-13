import axios from 'axios'
import { getCache } from '@/utils/auth'

/**
 * 精简版请求封装：响应拦截器直接返回后端 envelope（code/data/message），
 * 与从 pypano 带过来的 api/voice-clone.ts 契约一致。
 */
const instance = axios.create({
  baseURL: import.meta.env.VITE_GLOB_API_URL || '/v1',
  timeout: 30000,
})

instance.interceptors.request.use((config) => {
  config.headers['X-Requested-With'] = 'XMLHttpRequest'
  const token = getCache<string>('auth-token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

instance.interceptors.response.use(
  (response) => response.data,
  (error) => Promise.reject(error),
)

export interface HttpClient {
  get<T = unknown>(url: string, config?: unknown): Promise<T>
  post<T = unknown>(url: string, data?: unknown, config?: unknown): Promise<T>
  delete<T = unknown>(url: string, config?: unknown): Promise<T>
}

export default instance as unknown as HttpClient
