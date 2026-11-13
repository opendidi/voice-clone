<template>
  <div class="vc-page">
    <header class="vc-header">
      <div class="vc-header-inner">
        <div class="brand">
          <div class="brand-icon">
            <svg viewBox="0 0 32 32" width="26" height="26" aria-hidden="true">
              <g fill="none" stroke="#fff" stroke-width="2.6" stroke-linecap="round">
                <path d="M4 13v6M9.5 8v16M15 11v10M20.5 5v22M26 10v12" />
              </g>
            </svg>
          </div>
          <div class="brand-text">
            <h1>MOSS-TTS-Nano 语音克隆实验室</h1>
            <p>0.1B 参数 · 48kHz · 纯 CPU 推理 · 任务化推理链路</p>
          </div>
        </div>
        <div class="header-actions">
          <span class="status-pill" :class="statusClass">
            <i class="dot" />{{ statusText }}
          </span>
          <button class="ghost-btn" :disabled="statusLoading" @click="loadStatus">
            {{ statusLoading ? '检测中' : '刷新状态' }}
          </button>
        </div>
      </div>
    </header>

    <main class="vc-main">
      <section class="notice" :class="statusClass">
        <div class="notice-icon">
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="12" cy="12" r="9" /><path d="M12 8h.01M11 12h1v4h1" stroke-linecap="round" />
          </svg>
        </div>
        <div class="notice-body">
          <strong>{{ noticeTitle }}</strong>
          <span>{{ noticeHint }}</span>
        </div>
        <button v-if="status && !status.ready" class="text-btn" @click="useDemoBackend">
          用演示模式试链路
        </button>
      </section>

      <details v-if="status && !status.ready" class="setup-card">
        <summary>环境准备 / 引擎探测详情</summary>
        <div class="setup-inner">
          <div class="check-list">
            <div v-for="(item, key) in status.checks" :key="key" class="check-item">
              <span class="check-dot" :class="item.ok ? 'ok' : 'no'" />
              <span class="check-label">{{ item.label }}</span>
              <span class="check-detail" :title="item.detail">{{ item.detail }}</span>
            </div>
          </div>
          <div class="setup-cmd">
            <div class="setup-cmd-head">
              <span>快速开始（首次会自动下载 ONNX 权重）</span>
              <button class="text-btn" @click="copySetup">复制命令</button>
            </div>
            <pre>{{ status.setup_commands.join('\n') }}</pre>
            <p class="setup-tip">
              也可以把模型跑在独立环境里：配置 MOSS_TTS_PYTHON 指向该环境的 python.exe，
              或启动 <code>python scripts/moss_tts_bridge.py</code> 后配置 MOSS_TTS_SERVICE_URL。
            </p>
          </div>
        </div>
      </details>

      <div class="vc-grid">
        <section class="card">
          <div class="card-head">
            <span class="step">01</span>
            <div>
              <h2>参考音频</h2>
              <p>提供一段目标说话人的声音，模型从中提取音色</p>
            </div>
          </div>

          <div
            class="dropzone"
            :class="{ dragging, busy: converting }"
            role="button"
            tabindex="0"
            aria-label="选择或拖入参考音频文件"
            @dragover.prevent="dragging = true"
            @dragleave.prevent="dragging = false"
            @drop.prevent="onDrop"
            @click="fileInput?.click()"
            @keydown.enter.prevent="fileInput?.click()"
            @keydown.space.prevent="fileInput?.click()"
          >
            <input
              ref="fileInput"
              type="file"
              accept="audio/*,.wav,.mp3,.m4a,.flac,.ogg,.webm"
              hidden
              @change="onFileChange"
            />
            <svg viewBox="0 0 24 24" width="30" height="30" fill="none" stroke="currentColor" stroke-width="1.6">
              <path d="M12 16V4m0 0L8 8m4-4 4 4" stroke-linecap="round" stroke-linejoin="round" />
              <path d="M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" stroke-linecap="round" />
            </svg>
            <strong>{{ converting ? '正在转换为 48kHz WAV…' : '点击选择或拖入音频文件' }}</strong>
            <small>WAV / MP3 / M4A / FLAC / OGG / WebM，≤ 20MB</small>
          </div>

          <div class="record-row">
            <button v-if="!recording" class="outline-btn" :disabled="converting" @click="startRecord">
              <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8">
                <rect x="9" y="3" width="6" height="11" rx="3" />
                <path d="M5 11a7 7 0 0 0 14 0M12 18v3" stroke-linecap="round" />
              </svg>
              直接录音
            </button>
            <button v-else class="outline-btn recording" @click="stopRecord">
              <span class="rec-dot" />停止录音 · {{ recordSeconds.toFixed(1) }}s
            </button>
            <span class="record-tip">建议 3~10 秒清晰人声，最长 15 秒</span>
          </div>

          <div v-if="reference" class="ref-preview">
            <div class="ref-head">
              <div>
                <strong>{{ referenceName }}</strong>
                <span class="ref-sub">
                  已归一化 · {{ reference.sampleRate / 1000 }}kHz ·
                  {{ reference.channels === 2 ? '立体声' : '单声道' }} ·
                  {{ formatDuration(reference.duration) }} · {{ formatBytes(reference.size) }}
                </span>
              </div>
              <button class="text-btn danger" @click="clearReference">移除</button>
            </div>
            <p v-if="reference.clipped" class="quality-tip">音频超过 15 秒，已自动截取前 15 秒用于克隆。</p>
            <p v-if="reference.quality.warning" class="quality-tip warn">{{ reference.quality.warning }}</p>
            <audio :key="previewUrl" :src="previewUrl" controls class="audio-player" />
            <div class="ref-save">
              <input
                v-model="profileName"
                class="profile-name"
                maxlength="40"
                placeholder="给这个音色起个名字"
              />
              <button class="text-btn" :disabled="savingProfile" @click="saveProfile">
                {{ savingProfile ? '保存中…' : savedProfile ? '重新保存为档案' : '保存为语音档案' }}
              </button>
              <span v-if="savedProfile" class="ref-saved">已存为「{{ savedProfile.name }}」</span>
            </div>
          </div>
        </section>

        <section class="card">
          <div class="card-head">
            <span class="step">02</span>
            <div>
              <h2>要合成的文本</h2>
              <p>模型会用参考音频的音色朗读这段文字</p>
            </div>
          </div>

          <textarea
            v-model="text"
            class="text-area"
            :maxlength="maxTextChars"
            placeholder="例如：欢迎体验 MOSS-TTS-Nano 语音克隆，这是一个只有一亿参数的小模型。"
          />
          <div class="text-foot">
            <div class="examples">
              <button v-for="(item, i) in examples" :key="i" class="chip" @click="text = item">
                示例 {{ i + 1 }}
              </button>
            </div>
            <span>{{ text.length }} / {{ maxTextChars }}</span>
          </div>

          <div class="param-grid">
            <label>
              <span>推理后端</span>
              <select v-model="form.backend">
                <option value="">自动选择</option>
                <option value="onnx">本机 ONNX（常驻 / CLI）</option>
                <option value="cli">CLI 子进程</option>
                <option value="http">远程服务</option>
                <option value="mock">演示模式（不加载模型）</option>
              </select>
            </label>
            <label>
              <span>采样模式</span>
              <select v-model="form.sample_mode">
                <option value="fixed">fixed（默认，最快）</option>
                <option value="greedy">greedy（确定性）</option>
                <option value="full">full</option>
              </select>
            </label>
            <label>
              <span>随机种子</span>
              <input v-model="form.seed" type="text" inputmode="numeric" placeholder="留空则随机" />
            </label>
            <label>
              <span>CPU 线程</span>
              <input v-model.number="form.cpu_threads" type="number" min="1" max="32" />
            </label>
            <label>
              <span>最大生成帧数</span>
              <input v-model.number="form.max_new_frames" type="number" min="32" max="1500" />
            </label>
            <label>
              <span>当前模型</span>
              <input :value="status?.model || '—'" readonly class="readonly" />
            </label>
          </div>

          <button class="primary-btn" :disabled="!canSubmit || running || converting" @click="run">
            <span v-if="running" class="spinner" />
            {{ running ? `推理中 ${elapsed.toFixed(1)}s` : '开始语音克隆' }}
          </button>
          <button v-if="running" class="cancel-btn" @click="cancel">取消任务</button>
          <div v-if="running" class="progress-box">
            <div class="progress-head">
              <span>{{ stage || '排队中' }}<template v-if="detail"> · {{ detail }}</template></span>
              <strong>{{ percent }}%</strong>
            </div>
            <div class="progress-track"><i :style="{ width: `${percent}%` }" /></div>
          </div>
          <p class="backend-hint">
            本次将使用：<strong :class="{ 'is-demo': effectiveBackend === 'mock' }">{{ effectiveBackendLabel }}</strong>
          </p>
          <p class="submit-tip">
            推理以异步任务执行，可离开本区等待或随时取消；服务端最多同时跑
            {{ status?.max_inference_slots || 2 }} 路推理。
            <template v-if="!status?.allow_backend_override">
              除演示模式外，「推理后端」由服务端 .env 决定，前端选择不生效。
            </template>
          </p>

          <div v-if="jobError" class="error-box">
            <strong>生成失败</strong>
            <span>{{ jobError }}</span>
            <span v-if="jobHint" class="error-hint">{{ jobHint }}</span>
          </div>
        </section>
      </div>

      <section class="card result-card">
        <div class="card-head">
          <span class="step">03</span>
          <div>
            <h2>生成结果</h2>
            <p>在线试听、下载，或切换查看最近几次生成</p>
          </div>
        </div>

        <div v-if="!current" class="empty-result">
          <svg viewBox="0 0 24 24" width="34" height="34" fill="none" stroke="currentColor" stroke-width="1.4">
            <path d="M9 18V5l10-2v13" stroke-linecap="round" stroke-linejoin="round" />
            <circle cx="6" cy="18" r="3" /><circle cx="16" cy="16" r="3" />
          </svg>
          <span>还没有生成结果，准备好参考音频并输入文本后点击「开始语音克隆」</span>
        </div>

        <template v-else>
          <div v-if="current.demo" class="demo-warning">
            演示模式：下面是一段<strong>固定 1.4 秒的提示音</strong>，时长与输入文本无关，仅用于验证上传 / 任务 / 播放链路，
            <strong>没有加载模型，也不是克隆出的人声</strong>。想让音频按文本长度合成，需在 .env 把
            <code>MOSS_TTS_BACKEND</code> 切回 http / auto / onnx。
          </div>
          <div class="result-main">
            <audio
              v-if="audioSource.url.value"
              :key="audioSource.url.value"
              :src="audioSource.url.value"
              controls
              class="audio-player"
            />
            <a
              v-if="audioSource.url.value"
              class="download-btn"
              :href="audioSource.url.value"
              :download="`voice-clone-${current.id}.wav`"
            >
              下载 WAV
            </a>
          </div>
          <p v-if="audioSource.failed.value" class="audio-fail">{{ audioSource.failed.value }}</p>
          <div class="meta-grid">
            <div><span>推理耗时</span><strong>{{ (current.elapsed_ms / 1000).toFixed(2) }} s</strong></div>
            <div><span>音频时长</span><strong>{{ formatDuration(current.duration) }}</strong></div>
            <div><span>采样率</span><strong>{{ (current.sample_rate / 1000).toFixed(0) }} kHz</strong></div>
            <div><span>文件大小</span><strong>{{ formatBytes(current.size) }}</strong></div>
            <div><span>后端</span><strong>{{ current.backend }}</strong></div>
            <div><span>声道</span><strong>{{ current.channels === 2 ? '立体声' : '单声道' }}</strong></div>
          </div>
          <p v-if="current.log" class="result-log">{{ current.log }}</p>
          <p class="result-text">合成文本：{{ current.text }}</p>
        </template>

        <div v-if="history.length > 1" class="history">
          <span class="history-label">最近生成</span>
          <button
            v-for="(item, i) in history"
            :key="item.id"
            class="history-chip"
            :class="{ active: current?.id === item.id }"
            @click="selectHistory(item)"
          >
            #{{ history.length - i }} · {{ item.backend }} · {{ formatDuration(item.duration) }}
          </button>
        </div>
      </section>

      <footer class="vc-footer">
        <span>独立项目 · 后端 /v1/voice-clone/* · 产物与声纹保存在 var/ 数据目录</span>
        <a href="https://github.com/OpenMOSS/MOSS-TTS-Nano" target="_blank" rel="noopener noreferrer">
          OpenMOSS / MOSS-TTS-Nano
        </a>
      </footer>
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import {
  apiCreateCloneJob,
  apiSaveVoiceProfile,
  apiVoiceCloneStatus,
  type CloneResult,
  type CloneStatus,
  type VoiceProfile,
} from '@/api/voice-clone'
import { formatBytes, formatDuration, normalizeToWav, RECORD_MAX_SECONDS, type NormalizedAudio } from './audio'
import { useAudioSource } from './useAudioSource'
import { pickError, useVoiceJob } from './useJob'

const status = ref<CloneStatus | null>(null)
const statusError = ref('')
const statusLoading = ref(false)

const text = ref('欢迎体验 MOSS-TTS-Nano 语音克隆，这是一个只有一亿参数的小模型。')
const maxTextChars = ref(1000)
const form = reactive({
  backend: '',
  sample_mode: 'fixed',
  seed: '',
  cpu_threads: 4,
  max_new_frames: 375,
})
const examples = [
  '欢迎体验 MOSS-TTS-Nano 语音克隆，这是一个只有一亿参数的小模型。',
  '清晨的阳光洒在窗台上，咖啡的香气慢慢散开，新的一天开始了。',
  'Hello! This is a quick voice cloning test running entirely on CPU.',
]

const fileInput = ref<HTMLInputElement | null>(null)
const dragging = ref(false)
const converting = ref(false)
const reference = ref<NormalizedAudio | null>(null)
const referenceName = ref('')
const previewUrl = ref('')

const profileName = ref('')
const savingProfile = ref(false)
const savedProfile = ref<VoiceProfile | null>(null)

const recording = ref(false)
const recordSeconds = ref(0)
let recorder: MediaRecorder | null = null
let recorderStream: MediaStream | null = null
let recorderChunks: Blob[] = []
let recordTimer: number | null = null

const {
  error: jobError, hint: jobHint, running, elapsed, percent, stage, detail,
  start, cancel, reset,
} = useVoiceJob()
const audioSource = useAudioSource()
const current = ref<CloneResult | null>(null)
const history = ref<CloneResult[]>([])
const HISTORY_KEY = 'voice-clone-history'

const canSubmit = computed(() => !!reference.value && text.value.trim().length > 0)
const effectiveBackend = computed(() => form.backend || (status.value?.mode === 'real' ? '' : 'mock'))
const effectiveBackendLabel = computed(() => {
  const labels: Record<string, string> = {
    '': '自动选择（真实推理）',
    onnx: '本机 ONNX（真实推理）',
    cli: 'CLI 子进程（真实推理）',
    http: '远程服务（真实推理）',
    mock: '演示模式（固定 1.4 秒提示音，非克隆）',
  }
  return labels[effectiveBackend.value] || effectiveBackend.value
})
const statusClass = computed(() => {
  if (statusError.value || !status.value) return 'is-warn'
  if (status.value.mode === 'real') return 'is-ok'
  if (status.value.mode === 'demo') return 'is-demo'
  return 'is-warn'
})
const statusText = computed(() => {
  if (statusLoading.value) return '检测中'
  if (!status.value) return '引擎状态未知'
  if (status.value.mode === 'real') return `引擎就绪 · ${status.value.backend}`
  if (status.value.mode === 'demo') return '演示模式'
  return '引擎未就绪'
})
const noticeTitle = computed(() => {
  if (statusError.value) return '后端接口不可达，请确认 Flask 已启动'
  if (status.value?.mode === 'real') return '引擎已就绪，可以开始真实语音克隆'
  if (status.value?.mode === 'demo') return '当前为演示模式，生成结果不会经过模型推理'
  if (form.backend === 'mock') return '后端已连通，模型未安装；当前默认用演示模式验证链路'
  return 'MOSS-TTS-Nano 引擎尚未就绪'
})
const noticeHint = computed(() => {
  if (statusError.value) return statusError.value
  if (status.value?.mode === 'real') {
    return status.value.backend === 'cli'
      ? '引擎已就绪；推理在独立虚拟环境中执行（CLI 子进程），主进程无需加载模型依赖。'
      : '引擎已就绪，可以开始真实语音克隆。'
  }
  return status.value?.hint || '正在读取引擎探测结果…'
})

function loadHistory() {
  try {
    const raw = localStorage.getItem(HISTORY_KEY)
    const parsed = raw ? JSON.parse(raw) : []
    const cutoff = Date.now() / 1000 - 3600
    history.value = Array.isArray(parsed)
      ? parsed.filter((it: CloneResult) => it?.id && (it.created_at || 0) >= cutoff).slice(0, 6)
      : []
  } catch {
    history.value = []
  }
}

function saveHistory() {
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(history.value))
  } catch {
    /* 隐私模式下 localStorage 可能不可写，忽略 */
  }
}

async function loadStatus() {
  statusLoading.value = true
  statusError.value = ''
  try {
    status.value = await apiVoiceCloneStatus()
    maxTextChars.value = status.value.limits?.max_text_chars || 1000
    if (status.value.mode !== 'real') form.backend = 'mock'
  } catch (e) {
    statusError.value = pickError(e)
    status.value = null
    form.backend = 'mock'
  } finally {
    statusLoading.value = false
  }
}

function useDemoBackend() {
  form.backend = 'mock'
  message.info('已切换到演示模式，可先验证上传与播放链路')
}

async function copySetup() {
  if (!status.value) return
  try {
    await navigator.clipboard.writeText(status.value.setup_commands.join('\n'))
    message.success('命令已复制')
  } catch {
    message.warning('复制失败，请手动选择命令文本')
  }
}

async function useAudio(source: Blob, name: string) {
  converting.value = true
  try {
    const normalized = await normalizeToWav(source)
    if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
    reference.value = normalized
    referenceName.value = name.replace(/\.[^.]+$/, '') + '.wav'
    previewUrl.value = URL.createObjectURL(normalized.blob)
    savedProfile.value = null
    if (normalized.clipped) message.info('音频超过 15 秒，已自动截取前 15 秒')
    if (normalized.quality.warning) message.warning(normalized.quality.warning)
  } catch (e) {
    message.error(pickError(e))
  } finally {
    converting.value = false
  }
}

function onFileChange(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (file) void useAudio(file, file.name)
  input.value = ''
}

function onDrop(e: DragEvent) {
  dragging.value = false
  const file = e.dataTransfer?.files?.[0]
  if (file) void useAudio(file, file.name)
}

function clearReference() {
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
  reference.value = null
  referenceName.value = ''
  previewUrl.value = ''
  savedProfile.value = null
  profileName.value = ''
}

async function saveProfile() {
  if (!reference.value) {
    message.warning('请先选择或录制一段参考音频')
    return
  }
  savingProfile.value = true
  try {
    const name = profileName.value.trim() || referenceName.value || '我的声音'
    const p = await apiSaveVoiceProfile(name, reference.value.blob)
    savedProfile.value = p
    message.success(`已保存为语音档案「${p.name}」，可在「音色库与播报」页使用`)
  } catch (e) {
    message.error(pickError(e))
  } finally {
    savingProfile.value = false
  }
}

async function startRecord() {
  if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
    message.error('当前浏览器不支持录音，请上传音频文件')
    return
  }
  try {
    recorderStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true },
    })
    const candidates = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus']
    const mimeType = candidates.find((item) => MediaRecorder.isTypeSupported(item)) || ''
    recorder = new MediaRecorder(recorderStream, mimeType ? { mimeType } : undefined)
    recorderChunks = []
    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) recorderChunks.push(event.data)
    }
    recorder.onstop = () => {
      const blob = new Blob(recorderChunks, { type: mimeType || 'audio/webm' })
      recorderStream?.getTracks().forEach((track) => track.stop())
      recorderStream = null
      void useAudio(blob, `录音-${new Date().toLocaleTimeString('zh-CN').replace(/:/g, '-')}.webm`)
    }
    recorder.start()
    recording.value = true
    recordSeconds.value = 0
    recordTimer = window.setInterval(() => {
      recordSeconds.value += 0.1
      if (recordSeconds.value >= RECORD_MAX_SECONDS) {
        message.info(`录音已达 ${RECORD_MAX_SECONDS} 秒上限，自动停止`)
        stopRecord()
      }
    }, 100)
  } catch {
    message.error('无法访问麦克风，请检查浏览器权限')
  }
}

function stopRecord() {
  if (recordTimer !== null) { window.clearInterval(recordTimer); recordTimer = null }
  recording.value = false
  if (recorder?.state === 'recording') recorder.stop()
}

function selectHistory(item: CloneResult) {
  current.value = item
  void audioSource.load(item.id)
}

function run() {
  if (!reference.value || !text.value.trim() || running.value) return
  const audio = reference.value.blob
  reset()
  current.value = null
  audioSource.revoke()
  void start(
    () => apiCreateCloneJob({
      text: text.value.trim(),
      audio,
      sample_mode: form.sample_mode,
      seed: form.seed.trim(),
      cpu_threads: form.cpu_threads,
      max_new_frames: form.max_new_frames,
      backend: form.backend || (status.value?.mode === 'real' ? '' : 'mock'),
    }),
    async (res) => {
      const item: CloneResult = { ...res, created_at: Math.floor(Date.now() / 1000) }
      current.value = item
      history.value = [item, ...history.value].slice(0, 6)
      saveHistory()
      await audioSource.load(item.id)
      message.success(res.demo ? '演示音频已生成（非真实克隆）' : '语音克隆完成')
    },
  )
}

onMounted(() => {
  loadHistory()
  void loadStatus()
})
onBeforeUnmount(() => {
  if (recordTimer !== null) window.clearInterval(recordTimer)
  if (recorder?.state === 'recording') recorder.stop()
  recorderStream?.getTracks().forEach((track) => track.stop())
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
})
</script>

<style scoped>
.vc-page { min-height: 100vh; background: #f4f6fb; color: #1c2333; }
.vc-header { background: linear-gradient(110deg, #4b5bd6 0%, #5c6ff0 45%, #12a9c4 100%); color: #fff; }
.vc-header-inner { max-width: 1240px; margin: 0 auto; padding: 22px 28px; display: flex; align-items: center; justify-content: space-between; gap: 20px; }
.brand { display: flex; align-items: center; gap: 14px; }
.brand-icon { width: 46px; height: 46px; border-radius: 13px; background: rgba(255,255,255,.18); display: grid; place-items: center; border: 1px solid rgba(255,255,255,.25); }
.brand-text h1 { margin: 0 0 4px; font-size: 19px; font-weight: 650; letter-spacing: .2px; }
.brand-text p { margin: 0; font-size: 12px; opacity: .8; }
.header-actions { display: flex; align-items: center; gap: 10px; }
.status-pill { display: inline-flex; align-items: center; gap: 7px; padding: 6px 12px; border-radius: 999px; font-size: 12px; background: rgba(255,255,255,.16); border: 1px solid rgba(255,255,255,.22); white-space: nowrap; }
.status-pill .dot { width: 7px; height: 7px; border-radius: 50%; background: #ffd166; }
.status-pill.is-ok .dot { background: #7bf1a8; }
.status-pill.is-demo .dot { background: #ffd166; }
.status-pill.is-warn .dot { background: #ff9c9c; }
.ghost-btn { padding: 7px 13px; border-radius: 8px; border: 1px solid rgba(255,255,255,.35); color: #fff; font-size: 12px; text-decoration: none; background: transparent; cursor: pointer; }
.ghost-btn:hover { background: rgba(255,255,255,.14); }
.ghost-btn:disabled { opacity: .55; cursor: not-allowed; }

.vc-main { max-width: 1240px; margin: 0 auto; padding: 24px 28px 40px; }
.notice { display: flex; align-items: center; gap: 12px; padding: 14px 18px; border-radius: 12px; border: 1px solid #f0d9a8; background: #fff9ec; color: #8a6314; margin-bottom: 16px; }
.notice.is-ok { border-color: #bde6cd; background: #f0fbf4; color: #1f7a48; }
.notice.is-demo { border-color: #f0d9a8; background: #fff9ec; color: #8a6314; }
.notice-icon { display: grid; place-items: center; flex-shrink: 0; }
.notice-body { display: flex; flex-direction: column; gap: 3px; flex: 1; min-width: 0; }
.notice-body strong { font-size: 13px; }
.notice-body span { font-size: 12px; opacity: .88; }
.text-btn { border: none; background: none; color: #4b5bd6; font-size: 12px; cursor: pointer; white-space: nowrap; padding: 4px 6px; }
.text-btn:hover { text-decoration: underline; }
.text-btn.danger { color: #d05353; }

.setup-card { background: #fff; border: 1px solid #e6eaf2; border-radius: 12px; margin-bottom: 18px; overflow: hidden; }
.setup-card summary { padding: 13px 18px; cursor: pointer; font-size: 13px; font-weight: 600; color: #44506a; }
.setup-inner { padding: 0 18px 18px; display: grid; grid-template-columns: minmax(240px, 1fr) minmax(320px, 1.4fr); gap: 20px; }
.check-list { display: flex; flex-direction: column; gap: 9px; }
.check-item { display: flex; align-items: center; gap: 8px; font-size: 12px; min-width: 0; }
.check-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.check-dot.ok { background: #34b37a; }
.check-dot.no { background: #e2a23c; }
.check-label { width: 92px; color: #4a5470; flex-shrink: 0; }
.check-detail { color: #8a93a8; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.setup-cmd-head { display: flex; justify-content: space-between; font-size: 12px; color: #4a5470; margin-bottom: 8px; }
.setup-card pre { margin: 0; background: #161b2c; color: #cfd8f5; border-radius: 9px; padding: 14px; font-size: 11.5px; line-height: 1.7; overflow-x: auto; white-space: pre; }
.setup-tip { margin: 10px 0 0; font-size: 11.5px; color: #828ba1; line-height: 1.6; }
.setup-tip code { background: #f0f2f8; padding: 1px 5px; border-radius: 4px; }

.vc-grid { display: grid; grid-template-columns: minmax(330px, 0.85fr) minmax(420px, 1.15fr); gap: 18px; align-items: start; }
.card { background: #fff; border: 1px solid #e6eaf2; border-radius: 14px; padding: 20px; box-shadow: 0 2px 12px rgba(30, 45, 90, .035); }
.card-head { display: flex; align-items: flex-start; gap: 12px; margin-bottom: 18px; }
.card-head .step { width: 28px; height: 28px; border-radius: 8px; background: #eef0ff; color: #4b5bd6; font-size: 12px; font-weight: 700; display: grid; place-items: center; flex-shrink: 0; }
.card-head h2 { margin: 0 0 3px; font-size: 15px; font-weight: 650; }
.card-head p { margin: 0; font-size: 12px; color: #8a93a8; }

.dropzone { border: 1.5px dashed #c9d1e6; border-radius: 12px; background: #fafbff; padding: 26px 16px; display: flex; flex-direction: column; align-items: center; gap: 8px; color: #6b76a0; cursor: pointer; transition: .18s; text-align: center; }
.dropzone:hover, .dropzone.dragging { border-color: #5c6ff0; background: #f2f4ff; color: #4b5bd6; }
.dropzone:focus-visible { outline: 3px solid rgba(92,111,240,.35); outline-offset: 2px; }
.dropzone.busy { opacity: .65; pointer-events: none; }
.dropzone strong { font-size: 13px; color: #3b4563; }
.dropzone small { font-size: 11px; color: #98a1b8; }
.record-row { display: flex; align-items: center; gap: 10px; margin-top: 14px; flex-wrap: wrap; }
.outline-btn { display: inline-flex; align-items: center; gap: 7px; padding: 8px 13px; border-radius: 8px; border: 1px solid #d9dfee; background: #fff; color: #4a5470; font-size: 12px; cursor: pointer; }
.outline-btn:hover { border-color: #5c6ff0; color: #4b5bd6; }
.outline-btn:disabled { opacity: .5; cursor: not-allowed; }
.outline-btn.recording { border-color: #e8a0a0; color: #c34a4a; background: #fff6f6; }
.rec-dot { width: 8px; height: 8px; border-radius: 50%; background: #e05252; animation: pulse 1s infinite; }
@keyframes pulse { 50% { opacity: .3; } }
.record-tip { font-size: 11px; color: #98a1b8; }
.ref-preview { margin-top: 16px; padding: 13px; border-radius: 10px; background: #f7f9ff; border: 1px solid #e8ecfa; }
.ref-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 10px; margin-bottom: 10px; }
.ref-head strong { display: block; font-size: 12.5px; color: #38425e; word-break: break-all; }
.ref-sub { display: block; margin-top: 3px; font-size: 11px; color: #8a93a8; }
.quality-tip { margin: 0 0 8px; font-size: 11.5px; color: #6b76a0; }
.quality-tip.warn { color: #b7791f; }
.audio-player { width: 100%; height: 38px; }
.ref-save { display: flex; align-items: center; gap: 8px; margin-top: 12px; flex-wrap: wrap; }
.profile-name { flex: 1; min-width: 140px; height: 32px; padding: 0 10px; border-radius: 8px; border: 1px solid #d7ddf0; background: #fff; font-size: 13px; color: #1f2430; }
.profile-name:focus { outline: none; border-color: #6366f1; }
.ref-saved { font-size: 12px; color: #16a34a; }

.text-area { width: 100%; min-height: 108px; resize: vertical; padding: 12px 14px; border: 1px solid #dfe4f0; border-radius: 10px; font-size: 13px; line-height: 1.7; color: #2b3350; box-sizing: border-box; outline: none; font-family: inherit; }
.text-area:focus { border-color: #5c6ff0; box-shadow: 0 0 0 3px rgba(92,111,240,.1); }
.text-foot { display: flex; justify-content: space-between; align-items: center; gap: 10px; margin: 9px 0 16px; font-size: 11px; color: #98a1b8; }
.examples { display: flex; gap: 6px; flex-wrap: wrap; }
.chip { border: 1px solid #e2e7f4; background: #f7f9ff; color: #5a6488; border-radius: 999px; padding: 4px 10px; font-size: 11px; cursor: pointer; }
.chip:hover { border-color: #5c6ff0; color: #4b5bd6; }
.param-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.param-grid label { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.param-grid label > span { font-size: 11.5px; color: #6b76a0; }
.param-grid input, .param-grid select { width: 100%; box-sizing: border-box; height: 34px; padding: 0 9px; border: 1px solid #dfe4f0; border-radius: 8px; background: #fff; color: #2b3350; font-size: 12px; outline: none; font-family: inherit; }
.param-grid input:focus, .param-grid select:focus { border-color: #5c6ff0; }
.param-grid .readonly { background: #f7f8fc; color: #8a93a8; }
.primary-btn { width: 100%; margin-top: 18px; height: 44px; border: none; border-radius: 10px; background: linear-gradient(100deg, #4b5bd6, #5c6ff0 50%, #12a9c4); color: #fff; font-size: 14px; font-weight: 600; cursor: pointer; display: flex; align-items: center; justify-content: center; gap: 9px; }
.primary-btn:hover:not(:disabled) { filter: brightness(1.06); box-shadow: 0 6px 16px rgba(76, 92, 214, .22); }
.primary-btn:disabled { opacity: .55; cursor: not-allowed; }
.spinner { width: 14px; height: 14px; border: 2px solid rgba(255,255,255,.4); border-top-color: #fff; border-radius: 50%; animation: spin .7s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.cancel-btn { width: 100%; margin-top: 8px; height: 34px; border: 1px solid #e8a0a0; border-radius: 8px; background: #fff6f6; color: #c34a4a; font-size: 12px; cursor: pointer; }
.progress-box { margin-top: 12px; padding: 11px 13px; border-radius: 9px; background: #f7f9ff; border: 1px solid #e8ecfa; }
.progress-head { display: flex; justify-content: space-between; font-size: 11.5px; color: #4a5470; margin-bottom: 8px; }
.progress-track { height: 6px; border-radius: 999px; background: #e6eaf7; overflow: hidden; }
.progress-track i { display: block; height: 100%; background: #5c6ff0; transition: width .3s; }
.backend-hint { margin: 10px 0 0; font-size: 11.5px; color: #6b76a0; }
.backend-hint strong { color: #4b5bd6; }
.backend-hint strong.is-demo { color: #b7791f; }
.submit-tip { margin: 4px 0 0; font-size: 11px; color: #98a1b8; line-height: 1.6; }
.error-box { margin-top: 14px; padding: 12px 14px; border-radius: 9px; background: #fff4f4; border: 1px solid #f6d4d4; display: flex; flex-direction: column; gap: 5px; font-size: 12px; color: #a63f3f; }
.error-box strong { font-size: 12.5px; }
.error-hint { color: #8a6a6a; line-height: 1.6; }

.result-card { margin-top: 18px; }
.empty-result { min-height: 108px; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 10px; color: #a3abc0; font-size: 12px; border: 1px dashed #e0e5f1; border-radius: 10px; }
.demo-warning { padding: 11px 14px; border-radius: 9px; background: #fff8e8; border: 1px solid #f2dfae; color: #8a6314; font-size: 12px; line-height: 1.7; margin-bottom: 14px; }
.demo-warning code { background: #fdefcf; padding: 1px 5px; border-radius: 4px; font-size: 11px; }
.result-main { display: flex; align-items: center; gap: 14px; }
.result-main .audio-player { flex: 1; }
.download-btn { flex-shrink: 0; padding: 9px 15px; border-radius: 8px; background: #eef0ff; color: #4b5bd6; font-size: 12px; font-weight: 600; text-decoration: none; }
.download-btn:hover { background: #e2e6ff; }
.audio-fail { margin: 8px 0 0; font-size: 12px; color: #a63f3f; }
.meta-grid { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 10px; margin-top: 16px; }
.meta-grid > div { background: #f7f9fd; border-radius: 9px; padding: 10px 12px; display: flex; flex-direction: column; gap: 5px; }
.meta-grid span { font-size: 11px; color: #8a93a8; }
.meta-grid strong { font-size: 13px; color: #38425e; font-weight: 600; }
.result-log { margin: 14px 0 0; padding: 10px 12px; background: #f7f8fc; border-radius: 8px; font-size: 11px; color: #7b849c; font-family: Consolas, Monaco, monospace; word-break: break-all; }
.result-text { margin: 10px 0 0; font-size: 12px; color: #6b76a0; }
.history { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 16px; padding-top: 14px; border-top: 1px solid #eef1f7; }
.history-label { font-size: 11px; color: #98a1b8; margin-right: 2px; }
.history-chip { border: 1px solid #e2e7f4; background: #fff; color: #6b76a0; border-radius: 7px; padding: 5px 10px; font-size: 11px; cursor: pointer; }
.history-chip.active { border-color: #5c6ff0; color: #4b5bd6; background: #f2f4ff; }
.vc-footer { display: flex; justify-content: space-between; margin-top: 22px; font-size: 11px; color: #a3abc0; }
.vc-footer a { color: #5c6ff0; text-decoration: none; }

@media (max-width: 980px) {
  .vc-grid, .setup-inner { grid-template-columns: 1fr; }
  .meta-grid { grid-template-columns: repeat(3, 1fr); }
  .vc-header-inner { flex-direction: column; align-items: flex-start; }
}
@media (max-width: 620px) {
  .vc-main, .vc-header-inner { padding-left: 16px; padding-right: 16px; }
  .param-grid { grid-template-columns: repeat(2, 1fr); }
  .meta-grid { grid-template-columns: repeat(2, 1fr); }
}
</style>
