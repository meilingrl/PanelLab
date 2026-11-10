<script setup>
import { computed,ref,onMounted,onBeforeUnmount } from 'vue'
import { workspaceApi as api,kindLabels } from '../../composables/useWorkspaceApi'
import ConnectionPanel from '../../components/workspaces/ConnectionPanel.vue'
import AddServerForm from '../../components/workspaces/AddServerForm.vue'
import TaskForm from '../../components/workspaces/TaskForm.vue'
import TaskDetail from '../../components/workspaces/TaskDetail.vue'
import '../../components/workspaces/workspaces.css'
const authSuggestions=ref({})
const metadataRevision=ref(0)
const servers=ref([]),tasks=ref([]),serverId=ref(''),kind=ref(''),selectedId=ref(null),loading=ref(false),error=ref(''),showServer=ref(false),showForm=ref(false),editing=ref(null)
let seq=0,controller
const visible=computed(()=>tasks.value.filter(t=>(!serverId.value||t.server_id===Number(serverId.value))&&(!kind.value||t.kind===kind.value)))
const selected=computed(()=>visible.value.find(t=>t.id===selectedId.value)||visible.value[0]||null)
const connectionServer=computed(()=>servers.value.find(s=>s.id===Number(serverId.value))||servers.value.find(s=>s.id===selected.value?.server_id)||servers.value[0])
const taskServer=computed(()=>servers.value.find(s=>s.id===selected.value?.server_id))
async function load(){
 const n=++seq;controller?.abort();controller=new AbortController();loading.value=true;error.value=''
 const results=await Promise.allSettled([api.servers(controller.signal),api.tasks(controller.signal)])
 if(n!==seq)return
 const messages=[]
 if(results[0].status==='fulfilled')servers.value=results[0].value.items;else if(results[0].reason.name!=='AbortError')messages.push(results[0].reason.message)
 if(results[1].status==='fulfilled')tasks.value=results[1].value.items;else if(results[1].reason.name!=='AbortError')messages.push(results[1].reason.message)
 if(results.every(result=>result.status==='fulfilled'))metadataRevision.value++
 error.value=messages.join('；');loading.value=false
}
function newTask(){editing.value=null;showForm.value=true;showServer.value=false}
function edit(task){editing.value=task;showForm.value=true;showServer.value=false}
async function saved(task){showForm.value=false;editing.value=null;await load();if(task?.id){selectedId.value=task.id;serverId.value=String(task.server_id);kind.value=''}}
async function serverSaved(server,authMode){authSuggestions.value[server.id]=authMode;showServer.value=false;await load();serverId.value=String(server.id)}
async function serverUpdated(server){servers.value=servers.value.map(s=>s.id===server.id?server:s);await load()}
function removed(id){tasks.value=tasks.value.filter(t=>t.id!==id);selectedId.value=null}
onMounted(load)
onBeforeUnmount(()=>{seq++;controller?.abort()})
</script>
<template>
 <div class="workspaces">
  <header class="ws-page-head"><div><span class="ws-eyebrow">YOUR REMOTE WORK</span><h1>任务工作区</h1><p>连接服务器，找到任务，接着工作。</p></div><div class="ws-actions"><button @click="showServer=!showServer;showForm=false">添加服务器</button><button class="ws-primary" :disabled="!servers.length" @click="newTask">＋ 登记任务</button></div></header>
  <div v-if="error" class="ws-error" role="alert">{{ error }} · 已加载的数据保持显示 <button :disabled="loading" @click="load">重试</button></div>
  <AddServerForm v-if="showServer" @saved="serverSaved" @cancel="showServer=false" />
  <div class="ws-filter-bar"><label>服务器<select v-model="serverId"><option value="">全部服务器</option><option v-for="s in servers" :key="s.id" :value="String(s.id)">{{ s.name }} · {{ s.host }}</option></select></label><label>任务类型<select v-model="kind"><option value="">全部类型</option><option v-for="(label,value) in kindLabels" :key="value" :value="value">{{ label }}</option></select></label><button :disabled="loading" @click="load">{{ loading?'加载中…':'刷新列表' }}</button><span class="ws-muted">{{ visible.length }} 个任务</span></div>
  <ConnectionPanel v-if="connectionServer" :server="connectionServer" :initial-auth-mode="authSuggestions[connectionServer.id]" @updated="serverUpdated" />
  <section v-else-if="!loading&&!showServer" class="ws-panel ws-empty"><span class="ws-empty-mark">↗</span><h2>从一台服务器开始</h2><p>添加连接目标、核对主机指纹，然后浏览目录并登记已有任务。</p><button class="ws-primary" @click="showServer=true">添加第一台服务器</button></section>
  <TaskForm v-if="showForm" :key="editing?.id||'new'" :servers="servers" :task="editing||undefined" :initial-server="Number(serverId)||connectionServer?.id" @saved="saved" @cancel="showForm=false" />
  <div v-else class="ws-board"><aside class="ws-task-list" aria-label="任务列表"><div class="ws-list-heading"><h2>我的任务</h2><span>{{ visible.length }}</span></div><button v-for="task in visible" :key="task.id" :data-testid="'task-'+task.id" class="ws-task-card" :class="{selected:selected?.id===task.id}" :aria-pressed="selected?.id===task.id" @click="selectedId=task.id"><span class="ws-row"><span class="ws-badge">{{ kindLabels[task.kind] }}</span><span class="ws-muted">#{{ task.id }}</span></span><strong>{{ task.name }}</strong><span class="ws-card-server">{{ servers.find(s=>s.id===task.server_id)?.name||'服务器 #'+task.server_id }}</span><code>{{ task.root_path }}</code><span class="ws-card-foot">{{ task.pid?'绑定 PID '+task.pid:'未绑定进程' }}<span>打开 →</span></span></button><div v-if="!visible.length&&!loading" class="ws-empty-list"><h3>这里还没有任务</h3><p>登记已存在的训练、网站或定时任务，将目录和进程留在一个地方。</p><button :disabled="!servers.length" @click="newTask">登记任务</button></div></aside>
  <TaskDetail v-if="selected" :key="selected.id" :task="selected" :server="taskServer" :metadata-revision="metadataRevision" @refresh-task-info="load" @edit="edit" @deleted="removed" /><section v-else-if="servers.length" class="ws-panel ws-empty"><span class="ws-empty-mark">▱</span><h2>给远程工作一个入口</h2><p>选择任务即可查看观测、日志、文件和上报记录。</p></section></div>
 </div>
</template>
