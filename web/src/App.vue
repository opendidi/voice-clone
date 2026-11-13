<template>
  <div class="app-shell">
    <nav class="app-tabs" role="tablist" aria-label="语音克隆功能导航">
      <div class="tabs-inner">
        <div class="tabs-left">
          <button
            v-for="tab in tabs"
            :key="tab.key"
            role="tab"
            :aria-selected="active === tab.key"
            :class="{ active: active === tab.key }"
            @click="active = tab.key"
          >
            {{ tab.label }}
          </button>
        </div>
        <div class="tabs-right">
          <span class="tabs-tip">启用 API Key 时须在 localStorage 写入 auth-token</span>
          <button
            class="reset-btn"
            :disabled="resetting"
            title="清空当前账号的全部声纹档案、任务与合成产物（不可恢复）"
            @click="onReset"
          >
            {{ resetting ? '清空中…' : '清空数据' }}
          </button>
        </div>
      </div>
    </nav>
    <!-- 用 :key 强制重建，清空后让两个视图重新拉取空数据 -->
    <component :is="active === 'lab' ? VoiceClone : ProfilesPanel" :key="viewKey" />
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { Modal, message } from 'ant-design-vue'
import VoiceClone from '@/views/voice-clone/index.vue'
import ProfilesPanel from '@/views/voice-clone/ProfilesPanel.vue'
import { apiResetData } from '@/api/voice-clone'

const tabs = [
  { key: 'lab', label: '克隆实验室' },
  { key: 'profiles', label: '音色库与播报' },
] as const
const active = ref<string>('lab')
const viewKey = ref(0)
const resetting = ref(false)

function onReset() {
  Modal.confirm({
    title: '清空全部数据？',
    content:
      '将删除当前账号的全部声纹档案、合成任务与合成产物，且不可恢复。确定继续吗？',
    okText: '确认清空',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      resetting.value = true
      try {
        const res = await apiResetData()
        viewKey.value += 1 // 强制两个视图重建并重新拉取空数据
        message.success(
          `已清空：声纹 ${res.deleted_profiles} 条、任务 ${res.deleted_jobs} 条、产物 ${res.deleted_outputs} 个`,
        )
      } catch (err) {
        message.error(`清空失败：${(err as Error).message || '未知错误'}`)
        throw err // 让 Modal 保持 loading，便于重试
      } finally {
        resetting.value = false
      }
    },
  })
}
</script>

<style lang="scss" scoped src="./App.scss"></style>
