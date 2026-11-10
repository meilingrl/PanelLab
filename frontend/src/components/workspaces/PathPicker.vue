<script setup>
import { computed, ref, watch, onBeforeUnmount } from 'vue'
import { workspaceApi as api } from '../../composables/useWorkspaceApi'
const props=defineProps({serverId:{type:Number,required:true},mode:{type:String,default:'directory'},initial:String,root:String})
const emit=defineEmits(['select','close'])
const listing=ref(null),error=ref(''),busy=ref(false)
let seq=0,controller
const crumbs=computed(()=>{
 const cwd=listing.value?.cwd||'';const parts=cwd.split('/').filter(Boolean)
 return [{name:'/',path:'/'},...parts.map((name,i)=>({name,path:'/'+parts.slice(0,i+1).join('/')}))].filter(c=>!props.root||props.root==='/'||c.path===props.root||c.path.startsWith(props.root+'/'))
})
async function load(path){
 const n=++seq;controller?.abort();controller=new AbortController();busy.value=true;error.value=''
 try {const data=await api.serverFiles(props.serverId,path,controller.signal);if(n===seq)listing.value=data}
 catch(e){if(n===seq&&e.name!=='AbortError')error.value=e.message}
 finally{if(n===seq)busy.value=false}
}
function allowed(path){return !props.root||props.root==='/'||path===props.root||path.startsWith(props.root+'/')}
watch(()=>[props.serverId,props.initial],()=>{listing.value=null;load(props.initial)},{immediate:true})
onBeforeUnmount(()=>{seq++;controller?.abort()})
</script>
<template>
  <section class="ws-picker" aria-label="远程路径选择器">
    <div class="ws-row"><h4>{{ mode==='directory'?'选择任务目录':'选择日志文件' }}</h4><button type="button" @click="emit('close')" aria-label="关闭路径选择">关闭</button></div>
    <nav class="ws-crumbs" aria-label="目录层级"><button v-for="crumb in crumbs" :key="crumb.path" type="button" :disabled="busy" @click="load(crumb.path)">{{ crumb.name }}</button></nav>
    <p class="ws-mono ws-wrap">{{ listing?.cwd || '正在读取主目录…' }}</p>
    <button v-if="listing?.parent&&allowed(listing.parent)" type="button" :disabled="busy" @click="load(listing.parent)">↑ 上一级</button>
    <ul class="ws-file-list"><li v-for="entry in listing?.entries||[]" :key="entry.path"><button type="button" :data-testid="'path-open-'+entry.path" :disabled="busy||entry.disabled||!['directory','file'].includes(entry.type)||(mode==='directory'&&entry.type!=='directory')||!allowed(entry.path)" @click="entry.type==='directory'?load(entry.path):emit('select',entry.path)"><span>{{ entry.type==='directory'?'▱':'▤' }} {{ entry.name }}</span><small>{{ entry.type==='symlink'?'符号链接 · 不可选':entry.type==='directory'?'打开目录':'选择文件' }}</small></button></li></ul>
    <p v-if="listing?.truncated" class="ws-muted">只显示前 500 项。</p><p v-if="error" class="ws-error" role="alert">{{ error }} <button type="button" @click="load(listing?.cwd||initial)">重试</button></p>
    <button v-if="mode==='directory'" type="button" class="ws-primary" data-testid="choose-directory" :disabled="busy||!listing" @click="emit('select',listing.cwd)">使用此目录</button>
  </section>
</template>
