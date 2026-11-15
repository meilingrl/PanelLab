// Per-server terminal command history: local records, the server's own shell
// history, pinned commands, search, and keystroke tracking.

export const LOCAL_MAX = 300
export const CMD_MAX_LEN = 512
const LOCAL_PREFIX = 'panel_terminal_history:'
const PINS_PREFIX = 'panel_terminal_pins:'

// Lines that may carry a credential are never stored or shown. Kept in sync
// with LOOKS_SECRET in backend/services/shell_history.py.
export const SECRET_PATTERN = new RegExp([
  'passw|passphrase|secret|token|api[_-]?key|authorization|bearer|credential|private[_-]?key|cookie|sshpass',
  '\\b\\w*(?:pass|pwd)\\w*=',
  '://[^\\s/:@]+:[^\\s/@]+@',
  '\\s-u\\s*\\S+:\\S',
  '\\bsudo\\s+(?:-\\w+\\s+)*-S\\b',
  '\\b(?:mysql|mariadb|mysqldump|mysqladmin|mongo\\w*)\\b.*\\s-p\\S',
  '\\bdocker\\s+login\\b.*\\s(?:-p|--password)\\b',
  '\\bredis-cli\\b.*\\s-a\\s',
  '\\bsmbclient\\b.*%',
  '\\b(?:gh[pousr]_|github_pat_|glpat-|xox[abprs]-|sk-)[A-Za-z0-9_-]{8,}',
  '\\bAKIA[0-9A-Z]{16}\\b',
].join('|'), 'i')

// Replies to prompts (y/n, a single key) are not commands worth keeping.
const PROMPT_ANSWER = /^(?:.|y|n|yes|no|q)$/i

export function looksSecret(cmd) {
  return SECRET_PATTERN.test(cmd)
}

function readList(key) {
  try {
    const value = JSON.parse(localStorage.getItem(key) || '[]')
    return Array.isArray(value) ? value.filter((v) => typeof v === 'string') : []
  } catch {
    return []
  }
}

function writeList(key, list) {
  try {
    localStorage.setItem(key, JSON.stringify(list))
  } catch {}
}

export function loadLocal(serverId) {
  return serverId ? readList(LOCAL_PREFIX + serverId) : []
}

export function loadPins(serverId) {
  return serverId ? readList(PINS_PREFIX + serverId) : []
}

export function normalizeCommand(cmd) {
  const s = (cmd || '').trim()
  if (!s) return ''
  return s.length > CMD_MAX_LEN ? s.slice(0, CMD_MAX_LEN) : s
}

/** Put cmd first (deduped) and persist; returns the new list. */
export function recordLocal(serverId, list, cmd) {
  const s = normalizeCommand(cmd)
  if (!s || !serverId || looksSecret(s) || PROMPT_ANSWER.test(s)) return list
  const next = [s, ...list.filter((c) => c !== s)].slice(0, LOCAL_MAX)
  writeList(LOCAL_PREFIX + serverId, next)
  return next
}

export function clearLocal(serverId) {
  if (!serverId) return []
  try {
    localStorage.removeItem(LOCAL_PREFIX + serverId)
  } catch {}
  return []
}

export function togglePin(serverId, pins, cmd) {
  const s = normalizeCommand(cmd)
  if (!s || !serverId) return pins
  const next = pins.includes(s) ? pins.filter((c) => c !== s) : [s, ...pins]
  writeList(PINS_PREFIX + serverId, next)
  return next
}

/**
 * The 「最近」 list: pinned first, then this browser's records, then the
 * server's shell history; deduped and filtered by a case-insensitive query.
 */
export function mergeHistory({ local = [], server = [], pins = [], query = '' }) {
  const q = query.trim().toLowerCase()
  const seen = new Set()
  const out = []
  const add = (cmd, source) => {
    if (!cmd || seen.has(cmd) || looksSecret(cmd)) return
    if (q && !cmd.toLowerCase().includes(q)) return
    seen.add(cmd)
    out.push({ cmd, source, pinned: pins.includes(cmd) })
  }
  pins.forEach((c) => add(c, 'pinned'))
  local.forEach((c) => add(c, 'local'))
  server.forEach((c) => add(c, 'server'))
  return out
}

/**
 * Tracks what the user types so finished lines can be recorded. A line that
 * used arrow keys, Tab completion or other control sequences is not recorded,
 * because the text the shell actually ran is unknown here (the server's own
 * history still shows it).
 */
export function createLineTracker() {
  let buffer = ''
  let dirty = false
  return {
    /** Feed raw terminal input; returns the lines finished by Enter. */
    feed(data) {
      const done = []
      // Bracketed paste (bash 5.1+, zsh) wraps pasted text in these markers.
      data = data.replace(/\x1b\[20[01]~/g, '')
      for (const c of data) {
        if (c === '\r' || c === '\n') {
          if (!dirty && buffer.trim()) done.push(buffer)
          buffer = ''
          dirty = false
        } else if (c === '\x7f' || c === '\b') {
          buffer = buffer.slice(0, -1)
        } else if (c === '\x15' || c === '\x03') {
          // Ctrl+U clears the line; Ctrl+C abandons it.
          buffer = ''
          dirty = false
        } else if (c === '\t' || c === '\x1b' || c < ' ') {
          dirty = true
        } else {
          buffer += c
        }
      }
      return done
    },
    /** The line now holds exactly cmd (after a fill from the sidebar). */
    set(cmd) {
      buffer = cmd
      dirty = false
    },
  }
}

/** Bytes to send: Ctrl+E then Ctrl+U clear the whole line, then the command. */
export function fillSequence(cmd, run = false) {
  return '\x05\x15' + cmd + (run ? '\r' : '')
}

const PASSWORD_PROMPT = /password|passphrase|密码|口令/i

/**
 * Whether a finished line should be recorded, judged from the screen lines
 * between where it was typed and where the cursor is now: some line must end
 * with the command (so the shell echoed it; a password typed at a hidden
 * prompt is not echoed), that line must not be a password prompt, and no
 * full-screen program may be running.
 */
export function shouldRecord(cmd, screenLines, alternateScreen) {
  if (alternateScreen) return false
  const want = cmd.trim()
  return (screenLines || []).some((line) => {
    const text = (line || '').trimEnd()
    return text.endsWith(want) && !PASSWORD_PROMPT.test(text)
  })
}
