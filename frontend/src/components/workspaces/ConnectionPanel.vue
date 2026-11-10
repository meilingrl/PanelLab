<script setup>
import { computed, ref, watch, onBeforeUnmount } from 'vue'
import { workspaceApi as api, formatTime } from '../../composables/useWorkspaceApi'
import { serverBinding } from '../../composables/workspaceBinding'
import PrivateKeyPanel from './PrivateKeyPanel.vue'
import ServerEditForm from './ServerEditForm.vue'
const props=defineProps({server:{type:Object,required:true},initialAuthMode:String})
const emit=defineEmits(['connected','updated'])
const info=ref(null),probe=ref(null),error=ref(''),busy=ref(false),checked=ref(null),authMode=ref('password')
const modeChanged=computed(()=>!!info.value?.pinned&&authMode.value!==info.value.auth_mode)
const keyState=ref(null),editing=ref(false)
watch(()=>props.server.id,()=>{editing.value=false})
function edited(server){editing.value=false;emit('updated',server)}
// Key login needs a stored key before trust sends credentials; null means not loaded yet.
const keyMissing=computed(()=>authMode.value==='key'&&!keyState.value?.configured)
let generation=0,controller,modeTouched=false
watch(()=>serverBinding(props.server),async ()=>{
  const id=props.server.id
  const seq=++generation;controller?.abort();controller=new AbortController()
  modeTouched=false;info.value=null;probe.value=null;checked.value=null;error.value='';busy.value=false;authMode.value=props.initialAuthMode||'password'
  try {
    const result=await api.connection(id,controller.signal);if(seq!==generation)return
    info.value=result;authMode.value=result.auth_mode||props.initialAuthMode||'password'
    // Nothing pinned yet: a server whose key is already saved was set up for key login.
    if(!result.auth_mode&&!props.initialAuthMode){
      const key=await api.privateKey(id,controller.signal).catch(()=>null)
      if(seq===generation&&key?.configured&&!modeTouched&&!busy.value&&!probe.value)authMode.value='key'
    }
  }
  catch(e){if(seq===generation&&e.name!=='AbortError')error.value=e.message}
},{immediate:true})
onBeforeUnmount(()=>{generation++;controller?.abort()})
async function run(action){
  if(busy.value||(action==='check'&&modeChanged.value)||(action!=='probe'&&keyMissing.value))return
  const seq=generation;const id=props.server.id
  busy.value=true;error.value=''
  try{
    if(action==='probe') { const result=await api.probe(id,authMode.value);if(seq===generation)probe.value=result }
    else {
      if(action==='trust'){
        const saved=await api.trust(id,probe.value.token)
        if(seq!==generation)return
        // Trust is durable even if the separate authenticated check fails.
        info.value={...info.value,configured:true,pinned:true,...saved}
        probe.value=null
      }
      if(seq!==generation)return
      const result=await api.check(id)
      if(seq!==generation)return
      const summary=await api.connection(id)
      if(seq===generation){checked.value=result;probe.value=null;info.value=summary;emit('connected',result)}
    }
  } catch(e){if(seq===generation){error.value=e.message;checked.value=null}}
  finally{if(seq===generation)busy.value=false}
}
</script>
<template>
  <section class="ws-connection" aria-label="服务器连接">
    <div class="ws-row"><div><span class="ws-eyebrow">REMOTE CONNECTION</span><h3>{{ server.name }}</h3><p class="ws-mono">{{ server.username }}@{{ server.host }}:{{ server.port }}</p></div><span class="ws-badge" :class="{good:checked}">{{ checked?'已连接': info?.pinned?'已固定指纹 · 待检查':'需要确认指纹' }}</span></div>
    <p v-if="checked" class="ws-muted">检查于 {{ formatTime(checked.observed_at) }} · 主目录 {{ checked.home }}</p>
    <p v-if="info?.fingerprint" class="ws-mono ws-wrap">已保存 {{ info.fingerprint }}</p>
    <div class="ws-row ws-wrap">
      <label>认证方式 <select v-model="authMode" :disabled="busy" @change="modeTouched=true;probe=null;checked=null"><option value="password">SSH 密码</option><option value="key">SSH 私钥</option><option value="tailscale">Tailscale SSH（none）</option></select></label>
      <button data-testid="probe" :disabled="busy" @click="run('probe')">{{ busy?'处理中…':'获取 / 更新主机指纹' }}</button>
      <button v-if="info?.pinned" :disabled="busy||modeChanged||keyMissing" @click="run('check')">检查连接 / 重试</button>
      <button v-if="!editing" :disabled="busy" data-testid="edit-connection" @click="editing=true">编辑连接</button>
    </div>
    <ServerEditForm v-if="editing" :key="server.id" :server="server" :auth-mode="authMode" @saved="edited" @cancel="editing=false" />
    <PrivateKeyPanel v-if="authMode==='key'" :server-id="server.id" @change="keyState=$event" />
    <p v-if="keyMissing&&keyState" class="ws-notice">先保存私钥，再信任指纹并连接。</p>
    <p v-if="modeChanged" class="ws-notice">认证方式已改变。请重新获取指纹并明确确认信任后，才能按所选方式连接。</p>
    <div v-if="probe" class="ws-trust" role="region" aria-label="确认主机指纹">
      <strong>确认目标 {{ probe.host }}:{{ probe.port }}</strong><p>{{ probe.key_type }}</p><code class="ws-wrap">{{ probe.fingerprint }}</code>
      <p>请通过服务器控制台或管理员等独立渠道核对指纹。确认后才会保存信任并发送认证信息。</p>
      <button class="ws-primary" data-testid="trust" :disabled="busy||keyMissing" @click="run('trust')">已核对，信任此指纹并连接</button><button :disabled="busy" @click="probe=null">取消</button>
    </div>
    <p v-if="error" class="ws-error" role="alert">{{ error }}</p>
  </section>
</template>
