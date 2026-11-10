<script setup>
import { ref, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { agentApi } from '../../composables/useAgentApi'
import { useTerminalStore } from '../../composables/useTerminalStore'
// Uploads the Agent package to the SSH user's home folder; the user runs the one sudo command themselves.
// PanelLab never asks for, receives or sends a sudo password.
const props = defineProps({ serverId: { type: Number, required: true }, host: { type: String, default: '' } })
const emit = defineEmits(['recheck'])
const router = useRouter(), terminal = useTerminalStore()
const busy = ref(false), opening = ref(false), error = ref(''), copied = ref(''), result = ref(null)
let alive = true
onBeforeUnmount(() => { alive = false })
async function upload() {
  if (busy.value) return
  busy.value = true; error.value = ''; copied.value = ''
  try { const r = await agentApi.install(props.serverId); if (alive) result.value = r } catch (e) { if (alive) error.value = e.message || '上传失败' } finally { if (alive) busy.value = false }
}
async function copy() { try { await navigator.clipboard.writeText(result.value.command); copied.value = '命令已复制' } catch { copied.value = '无法自动复制，请手动选择上方命令复制' } }
async function openTerminal() {
  if (opening.value || !result.value) return
  opening.value = true; error.value = ''
  try { await terminal.createSession({ id: props.serverId, host: props.host }, { cwd: result.value.path }); if (alive) router.push('/terminal') } catch (e) { if (alive) error.value = e.message } finally { if (alive) opening.value = false }
}
</script>
<template>
  <section class="ws-panel agent-install" data-testid="agent-install">
    <h3>安装服务器 Agent</h3>
    <p>Agent 是运行在服务器上的小程序，负责按计划执行作业、记录结果并发送告警。安装过程如下：</p>
    <ol>
      <li>点击「上传安装包」，PanelLab 把安装包上传到你 SSH 用户的主目录（不需要密码，也不会执行任何东西）。</li>
      <li>你在服务器终端里自己运行页面给出的那一条 sudo 命令；sudo 密码只在终端里输入，PanelLab 不会询问或发送。</li>
      <li>安装脚本会创建系统用户 <code>panellab</code>，程序装在 <code>/opt/panellab-agent</code>，数据在 <code>/var/lib/panellab-agent</code>，配置在 <code>/etc/panellab-agent</code>，并把你的 SSH 用户加入 <code>panellab</code> 组。</li>
      <li>加入组之后需要重新连接 SSH（新开终端或重新登录）才会生效；然后点击「重新检查」。</li>
    </ol>
    <div class="ws-actions"><button class="ws-primary" data-testid="install-upload" :disabled="busy" @click="upload">{{ busy ? '上传中…' : result ? '重新上传安装包' : '上传安装包' }}</button></div>
    <p v-if="error" class="ws-error" role="alert" data-testid="install-error">{{ error }}</p>
    <div v-if="result" data-testid="install-next">
      <p>安装包已上传到 <code class="ws-mono">{{ result.path }}</code>。请在服务器上运行下面这条命令：</p>
      <pre class="ws-mono agent-command"><code data-testid="install-command">{{ result.command }}</code></pre>
      <div class="ws-actions"><button data-testid="install-copy" @click="copy">复制命令</button><button data-testid="install-terminal" :disabled="opening" @click="openTerminal">{{ opening ? '等待 SSH 就绪…' : '在终端打开' }}</button></div>
      <p v-if="copied" class="ws-muted" role="status">{{ copied }}</p>
      <p class="ws-muted">运行完成后请重新连接 SSH，再点击「重新检查」。</p>
    </div>
    <div class="ws-actions"><button data-testid="install-recheck" @click="emit('recheck')">重新检查</button></div>
  </section>
</template>
<style>
.agent-install ol{padding-left:20px;line-height:1.7}.agent-install .agent-command{padding:10px 12px;overflow-x:auto;background:var(--ws-tint);border:1px solid var(--border);border-radius:6px;white-space:pre-wrap;word-break:break-all}
.agent-form .agent-preview{margin-top:8px}.agent-form .agent-preview ul{margin:4px 0 0;padding-left:18px}.agent-form fieldset{margin:12px 0}.agent-form .ws-row{gap:8px;margin-bottom:6px;justify-content:flex-start}
</style>
