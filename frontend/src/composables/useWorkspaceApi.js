/** Shared bearer transport. Paths and search strings remain URL-encoded data. */
function errorMessage(data, status) {
  const detail = data?.detail
  if (Array.isArray(detail)) return detail.map(e => `${e.loc?.slice(1).join('.') || '输入'}: ${e.msg}`).join('；')
  return (typeof detail === 'string' ? detail : detail?.message) || `请求失败 (${status})`
}
async function request(path, { method='GET', body, signal, binary=false }={}) {
  const response = await fetch(`/api${path}`, {
    method, signal,
    headers: { Authorization: `Bearer ${localStorage.getItem('panel_token')}`, ...(body === undefined ? {} : {'Content-Type':'application/json'}) },
    ...(body === undefined ? {} : {body:JSON.stringify(body)}),
  })
  if (!response.ok) {
    const data=await response.json().catch(()=>null)
    const error=new Error(errorMessage(data,response.status));error.code=data?.detail?.code;error.status=response.status
    throw error
  }
  return binary ? response.blob() : response.json()
}
const query = values => {
  const params=new URLSearchParams()
  for(const [key,value] of Object.entries(values)) if(value!==undefined && value!==null)params.set(key,value)
  return params.size ? `?${params}` : ''
}
export const workspaceApi={
  servers:(signal)=>request('/servers',{signal}),
  addServer:(body)=>request('/servers',{method:'POST',body}),
  connection:(id,signal)=>request(`/servers/${id}/connection`,{signal}),
  probe:(id,auth_mode)=>request(`/servers/${id}/connection/probe`,{method:'POST',body:{auth_mode}}),
  trust:(id,token)=>request(`/servers/${id}/connection/trust`,{method:'POST',body:{token}}),
  check:(id)=>request(`/servers/${id}/connection/check`,{method:'POST'}),
  tailscale:()=>request('/remote/tailscale'),
  serverFiles:(id,path,signal)=>request(`/servers/${id}/files${query({path})}`,{signal}),
  processes:(id,search,signal)=>request(`/servers/${id}/snapshot${query({query:search})}`,{signal}),
  tasks:(signal)=>request('/workloads',{signal}),
  createTask:(body)=>request('/workloads',{method:'POST',body}),
  updateTask:(id,body)=>request(`/workloads/${id}`,{method:'PATCH',body}),
  deleteTask:(id)=>request(`/workloads/${id}`,{method:'DELETE'}),
  observation:(id,signal)=>request(`/workloads/${id}/observation`,{signal}),
  taskLog:(id,signal)=>request(`/workloads/${id}/log`,{signal}),
  files:(id,path,signal)=>request(`/workloads/${id}/files${query({path})}`,{signal}),
  text:(id,path,signal)=>request(`/workloads/${id}/text${query({path})}`,{signal}),
  download:(id,path,signal)=>request(`/workloads/${id}/download${query({path})}`,{binary:true,signal}),
  runs:(id,before_id,signal)=>request(`/workloads/${id}/runs${query({before_id,limit:20})}`,{signal}),
}
export function formatBytes(value) {
  if (!Number.isFinite(value)) return '不可用'
  const units=['B','KiB','MiB','GiB','TiB'];let i=0
  while(value>=1024&&i<units.length-1){value/=1024;i++}
  return `${value.toFixed(i?1:0)} ${units[i]}`
}
export function formatTime(value) { return value ? new Date(typeof value==='number'?value*1000:value).toLocaleString() : '未采样' }
export const kindLabels={training:'训练',website:'网站',scheduled:'定时任务'}
export const statusLabels={running:'进程运行中',exited_or_missing:'进程已退出或缺失',unbound:'未绑定进程',unknown:'状态未知'}
