// The panel <-> worker contract. Both src/panel.tsx and src/worker.ts read it.
export {}

export type ClaudeAccount = {
  email: string | null
  subscription: string | null
  session_percent: number | null
  weekly_percent: number | null
  scoped: Record<string, number | null>
  error: string | null
}

export type FleetNode = {
  hostname: string
  ip: string | null
  os: string | null
  self: boolean
  status: 'ok' | 'stale' | 'lost' | 'no-agent' | 'unreachable' | 'offline' | 'error'
  error: string | null
  age_s: number | null
  stats: {
    cpu?: { percent: number | null }
    memory?: { percent: number | null; used_gb: number | null; total_gb: number | null; available_gb: number | null }
    disks?: { primary?: { free_gb: number; total_gb: number; percent: number } | null }
    gpu?: { gpus: { name: string | null; utilization_percent: number | null; memory_used_mb: number | null; memory_total_mb: number | null }[] }
    host?: { cpu_model?: string | null; uptime_s?: number | null }
  } | null
  claude_accounts?: ClaudeAccount[]
}

export type Fleet = {
  generated_at: number
  via?: string
  from_host?: string
  summary: { total: number; ok: number }
  nodes: FleetNode[]
}

export type FleetAnswer = { fleet: Fleet | null; error: string | null; url: string; fetchedAt: number }

declare module 'orca' {
  interface PluginRequests {
    /** The last fleet document the worker fetched (fetching now if it has none). */
    'fleet.get': { output: FleetAnswer }
    /** Fetch again right now and answer the result. */
    'fleet.refresh': { output: FleetAnswer }
  }
  interface PluginPanelEvents {
    /** Posted to every open panel after each fetch. */
    'fleet.changed': FleetAnswer
  }
}
