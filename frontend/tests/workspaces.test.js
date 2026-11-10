import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import Workspaces from '../src/views/dashboard/Workspaces.vue'
import ConnectionPanel from '../src/components/workspaces/ConnectionPanel.vue'
import TaskForm from '../src/components/workspaces/TaskForm.vue'
import AddServerForm from '../src/components/workspaces/AddServerForm.vue'
import TaskDetail from '../src/components/workspaces/TaskDetail.vue'
import { workspaceApi } from '../src/composables/useWorkspaceApi'
const { push, createSession }=vi.hoisted(()=>({push:vi.fn(),createSession:vi.fn().mockResolvedValue({})}))
vi.mock('vue-router',()=>({useRouter:()=>({push})}))
vi.mock('../src/composables/useTerminalStore',()=>({useTerminalStore:()=>({createSession})}))
const server={id:71,name:'实验室',host:'fixture',port:22,username:'lab'}
const task={id:1,name:'训练 A',kind:'training',server_id:71,root_path:'/home/lab/run A',log_path:'/home/lab/run A/中文.log',pid:42,process_identity:'boot:12'}
let wrappers=[]
const mountIt=(c,p={})=>{const w=mount(c,{props:p});wrappers.push(w);return w}
function response(data,ok=true){return {ok,status:ok?200:503,json:async()=>data,blob:async()=>new Blob(['file'])}}
function fixture(path, options={}) {
 if(path==='/api/servers')return response({items:[server]})
 if(path==='/api/workloads')return response({items:[task,{...task,id:2,name:'训练 B'}]})
 if(path.endsWith('/connection'))return response({pinned:false})
 if(path.endsWith('/connection/probe'))return response({host:'fixture',port:22,fingerprint:'SHA256:fixture',key_type:'ssh-ed25519',token:'trust-token'})
 if(path.endsWith('/connection/trust'))return response({fingerprint:'SHA256:fixture',auth_mode:'password'})
 if(path.endsWith('/connection/check'))return response({ok:true,home:'/home/lab',observed_at:'2026-11-03T00:00:00Z'})
 if(path.includes('/snapshot'))return response({processes:[{pid:42,identity:'boot:12',command:'python train.py',cwd:'/home/lab/run A'}],warnings:[]})
 if(path.includes('/files')){const root=new URL(path,'http://fixture').searchParams.get('path')||'/home/lab';return response({cwd:root,parent:root==='/home/lab'?'/home':'/home/lab',entries:[{name:'run A',path:'/home/lab/run A',type:'directory'},{name:'中文.log',path:'/home/lab/run A/中文.log',type:'file'},{name:'link',path:root+'/link',type:'symlink',disabled:true}]})}
 if(path.endsWith('/observation'))return response({context:{workload:{...task,id:Number(path.split('/')[3])},server},workload_id:Number(path.split('/')[3]),observed_at:'2026-11-03T00:00:00Z',process_status:'unknown',system:{network:[{name:'eth0',received_bytes:123,sent_bytes:456}]},warnings:['PID unavailable']})
 if(path.endsWith('/log'))return response({path:task.log_path,context:{workload:task,server},text:'epoch 1 <script>raw</script>',truncated:true,size:80000})
 if(path.includes('/download'))return response({})
 if(path.includes('/runs'))return response({items:[{id:1,status:'succeeded',source:'reported',summary:'operator says complete',started_at:'2026-11-03T00:00:00Z'}]})
 if(options.method==='DELETE')return response({deleted:true})
 throw Error('Unexpected '+path)
}
beforeEach(()=>{vi.stubGlobal('localStorage',{getItem:()=> 'fixture-token'});vi.stubGlobal('fetch',vi.fn(async(...args)=>fixture(...args)));push.mockClear();createSession.mockClear()})
afterEach(()=>{wrappers.forEach(w=>w.unmount());wrappers=[];vi.unstubAllGlobals()})
it('shows target and fingerprint, trusts only on explicit confirmation click',async()=>{
 const w=mountIt(ConnectionPanel,{server});await flushPromises()
 await w.get('[data-testid="probe"]').trigger('click');await flushPromises()
 expect(w.text()).toContain('SHA256:fixture');expect(w.text()).toContain('fixture:22')
 expect(fetch.mock.calls.some(([url])=>url.endsWith('/connection/trust'))).toBe(false)
 await w.get('[data-testid="trust"]').trigger('click');await flushPromises()
 expect(fetch.mock.calls.find(([url])=>url.endsWith('/connection/trust'))[1].body).toBe(JSON.stringify({token:'trust-token'}))
 expect(w.text()).toContain('已连接')
})
it('creates with browsed Unicode path and selected process identity through query filtering',async()=>{
 const w=mountIt(TaskForm,{servers:[server]});await flushPromises()
 await w.get('[name="name"]').setValue('训练 A')
 await w.get('[data-testid="open-root-picker"]').trigger('click');await flushPromises()
 await w.get('[data-testid="path-open-/home/lab/run A"]').trigger('click');await flushPromises()
 expect(w.get('[data-testid="path-open-/home/lab/run A/link"]').attributes('disabled')).toBeDefined()
 await w.get('[data-testid="choose-directory"]').trigger('click');await flushPromises()
 await w.get('[name="process-query"]').setValue('train')
 await w.get('[data-testid="search-process"]').trigger('click');await flushPromises()
 expect(fetch.mock.calls.some(([url])=>url.includes('/snapshot?query=train'))).toBe(true)
 await w.get('[name="process"]').setValue('42')
 await w.get('form').trigger('submit');await flushPromises()
 const payload=JSON.parse(fetch.mock.calls.find(([url,o])=>url==='/api/workloads'&&o.method==='POST')[1].body)
 expect(payload.root_path).toBe('/home/lab/run A');expect(payload.pid).toBe(42);expect(payload.process_identity).toBe('boot:12')
})
it('stale task observation never applies to a newly selected task',async()=>{
 let release
 fetch.mockImplementation(async(url,o)=> url==='/api/workloads/1/observation'?new Promise(r=>{release=r}):fixture(url,o))
 const w=mountIt(Workspaces);await flushPromises()
 await w.get('[data-testid="task-2"]').trigger('click');await flushPromises()
 release(response({observed_at:'old',process_status:'running',system:{},warnings:['OLD TASK DATA']}));await flushPromises()
 expect(w.get('[data-testid="task-detail"]').text()).toContain('训练 B')
 expect(w.text()).not.toContain('OLD TASK DATA')
})
it('log refresh failure preserves text and marks it stale; download uses bearer path request',async()=>{
 const w=mountIt(TaskDetail,{task,server});await flushPromises()
 await w.get('[data-testid="tab-log"]').trigger('click');await flushPromises()
 expect(w.get('pre').text()).toContain('<script>raw</script>');expect(w.find('script').exists()).toBe(false)
 fetch.mockImplementation(async(url,o)=>url.endsWith('/log')?response({detail:{message:'offline'}},false):fixture(url,o))
 await w.get('[data-testid="refresh-log"]').trigger('click');await flushPromises()
 expect(w.get('pre').text()).toContain('epoch 1');expect(w.text()).toContain('已过期')
 await w.get('[data-testid="tab-files"]').trigger('click');await flushPromises()
 vi.stubGlobal('URL',Object.assign(URL,{createObjectURL:vi.fn(()=> 'blob:fixture'),revokeObjectURL:vi.fn()}))
 vi.spyOn(HTMLAnchorElement.prototype,'click').mockImplementation(()=>{})
 await w.get('[data-testid="download-/home/lab/run A/中文.log"]').trigger('click');await flushPromises()
 const request=fetch.mock.calls.find(([url])=>url.includes('/download?'))
 expect(new URL(request[0],'http://fixture').searchParams.get('path')).toBe(task.log_path)
 expect(request[1].headers.Authorization).toBe('Bearer fixture-token')
})
it('labels reported runs and opens the proper terminal directory, unregisters only after confirmation',async()=>{
 const w=mountIt(TaskDetail,{task,server});await flushPromises()
 expect(w.text()).toContain('未知');expect(w.text()).toContain('服务器级')
 await w.get('[data-testid="tab-runs"]').trigger('click');await flushPromises()
 expect(w.text()).toContain('上报记录');expect(w.text()).toContain('operator says complete')
 await w.get('[data-testid="open-terminal"]').trigger('click');await flushPromises()
 expect(createSession).toHaveBeenCalledWith(server,{cwd:task.root_path,signal:expect.any(AbortSignal)});expect(push).toHaveBeenCalledWith('/terminal')
 await w.get('[data-testid="delete-task"]').trigger('click');await flushPromises()
 expect(w.text()).toContain('远程进程和文件保持不变')
 expect(fetch.mock.calls.some(([,o])=>o.method==='DELETE')).toBe(false)
 await w.get('[data-testid="confirm-delete"]').trigger('click');await flushPromises()
 expect(fetch.mock.calls.some(([url,o])=>url==='/api/workloads/1'&&o.method==='DELETE')).toBe(true)
})
it('central API parses structured and validation errors without exposing objects',async()=>{
 fetch.mockResolvedValue(response({detail:[{loc:['body','name'],msg:'required'}]},false))
 await expect(workspaceApi.createTask({})).rejects.toThrow('name: required')
})
it('server switch ignores the previous trust follow-up response',async()=>{
 let release,connectionCalls=0
 fetch.mockImplementation(async(url,o)=>{
  if(url==='/api/servers/71/connection'&&++connectionCalls===2)return new Promise(r=>release=r)
  return fixture(url,o)
 })
 const w=mountIt(ConnectionPanel,{server});await flushPromises()
 await w.get('[data-testid="probe"]').trigger('click');await flushPromises()
 await w.get('[data-testid="trust"]').trigger('click');await flushPromises()
 await w.setProps({server:{...server,id:72,host:'new-fixture'}});await flushPromises()
 release(response({pinned:true,fingerprint:'OLD PIN'}));await flushPromises()
 expect(w.text()).not.toContain('OLD PIN')
 expect(w.emitted('connected')).toBeUndefined()
})
it('log file picker can choose a regular file when the task root is filesystem root',async()=>{
 const {default:PathPicker}=await import('../src/components/workspaces/PathPicker.vue')
 const w=mountIt(PathPicker,{serverId:71,mode:'file',initial:'/',root:'/'});await flushPromises()
 expect(w.get('[data-testid="path-open-/home/lab/run A/中文.log"]').attributes('disabled')).toBeUndefined()
 await w.get('[data-testid="path-open-/home/lab/run A/中文.log"]').trigger('click')
 expect(w.emitted('select')[0]).toEqual(['/home/lab/run A/中文.log'])
})

it('Tailscale candidate prefills target without trusting and carries chosen auth mode into connection probing',async()=>{
 fetch.mockImplementation(async(url,o)=>{
  if(url==='/api/remote/tailscale')return response({available:true,devices:[{name:'tail-lab',dns_name:'lab.tail.',addresses:['100.64.0.8'],online:true}]})
  if(url==='/api/servers'&&o.method==='POST')return response({...server,host:'100.64.0.8'})
  return fixture(url,o)
 })
 const w=mountIt(AddServerForm)
 await w.findAll('button').find(b=>b.text().includes('发现 Tailscale')).trigger('click');await flushPromises()
 await w.findAll('button').find(b=>b.text().includes('tail-lab')).trigger('click')
 expect(w.findAll('input')[1].element.value).toBe('100.64.0.8')
 expect(w.find('input[type="password"]').exists()).toBe(false)
 expect(fetch.mock.calls.some(([url])=>url.includes('/connection/trust'))).toBe(false)
 await w.findAll('input')[3].setValue('lab')
 await w.get('form').trigger('submit');await flushPromises()
 expect(w.emitted('saved')[0][1]).toBe('tailscale')
 const connection=mountIt(ConnectionPanel,{server,initialAuthMode:'tailscale'});await flushPromises()
 await connection.get('[data-testid="probe"]').trigger('click');await flushPromises()
 const probeRequest=fetch.mock.calls.find(([url])=>url.endsWith('/connection/probe'))
 expect(JSON.parse(probeRequest[1].body).auth_mode).toBe('tailscale')
})
it.each([['password','tailscale'],['tailscale','password']])('requires explicit fresh trust when changing saved %s auth to %s',async(saved,desired)=>{
 let savedMode=saved,probedMode
 fetch.mockImplementation(async(url,o)=>{
  if(url.endsWith('/connection'))return response({pinned:true,auth_mode:savedMode,fingerprint:'SHA256:fixture'})
  if(url.endsWith('/connection/probe'))probedMode=JSON.parse(o.body).auth_mode
  if(url.endsWith('/connection/trust'))savedMode=probedMode
  return fixture(url,o)
 })
 const w=mountIt(ConnectionPanel,{server});await flushPromises()
 await w.get('select').setValue(desired)
 const check=w.findAll('button').find(b=>b.text().includes('检查连接'))
 expect(check.attributes('disabled')).toBeDefined()
 expect(w.text()).toContain('重新获取指纹')
 await check.trigger('click');await flushPromises()
 expect(fetch.mock.calls.some(([url])=>url.endsWith('/connection/check'))).toBe(false)
 await w.get('[data-testid="probe"]').trigger('click');await flushPromises()
 expect(probedMode).toBe(desired)
 expect(fetch.mock.calls.some(([url])=>url.endsWith('/connection/trust'))).toBe(false)
 await w.get('[data-testid="trust"]').trigger('click');await flushPromises()
 expect(savedMode).toBe(desired)
 expect(w.text()).toContain('已连接')
})
it('renders network byte values as server-level cumulative counters',async()=>{
 const w=mountIt(TaskDetail,{task,server});await flushPromises()
 expect(w.text()).toContain('服务器网络 · 累计计数')
 expect(w.text()).toContain('累计接收 123 B')
 expect(w.text()).toContain('累计发送 456 B')
 expect(w.text()).not.toContain('B/s')
})
it.each([['password','tailscale'],['tailscale','password'],[null,'password']])('retries a failed first check after saved %s -> %s trust without trusting twice',async(saved,desired)=>{
 let savedMode=saved,probedMode,checks=0
 fetch.mockImplementation(async(url,o)=>{
  if(url.endsWith('/connection'))return response({configured:!!savedMode,pinned:!!savedMode,auth_mode:savedMode,fingerprint:savedMode?'SHA256:fixture':null})
  if(url.endsWith('/connection/probe')){probedMode=JSON.parse(o.body).auth_mode;return fixture(url,o)}
  if(url.endsWith('/connection/trust')){savedMode=probedMode;return response({fingerprint:'SHA256:fixture',auth_mode:savedMode})}
  if(url.endsWith('/connection/check')&&++checks===1)return response({detail:{code:'connection_failed',message:'temporarily unavailable'}},false)
  return fixture(url,o)
 })
 const w=mountIt(ConnectionPanel,{server});await flushPromises()
 await w.get('select').setValue(desired)
 await w.get('[data-testid="probe"]').trigger('click');await flushPromises()
 await w.get('[data-testid="trust"]').trigger('click');await flushPromises()
 expect(w.text()).toContain('temporarily unavailable')
 const retry=w.findAll('button').find(b=>b.text().includes('检查连接'))
 expect(retry).toBeDefined()
 expect(retry.attributes('disabled')).toBeUndefined()
 expect(w.find('[data-testid="trust"]').exists()).toBe(false)
 await retry.trigger('click');await flushPromises()
 expect(w.text()).toContain('已连接')
 expect(w.text()).not.toContain('temporarily unavailable')
 expect(fetch.mock.calls.filter(([url])=>url.endsWith('/connection/trust'))).toHaveLength(1)
 expect(checks).toBe(2)
})
it('process search reports its result count and suggests the process working directory',async()=>{
 fetch.mockImplementation(async(url,o)=>url.includes('/snapshot')?response({processes:[{pid:42,identity:'boot:12',command:'python train.py',cwd:'/home/lab/run A'},{pid:43,identity:'boot:13',command:'python eval.py',cwd:'/srv/eval'}],warnings:['GPU unavailable','Process identity unavailable']}):fixture(url,o))
 const w=mountIt(TaskForm,{servers:[server]});await flushPromises()
 await w.get('[data-testid="search-process"]').trigger('click');await flushPromises()
 expect(w.get('[data-testid="process-count"]').text()).toContain('找到 2 个进程')
 expect(w.text()).toContain('部分进程无法读取启动身份')
 expect(w.text()).not.toContain('GPU')
 await w.get('[name="process"]').setValue('42');await flushPromises()
 expect(w.text()).toContain('/home/lab/run A');expect(w.find('[data-testid="root-prefilled"]').exists()).toBe(true)
 // A different process replaces only the suggestion it made.
 await w.get('[name="process"]').setValue('43');await flushPromises()
 expect(w.text()).toContain('/srv/eval')
})
it('process directory never replaces a directory the user picked',async()=>{
 const w=mountIt(TaskForm,{servers:[server]});await flushPromises()
 await w.get('[data-testid="open-root-picker"]').trigger('click');await flushPromises()
 await w.get('[data-testid="choose-directory"]').trigger('click');await flushPromises()
 await w.get('[data-testid="search-process"]').trigger('click');await flushPromises()
 await w.get('[name="process"]').setValue('42');await flushPromises()
 expect(w.find('[data-testid="root-prefilled"]').exists()).toBe(false)
 await w.get('form').trigger('submit');await flushPromises()
 const payload=JSON.parse(fetch.mock.calls.find(([url,o])=>url==='/api/workloads'&&o.method==='POST')[1].body)
 expect(payload.root_path).toBe('/home/lab')
})
it('process search says so when nothing matches',async()=>{
 fetch.mockImplementation(async(url,o)=>url.includes('/snapshot')?response({processes:[],warnings:[]}):fixture(url,o))
 const w=mountIt(TaskForm,{servers:[server]});await flushPromises()
 await w.get('[data-testid="search-process"]').trigger('click');await flushPromises()
 expect(w.get('[data-testid="process-count"]').text()).toContain('没有匹配的进程')
})
