<template>
  <div class="pp-grid">
    <section class="card">
      <div class="card-head">
        <div>
          <h2>音色库</h2>
          <p>保存过的参考音频，按当前用户隔离</p>
        </div>
        <button class="outline-btn" :disabled="loading" @click="load">
          {{ loading ? '加载中…' : '刷新' }}
        </button>
      </div>

      <div v-if="!profiles.length" class="empty">
        还没有语音档案。到「克隆实验室」上传或录制一段参考音频，然后点「保存为语音档案」。
      </div>
      <ul v-else class="profile-list">
        <li
          v-for="item in profiles"
          :key="item.id"
          :class="{ active: selectedId === item.id }"
          @click="selectedId = item.id"
        >
          <div class="profile-info">
            <strong>{{ item.name }}</strong>
            <span>
              {{ formatDuration(item.duration) }} · {{ (item.sample_rate / 1000).toFixed(0) }}kHz ·
              {{ formatBytes(item.size) }} · {{ formatDate(item.created_at) }}
            </span>
          </div>
          <button class="text-btn danger" :disabled="deleting === item.id" @click.stop="remove(item)">
            {{ deleting === item.id ? '删除中' : '删除' }}
          </button>
        </li>
      </ul>
    </section>

    <section class="card">
      <div class="card-head">
        <div>
          <h2>文本播报</h2>
          <p>用选中的音色朗读文字，长文模式自动分句并拼接</p>
        </div>
        <span class="selected-tip">{{ selectedName || '未选择音色' }}</span>
      </div>

      <textarea
        v-model="text"
        class="text-area"
        :maxlength="longMode ? 5000 : 1000"
        placeholder="输入要朗读的内容，例如：欢迎使用语音克隆播报服务。"
      />
      <div class="text-foot">
        <label class="check-label">
          <input v-model="longMode" type="checkbox" />
          长文模式（自动分句 + 拼接，上限 5000 字）
        </label>
        <span>{{ text.length }} / {{ longMode ? 5000 : 1000 }}</span>
      </div>

      <div class="action-row">
        <button class="primary-btn" :disabled="!canSubmit || running" @click="run">
          {{ running ? `合成中 ${elapsed.toFixed(1)}s` : '开始合成' }}
        </button>
        <button v-if="running" class="outline-btn" @click="cancel">取消任务</button>
        <button class="outline-btn" :disabled="!canSubmit || running || segmentPlaying" @click="startSegment">
          逐句实时播报
        </button>
        <button v-if="segmentPlaying" class="outline-btn danger" @click="stopSegment">停止播报</button>
      </div>

      <div v-if="running" class="progress-box">
        <div class="progress-head">
          <span>{{ stage || '排队中' }}<template v-if="detail"> · {{ detail }}</template></span>
          <strong>{{ percent }}%</strong>
        </div>
        <div class="progress-track"><i :style="{ width: `${percent}%` }" /></div>
      </div>
      <div v-if="segmentPlaying" class="segment-tip">
        正在播报第 {{ segIndex }} / {{ segTotal }} 句（合成下一句与播放当前句重叠进行）
      </div>
      <div v-if="error" class="error-box">
        <strong>合成失败</strong><span>{{ error }}</span>
        <span v-if="hint" class="error-hint">{{ hint }}</span>
      </div>
      <div v-if="audioSource.failed.value" class="error-box"><span>{{ audioSource.failed.value }}</span></div>

      <div v-if="current" class="result-box">
        <audio v-if="audioSource.url.value" :key="audioSource.url.value" :src="audioSource.url.value" controls class="audio-player" />
        <a v-if="audioSource.url.value" class="download-btn" :href="audioSource.url.value" :download="`voice-clone-${current.id}.wav`">
          下载 WAV
        </a>
        <div class="meta-grid">
          <div><span>音频时长</span><strong>{{ formatDuration(current.duration) }}</strong></div>
          <div><span>推理耗时</span><strong>{{ (current.elapsed_ms / 1000).toFixed(2) }} s</strong></div>
          <div><span>句数</span><strong>{{ current.segments || 1 }}</strong></div>
          <div><span>后端</span><strong>{{ current.backend }}</strong></div>
        </div>
        <p v-if="current.truncated" class="warn-line">文本超过上限已截断，建议开启长文模式。</p>
        <p v-if="current.demo" class="warn-line">演示模式：这是固定提示音，不是克隆人声。</p>
        <p v-if="current.log" class="result-log">{{ current.log }}</p>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import {
  apiCreateTtsJob,
  apiDeleteVoiceProfile,
  apiVoiceProfiles,
  apiVoiceTtsSegment,
  voiceCloneAudioUrl,
  type CloneResult,
  type VoiceProfile,
} from '@/api/voice-clone'
import { formatBytes, formatDuration } from './audio'
import { splitSentences } from './text'
import { useAudioSource } from './useAudioSource'
import { pickError, useVoiceJob } from './useJob'

const {
  result, error, hint, running, elapsed, percent, stage, detail, start, cancel,
} = useVoiceJob()
const audioSource = useAudioSource()

const profiles = ref<VoiceProfile[]>([])
const loading = ref(false)
const deleting = ref('')
const selectedId = ref('')
const text = ref('欢迎使用语音克隆播报服务，这是一段用你自己音色朗读的文字。')
const longMode = ref(false)
const current = ref<CloneResult | null>(null)

const selectedName = computed(() => profiles.value.find((p) => p.id === selectedId.value)?.name || '')
const canSubmit = computed(() => !!selectedId.value && text.value.trim().length > 0 && !!profiles.value.length)

function formatDate(ts: number): string {
  return new Date(ts * 1000).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

async function load() {
  loading.value = true
  try {
    profiles.value = await apiVoiceProfiles()
    if (selectedId.value && !profiles.value.some((p) => p.id === selectedId.value)) selectedId.value = ''
    if (!selectedId.value && profiles.value.length) selectedId.value = profiles.value[0].id
  } catch (e) {
    message.error(pickError(e))
  } finally {
    loading.value = false
  }
}

async function remove(item: VoiceProfile) {
  if (!window.confirm(`确定删除音色「${item.name}」？该操作不可撤销。`)) return
  deleting.value = item.id
  try {
    await apiDeleteVoiceProfile(item.id)
    message.success('已删除')
    if (selectedId.value === item.id) selectedId.value = ''
    await load()
  } catch (e) {
    message.error(pickError(e))
  } finally {
    deleting.value = ''
  }
}

function run() {
  current.value = null
  audioSource.revoke()
  void start(
    () => apiCreateTtsJob({
      text: text.value.trim(),
      profile_id: selectedId.value,
      long: longMode.value,
    }),
    async (res) => {
      current.value = res
      await audioSource.load(res.id)
      message.success(longMode.value ? '长文播报已完成' : '合成完成')
    },
  )
}

// ── 逐句实时播报：前端分句，串行合成 + 播放，支持随时停止 ──
const segmentPlaying = ref(false)
const segIndex = ref(0)
const segTotal = ref(0)
let controller: AbortController | null = null
let audioEl: HTMLAudioElement | null = null

function playOne(url: string, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const audio = new Audio(url)
    audioEl = audio
    const cleanup = () => {
      audio.onended = null
      audio.onerror = null
      signal.removeEventListener('abort', onAbort)
      audioEl = null
    }
    const onAbort = () => {
      audio.pause()
      cleanup()
      resolve()
    }
    signal.addEventListener('abort', onAbort, { once: true })
    audio.onended = () => { cleanup(); resolve() }
    audio.onerror = () => { cleanup(); reject(new Error('音频播放失败')) }
    void audio.play().catch((err) => { cleanup(); reject(err) })
  })
}

async function startSegment() {
  if (!canSubmit.value || segmentPlaying.value) return
  const sentences = splitSentences(text.value.trim())
  if (!sentences.length) return
  segmentPlaying.value = true
  segTotal.value = sentences.length
  segIndex.value = 0
  controller = new AbortController()
  const signal = controller.signal
  try {
    for (let i = 0; i < sentences.length; i += 1) {
      if (!segmentPlaying.value || signal.aborted) break
      segIndex.value = i + 1
      const res = await apiVoiceTtsSegment(sentences[i], selectedId.value, signal)
      if (signal.aborted) break
      await playOne(voiceCloneAudioUrl(res.id), signal)
    }
  } catch (e) {
    if ((e as Error)?.name !== 'AbortError') message.error(pickError(e))
  } finally {
    segmentPlaying.value = false
    controller = null
  }
}

function stopSegment() {
  segmentPlaying.value = false
  controller?.abort()
  controller = null
  audioEl?.pause()
}

onMounted(() => { void load() })
onBeforeUnmount(stopSegment)
</script>

<style scoped>
.pp-grid { display: grid; grid-template-columns: minmax(300px, 0.9fr) minmax(420px, 1.1fr); gap: 18px; align-items: start; }
.card { background: #fff; border: 1px solid #e6eaf2; border-radius: 14px; padding: 20px; box-shadow: 0 2px 12px rgba(30,45,90,.035); }
.card-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 16px; }
.card-head h2 { margin: 0 0 3px; font-size: 15px; font-weight: 650; }
.card-head p { margin: 0; font-size: 12px; color: #8a93a8; }
.selected-tip { font-size: 11.5px; color: #4b5bd6; background: #eef0ff; border-radius: 999px; padding: 5px 10px; white-space: nowrap; }
.outline-btn { padding: 8px 13px; border-radius: 8px; border: 1px solid #d9dfee; background: #fff; color: #4a5470; font-size: 12px; cursor: pointer; }
.outline-btn:hover:not(:disabled) { border-color: #5c6ff0; color: #4b5bd6; }
.outline-btn:disabled { opacity: .5; cursor: not-allowed; }
.outline-btn.danger { color: #c34a4a; border-color: #e8a0a0; }
.empty { padding: 26px 16px; border: 1px dashed #e0e5f1; border-radius: 10px; color: #98a1b8; font-size: 12px; line-height: 1.7; text-align: center; }
.profile-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
.profile-list li { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 11px 12px; border: 1px solid #e8ecfa; border-radius: 9px; background: #fafbff; cursor: pointer; }
.profile-list li:hover { border-color: #c9d1e6; }
.profile-list li.active { border-color: #5c6ff0; background: #f2f4ff; }
.profile-info { min-width: 0; }
.profile-info strong { display: block; font-size: 12.5px; color: #38425e; word-break: break-all; }
.profile-info span { display: block; margin-top: 3px; font-size: 11px; color: #8a93a8; }
.text-btn { border: none; background: none; color: #4b5bd6; font-size: 12px; cursor: pointer; white-space: nowrap; }
.text-btn.danger { color: #d05353; }
.text-area { width: 100%; min-height: 120px; resize: vertical; padding: 12px 14px; border: 1px solid #dfe4f0; border-radius: 10px; font-size: 13px; line-height: 1.7; color: #2b3350; box-sizing: border-box; outline: none; font-family: inherit; }
.text-area:focus { border-color: #5c6ff0; box-shadow: 0 0 0 3px rgba(92,111,240,.1); }
.text-foot { display: flex; justify-content: space-between; align-items: center; gap: 10px; margin: 9px 0 16px; font-size: 11px; color: #8a93a8; }
.check-label { display: inline-flex; align-items: center; gap: 6px; cursor: pointer; }
.action-row { display: flex; gap: 9px; flex-wrap: wrap; }
.primary-btn { flex: 1; min-width: 130px; height: 40px; border: none; border-radius: 9px; background: linear-gradient(100deg, #4b5bd6, #5c6ff0 50%, #12a9c4); color: #fff; font-size: 13px; font-weight: 600; cursor: pointer; }
.primary-btn:disabled { opacity: .55; cursor: not-allowed; }
.progress-box { margin-top: 14px; padding: 11px 13px; border-radius: 9px; background: #f7f9ff; border: 1px solid #e8ecfa; }
.progress-head { display: flex; justify-content: space-between; font-size: 11.5px; color: #4a5470; margin-bottom: 8px; }
.progress-track { height: 6px; border-radius: 999px; background: #e6eaf7; overflow: hidden; }
.progress-track i { display: block; height: 100%; background: #5c6ff0; transition: width .3s; }
.segment-tip { margin-top: 12px; font-size: 12px; color: #1f7a48; background: #f0fbf4; border: 1px solid #bde6cd; border-radius: 8px; padding: 9px 12px; }
.error-box { margin-top: 14px; padding: 12px 14px; border-radius: 9px; background: #fff4f4; border: 1px solid #f6d4d4; display: flex; flex-direction: column; gap: 5px; font-size: 12px; color: #a63f3f; }
.error-hint { color: #8a6a6a; line-height: 1.6; }
.result-box { margin-top: 16px; padding-top: 14px; border-top: 1px solid #eef1f7; }
.audio-player { width: 100%; height: 38px; }
.download-btn { display: inline-block; margin-top: 10px; padding: 8px 14px; border-radius: 8px; background: #eef0ff; color: #4b5bd6; font-size: 12px; font-weight: 600; text-decoration: none; }
.meta-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; margin-top: 14px; }
.meta-grid > div { background: #f7f9fd; border-radius: 9px; padding: 9px 11px; display: flex; flex-direction: column; gap: 4px; }
.meta-grid span { font-size: 11px; color: #8a93a8; }
.meta-grid strong { font-size: 12.5px; color: #38425e; }
.warn-line { margin: 10px 0 0; font-size: 11.5px; color: #b7791f; }
.result-log { margin: 8px 0 0; font-size: 11px; color: #7b849c; word-break: break-all; }
@media (max-width: 980px) { .pp-grid { grid-template-columns: 1fr; } .meta-grid { grid-template-columns: repeat(2, 1fr); } }
</style>
