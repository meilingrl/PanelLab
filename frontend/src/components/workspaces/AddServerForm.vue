<script setup>
import { ref, reactive, onBeforeUnmount } from 'vue'
import { workspaceApi as api } from '../../composables/useWorkspaceApi'
const emit=defineEmits(['saved','cancel'])
const form=reactive({name:'',host:'',port:22,username:'',password:''})
const busy=ref(false),error=ref(''),devices=ref([]),discovery=ref(''),discovering=ref(false),authMode=ref('password')
let alive=true
onBeforeUnmount(()=>{alive=false;form.password=''})
async function discover(){if(discovering.value)return;discovering.value=true;discovery.value='';try{const result=await api.tailscale();if(alive){devices.value=result.devices;discovery.value=result.reason||`找到 ${result.devices.length} 个设备`}}catch(e){if(alive)discovery.value=e.message}finally{if(alive)discovering.value=false}}
function candidate(device){form.host=device.addresses?.[0]||device.dns_name;form.name ||= device.name;authMode.value='tailscale';form.password=''}
async function save(){
 if(busy.value)return;busy.value=true;error.value=''
 try{const server=await api.addServer({...form,port:Number(form.port),password:authMode.value==='password'?form.password:null});form.password='';if(alive)emit('saved',server,authMode.value)}catch(e){if(alive)error.value=e.message}finally{if(alive)busy.value=false}
}
</script>
<template>
 <section class="ws-panel ws-form" aria-label="添加服务器"><div class="ws-row"><h2>添加服务器</h2><button :disabled="busy" @click="emit('cancel')">关闭</button></div>
 <p class="ws-muted">先保存连接目标，再核对 SSH 主机指纹。不会自动信任设备。</p>
 <button :disabled="discovering||busy" @click="discover">{{ discovering?'正在发现…':'可选：发现 Tailscale 设备' }}</button><p v-if="discovery" role="status">{{ discovery }}</p>
 <div class="ws-candidates"><button v-for="device in devices" :key="device.dns_name||device.name" :disabled="busy" @click="candidate(device)">{{ device.name }} · {{ device.addresses?.[0]||device.dns_name }} · {{ device.online?'在线':'离线' }}</button></div>
 <form @submit.prevent="save"><div class="ws-form-grid"><label>显示名称<input v-model="form.name" required maxlength="64" :disabled="busy"></label><label>主机地址<input v-model="form.host" required maxlength="255" :disabled="busy" autocapitalize="off" spellcheck="false"></label><label>SSH 端口<input v-model="form.port" type="number" min="1" max="65535" required :disabled="busy"></label><label>SSH 用户名<input v-model="form.username" required maxlength="64" :disabled="busy" autocomplete="username"></label><label>认证方式<select v-model="authMode" :disabled="busy" @change="form.password=''"><option value="password">SSH 密码</option><option value="key">SSH 私钥</option><option value="tailscale">Tailscale SSH（none）</option></select></label><label v-if="authMode==='password'">SSH 密码<input v-model="form.password" type="password" maxlength="255" autocomplete="new-password" :disabled="busy"></label></div>
 <p v-if="authMode==='key'" class="ws-muted">保存后，在连接卡上传私钥，再核对主机指纹。</p><p v-if="authMode==='tailscale'" class="ws-muted">保存后，在连接卡选择 Tailscale SSH 并核对指纹。设备需要已有 Tailscale SSH 授权。</p><p v-if="error" class="ws-error" role="alert">{{ error }}</p><div class="ws-actions"><button class="ws-primary" :disabled="busy" type="submit">{{ busy?'保存中…':'保存服务器' }}</button><button type="button" :disabled="busy" @click="emit('cancel')">取消</button></div></form></section>
</template>
