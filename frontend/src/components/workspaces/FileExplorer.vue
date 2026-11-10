<script setup>
import { computed, ref, watch, onBeforeUnmount } from 'vue'
import { workspaceApi as api,formatBytes } from '../../composables/useWorkspaceApi'
import LogViewer from './LogViewer.vue'
import { taskBinding } from '../../composables/workspaceBinding'
const props=defineProps({task:{type:Object,required:true}})
const emit=defineEmits(['directory'])
const listing=ref(null),error=ref(''),busy=ref(false),downloading=ref(null),textPath=ref(null),stale=ref(false)
let seq=0,generation=0,controller,downloadController
const crumbs=computed(()=>{
 const cwd=listing.value?.cwd||props.task.root_path,root=props.task.root_path
 const suffix=cwd.slice(root==='/'?0:root.length).split('/').filter(Boolean)
 return [{name:root.split('/').filter(Boolean).at(-1)||'/',path:root},...suffix.map((name,i)=>({name,path:(root==='/'?'':root)+'/'+suffix.slice(0,i+1).join('/')}))]
})
async function load(path){
 const n=++seq;controller?.abort();controller=new AbortController();busy.value=true;error.value=''
 try{const result=await api.files(props.task.id,path,controller.signal);if(n===seq){listing.value=result;stale.value=false;textPath.value=null;emit('directory',result.cwd)}}
 catch(e){if(n===seq&&e.name!=='AbortError'){error.value=e.message;stale.value=true}}
 finally{if(n===seq)busy.value=false}
}
async function download(entry){
 if(downloading.value)return
 const n=generation;downloadController=new AbortController();downloading.value=entry.path;error.value=''
 try{const blob=await api.download(props.task.id,entry.path,downloadController.signal);if(n!==generation)return;const url=URL.createObjectURL(blob);const link=document.createElement('a');link.href=url;link.download=entry.name;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000)}catch(e){if(n===generation&&e.name!=='AbortError')error.value=e.message}finally{if(n===generation){downloading.value=null;downloadController=null}}
}
watch(()=>taskBinding(props.task),()=>{generation++;downloadController?.abort();listing.value=null;textPath.value=null;downloading.value=null;stale.value=false;load()},{immediate:true})
onBeforeUnmount(()=>{generation++;seq++;controller?.abort();downloadController?.abort()})
</script>
<template>
 <section aria-label="任务文件"><div class="ws-row"><h3>任务文件</h3><button :disabled="busy" @click="load(listing?.cwd)">{{ busy?'读取中…':'刷新目录' }}</button></div><p class="ws-muted">仅浏览任务根目录内的普通目录和文件。下载上限 16 MiB。</p>
 <nav class="ws-crumbs" aria-label="任务目录层级"><button v-for="crumb in crumbs" :key="crumb.path" :disabled="busy" @click="load(crumb.path)">{{ crumb.name }}</button></nav><p class="ws-mono ws-wrap">{{ listing?.cwd||task.root_path }}</p><button v-if="listing?.parent" :disabled="busy" @click="load(listing.parent)">↑ 上一级</button>
 <p v-if="error" class="ws-error" role="alert">{{ error }}</p><p v-if="stale" class="ws-notice">已过期 · 保留上次目录</p><p v-if="listing?.truncated" class="ws-notice">仅显示前 500 项。</p>
 <ul class="ws-file-list"><li v-for="entry in listing?.entries||[]" :key="entry.path"><div class="ws-file-row"><button :disabled="busy||entry.disabled||!['directory','file'].includes(entry.type)" @click="entry.type==='directory'?load(entry.path):textPath=entry.path"><span>{{ entry.type==='directory'?'▱':'▤' }} {{ entry.name }}</span><small>{{ entry.type==='directory'?'打开目录':entry.type==='file'?formatBytes(entry.size):'不可访问 · '+entry.type }}</small></button><button v-if="entry.type==='file'&&!entry.disabled" :data-testid="'download-'+entry.path" :aria-label="'下载 '+entry.name" :disabled="!!downloading" @click="download(entry)">{{ downloading===entry.path?'下载中…':'下载' }}</button></div></li></ul><p v-if="listing&&!listing.entries.length" class="ws-muted">此目录为空。</p><LogViewer v-if="textPath" :task-id="task.id" :path="textPath" />
 </section>
</template>
