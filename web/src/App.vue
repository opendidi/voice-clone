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
        <span class="tabs-tip">启用 API Key 时须在 localStorage 写入 auth-token</span>
      </div>
    </nav>
    <component :is="active === 'lab' ? VoiceClone : ProfilesPanel" />
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import VoiceClone from '@/views/voice-clone/index.vue'
import ProfilesPanel from '@/views/voice-clone/ProfilesPanel.vue'

const tabs = [
  { key: 'lab', label: '克隆实验室' },
  { key: 'profiles', label: '音色库与播报' },
] as const
const active = ref<string>('lab')
</script>

<style scoped>
.app-shell { min-height: 100vh; background: #f4f6fb; }
.app-tabs { background: #fff; border-bottom: 1px solid #e6eaf2; }
.tabs-inner { max-width: 1240px; margin: 0 auto; padding: 0 28px; display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.tabs-left { display: flex; gap: 4px; }
.app-tabs button { border: none; background: transparent; padding: 13px 16px; font-size: 13px; color: #6b76a0; cursor: pointer; border-bottom: 2px solid transparent; }
.app-tabs button:hover { color: #4b5bd6; }
.app-tabs button.active { color: #4b5bd6; border-bottom-color: #5c6ff0; font-weight: 600; }
.tabs-tip { font-size: 11px; color: #a3abc0; }
@media (max-width: 620px) { .tabs-inner { padding: 0 16px; } .tabs-tip { display: none; } }
</style>
