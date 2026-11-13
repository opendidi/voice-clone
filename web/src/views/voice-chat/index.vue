<template>
  <div class="vc-shell">
    <section class="chat-card">
      <!-- 顶部栏：标题 + 音色选择 + 能力标识 -->
      <header class="chat-top">
        <div class="chat-title">
          <h2>语音聊天</h2>
          <p>说句话，AI 听写、思考并用你的音色回复</p>
        </div>
        <div class="chat-top-right">
          <div class="profile-pick">
            <select v-model="selectedId" :disabled="running">
              <option value="">— 选择回复音色 —</option>
              <option v-for="p in profiles" :key="p.id" :value="p.id">{{ p.name }}</option>
            </select>
            <button class="ghost-btn" :disabled="loading" @click="loadProfiles">刷新</button>
          </div>
          <span class="cap-tip" :class="{ ready: asrReady && llmReady }">
            {{ asrReady && llmReady ? '真实模型已就绪' : '演示模式' }}
          </span>
        </div>
      </header>

      <!-- 会话区：可滚动的多轮气泡 -->
      <div ref="scrollEl" class="chat-scroll">
        <div v-if="!messages.length" class="empty-state">
          <div class="empty-ico">
            <svg viewBox="0 0 24 24" width="34" height="34" fill="none" stroke="currentColor" stroke-width="1.6">
              <rect x="9" y="3" width="6" height="11" rx="3" />
              <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
            </svg>
          </div>
          <h3>开始一段语音对话</h3>
          <p>点击下方麦克风说点什么，AI 会用你选的音色回复你。</p>
          <ul class="empty-tips">
            <li>先到「音色库与播报」存一个语音档案，再回来选为回复音色。</li>
            <li>支持连续多轮对话，AI 会记住前面的内容。</li>
            <li>当前为演示模式（固定提示音），配置真实模型后即你的音色。</li>
          </ul>
        </div>

        <div
          v-for="m in messages"
          :key="m.id"
          class="msg"
          :class="m.role"
        >
          <div class="avatar">{{ m.role === 'user' ? '我' : 'AI' }}</div>
          <div class="msg-body">
            <div class="bubble" :class="{ pending: m.state === 'thinking' && !m.text }">
              <span v-if="m.role === 'user'">{{ m.text || '…' }}</span>
              <template v-else>
                <span v-if="m.state === 'thinking' && !m.text" class="typing"><i></i><i></i><i></i></span>
                <span v-else>{{ m.text }}</span>
              </template>
            </div>

            <div v-if="m.role === 'assistant' && m.audioId" class="msg-audio">
              <audio :key="m.audioId" :src="voiceCloneAudioUrl(m.audioId)" controls preload="none" />
              <a class="dl" :href="voiceCloneAudioUrl(m.audioId)" :download="`reply-${m.audioId}.wav`">下载</a>
            </div>

            <div v-if="m.demo" class="demo-tag">演示音频</div>
            <div class="msg-time">{{ formatTime(m.ts) }}</div>
          </div>
        </div>

        <div v-if="error" class="msg-error">
          <strong>出错了：</strong>{{ error }}<span v-if="hint">（{{ hint }}）</span>
        </div>
      </div>

      <!-- 底部输入条：录音 + 状态 -->
      <footer class="chat-input">
        <div class="rec-wrap">
          <button
            class="mic-btn"
            :class="{ active: recording, blocked: !canRecord }"
            :disabled="!canRecord"
            :title="canRecord ? '点击开始/结束录音' : '请先在上方选择回复音色'"
            @click="toggleRecord"
          >
            <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8">
              <rect x="9" y="3" width="6" height="11" rx="3" />
              <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
            </svg>
          </button>
          <div class="rec-side">
            <div class="rec-label">
              {{ recording ? '点击结束并发送' : (canRecord ? '点击开始录音' : '请先选择音色') }}
            </div>
            <div v-if="recording" class="meter"><i :style="{ width: `${level * 100}%` }" /></div>
          </div>
        </div>
        <div v-if="running" class="input-status">
          {{ stage || '排队中' }}<template v-if="detail"> · {{ detail }}</template> · {{ percent }}%
        </div>
        <span v-if="recorderError" class="rec-err">{{ recorderError }}</span>
      </footer>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import {
  apiVoiceProfiles,
  voiceCloneAudioUrl,
  type VoiceProfile,
} from '@/api/voice-clone'
import {
  apiCreateVoiceChatTurn,
  apiGetVoiceChatJob,
  apiVoiceChatStatus,
  type VoiceChatHistoryItem,
  type VoiceChatJobStatus,
  type VoiceChatResult,
  type VoiceChatStatus,
} from '@/api/voice-chat'
import { useRecorder } from './useRecorder'

const { recording, level, error: recorderError, start, stop } = useRecorder()

const profiles = ref<VoiceProfile[]>([])
const loading = ref(false)
const selectedId = ref('')
const running = ref(false)
const stage = ref('')
const percent = ref(0)
const detail = ref('')
const error = ref('')
const hint = ref('')

const asrReady = ref(false)
const llmReady = ref(false)

const scrollEl = ref<HTMLElement | null>(null)

type MsgState = 'pending' | 'thinking' | 'speaking' | 'done' | 'error'
interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
  audioId?: string
  demo?: boolean
  ts: number
  state: MsgState
}
const messages = ref<ChatMessage[]>([])

const canRecord = computed(() => !!selectedId.value && !running.value)

let timer: number | null = null
let polls = 0
const MAX_POLLS = 1800

function genId(): string {
  return Math.random().toString(36).slice(2, 10)
}

function formatTime(ts: number): string {
  const d = new Date(ts)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}`
}

function scrollToBottom() {
  nextTick(() => {
    const el = scrollEl.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

watch(messages, scrollToBottom)

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
  polls = 0

  const userMsg: ChatMessage = { id: genId(), role: 'user', text: '', ts: Date.now(), state: 'pending' }
  const botMsg: ChatMessage = { id: genId(), role: 'assistant', text: '', ts: Date.now(), state: 'thinking' }
  messages.value.push(userMsg, botMsg)
  scrollToBottom()

  // 取历史中已完成的回合，作为多轮上下文发给 LLM
  const history: VoiceChatHistoryItem[] = messages.value
    .filter((m) => m.state === 'done' && m.text)
    .map((m) => ({ role: m.role === 'user' ? 'user' : 'assistant', content: m.text }))

  let created: VoiceChatJobStatus
  try {
    created = await apiCreateVoiceChatTurn(blob, selectedId.value, history)
  } catch (e) {
    userMsg.state = 'error'
    botMsg.state = 'error'
    error.value = (e as Error)?.message || '提交失败'
    running.value = false
    return
  }

  const applyResult = (res: VoiceChatResult) => {
    if (res.transcript) {
      userMsg.text = res.transcript
      userMsg.state = 'done'
    }
    if (res.reply) {
      botMsg.text = res.reply
      if (botMsg.state === 'thinking') botMsg.state = 'speaking'
    }
    if (res.audio?.id) {
      botMsg.audioId = res.audio.id
      botMsg.demo = !!res.audio.demo
    }
  }

  const step = async () => {
    if (!running.value) return
    try {
      const cur = await apiGetVoiceChatJob(created.id)
      stage.value = cur.stage
      percent.value = cur.percent
      detail.value = cur.detail
      if (cur.result) applyResult(cur.result)
      if (cur.status === 'succeeded') {
        botMsg.state = 'done'
        userMsg.state = 'done'
        running.value = false
        stopPolling()
        message.success('回复完成')
        scrollToBottom()
        return
      }
      if (cur.status === 'failed') {
        botMsg.state = 'error'
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
      botMsg.state = 'error'
      error.value = (e as Error)?.message || '轮询失败'
      running.value = false
      stopPolling()
      return
    }
    polls += 1
    if (polls > MAX_POLLS) {
      botMsg.state = 'error'
      error.value = '等待超时，请到后端确认任务状态'
      running.value = false
      stopPolling()
      return
    }
    timer = window.setTimeout(() => void step(), 700)
  }
  void step()
}

onMounted(() => {
  void loadProfiles()
  void loadStatus()
})

onBeforeUnmount(() => {
  stopPolling()
  if (recording.value) stop()
})
</script>

<style lang="scss" scoped src="./index.scss"></style>
