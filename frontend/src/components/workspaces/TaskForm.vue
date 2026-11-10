<script setup>
import { ref, reactive, watch, onBeforeUnmount } from 'vue'
import PathPicker from './PathPicker.vue'
import { workspaceApi as api } from '../../composables/useWorkspaceApi'
import { warningTexts } from '../../composables/remoteWarnings'
const props=defineProps({servers:{type:Array,required:true},task:Object,initialServer:Number})
const emit=defineEmits(['saved','cancel'])
const form=reactive({name:props.task?.name||'',kind:props.task?.kind||'training',server_id:props.task?.server_id||props.initialServer||props.servers[0]?.id||'',root_path:props.task?.root_path||'',log_path:props.task?.log_path||'',website_url:props.task?.website_url||'',schedule_note:props.task?.schedule_note||'',notes:props.task?.notes||''})
const picker=ref(null),query=ref(''),processes=ref([]),selectedPid=ref(props.task?.pid?String(props.task.pid):''),boundProcess=ref(props.task?.pid?{pid:props.task.pid,identity:props.task.process_identity,command:`已绑定 PID ${props.task.pid}`} :null),busy=ref(false),searching=ref(false),error=ref(''),warnings=ref([]),searched=ref(null),prefilledRoot=ref('')
let sequence=0,controller,alive=true
watch(()=>form.server_id,()=>{sequence++;controller?.abort();form.root_path='';form.log_path='';processes.value=[];selectedPid.value='';boundProcess.value=null;picker.value=null;searching.value=false;warnings.value=[];searched.value=null;prefilledRoot.value=''})
onBeforeUnmount(()=>{alive=false;sequence++;controller?.abort()})
function choosePath(path){
 if(picker.value==='root'){if(path!==form.root_path)form.log_path='';form.root_path=path;prefilledRoot.value=''}else form.log_path=path
 picker.value=null
}
async function search(){
 const seq=++sequence;controller?.abort();controller=new AbortController();searching.value=true;error.value=''
 try{const data=await api.processes(Number(form.server_id),query.value,controller.signal);if(seq===sequence){processes.value=data.processes;searched.value=data.processes.length;warnings.value=[...warningTexts((data.warnings||[]).filter(w=>w!=='GPU unavailable')),...(data.capabilities?.processes_truncated?['进程列表已截断，请缩小搜索范围。']:[])]}}
 catch(e){if(seq===sequence&&e.name!=='AbortError')error.value=e.message}
 finally{if(seq===sequence)searching.value=false}
}
function bind(){
 boundProcess.value=processes.value.find(p=>String(p.pid)===selectedPid.value)||null
 // Unbinding withdraws a directory that only came from the process.
 if(!boundProcess.value&&prefilledRoot.value&&form.root_path===prefilledRoot.value){form.root_path='';form.log_path='';prefilledRoot.value=''}
 // Suggest the process's working directory, but never replace a directory the user picked.
 const cwd=boundProcess.value?.cwd
 if(typeof cwd==='string'&&cwd.startsWith('/')&&!cwd.endsWith(' (deleted)')&&(!form.root_path||form.root_path===prefilledRoot.value)){
  if(cwd!==form.root_path)form.log_path=''
  form.root_path=cwd;prefilledRoot.value=cwd
 }
}
async function save(){
 if(busy.value)return
 if(!form.root_path){error.value='请先浏览并选择任务目录';return}
 busy.value=true;error.value=''
 const selected=boundProcess.value
 const payload={...form,server_id:Number(form.server_id),pid:selected?.pid||null,process_identity:selected?.identity||null,log_path:form.log_path||null,website_url:form.website_url||null,schedule_note:form.schedule_note||null,notes:form.notes||null}
 try{const data=props.task?await api.updateTask(props.task.id,payload):await api.createTask(payload);if(alive)emit('saved',data)}catch(e){if(alive)error.value=e.message}finally{if(alive)busy.value=false}
}
</script>
<template>
 <section class="ws-panel ws-form" aria-label="任务登记">
  <div class="ws-row"><div><span class="ws-eyebrow">TASK CONTEXT</span><h2>{{ task?'编辑任务':'登记远程任务' }}</h2></div><button type="button" :disabled="busy" @click="emit('cancel')">关闭</button></div>
  <p class="ws-muted">关联已有进程与文件，方便再次找到它。登记不会启动远程任务。</p>
  <form @submit.prevent="save">
   <div class="ws-form-grid"><label>任务名称<input name="name" v-model="form.name" required maxlength="100" placeholder="例如：视觉模型 · 第三轮训练" :disabled="busy"></label><label>任务类型<select name="kind" v-model="form.kind" :disabled="busy"><option value="training">训练</option><option value="website">网站</option><option value="scheduled">定时任务</option></select></label>
   <label class="ws-full">服务器<select name="server" v-model="form.server_id" required :disabled="busy"><option v-for="s in servers" :key="s.id" :value="s.id">{{ s.name }} · {{ s.username }}@{{ s.host }}</option></select></label></div>
   <div class="ws-field"><strong>任务目录</strong><div class="ws-path-row"><code>{{ form.root_path||'从服务器主目录开始选择' }}</code><button type="button" data-testid="open-root-picker" :disabled="busy||!form.server_id" @click="picker=picker==='root'?null:'root'">浏览目录</button></div></div>
   <div class="ws-field"><strong>日志文件（可选）</strong><div class="ws-path-row"><code>{{ form.log_path||'尚未选择日志' }}</code><button type="button" :disabled="busy||!form.root_path" @click="picker=picker==='log'?null:'log'">选择日志</button><button v-if="form.log_path" type="button" :disabled="busy" @click="form.log_path=''">清除</button></div></div>
   <PathPicker v-if="picker" :server-id="Number(form.server_id)" :mode="picker==='root'?'directory':'file'" :initial="picker==='root'?(form.root_path||undefined):form.root_path" :root="picker==='log'?form.root_path:undefined" @select="choosePath" @close="picker=null" />
   <fieldset :disabled="busy"><legend>关联已有进程（可选）</legend><div class="ws-search"><label>搜索 PID / 命令 / 目录<input name="process-query" v-model="query" maxlength="128" placeholder="例如 train.py" @keydown.enter.prevent="search"></label><button type="button" data-testid="search-process" :disabled="searching||!form.server_id" @click="search">{{ searching?'查找中…':'查找进程' }}</button></div>
   <label>选择进程<select name="process" v-model="selectedPid" @change="bind"><option value="">不绑定进程</option><option v-if="boundProcess&&!processes.some(p=>p.pid===boundProcess.pid)" :value="String(boundProcess.pid)">PID {{ boundProcess.pid }} · {{ boundProcess.command }}</option><option v-for="p in processes" :key="p.identity" :value="String(p.pid)">PID {{ p.pid }} · {{ p.command }}</option></select></label><p v-if="searched!==null&&!searching" class="ws-muted" data-testid="process-count">{{ searched?`找到 ${searched} 个进程，请在上方选择。`:'没有匹配的进程。' }}</p><p v-if="boundProcess" class="ws-mono ws-wrap">身份 {{ boundProcess.identity }}</p><p v-if="prefilledRoot&&form.root_path===prefilledRoot" class="ws-muted" data-testid="root-prefilled">已按该进程的工作目录填入任务目录，可点「浏览目录」改选。</p><p class="ws-muted">使用 PID 与启动身份共同识别；进程列表不是完整任务清单。</p><p v-for="warning in warnings" :key="warning" class="ws-muted">{{ warning }}</p></fieldset>
   <label v-if="form.kind==='website'" class="ws-field">网站地址（可选）<input v-model="form.website_url" type="url" maxlength="2048" placeholder="https://example.com" :disabled="busy"></label>
   <label v-if="form.kind==='scheduled'" class="ws-field">计划说明（可选）<input v-model="form.schedule_note" maxlength="200" placeholder="例如：每天凌晨由现有调度器运行" :disabled="busy"></label>
   <label class="ws-field">备注（可选）<textarea v-model="form.notes" rows="2" maxlength="2000" :disabled="busy" /></label>
   <p v-if="error" role="alert" class="ws-error">{{ error }}</p><div class="ws-actions"><button class="ws-primary" type="submit" :disabled="busy||!form.server_id">{{ busy?'保存中…':'保存任务' }}</button><button type="button" :disabled="busy" @click="emit('cancel')">取消</button></div>
  </form>
 </section>
</template>
