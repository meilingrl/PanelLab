<script setup>
import { ref, onMounted } from 'vue'
import HelloWorld from './components/HelloWorld.vue'

// 接口测试：环境配置检验（通过 Vite 代理访问后端，避免跨域）
const apiResult = ref(null)
const apiError = ref(null)

onMounted(async () => {
  try {
    const res = await fetch('/api/hello')
    const data = await res.json()
    apiResult.value = data
    apiError.value = null
  } catch (e) {
    apiError.value = e.message || '请求失败'
    apiResult.value = null
  }
})
</script>

<template>
  <div>
    <a href="https://vite.dev" target="_blank">
      <img src="/vite.svg" class="logo" alt="Vite logo" />
    </a>
    <a href="https://vuejs.org/" target="_blank">
      <img src="./assets/vue.svg" class="logo vue" alt="Vue logo" />
    </a>
  </div>
  <HelloWorld msg="Vite + Vue" />
  <!-- 阶段 0：接口测试 / 环境检验 -->
  <div class="env-check">
    <h3>接口测试（环境检验）</h3>
    <p v-if="apiResult" class="ok">{{ apiResult.message }} — {{ apiResult.service }} {{ apiResult.status }}</p>
    <p v-else-if="apiError" class="err">后端未响应: {{ apiError }}</p>
    <p v-else>请求中…</p>
  </div>
</template>

<style scoped>
.logo {
  height: 6em;
  padding: 1.5em;
  will-change: filter;
  transition: filter 300ms;
}
.logo:hover {
  filter: drop-shadow(0 0 2em #646cffaa);
}
.logo.vue:hover {
  filter: drop-shadow(0 0 2em #42b883aa);
}
.env-check {
  margin-top: 2em;
  padding: 1em;
  border-radius: 8px;
  background: #1a1a2e;
  color: #eee;
}
.env-check h3 { margin: 0 0 0.5em 0; font-size: 1rem; }
.env-check .ok { color: #42b883; }
.env-check .err { color: #e74c3c; }
</style>
