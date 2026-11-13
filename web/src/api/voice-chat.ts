/*
 * 语音聊天接口（MVP）：录音 -> ASR -> LLM -> TTS 一回合。
 * 对应后端 app/api/v1/voice_chat.py（/v1/voice-chat/*）
 *
 * 合成产物复用 MOSS-TTS，音频仍由 /v1/voice-clone/audio/<id> 提供，
 * 因此播放/下载沿用 api/voice-clone 的 apiFetchAudio 与 voiceCloneAudioUrl。
 */
import http from '@/utils/request'
import { unwrap, type Envelope } from '@/utils/envelope'
import { buildHeaders, resolveBase } from '@/api/voice-clone'

const BASE = resolveBase()

export interface VoiceChatAudio {
  id: string
  url: string
  demo: boolean
  duration: number
  sample_rate: number
}

export interface VoiceChatResult {
  transcript: string
  reply: string
  audio: VoiceChatAudio
  backends: { asr: string; llm: string; tts: string }
}

export interface VoiceChatJobStatus {
  id: string
  kind: string
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'canceled'
  stage: string
  percent: number
  detail: string
  result: VoiceChatResult | null
  error: string
  hint: string
  created_at: number
  updated_at: number
  elapsed_ms: number
}

export interface VoiceChatCapability {
  model: string
  backend: string
  ready: boolean
  hint: string
}

export interface VoiceChatStatus {
  asr: VoiceChatCapability
  llm: VoiceChatCapability
  tts: { backend: string; ready: boolean; mode?: string; hint: string }
}

/** 历史轮次，用于把前几轮对话传给 LLM 形成多轮记忆。 */
export interface VoiceChatHistoryItem {
  role: 'user' | 'assistant'
  content: string
}

export async function apiVoiceChatStatus(): Promise<VoiceChatStatus> {
  return unwrap(
    await http.get<Envelope<VoiceChatStatus>>('/voice-chat/status', {
      baseURL: BASE,
      headers: buildHeaders(),
    }),
  )
}

export async function apiCreateVoiceChatTurn(
  audio: Blob,
  profileId: string,
  history?: VoiceChatHistoryItem[],
): Promise<VoiceChatJobStatus> {
  const fd = new FormData()
  fd.append('audio', audio, 'input.wav')
  if (profileId) fd.append('profile_id', profileId)
  if (history && history.length) fd.append('history', JSON.stringify(history))
  return unwrap(
    await http.post<Envelope<VoiceChatJobStatus>>('/voice-chat/turn', fd, {
      baseURL: BASE,
      headers: { 'Content-Type': 'multipart/form-data', ...buildHeaders() },
      timeout: 120000,
    }),
  )
}

export async function apiGetVoiceChatJob(id: string): Promise<VoiceChatJobStatus> {
  return unwrap(
    await http.get<Envelope<VoiceChatJobStatus>>(`/voice-chat/turn/${id}`, {
      baseURL: BASE,
      headers: buildHeaders(),
    }),
  )
}

export async function apiCancelVoiceChatJob(id: string): Promise<VoiceChatJobStatus> {
  return unwrap(
    await http.delete<Envelope<VoiceChatJobStatus>>(`/voice-chat/turn/${id}`, {
      baseURL: BASE,
      headers: buildHeaders(),
    }),
  )
}
