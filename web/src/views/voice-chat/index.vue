<template>
  <div class="vc-shell">
    <section class="card">
      <div class="card-head">
        <div>
          <h2>语音聊天</h2>
          <p>说一句话，AI 听写、思考并用你的音色回复（MVP）</p>
        </div>
        <span class="cap-tip" :class="{ ready: asrReady && llmReady }">
          {{ asrReady && llmReady ? '真实模型已就绪' : '演示模式（未加载真实模型）' }}
        </span>
      </div>

      <div class="profile-row">
        <label>回复音色</label>
        <select v-model="selectedId" :disabled="running">
          <option value="">— 请选择音色 —</option>
          <option v-for="p in profiles" :key="p.id" :value="p.id">{{ p.name }}</option>
        </select>
        <button class="outline-btn" :disabled="loading" @click="loadProfiles">刷新</button>
      </div>
      <p v-if="!profiles.length" class="hint">
        还没有音色，请先到「音色库与播报」保存一个语音档案，语音聊天会用它来合成回复。
      </p>

      <div class="record-zone">
        <button
          class="rec-btn"
          :class="{ active: recording }"
          :disabled="!selectedId || running"
          @click="toggleRecord"
        >
          <span class="rec-dot" />
          {{ recording ? '停止并发送' : '按住说话 / 点击录音' }}
        </button>
        <div v-if="recording" class="meter"><i :style="{ width: `${level * 100}%` }" /></div>
        <span v-if="recorderError" class="hint danger">{{ recorderError }}</span>
      </div>

      <div v-if="running" class="progress-box">
        <div class="progress-head">
          <span>{{ stage || '排队中' }}<template v-if="detail"> · {{ detail }}</template></span>
          <strong>{{ percent }}%</strong>
        </div>
        <div class="progress-track"><i :style="{ width: `${percent}%` }" /></div>
      </div>

      <div v-if="error" class="error-box">
        <strong>失败了</strong><span>{{ error }}</span>
        <span v-if="hint" class="error-hint">{{ hint }}</span>
      </div>

      <div v-if="result" class="chat-box">
        <div class="bubble user">
          <span class="who">你</span>
          <p>{{ result.transcript }}</p>
        </div>
        <div class="bubble bot">
          <span class="who">AI</span>
          <p>{{ result.reply }}</p>
        </div>
        <div class="play-row">
          <audio v-if="audio.url.value" :key="audio.url.value" :src="audio.url.value" controls class="audio-player" />
          <a
            v-if="result"
            class="download-btn"
            :href="voiceCloneAudioUrl(result.audio.id)"
            :download="`voice-chat-${result.audio.id}.wav`"
          >下载回复音频</a>
        </div>
        <p v-if="result.audio.demo" class="warn-line">演示模式：回复音频是固定提示音，配置真实模型后即为你的音色。</p>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import {
  apiVoiceProfiles,
  apiFetchAudio,
  voiceCloneAudioUrl,
  type VoiceProfile,
} from '@/api/voice-clone'
import {
  apiCancelVoiceChatJob,
  apiCreateVoiceChatTurn,
  apiGetVoiceChatJob,
  apiVoiceChatStatus,
  type VoiceChatJobStatus,
  type VoiceChatResult,
  type VoiceChatStatus,
} from '@/api/voice-chat'
import { useAudioSource } from '../voice-clone/useAudioSource'
import { useRecorder } from './useRecorder'

const { recording, level, error: recorderError, start, stop } = useRecorder()
const audio = useAudioSource(apiFetchAudio)

const profiles = ref<VoiceProfile[]>([])
const loading = ref(false)
const selectedId = ref('')
const running = ref(false)
const stage = ref('')
const percent = ref(0)
const detail = ref('')
const error = ref('')
const hint = ref('')
const result = ref<VoiceChatResult | null>(null)

const asrReady = ref(false)
const llmReady = ref(false)

let timer: number | null = null
let polls = 0
const MAX_POLLS = 1800

const canRecord = computed(() => !!selectedId.value && !running.value)

async function loadProfiles() {
  loading.value = true
  try {
    profiles.value = await apiVoiceProfiles()
    if (selectedId.value && !profiles.value.some((p) => p.id === selectedId.value)) selectedId.value = ''
    if (!selectedId.value && profiles.value.length) selectedId.value = profiles.value[0].id
  } catch (e) {
    message.error((e as Error)?.message || '音色加载失败')
  } finally {
    loading.value = false
  }
}

async function loadStatus() {
  try {
    const st: VoiceChatStatus = await apiVoiceChatStatus()
    asrReady.value = st.asr.ready
    llmReady.value = st.llm.ready
  } catch {
    /* 探测失败不阻断主流程 */
  }
}

async function toggleRecord() {
  if (recording.value) {
    const blob = stop()
    if (blob.size < 1000) {
      message.warning('录音太短，已忽略')
      return
    }
    await submit(blob)
  } else {
    result.value = null
    audio.revoke()
    await start()
  }
}

function stopPolling() {
  if (timer !== null) {
    window.clearTimeout(timer)
    timer = null
  }
}

async function submit(blob: Blob) {
  error.value = ''
  hint.value = ''
  running.value = true
  stage.value = '提交中'
  percent.value = 5
  let created: VoiceChatJobStatus
  try {
    created = await apiCreateVoiceChatTurn(blob, selectedId.value)
  } catch (e) {
    error.value = (e as Error)?.message || '提交失败'
    running.value = false
    return
  }
  const step = async () => {
    if (!running.value) return
    try {
      const cur = await apiGetVoiceChatJob(created.id)
      stage.value = cur.stage
      percent.value = cur.percent
      detail.value = cur.detail
      if (cur.status === 'succeeded') {
        result.value = cur.result
        running.value = false
        stopPolling()
        if (cur.result) await audio.load(cur.result.audio.id)
        message.success('回复完成')
        return
      }
      if (cur.status === 'failed') {
        error.value = cur.error || '推理失败'
        hint.value = cur.hint
        running.value = false
        stopPolling()
        return
      }
      if (cur.status === 'canceled') {
        running.value = false
        stopPolling()
        return
      }
    } catch (e) {
      error.value = (e as Error)?.message || '轮询失败'
      running.value = false
      stopPolling()
      return
    }
    polls += 1
    if (polls > MAX_POLLS) {
      error.value = '等待超时，请到后端确认任务状态'
      running.value = false
      stopPolling()
      return
    }
    timer = window.setTimeout(() => void step(), 700)
  }
  void step()
}

async function cancel() {
  const id = (result.value as unknown as { id?: string })?.id
  stopPolling()
  running.value = false
  message.info('已停止')
  void id
}

onMounted(() => {
  void loadProfiles()
  void loadStatus()
})

onBeforeUnmount(() => {
  stopPolling()
  audio.revoke()
  if (recording.value) stop()
})
</script>

<style lang="scss" scoped src="./index.scss"></style>
