/*
 * 参考音频前端归一化与质量校验
 *
 * 模型侧（soundfile / ONNX runtime）只稳定支持 WAV/FLAC/OGG，而浏览器录音产出的是
 * webm/opus，用户上传的还可能是 mp3/m4a。统一在浏览器里解码并重采样成
 * 48kHz / 16-bit PCM WAV（模型原生采样率就是 48kHz），后端校验链路因此可以保持极简。
 */
import type { CloneStatus } from '@/api/voice-clone'

export const TARGET_RATE = 48000
export const MAX_REF_BYTES = 20 * 1024 * 1024
export const MIN_REF_SECONDS = 0.8
export const DEFAULT_MAX_SECONDS = 15
export const RECORD_MAX_SECONDS = 15

export interface AudioQuality {
  rms: number
  peak: number
  silent: boolean
  clipping: boolean
  warning: string
}

export interface NormalizedAudio {
  blob: Blob
  duration: number
  sampleRate: number
  channels: number
  size: number
  /** 音频长于上限被裁剪 */
  clipped: boolean
  quality: AudioQuality
}

type AudioContextCtor = typeof AudioContext

function getAudioContextCtor(): AudioContextCtor {
  const w = window as unknown as { AudioContext?: AudioContextCtor; webkitAudioContext?: AudioContextCtor }
  const Ctor = w.AudioContext || w.webkitAudioContext
  if (!Ctor) throw new Error('当前浏览器不支持 Web Audio API，请改用 Chrome / Edge')
  return Ctor
}

async function decode(source: Blob): Promise<AudioBuffer> {
  const Ctor = getAudioContextCtor()
  const ctx = new Ctor()
  try {
    return await ctx.decodeAudioData(await source.arrayBuffer())
  } catch {
    throw new Error('无法解码该音频文件，请换成 WAV / MP3 / M4A 格式重试')
  } finally {
    void ctx.close().catch(() => undefined)
  }
}

function writeAscii(view: DataView, offset: number, text: string): void {
  for (let i = 0; i < text.length; i += 1) view.setUint8(offset + i, text.charCodeAt(i))
}

/** AudioBuffer -> 标准 RIFF/WAVE（16-bit PCM，交错存储） */
export function encodeWav(buffer: AudioBuffer): Blob {
  const channels = Math.min(2, Math.max(1, buffer.numberOfChannels))
  const frames = buffer.length
  const blockAlign = channels * 2
  const dataSize = frames * blockAlign
  const arrayBuffer = new ArrayBuffer(44 + dataSize)
  const view = new DataView(arrayBuffer)

  writeAscii(view, 0, 'RIFF')
  view.setUint32(4, 36 + dataSize, true)
  writeAscii(view, 8, 'WAVE')
  writeAscii(view, 12, 'fmt ')
  view.setUint32(16, 16, true)
  view.setUint16(20, 1, true)
  view.setUint16(22, channels, true)
  view.setUint32(24, buffer.sampleRate, true)
  view.setUint32(28, buffer.sampleRate * blockAlign, true)
  view.setUint16(32, blockAlign, true)
  view.setUint16(34, 16, true)
  writeAscii(view, 36, 'data')
  view.setUint32(40, dataSize, true)

  const channelData: Float32Array[] = []
  for (let c = 0; c < channels; c += 1) channelData.push(buffer.getChannelData(c))

  let offset = 44
  for (let i = 0; i < frames; i += 1) {
    for (let c = 0; c < channels; c += 1) {
      const sample = Math.max(-1, Math.min(1, channelData[c][i] || 0))
      view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true)
      offset += 2
    }
  }
  return new Blob([arrayBuffer], { type: 'audio/wav' })
}

/** 粗略音质检测：静音 / 削波会显著影响克隆效果，提前提示用户重录 */
export function analyzeQuality(buffer: AudioBuffer): AudioQuality {
  const frames = buffer.length
  const step = Math.max(1, Math.floor(frames / 200000))
  let sumSq = 0
  let peak = 0
  let clipped = 0
  let count = 0
  for (let c = 0; c < buffer.numberOfChannels; c += 1) {
    const data = buffer.getChannelData(c)
    for (let i = 0; i < frames; i += step) {
      const value = Math.abs(data[i] || 0)
      sumSq += value * value
      if (value > peak) peak = value
      if (value >= 0.995) clipped += 1
      count += 1
    }
  }
  const rms = count ? Math.sqrt(sumSq / count) : 0
  const silent = rms < 0.003
  const clipping = count > 0 && clipped / count > 0.01
  let warning = ''
  if (silent) warning = '这段音频几乎是静音，克隆效果会很差，建议重录一段清晰人声'
  else if (clipping) warning = '检测到明显削波（音量过载），建议降低录音音量后重录'
  else if (rms < 0.02) warning = '音量偏小，建议在安静环境下靠近麦克风重录'
  return { rms, peak, silent, clipping, warning }
}

/** 解码 -> 重采样到 48kHz -> 裁剪 -> 编码 WAV -> 质量提示 */
export async function normalizeToWav(
  source: Blob,
  maxSeconds: number = DEFAULT_MAX_SECONDS,
): Promise<NormalizedAudio> {
  if (!source || source.size === 0) throw new Error('音频内容为空')
  if (source.size > MAX_REF_BYTES) {
    throw new Error(`音频文件超过 ${MAX_REF_BYTES / 1024 / 1024}MB 上限，请裁剪后重试`)
  }
  const decoded = await decode(source)
  if (!decoded.length || !Number.isFinite(decoded.duration)) {
    throw new Error('音频时长异常，请换一段录音')
  }
  if (decoded.duration < MIN_REF_SECONDS) {
    throw new Error(`参考音频太短（${decoded.duration.toFixed(1)}s），请录满 3~10 秒清晰人声`)
  }

  const frames = Math.max(
    1,
    Math.min(Math.ceil(decoded.duration * TARGET_RATE), Math.floor(maxSeconds * TARGET_RATE)),
  )
  const channels = Math.min(2, Math.max(1, decoded.numberOfChannels))
  const offline = new OfflineAudioContext(channels, frames, TARGET_RATE)
  const node = offline.createBufferSource()
  node.buffer = decoded
  node.connect(offline.destination)
  node.start(0)
  const rendered = await offline.startRendering()

  const blob = encodeWav(rendered)
  return {
    blob,
    duration: Number((rendered.length / rendered.sampleRate).toFixed(2)),
    sampleRate: rendered.sampleRate,
    channels,
    size: blob.size,
    clipped: decoded.duration > maxSeconds,
    quality: analyzeQuality(rendered),
  }
}

/** 后端状态接口里的限制值同步到前端（上传上限随配置变化） */
export function limitsFromStatus(status: CloneStatus | null) {
  return {
    maxRefBytes: status?.limits?.max_ref_bytes || MAX_REF_BYTES,
    maxTextChars: status?.limits?.max_text_chars || 1000,
    minRefSeconds: status?.limits?.min_ref_seconds || MIN_REF_SECONDS,
    maxRefSeconds: status?.limits?.max_ref_seconds || 30,
    longTtsMaxChars: status?.long_tts_max_chars || 5000,
  }
}

export function formatBytes(bytes: number): string {
  if (!bytes) return '0 B'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

export function formatDuration(seconds: number): string {
  if (!seconds || seconds < 0) return '0.0s'
  return `${seconds.toFixed(1)}s`
}
