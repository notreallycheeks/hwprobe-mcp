// Fleet panel: one card per host with CPU / RAM / disk / GPU meters and Claude usage.
import {
  atom,
  Badge,
  Button,
  Callout,
  Card,
  Heading,
  plugin,
  Progress,
  read,
  RelativeTime,
  Row,
  set,
  Stack,
  Text,
  type PanelContext
} from 'orca'
import type { FleetAnswer, FleetNode } from './protocol'

const answer = atom<FleetAnswer | null>(null)
const busy = atom(false)

async function refresh(): Promise<void> {
  set(busy, true)
  try {
    set(answer, await plugin.request('fleet.refresh'))
  } finally {
    set(busy, false)
  }
}

export function setup(ctx: PanelContext): void {
  ctx.subscriptions.push(plugin.on('fleet.changed', (payload) => set(answer, payload)))
  void plugin.request('fleet.get').then((a) => set(answer, a))
}

const pct = (v: number | null | undefined): number | undefined => (v == null ? undefined : Math.max(0, Math.min(100, v)))

function toneFor(status: FleetNode['status']): 'success' | 'warning' | 'danger' | 'neutral' {
  if (status === 'ok') return 'success'
  if (status === 'stale') return 'warning'
  if (status === 'offline') return 'neutral'
  return 'danger'
}

function Host({ n }: { n: FleetNode }) {
  const s = n.stats ?? {}
  const live = n.status === 'ok' || n.status === 'stale'
  const disk = s.disks?.primary ?? null
  const gpus = s.gpu?.gpus ?? []
  const accounts = n.claude_accounts ?? []
  return (
    <Card title={n.hostname} description={[n.os ?? '', n.ip ?? '', s.host?.cpu_model ?? ''].filter(Boolean).join(' · ')}>
      <Stack gap="xs">
        <Row justify="between">
          <Badge tone={toneFor(n.status)}>{n.status}</Badge>
          {n.age_s != null ? <Text muted size="xs">{Math.round(n.age_s)}s ago</Text> : null}
        </Row>
        {!live ? <Text tone="danger" size="xs">{n.error ?? n.status}</Text> : null}
        {live ? (
          <Stack gap="xs">
            <Progress label={`CPU ${s.cpu?.percent ?? '–'}%`} value={pct(s.cpu?.percent)} />
            <Progress label={`RAM ${s.memory?.used_gb ?? '–'}/${s.memory?.total_gb ?? '–'} GB`} value={pct(s.memory?.percent)} />
            <Progress label={disk ? `Disk ${disk.free_gb} GB free of ${disk.total_gb}` : 'Disk –'} value={pct(disk?.percent)} />
            {gpus.length === 0 ? (
              <Text muted size="xs">GPU: no telemetry</Text>
            ) : (
              gpus.map((g, i) => (
                <Progress
                  key={String(i)}
                  label={`${(g.name ?? 'GPU').replace('NVIDIA ', '').replace('GeForce ', '')} ${g.utilization_percent ?? '–'}%${g.memory_total_mb ? ` · ${g.memory_used_mb}/${g.memory_total_mb} MB` : ''}`}
                  value={pct(g.utilization_percent)}
                />
              ))
            )}
            {accounts.length === 0 ? (
              <Text muted size="xs">Claude: no account found</Text>
            ) : (
              accounts.map((a, i) => (
                <Stack key={String(i)} gap="xs">
                  <Text size="xs" weight="medium">
                    {a.email ?? 'orca'}
                    {a.subscription ? ` · ${a.subscription}` : ''}
                  </Text>
                  {a.error ? (
                    <Text tone="danger" size="xs">{a.error}</Text>
                  ) : (
                    <Row gap="xs" wrap>
                      <Progress label={`5h ${a.session_percent ?? '–'}%`} value={pct(a.session_percent)} />
                      <Progress label={`7d ${a.weekly_percent ?? '–'}%`} value={pct(a.weekly_percent)} />
                      {Object.entries(a.scoped ?? {}).map(([m, p]) =>
                        p == null ? null : <Progress key={m} label={`${m} ${p}%`} value={pct(p)} />
                      )}
                    </Row>
                  )}
                </Stack>
              ))
            )}
          </Stack>
        ) : null}
      </Stack>
    </Card>
  )
}

export default function Panel() {
  const a = read(answer)
  const loading = read(busy)
  const fleet = a?.fleet ?? null
  return (
    <Stack gap="md">
      <Row justify="between">
        <Heading>Fleet</Heading>
        <Button variant="ghost" icon="refresh" disabled={loading} onPress={refresh}>
          Refresh
        </Button>
      </Row>
      {a === null ? <Text muted>Loading…</Text> : null}
      {a?.error ? <Callout tone={fleet ? 'warning' : 'danger'} title={fleet ? 'Last fetch failed' : 'Cannot reach the fleet'}>{a.error}</Callout> : null}
      {fleet ? (
        <Text muted size="xs">
          {fleet.summary.ok}/{fleet.summary.total} hosts reporting via {fleet.via ?? 'peers'} · <RelativeTime at={a!.fetchedAt} />
        </Text>
      ) : null}
      {fleet ? fleet.nodes.map((n) => <Host key={n.hostname} n={n} />) : null}
    </Stack>
  )
}
