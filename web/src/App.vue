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

<style lang="scss" scoped src="./App.scss"></style>
