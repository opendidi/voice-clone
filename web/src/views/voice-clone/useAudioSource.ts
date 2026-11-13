/*
 * 把后端音频取成 Blob URL 供 <audio> / 下载链接使用。
 * 这样 API Key 模式下也能带上 Authorization 头（原生 audio 标签做不到）。
 */
import { onBeforeUnmount, ref } from 'vue'
import { apiFetchAudio } from '@/api/voice-clone'

export function useAudioSource() {
  const url = ref('')
  const loading = ref(false)
  const failed = ref('')
  let objectUrl = ''

  function revoke() {
    if (objectUrl) {
      URL.revokeObjectURL(objectUrl)
      objectUrl = ''
    }
    url.value = ''
  }

  async function load(id: string): Promise<boolean> {
    revoke()
    failed.value = ''
    loading.value = true
    try {
      const blob = await apiFetchAudio(id)
      objectUrl = URL.createObjectURL(blob)
      url.value = objectUrl
      return true
    } catch (e) {
      failed.value = (e as Error)?.message || '音频加载失败'
      return false
    } finally {
      loading.value = false
    }
  }

  onBeforeUnmount(revoke)

  return { url, loading, failed, load, revoke }
}
