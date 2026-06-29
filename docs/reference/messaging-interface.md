# Reference — Messaging Interface & CLI

Every topic and message the adapter publishes or accepts, and the CLI flags. For the data/control
plane model, see [explanation.md](../explanation.md); for client recipes, the
[how-to guides](../how-to-guides.md).

## Envelope

All messages use the GGCommons JSON envelope (`header` + `tags` + `body`). For messages the adapter
*consumes* (write/read/control), the **topic** selects the action; `header.name` is not validated and
a bare body object is accepted. Replies are full envelopes. Request/reply sets `header.reply_to` +
`header.correlation_id`; the adapter echoes the `correlation_id` to `reply_to`.

## Topics

| Plane | Message | Direction | Topic (default) | Reply |
|-------|---------|-----------|-----------------|-------|
| data | `SouthboundTagUpdate` | adapter → bus | `southbound/{site}/{ComponentName}/{InstanceId}/{tagId}` | — |
| data | write | bus → adapter | `southbound/{ComponentName}/{InstanceId}/write` | — |
| data | read | bus ↔ adapter | `southbound/{ComponentName}/{InstanceId}/read` | `SouthboundReadResult` |
| control | status | bus ↔ adapter | `southbound/{ComponentName}/{InstanceId}/control/status` | `status` |
| control | tags | bus ↔ adapter | `southbound/{ComponentName}/{InstanceId}/control/tags` | `tags` |
| control | `southbound_health` | adapter → metric target | per `metricEmission` | — |

## Sample object

| Field | Type | Notes |
|-------|------|-------|
| `value` | number \| boolean \| string | Per the tag's type (see [data-types.md](data-types.md)). |
| `quality` | string | Normalized `GOOD` \| `BAD` \| `UNCERTAIN`. |
| `qualityRaw` | string | `Good`, or the Modbus exception / timeout text on failure. |
| `sourceTs` | null | Modbus has no device timestamp. |
| `serverTs` | string | Adapter read time, ISO-8601 UTC. |

## Data plane

### `SouthboundTagUpdate` (adapter → bus)

```jsonc
"body": {
  "device": { "adapter": "modbus", "instance": "plc1", "endpoint": "tcp://10.0.0.50:502 unit=1" },
  "tag": {
    "id": "u1/holding/0/float32",
    "name": "Temperature",
    "address": { "unitId": 1, "table": "holding", "address": 0, "type": "float32", "wordOrder": "big", "byteOrder": "big" }
  },
  "samples": [ { "value": 21.4, "quality": "GOOD", "qualityRaw": "Good", "sourceTs": null, "serverTs": "2026-06-29T01:48:00Z" } ]
}
```

Published when a polled value changes (`publishMode: onChange`, gated by the tag's `deadband`) or every
poll (`always`). One message carries one tag's `samples` (one, or many when `publish.batchMs > 0`).

### write (bus → adapter)

Requires `write.enabled: true`. Fire-and-forget. A single object (no `writes` array) is also accepted.

```jsonc
"body": { "writes": [ { "name": "Setpoint", "value": 42.5 }, { "ns?": "...", "value": ... } ] }
```

A **tag-ref** is either `{ "name": "<configured tag>" }` (friendly; uses that tag's table/type/order)
or explicit `{ "unitId"?, "table", "address", "type", "wordOrder"?, "byteOrder"?, "scale"?, "offset"?, "count"? }`.
Entries without `value`, an unresolvable ref, a read-only table (`discrete`/`input`), or a `bit` tag
are skipped with a warning. Writes use FC5/FC15 (coil), FC6/FC16 (holding).

### read (request/reply)

```jsonc
// request body
"body": { "tags": [ { "name": "Temperature" }, { "unitId": 1, "table": "input", "address": 0, "type": "uint16" } ] }
// reply: header.name = "SouthboundReadResult"
"body": { "id": "plc1", "reads": [ { "tag": { "id": "...", "address": {...} },
            "value": 21.4, "quality": "GOOD", "qualityRaw": "Good", "sourceTs": null, "serverTs": "..." } ] }
```

`reads[i]` corresponds to `tags[i]`; unresolvable refs are omitted (match by `tag`). A node that errors
returns an entry with `quality: BAD` and the exception in `qualityRaw`.

## Control plane

- **status** (`…/control/status`) → `{ "id", "connected", "metrics": { "read": {interval,total}, "write": {interval,total} } }`.
- **tags** (`…/control/tags`) → `{ "id", "tags": [ { "name", "unitId", "tagId", "address" }, ... ] }` — the configured/polled tags.

### `southbound_health` (metric)

| Measure | Unit | Meaning |
|---------|------|---------|
| `connectionState` | Count | `1` connected, `0` down |
| `readErrors` | Count | read errors over the interval |

Dimension: `instance` (plus auto `coreName`/`component`).

## CLI

| Flag | Values | Notes |
|------|--------|-------|
| `--platform` | `GREENGRASS` \| `HOST` \| `KUBERNETES` \| `auto` | Default `auto`. |
| `--transport` | `MQTT [path]` \| `IPC` | HOST/K8s use MQTT; the path is the messaging config. |
| `-c/--config` | `FILE <path>` \| `ENV` \| `GG_CONFIG` \| `CONFIGMAP` \| … | Default from the platform. |
| `-t/--thing` | `<name>` | IoT Thing name; also `{ThingName}` in topics. |
