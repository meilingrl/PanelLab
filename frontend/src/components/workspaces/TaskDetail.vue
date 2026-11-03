<script setup>
import { computed,ref,watch,onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { useTerminalStore } from '../../composables/useTerminalStore'
import { workspaceApi as api,kindLabels } from '../../composables/useWorkspaceApi'
import { taskBinding } from '../../composables/workspaceBinding'
import ObservationPanel from './ObservationPanel.vue'
import LogViewer from './LogViewer.vue'
import FileExplorer from './FileExplorer.vue'
import ReportedRuns from './ReportedRuns.vue'
const props=defineProps({task:{type:Object,required:true},server:Object,metadataRevision:{type:Number,default:0}})
const emit=defineEmits(['edit','deleted','refresh-task-info'])
const router=useRouter(),terminal=useTerminalStore()
const tab=ref('overview'),directory=ref(props.task.root_path),confirmDelete=ref(false),deleting=ref(false),opening=ref(false),error=ref(''),copied=ref('')
const binding=computed(()=>taskBinding(props.task,props.server))
let generation=0,terminalController
watch(binding,()=>{generation++;terminalController?.abort();tab.value='overview';directory.value=props.task.root_path;confirmDelete.value=false;error.value='';copied.value='';deleting.value=false;opening.value=false})
onBeforeUnmount(()=>{generation++;terminalController?.abort()})
async function openTerminal(){
 if(opening.value||!props.server)return
 const seq=generation;terminalController=new AbortController();opening.value=true;error.value=''
 try{const session=await terminal.createSession(props.server,{cwd:directory.value,signal:terminalController.signal});if(seq===generation)router.push('/terminal');else terminal.closeSession(session.id)}catch(e){if(seq===generation&&e.name!=='AbortError')error.value=e.message}finally{if(seq===generation){opening.value=false;terminalController=null}}
}
async function remove(){if(deleting.value)return;const seq=generation;deleting.value=true;error.value='';try{await api.deleteTask(props.task.id);if(seq===generation)emit('deleted',props.task.id)}catch(e){if(seq===generation)error.value=e.message}finally{if(seq===generation)deleting.value=false}}
async function copy(value,label){try{await navigator.clipboard.writeText(value);copied.value=label+'已复制'}catch{copied.value='无法自动复制，请选择下方文本复制'}}
const tabs={overview:'概览',log:'日志',files:'文件',runs:'上报记录'}
</script>
<template>
 <article data-testid="task-detail" class="ws-panel ws-detail">
  <header class="ws-detail-head"><div class="ws-row ws-wrap"><span class="ws-badge">{{ kindLabels[task.kind] }} · #{{ task.id }}</span><span class="ws-muted">{{ server?.name||'服务器 #'+task.server_id }}</span></div><h2>{{ task.name }}</h2><p class="ws-mono ws-wrap">{{ task.root_path }}</p><div class="ws-actions"><button class="ws-primary" data-testid="open-terminal" :disabled="opening||!server" @click="openTerminal">{{ opening?'等待 SSH 就绪…':'在此目录打开终端' }}</button><button @click="emit('edit',task)">编辑任务</button><button @click="copy(String(task.id),'任务 ID')">复制 ID</button></div><p class="ws-muted ws-wrap">终端目录：{{ directory }}</p><p v-if="error" class="ws-error" role="alert">{{ error }}</p></header>
  <div class="ws-tabs" role="tablist" aria-label="任务详情"><button v-for="(label,key) in tabs" :id="'tab-'+task.id+'-'+key" :key="key" :data-testid="'tab-'+key" role="tab" :aria-selected="tab===key" :aria-controls="'pane-'+task.id" @click="tab=key">{{ label }}</button></div>
  <div :id="'pane-'+task.id" class="ws-detail-content" role="tabpanel" :aria-labelledby="'tab-'+task.id+'-'+tab">
   <div v-show="tab==='overview'"><ObservationPanel :key="binding" :task="task" :server="server" :active="tab==='overview'" :metadata-revision="metadataRevision" @refresh-task-info="emit('refresh-task-info')" /><dl class="ws-facts"><div v-if="task.website_url"><dt>网站地址</dt><dd><a :href="task.website_url" target="_blank" rel="noopener noreferrer">{{ task.website_url }}</a></dd></div><div v-if="task.schedule_note"><dt>计划说明</dt><dd>{{ task.schedule_note }}</dd></div><div v-if="task.notes"><dt>备注</dt><dd>{{ task.notes }}</dd></div></dl>
   <details class="ws-cli"><summary>使用 CLI 查看同一任务</summary><code class="ws-wrap">python scripts/panellab.py --token-file /secure/panel-token observe {{ task.id }}</code><button @click="copy(`python scripts/panellab.py --token-file /secure/panel-token observe ${task.id}`,'CLI 示例')">复制 CLI 示例</button><p class="ws-muted">令牌文件路径为占位示例，请替换为你的安全令牌文件。</p></details><p v-if="copied" role="status">{{ copied }}</p>
   <div class="ws-danger-zone"><button data-testid="delete-task" @click="confirmDelete=true">取消登记此任务</button><div v-if="confirmDelete" role="alertdialog" aria-label="确认取消登记"><strong>仅删除任务登记及其上报记录。远程进程和文件保持不变。</strong><div class="ws-actions"><button data-testid="confirm-delete" :disabled="deleting" @click="remove">{{ deleting?'取消登记中…':'确认取消登记' }}</button><button :disabled="deleting" @click="confirmDelete=false">保留任务</button></div></div></div></div>
   <LogViewer v-if="tab==='log'" :key="binding+'-log'" :task-id="task.id" :path="task.log_path" />
   <FileExplorer v-if="tab==='files'" :key="binding+'-files'" :task="task" @directory="directory=$event" />
   <ReportedRuns v-if="tab==='runs'" :key="task.id+'-runs'" :task-id="task.id" />
  </div>
 </article>
</template>
