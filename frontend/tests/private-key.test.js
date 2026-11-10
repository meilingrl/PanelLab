import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import ConnectionPanel from '../src/components/workspaces/ConnectionPanel.vue'
import AddServerForm from '../src/components/workspaces/AddServerForm.vue'

const server={id:71,name:'实验室',host:'fixture',port:22,username:'lab'}
const KEY='-----BEGIN OPENSSH PRIVATE KEY-----\nfixture\n-----END OPENSSH PRIVATE KEY-----\n'
let wrappers=[],stored,connection
const mountIt=(c,p={})=>{const w=mount(c,{props:p});wrappers.push(w);return w}
const response=(data,ok=true,status=ok?200:422)=>({ok,status,json:async()=>data})
const calls=suffix=>fetch.mock.calls.filter(([url])=>url.endsWith(suffix))
beforeEach(()=>{
 stored={configured:false};connection={pinned:false}
 vi.stubGlobal('localStorage',{getItem:()=>'fixture-token'})
 vi.stubGlobal('fetch',vi.fn(async(url,o={})=>{
  if(url.endsWith('/private-key')&&o.method==='PUT'){
   const body=JSON.parse(o.body)
   if(body.passphrase!=='right')return response({detail:{code:'private_key_passphrase_invalid',message:'The passphrase does not unlock this private key'}},false)
   stored={configured:true,key_type:'ssh-ed25519',fingerprint:'SHA256:client'};return response(stored)
  }
  if(url.endsWith('/private-key')&&o.method==='DELETE'){stored={configured:false};return response(stored)}
  if(url.endsWith('/private-key'))return response(stored)
  if(url.endsWith('/connection'))return response(connection)
  if(url.endsWith('/connection/probe'))return response({host:'fixture',port:22,fingerprint:'SHA256:host',key_type:'ssh-ed25519',token:'trust-token'})
  if(url.endsWith('/connection/trust')){connection={pinned:true,configured:true,auth_mode:'key',fingerprint:'SHA256:host'};return response({fingerprint:'SHA256:host',auth_mode:'key'})}
  if(url.endsWith('/connection/check'))return response({ok:true,home:'/home/lab',observed_at:'2026-11-10T00:00:00Z'})
  if(url==='/api/servers'&&o.method==='POST')return response(server)
  throw Error('Unexpected '+url)
 }))
})
afterEach(()=>{wrappers.forEach(w=>w.unmount());wrappers=[];vi.unstubAllGlobals()})

it('add-server form offers key login without sending a password',async()=>{
 const w=mountIt(AddServerForm)
 await w.get('select').setValue('key')
 expect(w.find('input[type="password"]').exists()).toBe(false)
 expect(w.text()).toContain('在连接卡上传私钥')
 const inputs=w.findAll('input');await inputs[0].setValue('lab');await inputs[1].setValue('fixture');await inputs[3].setValue('lab')
 await w.get('form').trigger('submit');await flushPromises()
 expect(JSON.parse(calls('/api/servers')[0][1].body).password).toBe(null)
 expect(w.emitted('saved')[0][1]).toBe('key')
})

it('blocks trust until a key is saved, clears the passphrase after a failed unlock, then connects',async()=>{
 const w=mountIt(ConnectionPanel,{server,initialAuthMode:'key'});await flushPromises()
 expect(w.text()).toContain('尚未保存私钥')
 await w.get('[data-testid="probe"]').trigger('click');await flushPromises()
 expect(w.get('[data-testid="trust"]').attributes('disabled')).toBeDefined()
 await w.get('[data-testid="trust"]').trigger('click');await flushPromises()
 expect(calls('/connection/trust')).toHaveLength(0)

 await w.get('[data-testid="key-text"]').setValue(KEY)
 await w.get('[data-testid="key-passphrase"]').setValue('wrong')
 await w.get('[data-testid="key-save"]').trigger('click');await flushPromises()
 expect(w.text()).toContain('passphrase does not unlock')
 expect(w.get('[data-testid="key-text"]').element.value).toBe(KEY)
 expect(w.get('[data-testid="key-passphrase"]').element.value).toBe('')

 await w.get('[data-testid="key-passphrase"]').setValue('right')
 await w.get('[data-testid="key-save"]').trigger('click');await flushPromises()
 expect(w.get('[data-testid="key-summary"]').text()).toContain('SHA256:client')
 expect(w.html()).not.toContain('fixture\n-----END')
 expect(w.find('[data-testid="key-text"]').exists()).toBe(false)

 await w.get('[data-testid="trust"]').trigger('click');await flushPromises()
 expect(calls('/connection/trust')).toHaveLength(1)
 expect(w.text()).toContain('已连接')
})

it('deleting the saved key disables checking again',async()=>{
 stored={configured:true,key_type:'ssh-ed25519',fingerprint:'SHA256:client'}
 connection={pinned:true,configured:true,auth_mode:'key',fingerprint:'SHA256:host'}
 const w=mountIt(ConnectionPanel,{server});await flushPromises()
 const check=()=>w.findAll('button').find(b=>b.text().includes('检查连接'))
 expect(check().attributes('disabled')).toBeUndefined()
 await w.get('[data-testid="key-delete"]').trigger('click');await flushPromises()
 expect(check().attributes('disabled')).toBeDefined()
 expect(w.text()).toContain('先保存私钥')
})
