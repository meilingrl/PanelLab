<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { agentApi } from '../../composables/useAgentApi'
// Reads a run's output by byte offset. While the run is live, "follow" polls every 2 s; the next request is
// scheduled only after the previous one finishes, and polling stops at eof or while the page is hidden.
const props = defineProps({ serverId: { type: Number, required: true }, run: { type: Object, required: true } })
const emit = defineEmits(['finished'])
const text = ref(''), error = ref(''), truncated = ref(false), eof = ref(false), busy = ref(false)
const follow = ref(props.run.status === 'running')
let offset = 0, alive = true, timer = null, controller = null
const clear = () => { clearTimeout(timer); timer = null }
async function readMore() {
  if (busy.value || !alive) return
  busy.value = true; error.value = ''
  controller = new AbortController()
  try {
    // Drain what is available right away; only an empty read waits for the next tick.
    for (;;) {
      const r = await agentApi.output(props.serverId, props.run.id, { offset }, controller.signal)
      if (!alive) return
      text.value += r.data || ''; truncated.value = !!r.truncated
      const advanced = r.offset > offset; offset = r.offset
      if (r.eof) { eof.value = true; follow.value = false; emit('finished'); return }
      if (!advanced || !r.data) break
    }
  } catch (e) { if (alive && e.name !== 'AbortError') error.value = e.message } finally { if (alive) busy.value = false }
  schedule()
}
function schedule() {
  clear()
  if (!alive || !follow.value || eof.value) return
  timer = setTimeout(() => { timer = null; if (!document.hidden) readMore(); }, 2000)
}
const onVisibility = () => { if (!document.hidden && follow.value && !eof.value && !busy.value && !timer) readMore() }
function toggle() { follow.value ? (clear(), follow.value = false) : (follow.value = true, readMore()) }
// A hidden page leaves the timer fired-and-skipped; visibilitychange restarts the loop.
onMounted(() => { document.addEventListener('visibilitychange', onVisibility); readMore() })
onBeforeUnmount(() => { alive = false; clear(); controller?.abort(); document.removeEventListener('visibilitychange', onVisibility) })
</script>
<template>
  <section aria-label="运行输出">
    <div class="ws-row ws-wrap"><h4>运行 #{{ run.id }} 输出</h4>
      <span><label v-if="!eof" class="ws-follow-label"><input type="checkbox" :checked="follow" @change="toggle">跟随（每 2 秒）</label>
      <button v-if="!eof && !follow" :disabled="busy" @click="readMore">刷新输出</button></span></div>
    <p v-if="truncated" class="ws-notice">输出超过 10 MiB，已截断</p>
    <p v-if="error" class="ws-error" role="alert">{{ error }}</p>
    <pre class="ws-log" data-testid="run-output">{{ text || (eof ? '（无输出）' : busy ? '读取中…' : '（暂无输出）') }}</pre>
  </section>
</template>
