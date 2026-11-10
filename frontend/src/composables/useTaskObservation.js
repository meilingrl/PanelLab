import { ref } from 'vue'
import { workspaceApi } from './useWorkspaceApi'
import { useBoundTaskRead } from './useBoundTaskRead'
import { deriveObservationRates } from './observationRates'

export function useTaskObservation(props) {
  const rates = ref(deriveObservationRates(null, null))
  let baseline = null
  const reader = useBoundTaskRead(props, {
    read: workspaceApi.observation,
    label: '观测',
    onSample(sample) {
      rates.value = deriveObservationRates(baseline, sample)
      baseline = sample
    },
    onDiscontinuity(lastGood) {
      baseline = null
      rates.value = deriveObservationRates(null, lastGood)
    },
  })
  return { ...reader, observation: reader.latest, rates }
}
