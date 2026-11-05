// Mounts the panel with a stand-in worker (`orca plugin test orca-plugin`).
import { assert, mockHost, mount, test } from 'orca/testing'
import * as panel from './panel'

const fleet = {
  generated_at: 0,
  via: 'hub',
  summary: { total: 2, ok: 1 },
  nodes: [
    {
      hostname: 'alpha', ip: '100.64.0.10', os: 'linux', self: true, status: 'ok', error: null, age_s: 2,
      stats: { cpu: { percent: 12 }, memory: { percent: 18, used_gb: 11, total_gb: 62, available_gb: 51 }, disks: { primary: { free_gb: 265, total_gb: 937, percent: 70 } }, gpu: { gpus: [] }, host: {} },
      claude_accounts: [{ email: 'me@example.com', subscription: 'max', session_percent: 76, weekly_percent: 28, scoped: { Fable: 20 }, error: null }]
    },
    { hostname: 'bravo', ip: '100.64.0.11', os: 'windows', self: false, status: 'lost', error: 'no report for 20 min', age_s: 1200, stats: null, claude_accounts: [] }
  ]
}

test('renders one card per host with meters and Claude usage', async () => {
  const answer = { fleet, error: null, url: 'http://127.0.0.1:7790', fetchedAt: Date.now() }
  const host = mockHost({ requests: { 'fleet.get': () => answer, 'fleet.refresh': () => answer } })
  const view = await mount(panel, { host })
  try {
    await view.settled()
    const drawn = JSON.stringify(view.tree())
    for (const needle of ['"alpha"', '"bravo"', 'no report for 20 min', 'me@example.com', 'CPU 12%', '5h 76%', 'Fable 20%']) {
      assert.ok(drawn.includes(needle), `expected the panel to show ${needle}`)
    }
    assert.deepEqual(view.errors, [])
    assert.deepEqual(view.refusals, [])
  } finally {
    await view.unmount()
  }
})
