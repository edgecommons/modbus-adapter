# Reference — Configuration

Every configuration option. For *why* these exist, see [explanation.md](../explanation.md); for tasks,
see the [how-to guides](../how-to-guides.md); for the type system, see [data-types.md](data-types.md).

## Config source

The adapter reads one JSON document from `-c/--config`, defaulting by platform: `HOST` → `FILE`,
`GREENGRASS` → `GG_CONFIG`, `KUBERNETES` → `CONFIGMAP`. Adapter settings live under `component`; the
sibling sections (`tags`, `hierarchy`, `identity`, `topic`, `messaging`, `logging`, `metricEmission`,
`heartbeat`) are standard ggcommons sections.

## Top-level sections

| Section | Required | Purpose |
|---------|----------|---------|
| `component` | yes | Adapter instances and global defaults (this document). |
| `tags` | recommended | Business metadata attached to every message's `tags`. |
| `hierarchy` | optional | UNS enterprise-hierarchy level names; last level is the device (thing). Absent ⇒ `["device"]`. |
| `identity` | optional | Values for every hierarchy level except the last (which is the resolved thing name). |
| `topic` | optional | `includeRoot` (default `false`) — insert the site level after `ecv1` on a multi-site broker. |
| `messaging` | HOST/KUBERNETES | MQTT broker connection (or `--transport MQTT <file>`). |
| `metricEmission` | optional | Routes `southbound_health` (`target`: `log`/`messaging`/`cloudwatch`/`prometheus`). `messaging` auto-routes to the UNS `metric` class. |
| `logging`, `heartbeat` | optional | Standard ggcommons sections. |

UNS topics are `ecv1/{device}/{component}/{instance}/{class}[/channel]` — built and validated by the
library from the identity above; there are no per-instance/per-signal topic templates.

## `component.global.defaults`

| Key | Type | Default | Definition |
|-----|------|---------|-----------|
| `pollIntervalMs` | number | `1000` | Fallback poll interval for a group. |
| `publishMode` | string | `onChange` | `onChange` (publish when the value changes past its deadband) or `always` (every poll). |
| `batchMs` | number | `0` | If `>0`, buffer a signal's samples and publish one message per `batchMs`; `0` = publish each immediately. |
| `maxGap` | number | `0` | Max address gap the poller will bridge when coalescing signals into one Modbus read. |

## `component.instances[]`

| Key | Type | Definition |
|-----|------|-----------|
| `id` | string | Stable instance id; the `{instance}` token of the `data`/`evt` topics and `device.instance`. |
| `adapter` | string | Informational; echoed as `device.adapter` (`"modbus"`). |
| `connection` | object | Transport + endpoint (below). |
| `defaults` | object | Per-instance overrides of `global.defaults`. |
| `publish` | object | `batchMs` (buffer window). |
| `write` | object | `enabled` (default `false`) — whether the `sb/write` verb accepts writes for this instance. |
| `pollGroups` | array | Groups of signals polled together (below). |

### `connection`

| Key | Type | Default | Definition |
|-----|------|---------|-----------|
| `transport` | string | `tcp` | `tcp`, `rtu` (serial), or `rtutcp` (RTU framing over TCP). |
| `host` | string | `127.0.0.1` | TCP / RTU-over-TCP host. |
| `port` | number | `502` | TCP / RTU-over-TCP port. |
| `unitId` | number | `1` | Default Modbus unit/slave id (overridable per poll group / signal-ref). |
| `timeoutMs` | number | `1000` | Request timeout. |
| `serialPort` | string | — | RTU only, e.g. `COM3` / `/dev/ttyUSB0`. |
| `baudRate`, `parity`, `stopBits`, `byteSize` | — | `9600`, `N`, `1`, `8` | RTU serial line settings. |

### `pollGroups[]`

| Key | Type | Default | Definition |
|-----|------|---------|-----------|
| `id` | string | random | Group id (logs + the `sb/signals` control query). |
| `pollIntervalMs` | number | instance default | How often this group is read. |
| `unitId` | number | connection `unitId` | Modbus unit id for this group's reads. |
| `publishMode` | string | instance default | `onChange` / `always`. |
| `maxGap` | number | instance default | Coalescing gap (registers/bits). |
| `signals` | array | `[]` | The signals (below). |

### Signal (entries of `pollGroups[].signals`)

| Key | Type | Default | Definition |
|-----|------|---------|-----------|
| `name` | string | **required** | Human name; the `data` channel token (sanitized) and the friendly write/read ref. |
| `table` | string | **required** | `coil` / `discrete` / `holding` / `input`. |
| `address` | number | **required** | 0-based PDU register/bit address. |
| `type` | string | `uint16` (bool for bit tables) | See [data-types.md](data-types.md). |
| `count` | number | — | Registers for `string`. |
| `wordOrder` / `byteOrder` | string | `big` / `big` | Multi-register order (see data-types). |
| `bit` | number | — | Extract one bit (0–15) of a holding/input register as a bool. |
| `scale` / `offset` | number | — | Linear transform on numeric values. |
| `deadband` | object | `{type:"none"}` | `type`: `none`/`absolute`/`percent`; `value`: number. Gates `onChange` publishing. |

## Identity & the UNS device tree

`hierarchy.levels` names the enterprise tree, deepest (the device) last; `identity` supplies every
level's value **except** the last (the last is always the resolved thing name). The values become the
envelope `identity.hier`/`path`. With the default (`["device"]`) topics are
`ecv1/{thing}/ModbusAdapter/{instance}/...`; `topic.includeRoot: true` (multi-site broker) prepends the
first level (site) after `ecv1`.

```jsonc
"hierarchy": { "levels": ["site", "shop", "line", "device"] },
"identity":  { "site": "plant1", "shop": "assembly", "line": "5" }
// -> identity.path = "plant1/assembly/5/<thing>", topics device token = <thing>
```

## Precedence

`pollIntervalMs` / `publishMode` / `maxGap` resolve: **signal/group value ▸ instance `defaults` ▸
`global.defaults` ▸ built-in**.

## Complete example

```jsonc
{
  "tags": { "appId": "line5" },
  "hierarchy": { "levels": ["site", "shop", "line", "device"] },
  "identity": { "site": "plant1", "shop": "assembly", "line": "5" },
  "messaging": { "local": { "type": "mqtt", "host": "localhost", "port": 1883 } },
  "metricEmission": { "target": "messaging" },
  "component": {
    "global": { "defaults": { "pollIntervalMs": 1000, "publishMode": "onChange", "maxGap": 8 } },
    "instances": [
      {
        "id": "plc1",
        "connection": { "transport": "tcp", "host": "10.0.0.50", "port": 502, "unitId": 1 },
        "publish": { "batchMs": 0 },
        "write":   { "enabled": true },
        "pollGroups": [
          { "id": "fast", "pollIntervalMs": 500,
            "signals": [
              { "name": "Temperature", "table": "holding", "address": 0, "type": "float32", "scale": 0.1,
                "deadband": { "type": "absolute", "value": 0.2 } },
              { "name": "Setpoint", "table": "holding", "address": 2, "type": "float32" },
              { "name": "RunCmd",  "table": "coil",   "address": 0, "type": "bool" },
              { "name": "Alarm3",  "table": "holding","address": 10, "type": "bool", "bit": 3 }
            ] }
        ]
      }
    ]
  }
}
```

## Accepted but not implemented

- **Single-bit writes** (a `bit` signal) require a read-modify-write and are skipped with a warning.
- **Modbus security** (Modbus/TLS): not supported — classic Modbus is plaintext; secure the network
  instead (there is no credential/cert handling).
