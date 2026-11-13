import { createApp } from 'vue'
// 只按需引入 message 的样式，避免把整套 antd CSS（约 600KB）打进产物
import 'ant-design-vue/es/message/style/css'
import App from './App.vue'

createApp(App).mount('#app')
