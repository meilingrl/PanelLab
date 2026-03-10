/**
 * 终端会话全局 store：持久连接 + 多会话，路由切换不关闭连接。
 * 无 Pinia，使用模块级 ref 保证单例。
 */
import { ref, computed } from 'vue'

const BUFFER_MAX_LINES = 500

const sessions = ref([])
const currentSessionId = ref(null)

function updateSessionStatus(sessionId, status, ws) {
  const idx = sessions.value.findIndex((s) => s.id === sessionId)
  if (idx === -1) return
  const arr = [...sessions.value]
  const prev = arr[idx]
  arr[idx] = { ...prev, status, ws: ws !== undefined ? ws : prev.ws }
  sessions.value = arr
}

export function useTerminalStore() {
  const currentSession = computed(() => {
    const id = currentSessionId.value
    if (!id) return null
    return sessions.value.find((s) => s.id === id) || null
  })

  function createSession(server) {
    const token = localStorage.getItem('panel_token')
    if (!token) {
      return Promise.reject(new Error('未登录'))
    }
    const id = crypto.randomUUID?.() ?? `t-${Date.now()}-${Math.random().toString(36).slice(2)}`
    const session = {
      id,
      serverId: server.id,
      host: server.host,
      status: 'connecting',
      ws: null,
      buffer: [],
      attachedTerm: null,
    }
    sessions.value = [...sessions.value, session]
    currentSessionId.value = id

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const wsUrl = `${protocol}//${window.location.host}/api/ws/terminal?token=${encodeURIComponent(token)}&server_id=${server.id}`

    return new Promise((resolve, reject) => {
      let resolved = false
      try {
        const ws = new WebSocket(wsUrl)
        session.ws = ws

        ws.onopen = () => {
          updateSessionStatus(id, 'connected', ws)
          if (!resolved) {
            resolved = true
            resolve(sessions.value.find((s) => s.id === id))
          }
        }

        ws.onmessage = (event) => {
          const data = event.data
          const s = sessions.value.find((ss) => ss.id === id)
          if (s?.attachedTerm && typeof s.attachedTerm.write === 'function') {
            s.attachedTerm.write(data)
          }
          if (s) {
            s.buffer.push(data)
            if (s.buffer.length > BUFFER_MAX_LINES) s.buffer.shift()
          }
        }

        ws.onclose = () => {
          updateSessionStatus(id, 'disconnected', null)
          const s = sessions.value.find((ss) => ss.id === id)
          if (s?.attachedTerm && typeof s.attachedTerm.writeln === 'function') {
            s.attachedTerm.writeln('\r\n\x1b[33m[系统]\x1b[0m 连接已断开')
          }
          if (!resolved) {
            resolved = true
            reject(new Error('连接已关闭'))
          }
        }

        ws.onerror = () => {
          if (!resolved) {
            resolved = true
            reject(new Error('WebSocket 错误'))
          }
        }
      } catch (err) {
        session.status = 'disconnected'
        sessions.value = sessions.value.filter((s) => s.id !== id)
        if (currentSessionId.value === id) {
          const rest = sessions.value
          currentSessionId.value = rest.length ? rest[0].id : null
        }
        reject(err)
      }
    })
  }

  function attachSession(sessionId, term) {
    const session = sessions.value.find((s) => s.id === sessionId)
    if (!session) return
    session.attachedTerm = term
    if (session.buffer.length > 0) {
      try {
        session.buffer.forEach((chunk) => term.write(chunk))
      } catch (_) {}
    }
  }

  function detachSession(sessionId) {
    const session = sessions.value.find((s) => s.id === sessionId)
    if (session) session.attachedTerm = null
  }

  function sendToSession(sessionId, data) {
    const session = sessions.value.find((s) => s.id === sessionId)
    if (session?.ws && session.ws.readyState === WebSocket.OPEN) {
      session.ws.send(data)
    }
  }

  function setCurrentSession(sessionId) {
    currentSessionId.value = sessionId
  }

  function closeSession(sessionId) {
    const session = sessions.value.find((s) => s.id === sessionId)
    if (session?.ws) {
      session.ws.close()
      session.ws = null
    }
    sessions.value = sessions.value.filter((s) => s.id !== sessionId)
    if (currentSessionId.value === sessionId) {
      const rest = sessions.value
      currentSessionId.value = rest.length ? rest[0].id : null
    }
  }

  function closeAllSessions() {
    sessions.value.forEach((s) => {
      if (s.ws) s.ws.close()
    })
    sessions.value = []
    currentSessionId.value = null
  }

  return {
    sessions,
    currentSessionId,
    currentSession,
    createSession,
    attachSession,
    detachSession,
    sendToSession,
    setCurrentSession,
    closeSession,
    closeAllSessions,
  }
}
