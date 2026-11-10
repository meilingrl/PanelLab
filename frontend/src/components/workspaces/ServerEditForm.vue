<script setup>
import { computed, reactive, ref, onBeforeUnmount } from 'vue'
import { workspaceApi as api } from '../../composables/useWorkspaceApi'
// Mounted per server (keyed by the parent), so a late reply never lands on another server's form.
const props=defineProps({server:{type:Object,required:true},authMode:String})
const emit=defineEmits(['saved','cancel'])
const form=reactive({name:props.server.name,host:props.server.host,port:props.server.port,username:props.server.username,password:''})
const busy=ref(false),error=ref('')
const hostChanged=computed(()=>form.host.trim()!==props.server.host)
let alive=true
onBeforeUnmount(()=>{alive=false;form.password=''})
async function save(){
 if(busy.value)return;busy.value=true;error.value=''
 try{
  const saved=await api.updateServer(props.server.id,{name:form.name,host:form.host.trim(),port:Number(form.port),username:form.username.trim(),password:props.authMode==='password'&&form.password?form.password:null})
  form.password=''
  if(alive)emit('saved',saved)
 }catch(e){if(alive)error.value=e.message}
 finally{if(alive)busy.value=false}
}
</script>
<template>
 <form class="ws-trust" aria-label="编辑连接" @submit.prevent="save">
  <strong>编辑连接</strong>
  <div class="ws-form-grid"><label>显示名称<input v-model="form.name" required maxlength="64" :disabled="busy"></label><label>主机地址<input v-model="form.host" required maxlength="255" :disabled="busy" autocapitalize="off" spellcheck="false" data-testid="edit-host"></label><label>SSH 端口<input v-model="form.port" type="number" min="1" max="65535" required :disabled="busy"></label><label>SSH 用户名<input v-model="form.username" required maxlength="64" :disabled="busy" autocomplete="username" data-testid="edit-username"></label><label v-if="authMode==='password'">SSH 密码<input v-model="form.password" type="password" maxlength="255" autocomplete="new-password" placeholder="留空则不修改" :disabled="busy" data-testid="edit-password"></label></div>
  <p class="ws-muted">修改主机、端口或用户名后，已确认的指纹会失效，需要重新核对。</p>
  <p v-if="hostChanged" class="ws-notice" data-testid="edit-host-notice">已保存的密码或私钥会继续用于新地址（核对并信任新指纹后才会发送）。新服务器使用不同凭据时，请一并更新。</p>
  <p v-if="error" class="ws-error" role="alert">{{ error }}</p>
  <div class="ws-actions"><button class="ws-primary" type="submit" :disabled="busy" data-testid="edit-save">{{ busy?'保存中…':'保存连接' }}</button><button type="button" :disabled="busy" @click="emit('cancel')">取消</button></div>
 </form>
</template>
