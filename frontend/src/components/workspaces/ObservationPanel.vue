<script setup>
import { ref, watch, onBeforeUnmount, computed } from 'vue'
import { workspaceApi as api, formatBytes, formatTime, statusLabels } from '../../composables/useWorkspaceApi'
const props=defineProps({task:{type:Object,required:true}})
const observation=ref(null),error=ref(''),busy=ref(false),loadedAt=ref(0),clock=ref(Date.now())
let seq=0,controller
const stale=computed(()=>!!error.value || (loadedAt.value>0&&clock.value-loadedAt.value>30000))
const timer=setInterval(()=>{clock.value=Date.now()},5000)
async function refresh(){
 const n=++seq;controller?.abort();controller=new AbortController();busy.value=true;error.value=''
 try{const data=await api.observation(props.task.id,controller.signal);if(n===seq){observation.value=data;loadedAt.value=Date.now();clock.value=Date.now()}}
 catch(e){if(n===seq&&e.name!=='AbortError')error.value=e.message}
 finally{if(n===seq)busy.value=false}
}
watch(()=>props.task.id,()=>{observation.value=null;loadedAt.value=0;refresh()},{immediate:true})
onBeforeUnmount(()=>{seq++;controller?.abort();clearInterval(timer)})
</script>
<template>
 <section aria-label="任务观测">
  <div class="ws-row"><h3>进程观测</h3><button :disabled="busy" @click="refresh">{{ busy?'观测中…':'刷新观测' }}</button></div>
  <div class="ws-row ws-wrap"><strong class="ws-status">{{ observation ? statusLabels[observation.process_status]||'状态未知' : '尚无观测' }}</strong><span v-if="stale" class="ws-badge warn">已过期 · 上次数据</span><span class="ws-muted">采样于 {{ formatTime(observation?.observed_at) }}</span></div>
  <p v-if="error" class="ws-error" role="alert">{{ error }}{{ observation?' · 保留上次观测':'' }}</p>
  <p class="ws-muted">这是一次进程状态采样，运行中不代表训练有进展、网站健康或任务成功。</p>
  <dl class="ws-facts"><div><dt>进程 PID</dt><dd>{{ task.pid??'未绑定' }}</dd></div><div><dt>启动身份</dt><dd class="ws-mono">{{ task.process_identity||'未绑定' }}</dd></div><div v-if="observation?.process"><dt>进程命令</dt><dd class="ws-mono">{{ observation.process.command }}</dd></div><div v-if="observation?.process"><dt>进程状态 / 内存</dt><dd>{{ observation.process.state||'不可用' }} / {{ formatBytes(observation.process.memory_bytes) }}</dd></div><div><dt>日志文件</dt><dd>{{ task.log_path||'未选择' }}</dd></div><div v-if="task.log_path&&observation"><dt>日志最近修改</dt><dd>{{ observation.log?.available?formatTime(observation.log.modified_at):'不可用' }} · {{ formatBytes(observation.log?.size) }}</dd></div></dl>
  <h3 class="ws-subheading">服务器级资源</h3><p class="ws-muted">整台服务器的共享资源，不归因于当前任务。</p>
  <div class="ws-metric-grid"><div class="ws-metric"><span>服务器 CPU</span><strong>{{ observation?.system?.cpu?.percent==null?'不可用':observation.system.cpu.percent+'%' }}</strong><small>负载 {{ observation?.system?.cpu?.load_average?.join(' / ')||'不可用' }}</small></div><div class="ws-metric"><span>服务器内存</span><strong>{{ formatBytes(observation?.system?.memory?.available_bytes) }}</strong><small>可用 / 总计 {{ formatBytes(observation?.system?.memory?.total_bytes) }}</small></div></div>
  <h4>服务器网络 · 累计计数</h4><p v-if="!observation?.system?.network?.length" class="ws-muted">不可用</p><div v-for="net in observation?.system?.network||[]" :key="net.name" class="ws-counter"><code>{{ net.name }}</code><span>累计接收 {{ formatBytes(net.received_bytes) }}</span><span>累计发送 {{ formatBytes(net.sent_bytes) }}</span></div>
  <h4>服务器 GPU</h4><p v-if="!observation?.system?.gpu?.length" class="ws-muted">不可用</p><div v-for="gpu in observation?.system?.gpu||[]" :key="gpu.index" class="ws-counter"><span>{{ gpu.name }}</span><span>利用率 {{ gpu.utilization_percent==null?'不可用':gpu.utilization_percent+'%' }}</span><span>显存 {{ formatBytes(gpu.memory_used_bytes) }} / {{ formatBytes(gpu.memory_total_bytes) }}</span></div>
  <p v-for="warning in observation?.warnings||[]" :key="warning" class="ws-notice">{{ warning }}</p>
 </section>
</template>
