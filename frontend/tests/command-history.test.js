import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  clearLocal, createLineTracker, fillSequence, loadLocal, loadPins, looksSecret, mergeHistory,
  recordLocal, shouldRecord, togglePin, LOCAL_MAX,
} from '../src/composables/commandHistory'

beforeEach(() => {
  const data = new Map()
  vi.stubGlobal('localStorage', {
    getItem: (k) => (data.has(k) ? data.get(k) : null),
    setItem: (k, v) => data.set(k, String(v)),
    removeItem: (k) => data.delete(k),
  })
})

describe('per-server local history', () => {
  it('records newest first, deduped, per server', () => {
    let a = recordLocal(1, loadLocal(1), 'ls')
    a = recordLocal(1, a, 'df -h')
    a = recordLocal(1, a, 'ls')
    recordLocal(2, loadLocal(2), 'uptime')
    expect(loadLocal(1)).toEqual(['ls', 'df -h'])
    expect(loadLocal(2)).toEqual(['uptime'])
  })

  it('caps the list and ignores blank commands', () => {
    let list = []
    for (let i = 0; i < LOCAL_MAX + 5; i++) list = recordLocal(1, list, `c${i}`)
    expect(list).toHaveLength(LOCAL_MAX)
    expect(recordLocal(1, list, '   ')).toBe(list)
  })

  it('clears only that server', () => {
    recordLocal(1, [], 'ls')
    recordLocal(2, [], 'pwd')
    expect(clearLocal(1)).toEqual([])
    expect(loadLocal(1)).toEqual([])
    expect(loadLocal(2)).toEqual(['pwd'])
  })

  it('survives corrupt storage', () => {
    localStorage.setItem('panel_terminal_history:1', '{bad')
    expect(loadLocal(1)).toEqual([])
  })
})

describe('pins and merge', () => {
  it('toggles pins per server', () => {
    let pins = togglePin(1, loadPins(1), 'htop')
    expect(loadPins(1)).toEqual(['htop'])
    pins = togglePin(1, pins, 'htop')
    expect(loadPins(1)).toEqual([])
    expect(pins).toEqual([])
  })

  it('orders pinned, then local, then server, deduped', () => {
    const items = mergeHistory({ local: ['ls', 'df -h'], server: ['df -h', 'git pull'], pins: ['git pull'] })
    expect(items.map((i) => [i.cmd, i.source, i.pinned])).toEqual([
      ['git pull', 'pinned', true], ['ls', 'local', false], ['df -h', 'local', false],
    ])
  })

  it('filters case-insensitively', () => {
    const items = mergeHistory({ local: ['sudo NGINX -t', 'ls'], server: ['nginx -s reload'], query: 'nginx' })
    expect(items.map((i) => i.cmd)).toEqual(['sudo NGINX -t', 'nginx -s reload'])
  })
})

describe('line tracker', () => {
  it('records typed lines with backspace', () => {
    const t = createLineTracker()
    expect(t.feed('lss')).toEqual([])
    expect(t.feed('\x7f -la\r')).toEqual(['ls -la'])
  })

  it('does not record lines using arrows or tab completion', () => {
    const t = createLineTracker()
    expect(t.feed('\x1b[A\r')).toEqual([])
    expect(t.feed('cd /sr\t\r')).toEqual([])
    expect(t.feed('pwd\r')).toEqual(['pwd'])
  })

  it('handles pasted multi-line input and Ctrl+C', () => {
    const t = createLineTracker()
    expect(t.feed('echo a\recho b\r')).toEqual(['echo a', 'echo b'])
    expect(t.feed('rm -rf x\x03\r')).toEqual([])
  })

  it('knows the line after a sidebar fill', () => {
    const t = createLineTracker()
    t.feed('half typed')
    t.set('df -h')
    expect(t.feed(' /\r')).toEqual(['df -h /'])
  })
})

it('fill clears the line first and runs only when asked', () => {
  expect(fillSequence('ls')).toBe('\x05\x15ls')
  expect(fillSequence('ls', true)).toBe('\x05\x15ls\r')
})

describe('secrets and prompts', () => {
  it('matches the backend secret shapes and keeps common commands', () => {
    for (const s of ['docker login -u me -p hunter2', 'curl -u me:pw https://x', 'export DB_PASS=abc',
      'mysql -u root -pS3cret', 'echo ghp_abcdefghijklmnop', 'git clone https://me:tok@github.com/a/b']) {
      expect(looksSecret(s), s).toBe(true)
    }
    for (const s of ['netstat -plnt', 'mkdir -p dir', 'ssh -p 22 host', 'rsync -Pav a b', 'git push']) {
      expect(looksSecret(s), s).toBe(false)
    }
  })

  it('never stores secret-looking lines or prompt answers', () => {
    let list = recordLocal(1, [], 'export API_KEY=abc')
    list = recordLocal(1, list, 'y')
    list = recordLocal(1, list, 'ls')
    expect(list).toEqual(['ls'])
  })

  it('hides old secret entries already in storage', () => {
    const items = mergeHistory({ local: ['echo token=x', 'ls'] })
    expect(items.map((i) => i.cmd)).toEqual(['ls'])
  })

  it('records only echoed lines outside password prompts and full-screen programs', () => {
    expect(shouldRecord('ls -la', ['admin@host:~$ ls -la', 'total 0'], false)).toBe(true)
    expect(shouldRecord('sudo -k', ['admin@host:~$ uname -r', '6.8.0', 'admin@host:~$ sudo -k'], false)).toBe(true)
    expect(shouldRecord('hunter2', ['[sudo] password for admin: '], false)).toBe(false)
    expect(shouldRecord('hunter2', ['admin@host:~$ '], false)).toBe(false)
    expect(shouldRecord(':wq', [':wq'], true)).toBe(false)
    expect(shouldRecord('x', ['Enter passphrase for key: x'], false)).toBe(false)
  })

  it('records pasted commands despite bracketed-paste markers', () => {
    const t = createLineTracker()
    expect(t.feed('\x1b[200~df -h\x1b[201~\r')).toEqual(['df -h'])
  })
})
