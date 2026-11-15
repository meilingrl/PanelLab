import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  clearLocal, createLineTracker, fillSequence, loadLocal, loadPins, mergeHistory,
  recordLocal, togglePin, LOCAL_MAX,
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
  expect(fillSequence('ls')).toBe('\x15ls')
  expect(fillSequence('ls', true)).toBe('\x15ls\r')
})
