<script setup>
import { ref, watch, onBeforeUnmount } from 'vue'
import { workspaceApi as api,formatTime } from '../../composables/useWorkspaceApi'
const props=defineProps({taskId:{type:Number,required:true}})
const items=ref([]),busy=ref(false),error=ref(''),more=ref(false)
let seq=0,controller
async function load(append=false){
 if(busy.value)return
 const n=++seq;controller?.abort();controller=new AbortController();busy.value=true;error.value=''
 try{const data=await api.runs(props.taskId,append?items.value.at(-1)?.id:undefined,controller.signal);if(n===seq){items.value=append?[...items.value,...data.items]:data.items;more.value=data.items.length===20}}
 catch(e){if(n===seq&&e.name!=='AbortError')error.value=e.message}
 finally{if(n===seq)busy.value=false}
}
watch(()=>props.taskId,()=>{seq++;controller?.abort();items.value=[];busy.value=false;load()},{immediate:true})
onBeforeUnmount(()=>{seq++;controller?.abort()})
const labels={running:'上报：运行中',succeeded:'上报：成功',failed:'上报：失败'}
</script>
<template>
 <section aria-label="上报记录"><div class="ws-row"><h3>上报记录</h3><button :disabled="busy" @click="load()">{{ busy?'读取中…':'刷新记录' }}</button></div><p class="ws-muted">用户或 CLI 提交的运行报告，不是调度器执行结果，也未独立验证任务成功。</p><p v-if="error" class="ws-error" role="alert">{{ error }} · 已保留加载过的记录</p><article v-for="run in items" :key="run.id" class="ws-run"><div class="ws-row"><strong>{{ labels[run.status]||run.status }}</strong><span class="ws-badge">reported · 上报</span></div><p>{{ run.summary||'无摘要' }}</p><dl class="ws-facts"><div><dt>开始</dt><dd>{{ formatTime(run.started_at) }}</dd></div><div><dt>结束</dt><dd>{{ formatTime(run.finished_at) }}</dd></div><div><dt>退出码 / 上报人</dt><dd>{{ run.exit_code??'未提供' }} / {{ run.reported_by??'未提供' }}</dd></div><div v-if="run.artifact_path"><dt>产物路径（引用）</dt><dd>{{ run.artifact_path }}</dd></div></dl></article><p v-if="!items.length&&!busy" class="ws-muted">尚无上报记录。可通过 CLI report 提交。</p><button v-if="more" :disabled="busy" @click="load(true)">加载更早记录</button>
 </section>
</template>
