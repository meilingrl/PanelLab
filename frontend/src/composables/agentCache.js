/** In-memory cache for the Server Agent page, so returning to it renders the last data at once.
 *  Never persisted (no localStorage); cleared on logout / sign-in. */
const entries = new Map()   // server id -> { status, jobs, lastRuns, previews, savedAt }
let lastServerKey = ''      // e.g. 'server:71'

export const getAgentCache = id => entries.get(Number(id))
export const setAgentCache = (id, entry) => { entries.set(Number(id), { ...entry, savedAt: Date.now() }) }
export const deleteAgentCache = id => { entries.delete(Number(id)) }
export const getLastServer = () => lastServerKey
export const setLastServer = key => { lastServerKey = key || '' }
export function clearAgentCache() { entries.clear(); lastServerKey = '' }
