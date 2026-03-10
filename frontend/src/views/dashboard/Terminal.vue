<template>
  <div class="terminal-page">
    <section class="terminal-section">
      <div class="terminal-toolbar">
        <div class="terminal-tabs">
          <button
            v-for="s in sessionList"
            :key="s.id"
            type="button"
            class="terminal-tab"
            :class="{ active: currentId === s.id }"
            :title="s.host"
            @click="store.setCurrentSession(s.id)"
          >
            <span class="terminal-tab-label">{{ s.host }}</span>
            <span
              class="terminal-tab-status"
              :data-status="s.status"
              :title="statusTitle(s.status)"
            />
            <button
              type="button"
              class="terminal-tab-close"
              aria-label="关闭该终端"
              title="关闭该终端会话"
              @click.stop="store.closeSession(s.id)"
            >
              ×
            </button>
          </button>
          <div class="terminal-tab-actions">
            <button
              type="button"
              class="btn-terminal btn-new"
              :disabled="serverList.length === 0"
              title="新建终端（从服务器库选择）"
              @click="showServerPicker = true"
            >
              + 新建终端
            </button>
          </div>
        </div>
      </div>
      <div class="terminal-body">
        <div class="terminal-container">
          <div
            v-if="!currentSession"
            class="terminal-placeholder"
          >
            <p>点击「新建终端」从服务器库选择一台服务器连接。</p>
            <p v-if="serverList.length === 0" class="hint">请先在用户中心 → 服务器数据中心添加服务器。</p>
          </div>
          <div
            ref="terminalRef"
            class="terminal-view"
            :class="{ 'terminal-dim': !currentSession || currentSession.status !== 'connected' }"
          />
        </div>
        <aside class="terminal-history-sidebar" aria-label="终端指令侧栏">
          <div class="terminal-history-header">
            <h3 class="terminal-history-title">常用指令</h3>
          </div>
          <ul class="terminal-history-list terminal-quick-list">
            <li
              v-for="(group, gIndex) in builtinCommandGroups"
              :key="gIndex"
              class="terminal-quick-group"
            >
              <div class="terminal-quick-group-title">{{ group.name }}</div>
              <button
                v-for="(item, index) in group.commands"
                :key="index"
                type="button"
                class="terminal-history-item"
                :title="item.cmd"
                @click="sendBuiltinCommand(item.cmd)"
              >
                {{ item.label || item.cmd }}
              </button>
            </li>
          </ul>
          <div class="terminal-history-header terminal-history-header--compact">
            <h3 class="terminal-history-title">历史指令</h3>
            <button
              type="button"
              class="btn-clear-history"
              :disabled="commandHistory.length === 0"
              @click="clearHistory"
              title="清空历史"
            >
              清空
            </button>
          </div>
          <ul class="terminal-history-list">
            <li v-for="(cmd, index) in commandHistory" :key="index">
              <button
                type="button"
                class="terminal-history-item"
                :title="cmd"
                @click="sendHistoryCommand(cmd)"
              >
                {{ cmd }}
              </button>
            </li>
            <li v-if="commandHistory.length === 0" class="terminal-history-empty">
              暂无历史指令
            </li>
          </ul>
        </aside>
      </div>
      <!-- 新建终端：选择服务器 -->
      <div v-if="showServerPicker" class="terminal-modal-overlay" @click.self="showServerPicker = false">
        <div class="terminal-modal">
          <h3 class="terminal-modal-title">选择服务器</h3>
          <p v-if="serverList.length === 0" class="terminal-modal-hint">请先在用户中心 → 服务器数据中心添加服务器。</p>
          <ul v-else class="terminal-server-list">
            <li
              v-for="srv in serverList"
              :key="srv.id"
              class="terminal-server-item"
              @click="onPickServer(srv)"
            >
              <span class="srv-host">{{ srv.host }}</span>
              <span class="srv-name">{{ srv.name }}</span>
            </li>
          </ul>
          <button type="button" class="btn-secondary" @click="showServerPicker = false">取消</button>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick, onMounted, onBeforeUnmount } from 'vue'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import { useTerminalStore } from '../../composables/useTerminalStore'

const HISTORY_KEY = 'panel_terminal_command_history'
const HISTORY_MAX = 500
const CMD_MAX_LEN = 512

const store = useTerminalStore()
const terminalRef = ref(null)
const serverList = ref([])
const showServerPicker = ref(false)
const commandHistory = ref([])

let term = null
let fitAddon = null
let resizeObserver = null
let lineBuffer = ''

const sessionList = computed(() => store.sessions.value)
const currentId = computed(() => store.currentSessionId.value)
const currentSession = computed(() => store.currentSession.value)

const builtinCommandGroups = [
  { name: '系统', commands: [
    { label: '磁盘使用情况 (df -h)', cmd: 'df -h' },
    { label: '内存使用情况 (free -m)', cmd: 'free -m' },
    { label: '负载情况 (uptime)', cmd: 'uptime' },
    { label: '进程列表 (top)', cmd: 'top' },
  ]},
  { name: '网络', commands: [
    { label: '网络接口 (ip a)', cmd: 'ip a' },
    { label: '监听端口 (ss -tulnp)', cmd: 'ss -tulnp' },
    { label: '路由表 (ip route)', cmd: 'ip route' },
  ]},
  { name: 'Docker', commands: [
    { label: '容器列表 (docker ps)', cmd: 'docker ps' },
    { label: '所有容器 (docker ps -a)', cmd: 'docker ps -a' },
    { label: '镜像列表 (docker images)', cmd: 'docker images' },
  ]},
  { name: '日志', commands: [
    { label: '系统日志 (journalctl -xe)', cmd: 'journalctl -xe' },
    { label: 'Nginx 日志示例', cmd: 'tail -n 100 /var/log/nginx/access.log' },
  ]},
]

function loadHistory() {
  try {
    const raw = localStorage.getItem(HISTORY_KEY)
    commandHistory.value = raw ? (JSON.parse(raw) || []) : []
  } catch {
    commandHistory.value = []
  }
}

function saveHistory() {
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(commandHistory.value))
  } catch {}
}

function pushCommandToHistory(cmd) {
  const trimmed = (cmd || '').trim()
  if (!trimmed) return
  const s = trimmed.length > CMD_MAX_LEN ? trimmed.slice(0, CMD_MAX_LEN) : trimmed
  const list = commandHistory.value
  const idx = list.indexOf(s)
  if (idx !== -1) {
    commandHistory.value = [s, ...list.slice(0, idx), ...list.slice(idx + 1)]
  } else {
    commandHistory.value = [s, ...list].slice(0, HISTORY_MAX)
  }
  saveHistory()
}

function clearHistory() {
  commandHistory.value = []
  localStorage.removeItem(HISTORY_KEY)
}

function sendHistoryCommand(cmd) {
  const s = (cmd || '').trim()
  if (!s || !currentSession.value || currentSession.value.status !== 'connected') return
  store.sendToSession(store.currentSessionId.value, `${s}\r`)
}

function sendBuiltinCommand(cmd) {
  const s = (cmd || '').trim()
  if (!s || !currentSession.value || currentSession.value.status !== 'connected') return
  store.sendToSession(store.currentSessionId.value, `${s}\r`)
  pushCommandToHistory(s)
}

function statusTitle(status) {
  const t = { connecting: '连接中', connected: '已连接', disconnected: '已断开', error: '错误' }
  return t[status] || status
}

async function loadServers() {
  try {
    const res = await fetch('/api/servers', {
      headers: { Authorization: `Bearer ${localStorage.getItem('panel_token')}` },
    })
    const data = await res.json().catch(() => ({}))
    serverList.value = (data.items || [])
  } catch {
    serverList.value = []
  }
}

function onPickServer(srv) {
  showServerPicker.value = false
  store.createSession(srv).then(() => {
    nextTick(() => refreshTerminalView())
  }).catch((err) => {
    alert(err?.message || '连接失败')
  })
}

function attachCurrent() {
  const id = store.currentSessionId.value
  if (!id || !term) return
  store.attachSession(id, term)
}

function detachCurrent() {
  const id = store.currentSessionId.value
  if (id) store.detachSession(id)
}

function refreshTerminalView() {
  if (!term || !terminalRef.value || !store.currentSessionId.value) return
  if (!term.element || term.element !== terminalRef.value) {
    term.open(terminalRef.value)
  }
  fitAddon?.fit()
  attachCurrent()
  if (resizeObserver) {
    try { resizeObserver.observe(terminalRef.value) } catch (_) {}
  }
  const session = store.currentSession.value
  if (session?.ws?.readyState === WebSocket.OPEN) {
    try {
      session.ws.send(JSON.stringify({
        type: 'resize',
        cols: term.cols,
        rows: term.rows,
      }))
    } catch (_) {}
  }
}

onMounted(async () => {
  loadHistory()
  await loadServers()

  if (store.sessions.value.length === 0 && serverList.value.length > 0) {
    store.createSession(serverList.value[0]).catch(() => {})
  }

  term = new Terminal({
    cursorBlink: true,
    theme: {
      background: '#f8f9fa',
      foreground: '#212529',
      cursor: '#212529',
      cursorAccent: '#f8f9fa',
      selectionBackground: 'rgba(0,0,0,0.12)',
      black: '#212529',
      red: '#c92a2a',
      green: '#2b8a3e',
      yellow: '#a96b00',
      blue: '#1864ab',
      magenta: '#862e9c',
      cyan: '#0b7285',
      white: '#495057',
      brightBlack: '#868e96',
      brightRed: '#c92a2a',
      brightGreen: '#2b8a3e',
      brightYellow: '#a96b00',
      brightBlue: '#1864ab',
      brightMagenta: '#862e9c',
      brightCyan: '#0b7285',
      brightWhite: '#212529',
    },
    fontFamily: 'ui-monospace, "Cascadia Code", "Segoe UI Mono", Consolas, monospace',
    fontSize: 14,
    lineHeight: 1.35,
    letterSpacing: 0,
  })
  fitAddon = new FitAddon()
  term.loadAddon(fitAddon)

  if (terminalRef.value) {
    term.open(terminalRef.value)
    fitAddon.fit()
    attachCurrent()
  }

  term.onData((data) => {
    if (data.includes('\r') || data.includes('\n')) {
      const full = lineBuffer + data
      const parts = full.split(/\r\n|\r|\n/)
      for (let i = 0; i < parts.length - 1; i++) pushCommandToHistory(parts[i])
      lineBuffer = parts[parts.length - 1] ?? ''
    } else if (data === '\x7f') {
      lineBuffer = lineBuffer.slice(0, -1)
    } else {
      for (let i = 0; i < data.length; i++) {
        const c = data[i]
        if (c >= ' ' || c === '\t') lineBuffer += c
      }
    }
    const sid = store.currentSessionId.value
    if (sid && currentSession.value?.status === 'connected') {
      store.sendToSession(sid, data)
    }
  })

  resizeObserver = new ResizeObserver(() => {
    if (!fitAddon || !term || !terminalRef.value || terminalRef.value.clientHeight <= 0) return
    try {
      fitAddon.fit()
      const session = store.currentSession.value
      if (session?.ws?.readyState === WebSocket.OPEN) {
        session.ws.send(JSON.stringify({
          type: 'resize',
          cols: term.cols,
          rows: term.rows,
        }))
      }
    } catch (_) {}
  })
  if (terminalRef.value) resizeObserver.observe(terminalRef.value)
})

watch(
  () => store.currentSessionId.value,
  (newId, oldId) => {
    if (!term) return
    if (oldId) store.detachSession(oldId)
    term.clear()
    if (newId) {
      store.attachSession(newId, term)
    }
    const session = store.currentSession.value
    if (session?.ws?.readyState === WebSocket.OPEN && fitAddon) {
      try {
        fitAddon.fit()
        session.ws.send(JSON.stringify({
          type: 'resize',
          cols: term.cols,
          rows: term.rows,
        }))
      } catch (_) {}
    }
  }
)

// 无当前会话时确保清空 xterm 并避免残留提示符（占位层透明时仍会透出）
watch(
  () => store.sessions.value.length === 0,
  (noSessions) => {
    if (noSessions && term) {
      nextTick(() => {
        try { term.clear() } catch (_) {}
      })
    }
  }
)

onBeforeUnmount(() => {
  if (resizeObserver && terminalRef.value) {
    try { resizeObserver.unobserve(terminalRef.value) } catch (_) {}
    resizeObserver.disconnect()
  }
  detachCurrent()
  if (term) {
    term.dispose()
    term = null
  }
})
</script>

<style scoped>
.terminal-page {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.terminal-section {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 12px;
  box-shadow: 0 1px 3px var(--shadow);
  overflow: hidden;
}

.terminal-toolbar {
  padding: 0 0.5rem;
  border-bottom: 1px solid var(--border);
  background: var(--bg-secondary);
}

.terminal-tabs {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.25rem;
  min-height: 48px;
}

.terminal-tab {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  padding: 0.5rem 0.5rem 0.5rem 0.75rem;
  font-size: 0.85rem;
  color: var(--text-secondary);
  background: var(--bg-tertiary);
  border: 1px solid var(--border);
  border-bottom: 2px solid transparent;
  border-radius: 6px 6px 0 0;
  cursor: pointer;
  max-width: 180px;
  transition: background 0.15s, border-color 0.15s, color 0.15s;
}

.terminal-tab:hover {
  color: var(--text-primary);
}

.terminal-tab.active {
  color: var(--text-primary);
  font-weight: 600;
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-bottom: 3px solid #2563eb;
  padding: 0.5rem 0.5rem calc(0.5rem - 1px) 0.75rem;
}

.terminal-tab-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.terminal-tab-status {
  width: 8px;
  height: 8px;
  min-width: 8px;
  min-height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
  background-color: #868e96;
}

.terminal-tab-status[data-status="connected"] {
  background-color: #22c55e;
}

.terminal-tab-status[data-status="connecting"] {
  background-color: #eab308;
}

.terminal-tab-status[data-status="disconnected"],
.terminal-tab-status[data-status="error"] {
  background-color: #ef4444;
}

.terminal-tab-close {
  padding: 0 0.25rem;
  font-size: 1.1rem;
  line-height: 1;
  color: var(--text-secondary);
  background: none;
  border: none;
  cursor: pointer;
  border-radius: 2px;
  opacity: 0;
  transition: opacity 0.15s;
}

.terminal-tab:hover .terminal-tab-close {
  opacity: 1;
}

.terminal-tab-close:hover {
  color: var(--text-primary);
  background: var(--bg-tertiary);
}

.terminal-tab-actions {
  margin-left: 0.75rem;
  padding-left: 0.5rem;
  border-left: 1px solid var(--border);
}

.btn-terminal.btn-new {
  padding: 0.4rem 0.75rem;
  font-size: 0.85rem;
  border: 1px dashed var(--border);
  background: transparent;
}

.btn-terminal.btn-new:hover:not(:disabled) {
  border-style: solid;
  background: var(--bg-tertiary);
}

.btn-terminal:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.terminal-body {
  flex: 1;
  display: flex;
  flex-direction: row;
  min-height: 0;
}

.terminal-container {
  flex: 1;
  min-width: 0;
  min-height: 280px;
  padding: 1rem;
  background: #f8f9fa;
  position: relative;
}

.terminal-placeholder {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  background: #f8f9fa;
  color: #868e96;
  font-size: 0.9rem;
  z-index: 1;
  pointer-events: none;
}

.terminal-placeholder .hint {
  margin-top: 0.5rem;
  font-size: 0.85rem;
  color: var(--text-muted);
}

.terminal-view.terminal-dim {
  opacity: 0.9;
}

.terminal-view {
  width: 100%;
  height: 100%;
  min-height: 240px;
}

.terminal-history-sidebar {
  width: 280px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  border-left: 1px solid var(--border);
  background: var(--bg-secondary);
  overflow: hidden;
}

.terminal-history-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.75rem 1rem;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}

.terminal-history-header--compact {
  padding-top: 0.5rem;
  padding-bottom: 0.5rem;
}

.terminal-history-title {
  margin: 0;
  font-size: 0.9rem;
  font-weight: 600;
  color: var(--text-primary);
}

.btn-clear-history {
  padding: 0.25rem 0.5rem;
  font-size: 0.8rem;
  color: var(--text-secondary);
  background: transparent;
  border: 1px solid var(--border);
  border-radius: 4px;
  cursor: pointer;
}

.btn-clear-history:hover:not(:disabled) {
  color: var(--text-primary);
  border-color: var(--text-secondary);
}

.btn-clear-history:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.terminal-history-list {
  flex: 1;
  overflow-y: auto;
  margin: 0;
  padding: 0.5rem 0;
  list-style: none;
}

.terminal-quick-list {
  border-bottom: 1px solid var(--border);
  flex: 0 0 auto;
}

.terminal-quick-group {
  padding: 0.25rem 0.5rem 0.5rem;
}

.terminal-quick-group-title {
  padding: 0.25rem 0.75rem;
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--text-secondary);
  text-transform: uppercase;
}

.terminal-history-item {
  display: block;
  width: 100%;
  padding: 0.5rem 1rem;
  font-size: 0.85rem;
  font-family: ui-monospace, "Cascadia Code", "Segoe UI Mono", Consolas, monospace;
  color: var(--text-primary);
  background: transparent;
  border: none;
  border-radius: 0;
  text-align: left;
  cursor: pointer;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.terminal-history-item:hover {
  background: var(--bg-tertiary);
}

.terminal-history-empty {
  padding: 1rem;
  font-size: 0.85rem;
  color: var(--text-secondary);
}

.terminal-modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.terminal-modal {
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 1.5rem;
  min-width: 320px;
  max-width: 90vw;
}

.terminal-modal-title {
  margin: 0 0 1rem 0;
  font-size: 1.1rem;
  color: var(--text-primary);
}

.terminal-modal-hint {
  margin: 0 0 1rem 0;
  font-size: 0.9rem;
  color: var(--text-secondary);
}

.terminal-server-list {
  list-style: none;
  margin: 0 0 1rem 0;
  padding: 0;
}

.terminal-server-item {
  padding: 0.6rem 0.75rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  margin-bottom: 0.5rem;
  cursor: pointer;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 0.5rem;
}

.terminal-server-item:hover {
  background: var(--bg-tertiary);
}

.srv-host {
  font-family: ui-monospace, Consolas, monospace;
  font-size: 0.9rem;
  color: var(--text-primary);
}

.srv-name {
  font-size: 0.85rem;
  color: var(--text-secondary);
}

.btn-secondary {
  padding: 0.5rem 1rem;
  font-size: 0.9rem;
  color: var(--text-secondary);
  background: transparent;
  border: 1px solid var(--border);
  border-radius: 6px;
  cursor: pointer;
}

.btn-secondary:hover {
  color: var(--text-primary);
  background: var(--bg-tertiary);
}

:deep(.xterm) {
  height: 100%;
  padding: 6px 0;
}

:deep(.xterm-viewport) {
  overflow-y: auto !important;
  background: #f8f9fa !important;
}

:deep(.xterm-screen) {
  background: #f8f9fa !important;
}
</style>
