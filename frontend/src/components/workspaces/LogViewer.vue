<script setup>
import { ref, watch, onBeforeUnmount, computed } from 'vue'
import { workspaceApi as api,formatTime } from '../../composables/useWorkspaceApi'
const props=defineProps({taskId:{type:Number,required:true},path:String})
const data=ref(null),busy=ref(false),error=ref(''),loadedAt=ref(null),now=ref(Date.now())
let seq=0,controller
const stale=computed(()=>!!error.value||(loadedAt.value&&now.value-loadedAt.value>30000))
const timer=setInterval(()=>{now.value=Date.now()},5000)
async function refresh(){
 if(!props.path)return
 const n=++seq;controller?.abort();controller=new AbortController();busy.value=true;error.value=''
 try{const result=await api.text(props.taskId,props.path,controller.signal);if(n===seq){data.value=result;loadedAt.value=Date.now();now.value=Date.now()}}
 catch(e){if(n===seq&&e.name!=='AbortError')error.value=e.message}
 finally{if(n===seq)busy.value=false}
}
watch(()=>[props.taskId,props.path],()=>{seq++;controller?.abort();data.value=null;loadedAt.value=null;error.value='';busy.value=false;refresh()},{immediate:true})
onBeforeUnmount(()=>{seq++;controller?.abort();clearInterval(timer)})
</script>
<template>
 <section aria-label="文本预览"><div class="ws-row"><h3>文本末尾 · 最多 64 KiB</h3><button data-testid="refresh-log" :disabled="busy||!path" @click="refresh">{{ busy?'读取中…':'刷新文本' }}</button></div><p class="ws-mono ws-wrap">{{ path||'尚未选择日志文件。可在编辑任务中浏览并选择。' }}</p>
 <p v-if="loadedAt" class="ws-muted">读取于 {{ formatTime(new Date(loadedAt).toISOString()) }} <span v-if="stale" class="ws-badge warn">已过期 · 上次文本</span></p><p v-if="error" class="ws-error" role="alert">{{ error }}{{ data?' · 保留上次文本':'' }}</p><p v-if="data?.truncated" class="ws-notice">文件较大，仅显示末尾 64 KiB。</p><p v-if="data?.changed_during_read" class="ws-notice">文件在读取期间变化，这份文本可能不是同一时刻的快照。</p><pre v-if="data" class="ws-log" tabindex="0">{{ data.text||'（空文件）' }}</pre>
 </section>
</template>
