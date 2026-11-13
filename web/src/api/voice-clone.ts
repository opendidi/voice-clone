/*
 * MOSS-TTS-Nano 语音克隆 API
 * 对应后端 app/api/v1/voice_clone.py（/v1/voice-clone/*）
 *
 * 耗时推理统一走 /jobs 异步链路：提交 -> 轮询 -> 取消。
 * 所有请求（含 fetch 版句级合成）共用 buildHeaders，避免鉴权头不一致。
 */
import http from '@/utils/request'
import { getCache } from '@/utils/auth'
import { unwrap, type Envelope } from '@/utils/envelope'

export type { Envelope } from '@/utils/envelope'

export function resolveBase(): string {
  const configured = String(import.meta.env.VITE_GLOB_API_URL || '').replace(/\/$/, '')
  if (!import.meta.env.DEV) return configured
  try {
    const override = new URLSearchParams(window.location.search).get('api')
    if (override && /^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?(\/.*)?$/i.test(override)) {
      return override.replace(/\/$/, '')
    }
  } catch {
    /* 忽略非法 query */
  }
  return configured
}

const BASE = resolveBase()

const API = {
  status: '/voice-clone/status',
  jobs: '/voice-clone/jobs',
  audio: '/voice-clone/audio',
  profiles: '/voice-clone/profiles',
  reset: '/voice-clone/reset',
  ttsSegment: '/voice-clone/tts/segment',
} as const

/** axios 与原生 fetch 共用的请求头，保证 API Key 模式下鉴权一致 */
export function buildHeaders(json = false): Record<string, string> {
  const headers: Record<string, string> = {
    'X-Requested-With': 'XMLHttpRequest',
    'X-Request-Id': crypto.randomUUID().replace(/-/g, '').slice(0, 16),
  }
  if (json) headers['Content-Type'] = 'application/json'
  const token = getCache<string>('auth-token')
  if (token) headers.Authorization = `Bearer ${token}`
  return headers
}

export interface EngineCheck {
  ok: boolean
  detail: string
  label: string
}

export interface CloneStatus {
  ready: boolean
  backend: string
  mode: 'real' | 'demo' | 'unavailable'
  model: string
  checks: Record<string, EngineCheck>
  available_backends: string[]
  config: Record<string, string | number>
  limits: {
    max_ref_bytes: number
    max_text_chars: number
    min_ref_seconds: number
    max_ref_seconds: number
  }
  allow_backend_override?: boolean
  max_inference_slots?: number
  long_tts_max_chars?: number
  hint: string
  setup_commands: string[]
}

export interface CloneResult {
  id: string
  url: string
  backend: string
  demo: boolean
  model: string
  elapsed_ms: number
  sample_rate: number
  duration: number
  channels: number
  size: number
  text: string
  log: string
  truncated?: boolean
  segments?: number
  reference?: {
    duration: number
    sample_rate: number
    channels: number
    size: number
  }
  created_at?: number
}

export interface JobStatus {
  id: string
  kind: 'clone' | 'tts' | 'long_tts' | string
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'canceled'
  stage: string
  percent: number
  detail: string
  result: CloneResult | null
  error: string
  hint: string
  created_at: number
  updated_at: number
  elapsed_ms: number
}

export interface CloneParams {
  text: string
  audio: Blob
  sample_mode?: string
  seed?: string
  cpu_threads?: number
  max_new_frames?: number
  backend?: string
}

export interface VoiceProfile {
  id: string
  name: string
  file: string
  sample_rate: number
  channels: number
  duration: number
  size: number
  created_at: number
}

export interface VoiceTtsJobParams {
  text: string
  profile_id: string
  long?: boolean
  sample_mode?: string
  seed?: string
  cpu_threads?: number
  max_new_frames?: number
}

const UPLOAD_TIMEOUT = 120000

export async function apiVoiceCloneStatus(): Promise<CloneStatus> {
  return unwrap(await http.get<Envelope<CloneStatus>>(API.status, { baseURL: BASE }))
}

/** 上传参考音频 + 文本，创建克隆任务（202） */
export async function apiCreateCloneJob(params: CloneParams): Promise<JobStatus> {
  const fd = new FormData()
  fd.append('audio', params.audio, 'reference.wav')
  fd.append('text', params.text)
  fd.append('sample_mode', params.sample_mode || 'fixed')
  if (params.seed) fd.append('seed', params.seed)
  if (params.cpu_threads) fd.append('cpu_threads', String(params.cpu_threads))
  if (params.max_new_frames) fd.append('max_new_frames', String(params.max_new_frames))
  if (params.backend) fd.append('backend', params.backend)
  return unwrap(await http.post<Envelope<JobStatus>>(API.jobs, fd, {
    baseURL: BASE,
    headers: { 'Content-Type': 'multipart/form-data', ...buildHeaders() },
    timeout: UPLOAD_TIMEOUT,
  }))
}

/** 用已存音色合成文本；long=true 走自动分句 + 拼接 */
export async function apiCreateTtsJob(params: VoiceTtsJobParams): Promise<JobStatus> {
  return unwrap(await http.post<Envelope<JobStatus>>(API.jobs, {
    text: params.text,
    profile_id: params.profile_id,
    long: Boolean(params.long),
    sample_mode: params.sample_mode,
    seed: params.seed,
    cpu_threads: params.cpu_threads,
    max_new_frames: params.max_new_frames,
  }, { baseURL: BASE, headers: buildHeaders(true), timeout: UPLOAD_TIMEOUT }))
}

export async function apiGetJob(id: string): Promise<JobStatus> {
  return unwrap(await http.get<Envelope<JobStatus>>(`${API.jobs}/${id}`, {
    baseURL: BASE, headers: buildHeaders(),
  }))
}

export async function apiCancelJob(id: string): Promise<JobStatus> {
  return unwrap(await http.delete<Envelope<JobStatus>>(`${API.jobs}/${id}`, {
    baseURL: BASE, headers: buildHeaders(),
  }))
}

export function voiceCloneAudioUrl(id: string, download = false): string {
  return `${BASE}${API.audio}/${id}${download ? '?download=1' : ''}`
}

/**
 * 音频用 fetch 带鉴权头下载成 Blob。<audio src> / <a download> 无法自定义请求头，
 * 启用 API Key 后必须走这条路，否则播放器会拿到 401。
 */
export async function apiFetchAudio(id: string): Promise<Blob> {
  const resp = await fetch(voiceCloneAudioUrl(id), { headers: buildHeaders() })
  if (!resp.ok) throw new Error(`音频加载失败（${resp.status}）`)
  return resp.blob()
}

/** 主动删除自己的合成音频与元数据（含合成文本），不必等 1 小时过期 */
export async function apiDeleteGeneratedAudio(id: string): Promise<void> {
  unwrap(await http.delete<Envelope<{ id: string }>>(`${API.audio}/${id}`, {
    baseURL: BASE, headers: buildHeaders(),
  }))
}

/** 试听语音档案里的参考音频（声纹数据，走带鉴权头的 fetch） */
export async function apiFetchProfileAudio(id: string): Promise<Blob> {
  const resp = await fetch(`${BASE}${API.profiles}/${id}/audio`, { headers: buildHeaders() })
  if (!resp.ok) throw new Error(`参考音频加载失败（${resp.status}）`)
  return resp.blob()
}

// ── 语音档案（命名音色，按用户隔离）────────────────────────

export async function apiVoiceProfiles(): Promise<VoiceProfile[]> {
  const res = await http.get<Envelope<{ profiles: VoiceProfile[] }>>(API.profiles, {
    baseURL: BASE, headers: buildHeaders(),
  })
  return unwrap(res).profiles || []
}

export async function apiSaveVoiceProfile(name: string, audio: Blob): Promise<VoiceProfile> {
  const fd = new FormData()
  fd.append('audio', audio, 'reference.wav')
  fd.append('name', name)
  const res = await http.post<Envelope<{ profile: VoiceProfile }>>(API.profiles, fd, {
    baseURL: BASE,
    headers: { 'Content-Type': 'multipart/form-data', ...buildHeaders() },
    timeout: UPLOAD_TIMEOUT,
  })
  return unwrap(res).profile
}

export async function apiDeleteVoiceProfile(id: string): Promise<void> {
  await http.delete<Envelope<{ ok: boolean }>>(`${API.profiles}/${id}`, {
    baseURL: BASE, headers: buildHeaders(),
  })
}

export interface ResetDataResult {
  deleted_profiles: number
  deleted_jobs: number
  deleted_outputs: number
  message: string
}

/**
 * 一键清空当前用户的数据（声纹档案 + 任务 + 合成产物）。
 * 销毁性操作，后端按当前登录用户归属隔离删除；前端会二次确认。
 */
export async function apiResetData(): Promise<ResetDataResult> {
  return unwrap(await http.post<Envelope<ResetDataResult>>(API.reset, {
    baseURL: BASE, headers: buildHeaders(true),
  }))
}

/**
 * 句级合成（实时播报专用）：与 axios 请求共用 buildHeaders，
 * 避免单机多用户模式下因缺少身份头而落到别的 uid。
 */
export async function apiVoiceTtsSegment(
  text: string,
  profileId: string,
  signal?: AbortSignal,
): Promise<CloneResult> {
  const resp = await fetch(`${BASE}${API.ttsSegment}`, {
    method: 'POST',
    headers: buildHeaders(true),
    body: JSON.stringify({ text, profile_id: profileId }),
    ...(signal ? { signal } : {}),
  })
  const body = (await resp.json().catch(() => null)) as Envelope<CloneResult> | null
  if (resp.ok && body) return unwrap(body)
  throw new Error(body?.message || `句子合成失败（${resp.status}）`)
}
