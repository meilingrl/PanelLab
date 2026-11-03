import { expect, it } from 'vitest'
import { deriveObservationRates, observationContextMatches } from '../src/composables/observationRates'
const cpu = values => Object.fromEntries(['user', 'nice', 'system', 'idle', 'iowait', 'irq', 'softirq', 'steal'].map((key, i) => [key, values[i]]))
function sample(time = 10, ticks = [100, 10, 20, 200, 50, 10, 10, 0], networks) {
 return { sampling: { boot_id: 'boot', monotonic_seconds: time }, system: { cpu: { ticks: cpu(ticks) }, network: networks ?? [{ name: 'eth0', ifindex: 7, received_bytes: 100, sent_bytes: 200 }] } }
}
const previous = () => sample()
const current = () => sample(15, [110, 15, 25, 230, 60, 15, 15, 10], [{ name: 'eth0', ifindex: 7, received_bytes: 600, sent_bytes: 1200 }])
it('uses the first eight tick deltas and excludes both idle and iowait from busy', () => {
 const result = deriveObservationRates(previous(), current())
 // Deltas: 10+5+5+30+10+5+5+10=80; busy=40. Guest duplicates never enter the sum.
 expect(result.cpuPercent).toBe(50); expect(result.intervalSeconds).toBe(5)
 expect(result.network[0]).toMatchObject({ receivedPerSecond: 100, sentPerSecond: 200 })
 expect(deriveObservationRates(null, current()).cpuPercent).toBeNull()
})
it.each([0.5, 120])('accepts inclusive clock interval %s', dt => {
 const now = current(); now.sampling.monotonic_seconds = 10 + dt
 expect(deriveObservationRates(previous(), now).cpuPercent).toBe(50)
})
it.each([0, -1, 0.49, 120.01, Infinity, NaN, true, '15'])('rejects invalid clock interval/value %s', value => {
 const now = current(); now.sampling.monotonic_seconds = typeof value === 'number' && Number.isFinite(value) ? 10 + value : value
 const result = deriveObservationRates(previous(), now)
 expect(result.cpuPercent).toBeNull(); expect(result.network[0].receivedPerSecond).toBeNull()
})
it.each(['boot_id', 'monotonic_seconds'])('rejects absent sampling %s', key => {
 const now = current(); delete now.sampling[key]
 expect(deriveObservationRates(previous(), now).cpuPercent).toBeNull()
})
it('reboot and unusable boot identifiers cannot derive rates', () => {
 for (const boot of ['new-boot', '', 'bad boot', true, null, 'a'.repeat(129)]) {
  const now = current(); now.sampling.boot_id = boot
  expect(deriveObservationRates(previous(), now).cpuPercent).toBeNull()
 }
})
it.each(['user', 'nice', 'system', 'idle', 'iowait', 'irq', 'softirq', 'steal'])('any regressed CPU field %s invalidates CPU alone', key => {
 const before = previous(), now = current(); before.system.cpu.ticks[key] = 100; now.system.cpu.ticks[key] = 99
 const result = deriveObservationRates(before, now)
 expect(result.cpuPercent).toBeNull(); expect(result.network[0].receivedPerSecond).toBe(100)
})
it.each([null, true, -1, 1.5, '10', Infinity, Number.MAX_SAFE_INTEGER + 1])('rejects malformed CPU/network counters %s', value => {
 const now = current(); now.system.cpu.ticks.user = value; now.system.network[0].sent_bytes = value
 const result = deriveObservationRates(previous(), now)
 expect(result.cpuPercent).toBeNull(); expect(result.network[0].receivedPerSecond).toBeNull(); expect(result.network[0].sentPerSecond).toBeNull()
})
it('rejects unsafe aggregate CPU deltas and zero aggregate change', () => {
 const before = sample(10, [0, 0, 0, 0, 0, 0, 0, 0])
 const now = sample(15, [Number.MAX_SAFE_INTEGER, 1, 0, 0, 0, 0, 0, 0])
 expect(deriveObservationRates(before, now).cpuPercent).toBeNull()
 expect(deriveObservationRates(previous(), sample(15)).cpuPercent).toBeNull()
})
it.each(['new name', 'new index', 'missing index', 'regressed RX', 'regressed TX'])('primes an interface with %s instead of reporting a false rate', reason => {
 const now = current(), net = now.system.network[0]
 if (reason === 'new name') net.name = 'eth1'
 if (reason === 'new index') net.ifindex = 8
 if (reason === 'missing index') net.ifindex = null
 if (reason === 'regressed RX') net.received_bytes = 99
 if (reason === 'regressed TX') net.sent_bytes = 199
 expect(deriveObservationRates(previous(), now).network[0].receivedPerSecond).toBeNull()
})
it('keeps individual interface rates including zero without summing interfaces', () => {
 const now = current(); now.system.network.push({ name: 'eth1', ifindex: 8, received_bytes: 9000, sent_bytes: 8000 })
 now.system.network[0].received_bytes = 100
 const result = deriveObservationRates(previous(), now)
 expect(result.network).toHaveLength(2); expect(result.network[0].receivedPerSecond).toBe(0); expect(result.network[1].receivedPerSecond).toBeNull()
})
const task = { id: 1, server_id: 71, root_path: '/run', log_path: null, pid: null, process_identity: null }
const server = { id: 71, host: 'host', port: 22, username: 'lab' }
it('normalizes numeric identity fields and optional nulls while ignoring display-only fields', () => {
 const data = { context: { workload: { ...task, id: '1', name: 'different', notes: 'different' }, server: { ...server, port: '22', name: 'different' } } }
 expect(observationContextMatches(data, task, server)).toBe(true)
})
it.each(['id', 'server_id', 'root_path', 'log_path', 'pid', 'process_identity'])('context rejects workload %s changes', key => {
 const data = { context: { workload: { ...task, [key]: key.includes('id') ? 72 : '/new' }, server } }
 expect(observationContextMatches(data, task, server)).toBe(false)
})
it.each(['id', 'host', 'port', 'username'])('context rejects server %s changes', key => {
 const data = { context: { workload: task, server: { ...server, [key]: key === 'id' || key === 'port' ? 72 : 'other' } } }
 expect(observationContextMatches(data, task, server)).toBe(false)
})
it('missing identity/context cannot validate even if both sides omit the same field', () => {
 expect(observationContextMatches({}, task, server)).toBe(false)
 expect(observationContextMatches({ context: { workload: task, server } }, task)).toBe(false)
 const incomplete = { ...server }; delete incomplete.host
 expect(observationContextMatches({ context: { workload: task, server: incomplete } }, task, incomplete)).toBe(false)
})
it.each(['log_path', 'pid', 'process_identity'])('missing optional context key %s is incomplete evidence, distinct from explicit null', key => {
 const workload = { ...task }; delete workload[key]
 expect(observationContextMatches({ context: { workload, server } }, task, server)).toBe(false)
})
it('a duplicate interface identity cannot form an unambiguous rate', () => {
 const before = previous(); before.system.network.push({ ...before.system.network[0] })
 expect(deriveObservationRates(before, current()).network[0].receivedPerSecond).toBeNull()
})
