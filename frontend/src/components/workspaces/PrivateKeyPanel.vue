<script setup>
import { ref, watch, onBeforeUnmount } from 'vue'
import { workspaceApi as api } from '../../composables/useWorkspaceApi'
const props=defineProps({serverId:{type:Number,required:true}})
const emit=defineEmits(['change'])
const KEY_LIMIT=16*1024
const info=ref(null),text=ref(''),passphrase=ref(''),editing=ref(false),busy=ref(false),error=ref('')
let generation=0,alive=true
function clearSecrets(){text.value='';passphrase.value=''}
function publish(value){info.value=value;emit('change',value)}
watch(()=>props.serverId,async id=>{
  const seq=++generation;publish(null);editing.value=false;error.value='';clearSecrets()
  try{const result=await api.privateKey(id);if(seq===generation){publish(result);editing.value=!result.configured}}
  catch(e){if(seq===generation)error.value=e.message}
},{immediate:true})
onBeforeUnmount(()=>{alive=false;generation++;clearSecrets()})
async function pick(event){
  const file=event.target.files?.[0];event.target.value=''
  if(!file)return
  if(file.size>KEY_LIMIT){error.value='私钥文件超过 16 KiB，请确认选择的是私钥文件';return}
  text.value=await file.text();error.value=''
}
async function save(){
  if(busy.value||!text.value.trim())return
  const seq=generation;busy.value=true;error.value=''
  try{
    const result=await api.setPrivateKey(props.serverId,{private_key:text.value,passphrase:passphrase.value||null})
    clearSecrets()
    if(seq===generation&&alive){publish(result);editing.value=false}
  }catch(e){passphrase.value='';if(seq===generation&&alive)error.value=e.message}
  finally{if(seq===generation&&alive)busy.value=false}
}
async function remove(){
  if(busy.value)return
  const seq=generation;busy.value=true;error.value=''
  try{const result=await api.deletePrivateKey(props.serverId);if(seq===generation&&alive){publish(result);editing.value=true}}
  catch(e){if(seq===generation&&alive)error.value=e.message}
  finally{if(seq===generation&&alive)busy.value=false}
}
</script>
<template>
  <div class="ws-trust" role="region" aria-label="SSH 私钥">
    <strong>SSH 私钥</strong>
    <p v-if="info?.configured" class="ws-mono ws-wrap" data-testid="key-summary">已保存 {{ info.key_type }} · {{ info.fingerprint }}</p>
    <p v-else-if="info" class="ws-muted">尚未保存私钥。上传后才能用私钥登录。</p>
    <template v-if="editing">
      <p class="ws-muted">粘贴或选择 OpenSSH / PEM 格式私钥（Ed25519、ECDSA 或 2048 位以上 RSA）。口令只用于本次解锁，不会保存；私钥以面板 SECRET_KEY 加密保存，界面和接口不会再显示它。</p>
      <label>私钥文件<input type="file" :disabled="busy" @change="pick"></label>
      <label>私钥内容<textarea v-model="text" rows="5" :disabled="busy" spellcheck="false" autocomplete="off" placeholder="-----BEGIN OPENSSH PRIVATE KEY-----" data-testid="key-text"></textarea></label>
      <label>口令（如有）<input v-model="passphrase" type="password" maxlength="1024" :disabled="busy" autocomplete="off" data-testid="key-passphrase"></label>
      <div class="ws-actions"><button class="ws-primary" data-testid="key-save" :disabled="busy||!text.trim()" @click="save">{{ busy?'保存中…':'保存私钥' }}</button><button v-if="info?.configured" :disabled="busy" @click="editing=false;clearSecrets()">取消</button></div>
    </template>
    <div v-else-if="info?.configured" class="ws-actions"><button :disabled="busy" @click="editing=true">替换私钥</button><button :disabled="busy" data-testid="key-delete" @click="remove">删除私钥</button></div>
    <p v-if="error" class="ws-error" role="alert">{{ error }}</p>
  </div>
</template>
