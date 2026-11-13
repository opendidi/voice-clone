/*
 * 后端统一响应封装（code/data/message/...）与解包逻辑。
 * 独立成模块，便于在不依赖 axios / window 的单元测试里直接校验容错行为。
 */
export interface Envelope<T> {
  code: number
  data: T
  message: string
  request_id?: string
  timestamp?: number
}

export function unwrap<T>(res: Envelope<T>): T {
  // 网关错误 / 413 HTML / 服务未启动都可能让响应体不是 envelope，
  // 这里给出可读提示，避免前端报 "Cannot read properties of undefined"
  if (!res || typeof res !== 'object') {
    throw new Error('后端返回了非预期响应，请检查网关、服务状态或上传文件大小')
  }
  if (res.code === 200) return res.data
  throw new Error(res.message || `请求失败（code=${res.code ?? 'unknown'}）`)
}
