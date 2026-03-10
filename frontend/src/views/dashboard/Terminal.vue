<template>
  <div class="terminal-page">
    <section class="terminal-section">
      <div class="terminal-toolbar">
        <h2 class="terminal-heading">远程终端</h2>
        <div class="terminal-actions">
          <button
            type="button"
            class="btn-terminal"
            :disabled="status === 'connected' || status === 'connecting'"
            @click="connect"
          >
            连接
          </button>
          <button
            type="button"
            class="btn-terminal"
            :disabled="status === 'disconnected'"
            @click="disconnect"
          >
            断开
          </button>
        </div>
      </div>
      <div class="terminal-container">
        <div v-if="status === 'disconnected'" class="terminal-placeholder">
          点击「连接」访问远程服务器
        </div>
        <div ref="terminalRef" class="terminal-view" :class="{ 'terminal-dim': status === 'disconnected' }"></div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'

const terminalRef = ref(null)
const status = ref('disconnected') // 'disconnected', 'connecting', 'connected'

let term = null
let fitAddon = null
let ws = null
let resizeObserver = null

const getToken = () => localStorage.getItem('panel_token')

const connect = () => {
  if (status.value !== 'disconnected') return
  
  const token = getToken()
  if (!token) {
    alert('未登录或登录已过期')
    return
  }
  
  term.clear()
  term.writeln('\x1b[33m[系统]\x1b[0m 正在连接...')
  status.value = 'connecting'
  
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  // 在 Vite 代理或生产环境中，直接请求当前 host 的 /api 即可
  const wsUrl = `${protocol}//${window.location.host}/api/ws/terminal?token=${encodeURIComponent(token)}`
  
  try {
    ws = new WebSocket(wsUrl)
  } catch (err) {
    term.writeln(`\x1b[31m[错误]\x1b[0m WebSocket 创建失败: ${err.message}`)
    status.value = 'disconnected'
    return
  }

  ws.onopen = () => {
    status.value = 'connected'
    fitAddon.fit()
    ws.send(JSON.stringify({
      type: 'resize',
      cols: term.cols,
      rows: term.rows
    }))
  }

  ws.onmessage = (event) => {
    term.write(event.data)
  }

  ws.onclose = (event) => {
    status.value = 'disconnected'
    term.writeln(`\r\n\x1b[33m[系统]\x1b[0m 连接已断开 (代码: ${event.code})`)
    ws = null
  }

  ws.onerror = (error) => {
    term.writeln(`\r\n\x1b[31m[错误]\x1b[0m WebSocket 连接出错`)
  }
}

const disconnect = () => {
  if (ws) {
    ws.close()
  }
}

onMounted(() => {
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
      brightWhite: '#212529'
    },
    fontFamily: 'ui-monospace, "Cascadia Code", "Segoe UI Mono", Consolas, monospace',
    fontSize: 14,
    lineHeight: 1.35,
    letterSpacing: 0
  })
  
  fitAddon = new FitAddon()
  term.loadAddon(fitAddon)
  
  term.open(terminalRef.value)
  fitAddon.fit()

  term.onData((data) => {
    if (ws && status.value === 'connected') {
      ws.send(data)
    }
  })

  resizeObserver = new ResizeObserver(() => {
    if (fitAddon && term && terminalRef.value && terminalRef.value.clientHeight > 0) {
      try {
        fitAddon.fit()
        if (ws && status.value === 'connected') {
          ws.send(JSON.stringify({
            type: 'resize',
            cols: term.cols,
            rows: term.rows
          }))
        }
      } catch (e) {
        // ignore resize errors
      }
    }
  })
  
  resizeObserver.observe(terminalRef.value)
})

onBeforeUnmount(() => {
  if (resizeObserver) {
    resizeObserver.disconnect()
  }
  if (ws) {
    ws.close()
  }
  if (term) {
    term.dispose()
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
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1rem 1.25rem;
  border-bottom: 1px solid var(--border);
  background: var(--bg-secondary);
}

.terminal-heading {
  margin: 0;
  font-size: 1.125rem;
  font-weight: 600;
  color: var(--text-primary);
}

.terminal-actions {
  display: flex;
  gap: 0.5rem;
}

.btn-terminal {
  padding: 0.5rem 1rem;
  font-size: 0.9rem;
  color: var(--text-primary);
  background: var(--bg-tertiary);
  border: 1px solid var(--border);
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.2s, border-color 0.2s;
}

.btn-terminal:hover:not(:disabled) {
  background: var(--bg-tertiary);
  border-color: var(--text-secondary);
}

.btn-terminal:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.terminal-container {
  flex: 1;
  min-height: 280px;
  padding: 1rem;
  background: #f8f9fa;
  position: relative;
}

.terminal-placeholder {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #868e96;
  font-size: 0.9rem;
  z-index: 1;
  pointer-events: none;
}

.terminal-view.terminal-dim {
  opacity: 0.9;
}

.terminal-view {
  width: 100%;
  height: 100%;
  min-height: 240px;
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
