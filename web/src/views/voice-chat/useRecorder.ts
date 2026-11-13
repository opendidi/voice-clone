/*
 * 浏览器录音 -> 16-bit PCM WAV Blob。
 * 用 Web Audio API 抓原始 PCM 再手动编码成 WAV，而非 MediaRecorder 的 webm/opus，
 * 这样后端（含真实 SenseVoice）能直接按 WAV 解析，演示链路也无需转码。
 */
import { onBeforeUnmount, ref } from 'vue'

function encodeWav(samples: Float32Array, sampleRate: number): Blob {
  const buffer = new ArrayBuffer(44 + samples.length * 2)
  const view = new DataView(buffer)
  const writeString = (off: number, s: string) => {
    for (let i = 0; i < s.length; i += 1) view.setUint8(off + i, s.charCodeAt(i))
  }
  writeString(0, 'RIFF')
  view.setUint32(4, 36 + samples.length * 2, true)
  writeString(8, 'WAVE')
  writeString(12, 'fmt ')
  view.setUint32(16, 16, true)
  view.setUint32(20, 1, true)
  view.setUint16(22, 1, true)
  view.setUint32(24, sampleRate, true)
  view.setUint32(28, sampleRate * 2, true)
  view.setUint16(32, 2, true)
  view.setUint16(34, 16, true)
  writeString(36, 'data')
  view.setUint32(40, samples.length * 2, true)
  let offset = 44
  for (let i = 0; i < samples.length; i += 1) {
    const s = Math.max(-1, Math.min(1, samples[i]))
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true)
    offset += 2
  }
  return new Blob([view], { type: 'audio/wav' })
}

export function useRecorder() {
  const recording = ref(false)
  const level = ref(0)
  const error = ref('')

  let ctx: AudioContext | null = null
  let stream: MediaStream | null = null
  let processor: ScriptProcessorNode | null = null
  let source: MediaStreamAudioSourceNode | null = null
  let silent: GainNode | null = null
  let chunks: Float32Array[] = []

  async function start() {
    if (recording.value) return
    error.value = ''
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const Ctx: typeof AudioContext =
        window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext
      ctx = new Ctx()
      source = ctx.createMediaStreamSource(stream)
      processor = ctx.createScriptProcessor(4096, 1, 1)
      chunks = []
      processor.onaudioprocess = (e: AudioProcessingEvent) => {
        const ch = e.inputBuffer.getChannelData(0)
        chunks.push(new Float32Array(ch))
        let sum = 0
        for (let i = 0; i < ch.length; i += 1) sum += ch[i] * ch[i]
        level.value = Math.min(1, Math.sqrt(sum / ch.length) * 3)
      }
      // 静音增益：ScriptProcessor 必须接到图里才会触发回调，但不要把麦克风回放出来
      silent = ctx.createGain()
      silent.gain.value = 0
      source.connect(processor)
      processor.connect(silent)
      silent.connect(ctx.destination)
      recording.value = true
    } catch (e) {
      error.value = '无法访问麦克风：' + ((e as Error)?.message || String(e))
      cleanup()
    }
  }

  function cleanup() {
    try {
      if (processor) {
        processor.onaudioprocess = null
        processor.disconnect()
      }
      if (source) source.disconnect()
      if (silent) silent.disconnect()
      if (stream) stream.getTracks().forEach((t) => t.stop())
      if (ctx) void ctx.close()
    } catch {
      /* 忽略清理异常 */
    }
    processor = null
    source = null
    silent = null
    stream = null
    ctx = null
  }

  function stop(): Blob {
    if (!recording.value) return new Blob()
    recording.value = false
    level.value = 0
    const sampleRate = ctx?.sampleRate || 48000
    const total = chunks.reduce((a, c) => a + c.length, 0)
    const all = new Float32Array(total)
    let offset = 0
    for (const c of chunks) {
      all.set(c, offset)
      offset += c.length
    }
    cleanup()
    chunks = []
    return encodeWav(all, sampleRate)
  }

  onBeforeUnmount(() => {
    if (recording.value) stop()
  })

  return { recording, level, error, start, stop }
}
