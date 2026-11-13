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
          <div class="profile-actions">
            <button class="text-btn" @click.stop="togglePreview(item.id)">
              {{ previewId === item.id ? '关闭试听' : '试听' }}
            </button>
            <button class="text-btn danger" :disabled="deleting === item.id" @click.stop="remove(item)">
              {{ deleting === item.id ? '删除中' : '删除' }}
            </button>
          </div>
        </li>
      </ul>
      <div v-if="previewId" class="profile-preview">
        <audio
          v-if="preview.url.value"
          :key="preview.url.value"
          :src="preview.url.value"
          controls
          class="audio-player"
        />
        <span v-if="preview.loading.value">加载中…</span>
        <span v-if="preview.failed.value" class="preview-fail">{{ preview.failed.value }}</span>
      </div>
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
  apiFetchProfileAudio,
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
// 试听参考音频走另一个 fetcher，Blob URL 的创建/回收仍由同一个 hook 管理
const preview = useAudioSource(apiFetchProfileAudio)
const previewId = ref('')

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
    if (previewId.value === item.id) {
      previewId.value = ''
      preview.revoke()
    }
    await load()
  } catch (e) {
    message.error(pickError(e))
  } finally {
    deleting.value = ''
  }
}

async function togglePreview(id: string) {
  if (previewId.value === id) {
    previewId.value = ''
    preview.revoke()
    return
  }
  previewId.value = id
  const ok = await preview.load(id)
  if (!ok && previewId.value === id) previewId.value = ''
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

<style lang="scss" scoped src="./ProfilesPanel.scss"></style>
