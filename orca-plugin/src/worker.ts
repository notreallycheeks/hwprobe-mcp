// Polls the hub (or the local agent) and pushes the fleet document to every open panel.
import type { OrcaWorkerApi } from 'orca/worker'
import type { Fleet, FleetAnswer } from './protocol'

const LAST = 'fleet.last'

let timer: ReturnType<typeof setTimeout> | null = null

/** Orca awaits this before stopping the worker: stop polling so nothing outlives it. */
export function deactivate(): void {
  if (timer) clearTimeout(timer)
  timer = null
}

async function settingsOf(orca: OrcaWorkerApi): Promise<{ url: string; token: string; every: number }> {
  const url = String((await orca.settings.get('url')) ?? 'http://127.0.0.1:7788').replace(/\/+$/, '')
  const token = String((await orca.settings.get('token')) ?? '')
  const every = Math.max(5, Number((await orca.settings.get('refreshSeconds')) ?? 15))
  return { url, token, every }
}

async function fetchFleet(orca: OrcaWorkerApi): Promise<FleetAnswer> {
  const { url, token } = await settingsOf(orca)
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (token) headers.Authorization = `Bearer ${token}`
  const controller = new AbortController()
  const abortTimer = setTimeout(() => controller.abort(), 12_000)
  try {
    const res = await fetch(`${url}/v1/fleet?timeout=4`, { headers, signal: controller.signal })
    if (!res.ok) {
      const hint = res.status === 401 || res.status === 403 ? " (set the hub token in this plugin's settings)" : ''
      return { fleet: null, error: `HTTP ${res.status}${hint}`, url, fetchedAt: Date.now() }
    }
    const fleet = (await res.json()) as Fleet
    return { fleet, error: null, url, fetchedAt: Date.now() }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return { fleet: null, error: `${msg} — is hwprobe-fleet running at ${url}?`, url, fetchedAt: Date.now() }
  } finally {
    clearTimeout(abortTimer)
  }
}

export default function activate(orca: OrcaWorkerApi): void {
  let last: FleetAnswer | null = null

  async function refresh(): Promise<FleetAnswer> {
    const answer = await fetchFleet(orca)
    // Keep the last good document so a hub blip does not blank the panel.
    if (answer.fleet || !last?.fleet) last = answer
    else last = { ...last, error: answer.error, fetchedAt: answer.fetchedAt }
    await orca.storage.set(LAST, last)
    orca.panels.post('fleet.changed', last)
    return last
  }

  async function schedule(): Promise<void> {
    if (timer) clearTimeout(timer)
    const { every } = await settingsOf(orca)
    timer = setTimeout(async () => {
      await refresh().catch(() => undefined)
      void schedule()
    }, every * 1000)
  }

  orca.panels.handle('fleet.get', async () => {
    if (last) return last
    const stored = (await orca.storage.get(LAST)) as FleetAnswer | undefined
    if (stored?.fleet) last = stored
    const answer = await refresh()
    void schedule()
    return answer
  })

  orca.panels.handle('fleet.refresh', async () => {
    const answer = await refresh()
    void schedule()
    return answer
  })

  orca.settings.onDidChange(() => {
    void refresh().then(() => schedule())
  })
}
