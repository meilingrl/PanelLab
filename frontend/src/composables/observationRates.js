const CPU_FIELDS = ['user', 'nice', 'system', 'idle', 'iowait', 'irq', 'softirq', 'steal']
const safeCounter = value => Number.isSafeInteger(value) && value >= 0
const positiveInteger = value => Number.isSafeInteger(value) && value > 0

function usableSampling(sample) {
  const boot = sample?.sampling?.boot_id
  const time = sample?.sampling?.monotonic_seconds
  return typeof boot === 'string' && boot.length > 0 && boot.length <= 128 &&
    !/[\s\x00-\x1f\x7f]/.test(boot) &&
    typeof time === 'number' && Number.isFinite(time) && time >= 0
}

function interfaceKey(net) {
  if (typeof net?.name !== 'string' || !net.name || /[\s/:\x00-\x1f\x7f]/.test(net.name) ||
      !positiveInteger(net.ifindex)) return null
  return JSON.stringify([net.name, net.ifindex])
}

function uniqueInterfaces(network) {
  const entries = new Map()
  for (const net of Array.isArray(network) ? network : []) {
    const key = interfaceKey(net)
    if (key) entries.set(key, entries.has(key) ? null : net)
  }
  return entries
}

/** Remote cumulative counters only; receipt timestamps never provide the rate clock. */
export function deriveObservationRates(previous, current) {
  const network = (Array.isArray(current?.system?.network) ? current.system.network : []).map(net => ({
    name: net.name,
    ifindex: net.ifindex,
    receivedPerSecond: null,
    sentPerSecond: null,
  }))
  const result = { cpuPercent: null, intervalSeconds: null, network }
  if (!usableSampling(previous) || !usableSampling(current) ||
      previous.sampling.boot_id !== current.sampling.boot_id) return result
  const seconds = current.sampling.monotonic_seconds - previous.sampling.monotonic_seconds
  if (!Number.isFinite(seconds) || seconds < 0.5 || seconds > 120) return result
  result.intervalSeconds = seconds

  const before = previous.system?.cpu?.ticks
  const after = current.system?.cpu?.ticks
  const deltas = CPU_FIELDS.map(key => {
    if (!safeCounter(before?.[key]) || !safeCounter(after?.[key]) || after[key] < before[key]) return null
    return after[key] - before[key]
  })
  if (deltas.every(value => value !== null)) {
    const total = deltas.reduce((sum, value) => sum + value, 0)
    if (Number.isSafeInteger(total) && total > 0) {
      result.cpuPercent = ((total - deltas[3] - deltas[4]) / total) * 100
    }
  }

  const oldInterfaces = uniqueInterfaces(previous.system?.network)
  const newInterfaces = uniqueInterfaces(current.system?.network)
  network.forEach((rates, index) => {
    const net = current.system.network[index]
    const key = interfaceKey(net)
    const old = oldInterfaces.get(key)
    if (!old || !newInterfaces.get(key)) return
    if (!['received_bytes', 'sent_bytes'].every(field =>
      safeCounter(net[field]) && safeCounter(old[field]) && net[field] >= old[field])) return
    rates.receivedPerSecond = (net.received_bytes - old.received_bytes) / seconds
    rates.sentPerSecond = (net.sent_bytes - old.sent_bytes) / seconds
  })
  return result
}

function identityNumber(value, optional = false) {
  if (optional && value == null) return null
  if (typeof value !== 'number' && (typeof value !== 'string' || !/^\d+$/.test(value))) return undefined
  const number = Number(value)
  return positiveInteger(number) ? number : undefined
}
function identityText(value, optional = false) {
  if (optional && value == null) return null
  return typeof value === 'string' && value.length > 0 ? value : undefined
}
function normalizedContext(task, server) {
  if (!task || !server) return null
  const fields = [
    identityNumber(task.id), identityNumber(task.server_id), identityText(task.root_path),
    identityText(task.log_path, true), identityNumber(task.pid, true), identityText(task.process_identity, true),
    identityNumber(server.id), identityText(server.host), identityNumber(server.port), identityText(server.username),
  ]
  if (fields.some(value => value === undefined) || fields[1] !== fields[6]) return null
  return JSON.stringify(fields)
}

/** Names/notes are display metadata; a complete remote binding must match. */
export function observationContextMatches(observation, task, server) {
  const workload = observation?.context?.workload
  const target = observation?.context?.server
  if (!workload || !target ||
      !['id', 'server_id', 'root_path', 'log_path', 'pid', 'process_identity'].every(key => Object.hasOwn(workload, key)) ||
      !['id', 'host', 'port', 'username'].every(key => Object.hasOwn(target, key))) return false
  const expected = normalizedContext(task, server)
  const received = normalizedContext(observation?.context?.workload, observation?.context?.server)
  return expected !== null && received !== null && expected === received
}
