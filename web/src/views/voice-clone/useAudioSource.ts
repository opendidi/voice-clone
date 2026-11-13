/*
 * 把后端音频取成 Blob URL 供 <audio> / 下载链接使用。
 * 这样 API Key 模式下也能带上 Authorization 头（原生 audio 标签做不到）。
 *
 * 用递增序号保证「快速连续切换音频」时只有最后一次请求能写入结果，
 * 避免先前的响应覆盖当前播放源，也避免 Blob URL 泄漏。
 */
import { onBeforeUnmount, ref } from 'vue'
import { apiFetchAudio } from '@/api/voice-clone'

export function useAudioSource(fetcher: (id: string) => Promise<Blob> = apiFetchAudio) {
  const url = ref('')
  const loading = ref(false)
  const failed = ref('')
  let objectUrl = ''
  let loadSeq = 0

  function revoke() {
    loadSeq += 1
    if (objectUrl) {
      URL.revokeObjectURL(objectUrl)
      objectUrl = ''
    }
    url.value = ''
    failed.value = ''
  }

  async function load(id: string): Promise<boolean> {
    revoke()
    const seq = loadSeq
    loading.value = true
    try {
      const blob = await fetcher(id)
      if (seq !== loadSeq) return false
      objectUrl = URL.createObjectURL(blob)
      url.value = objectUrl
      return true
    } catch (e) {
      if (seq !== loadSeq) return false
      failed.value = (e as Error)?.message || '音频加载失败'
      return false
    } finally {
      if (seq === loadSeq) loading.value = false
    }
  }

  onBeforeUnmount(revoke)

  return { url, loading, failed, load, revoke }
}
