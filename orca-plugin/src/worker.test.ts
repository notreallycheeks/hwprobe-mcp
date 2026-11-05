// Runs src/worker.ts's activate(orca) over a mock host (`orca plugin test orca-plugin`).
// `fetch` is stubbed so the test never touches the network.
import { assert, mockHost, mountWorker, test } from 'orca/testing'
import * as main from './worker'

const fleetDoc = { generated_at: 1, via: 'hub', summary: { total: 1, ok: 1 }, nodes: [{ hostname: 'alpha', status: 'ok', stats: {} }] }
const realFetch = globalThis.fetch

test('fleet.get fetches /v1/fleet with the token from settings and posts fleet.changed', async () => {
  const calls: { url: string; auth: string | undefined }[] = []
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    const headers = new Headers(init?.headers)
    calls.push({ url: String(input), auth: headers.get('authorization') ?? undefined })
    return new Response(JSON.stringify(fleetDoc), { status: 200, headers: { 'content-type': 'application/json' } })
  }) as typeof fetch
  try {
    const host = mockHost({ settings: { url: 'http://hub.test:7790/', token: 'secret', refreshSeconds: 60 } })
    const worker = await mountWorker(main, { host })
    const answer = await worker.request('fleet.get')
    assert.equal(answer.error, null)
    assert.equal(answer.url, 'http://hub.test:7790')
    assert.deepEqual(answer.fleet, fleetDoc)
    assert.deepEqual(calls, [{ url: 'http://hub.test:7790/v1/fleet?timeout=4', auth: 'Bearer secret' }])
    assert.deepEqual(worker.posts.map((p) => p.event), ['fleet.changed'])
    assert.deepEqual(host.data().storage['fleet.last'], answer)
    assert.deepEqual(worker.refusals, [])
    await worker.deactivate()
  } finally {
    globalThis.fetch = realFetch
  }
})

test('fleet.get reports an error (and no document) when the fleet cannot be reached', async () => {
  globalThis.fetch = (async () => {
    throw new Error('connect ECONNREFUSED')
  }) as typeof fetch
  try {
    const host = mockHost({ settings: { url: 'http://127.0.0.1:1', token: '', refreshSeconds: 60 } })
    const worker = await mountWorker(main, { host })
    const answer = await worker.request('fleet.get')
    assert.equal(answer.fleet, null)
    assert.ok(String(answer.error).includes('ECONNREFUSED'))
    await worker.deactivate()
  } finally {
    globalThis.fetch = realFetch
  }
})
