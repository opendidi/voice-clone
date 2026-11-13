/*
 * 任务化推理的通用轮询逻辑：提交 -> 轮询 /jobs/<id> -> 完成 / 失败 / 取消。
 * 克隆实验室与音色库播报页共用同一套状态机。
 */
import { computed, onBeforeUnmount, ref } from 'vue'
import {
  apiCancelJob,
  apiGetJob,
  type CloneResult,
  type JobStatus,
} from '@/api/voice-clone'

export interface ApiError {
  message?: string
  response?: { data?: { message?: string; data?: { hint?: string } } }
}

export function pickError(e: unknown): string {
  const err = e as ApiError
  return err?.response?.data?.message || err?.message || '请求失败，请确认后端服务已启动'
}

export function pickHint(e: unknown): string {
  const err = e as ApiError
  return err?.response?.data?.data?.hint || ''
}

const POLL_INTERVAL = 800
const MAX_POLLS = 1800

export function useVoiceJob() {
  const job = ref<JobStatus | null>(null)
  const result = ref<CloneResult | null>(null)
  const error = ref('')
  const hint = ref('')
  const running = ref(false)
  const elapsed = computed(() => (job.value?.elapsed_ms || 0) / 1000)
  const percent = computed(() => job.value?.percent || 0)
  const stage = computed(() => job.value?.stage || '')
  const detail = computed(() => job.value?.detail || '')

  let timer: number | null = null
  let polls = 0

  function stopPolling() {
    if (timer !== null) {
      window.clearTimeout(timer)
      timer = null
    }
  }

  function finish() {
    running.value = false
    stopPolling()
  }

  function reset() {
    stopPolling()
    job.value = null
    result.value = null
    error.value = ''
    hint.value = ''
    polls = 0
    running.value = false
  }

  async function start(
    creator: () => Promise<JobStatus>,
    onDone?: (result: CloneResult) => void,
  ) {
    reset()
    running.value = true
    let created: JobStatus
    try {
      created = await creator()
    } catch (e) {
      error.value = pickError(e)
      hint.value = pickHint(e)
      finish()
      return
    }
    job.value = created

    const step = async () => {
      if (!running.value) return
      try {
        const current = await apiGetJob(created.id)
        job.value = current
        if (current.status === 'succeeded') {
          result.value = current.result
          finish()
          if (current.result) onDone?.(current.result)
          return
        }
        if (current.status === 'failed') {
          error.value = current.error || '推理失败'
          hint.value = current.hint
          finish()
          return
        }
        if (current.status === 'canceled') {
          finish()
          return
        }
      } catch (e) {
        error.value = pickError(e)
        finish()
        return
      }
      polls += 1
      if (polls > MAX_POLLS) {
        error.value = '等待超时，请到后端确认任务状态'
        finish()
        return
      }
      timer = window.setTimeout(() => void step(), POLL_INTERVAL)
    }
    void step()
  }

  async function cancel() {
    const id = job.value?.id
    if (!id) return
    try {
      const canceled = await apiCancelJob(id)
      if (job.value) job.value = canceled
    } catch (e) {
      error.value = pickError(e)
    }
    finish()
  }

  onBeforeUnmount(stopPolling)

  return { job, result, error, hint, running, elapsed, percent, stage, detail, start, cancel, reset }
}
