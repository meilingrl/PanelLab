import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { taskBinding } from './workspaceBinding'
import { observationContextMatches } from './observationRates'

/** A single bounded read, optionally repeated while this task view is visible. */
export function useBoundTaskRead(props, { read, label, available, bindingError, onSample, onDiscontinuity }) {
  const latest = ref(null)
  const error = ref('')
  const busy = ref(false)
  const follow = ref(false)
  const intervalInput = ref('5')
  const loadedAt = ref(null)
  const clock = ref(Date.now())
  const visible = ref(!document.hidden)
  const blocked = ref(false)
  const metadataRefreshed = ref(false)
  const paused = ref(false)
  const intervalSeconds = computed(() => {
    const raw = String(intervalInput.value)
    const value = Number(raw)
    return /^\d+$/.test(raw) && Number.isInteger(value) && value >= 2 && value <= 60 ? value : null
  })
  const intervalError = computed(() => intervalSeconds.value === null ? '请输入 2–60 秒之间的整数；自动跟随已暂停。' : '')
  const eligible = computed(() => props.active !== false && visible.value && (available?.() ?? true))
  const ageExpired = computed(() => loadedAt.value !== null &&
    clock.value - loadedAt.value > Math.max(30, 2 * (intervalSeconds.value ?? 5)) * 1000)
  const stale = computed(() => !!latest.value && (
    !!error.value || blocked.value || paused.value || !eligible.value || ageExpired.value
  ))
  const canRefresh = computed(() => eligible.value && !busy.value && (!blocked.value || metadataRefreshed.value))
  let generation = 0
  let controller
  let nextTimer
  let ageTimer
  let hasSettledRead = false
  let consecutiveFailures = 0
  let disposed = false

  function blockBinding() {
    blocked.value = true
    metadataRefreshed.value = false
    error.value = label + '与当前任务或服务器信息不一致，已停止自动跟随。请刷新任务信息。'
    onDiscontinuity?.(latest.value)
  }
  function clearNext() {
    clearTimeout(nextTimer)
    nextTimer = undefined
  }
  function suspend() {
    generation++
    controller?.abort()
    controller = undefined
    busy.value = false
    clearNext()
    onDiscontinuity?.(latest.value)
    paused.value = true
  }
  function canFollow() {
    return !disposed && eligible.value && follow.value && intervalSeconds.value !== null && !blocked.value
  }
  function schedule() {
    clearNext()
    if (!canFollow() || busy.value) return
    const seconds = Math.min(60, intervalSeconds.value * 2 ** Math.min(consecutiveFailures, 6))
    nextTimer = setTimeout(() => refresh(), seconds * 1000)
  }
  async function refresh() {
    if (disposed || !canRefresh.value) return
    clearNext()
    const requestGeneration = ++generation
    controller = new AbortController()
    busy.value = true
    // Keep the previous error/stale marker while a retry is in flight.
    try {
      const data = await read(props.task.id, controller.signal)
      if (disposed || requestGeneration !== generation) return
      if (!observationContextMatches(data, props.task, props.server)) {
        blockBinding()
        return
      }
      clock.value = Date.now()
      onSample?.(data)
      latest.value = data
      loadedAt.value = clock.value
      error.value = ''
      blocked.value = false
      metadataRefreshed.value = false
      paused.value = false
      consecutiveFailures = 0
    } catch (failure) {
      if (disposed || requestGeneration !== generation) return
      if (bindingError?.(failure)) {
        blockBinding()
        return
      }
      error.value = failure.name === 'AbortError' ? label + '请求已中止，请重试。' : failure.message || label + '失败'
      consecutiveFailures++
      onDiscontinuity?.(latest.value)
    } finally {
      if (!disposed && requestGeneration === generation) {
        hasSettledRead = true
        busy.value = false
        controller = undefined
        schedule()
      }
    }
  }
  function updateClock() {
    clock.value = Date.now()
    if (ageExpired.value) onDiscontinuity?.(latest.value)
  }
  function startAgeTimer() {
    clearInterval(ageTimer)
    ageTimer = undefined
    if (!eligible.value || disposed) return
    updateClock()
    ageTimer = setInterval(updateClock, 1000)
  }
  function onVisibility() { visible.value = !document.hidden }
  document.addEventListener('visibilitychange', onVisibility)

  watch(() => taskBinding(props.task, props.server), () => {
    suspend()
    latest.value = null
    hasSettledRead = false
    loadedAt.value = null
    error.value = ''
    blocked.value = false
    metadataRefreshed.value = false
    consecutiveFailures = 0
    paused.value = false
    if (eligible.value) refresh()
  }, { immediate: true, flush: 'sync' })
  watch(eligible, active => {
    startAgeTimer()
    if (!active) suspend()
    else if (!hasSettledRead || canFollow()) refresh()
  }, { immediate: true, flush: 'sync' })
  watch(follow, enabled => {
    if (!enabled) suspend()
    else if (canFollow()) refresh()
  }, { flush: 'sync' })
  watch(intervalSeconds, (value, previous) => {
    if (value === null) suspend()
    else if (previous === null && canFollow()) refresh()
    else schedule()
  }, { flush: 'sync' })
  watch(() => props.metadataRevision, (value, previous) => {
    if (value !== previous && blocked.value) metadataRefreshed.value = true
  })
  onBeforeUnmount(() => {
    disposed = true
    suspend()
    clearInterval(ageTimer)
    document.removeEventListener('visibilitychange', onVisibility)
  })
  return {
    latest, loadedAt, error, busy, follow, intervalInput, intervalError, intervalSeconds,
    stale, blocked, metadataRefreshed, eligible, canRefresh, refresh,
  }
}
