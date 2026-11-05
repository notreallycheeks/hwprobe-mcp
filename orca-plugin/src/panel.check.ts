// `orca plugin check` renders the panel once per scenario with the worker on a fresh host.
import { defineCheck } from 'orca/testing'

const node = {
  hostname: 'alpha',
  ip: '100.64.0.10',
  os: 'linux',
  self: true,
  status: 'ok',
  error: null,
  age_s: 3,
  stats: {
    cpu: { percent: 12 },
    memory: { percent: 18, used_gb: 11, total_gb: 62, available_gb: 51 },
    disks: { primary: { free_gb: 265, total_gb: 937, percent: 70 } },
    gpu: { gpus: [{ name: 'NVIDIA GeForce RTX 3080 Ti', utilization_percent: 4, memory_used_mb: 512, memory_total_mb: 16384 }] },
    host: { cpu_model: 'i7-12800H', uptime_s: 3600 }
  },
  claude_accounts: [{ email: 'me@example.com', subscription: 'max', session_percent: 76, weekly_percent: 28, scoped: { Fable: 20 }, error: null }]
}

export default defineCheck({
  fresh: {},
  populated: {
    storage: {
      'fleet.last': {
        fleet: { generated_at: 0, via: 'hub', summary: { total: 2, ok: 1 }, nodes: [node, { ...node, hostname: 'bravo', self: false, status: 'stale', error: 'last report 240s ago', age_s: 240 }] },
        error: null,
        url: 'http://127.0.0.1:7788',
        fetchedAt: Date.now()
      }
    }
  }
})
