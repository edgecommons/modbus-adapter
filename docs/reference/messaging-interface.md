# Reference — Messaging Interface & CLI

Every topic and message the adapter publishes or accepts, and the CLI flags. Addressing follows the
**Unified Namespace (UNS)**: `ecv1/{device}/{component}/{instance}/{class}[/channel]`. For the
data/control plane model, see [explanation.md](../explanation.md); for client recipes, the
[how-to guides](../how-to-guides.md).

- `{device}` — the resolved Thing name (the last `hierarchy` level).
- `{component}` — the sanitized component short name, `ModbusAdapter`.
- `{instance}` — a device instance id (`plc1`, …) for `data`/`evt`; `main` for the shared command
  inbox, the `state` keepalive, and `metric`.

## Envelope

All messages use the GGCommons JSON envelope — since the UNS change, `{header, identity, tags, body}`.
The library stamps the top-level **`identity`** (`{hier, path, component, instance}`) on every message
built from config; the former `tags.thing` is removed. `tags` is arbitrary business metadata.
Request/reply carries `header.reply_to` + `header.correlation_id`; the reply is published to
`reply_to` with the same `correlation_id`.

```jsonc
"identity": {
  "hier": [ { "level": "site", "value": "lab" }, { "level": "shop", "value": "s1" },
            { "level": "line", "value": "l1" }, { "level": "device", "value": "gw-01" } ],
  "path": "lab/s1/l1/gw-01", "component": "ModbusAdapter", "instance": "plc1"
}
```

## Topics

| Class | Message | Direction | Topic | Reply |
|-------|---------|-----------|-------|-------|
| `data` | `SouthboundSignalUpdate` | adapter → bus | `ecv1/{device}/ModbusAdapter/{instance}/data/{signal}` | — |
| `evt` | `SouthboundEvent` | adapter → bus | `ecv1/{device}/ModbusAdapter/{instance}/evt/{connection\|write}` | — |
| `cmd` | `sb/read` | bus → adapter | `ecv1/{device}/ModbusAdapter/main/cmd/sb/read` | `{ok,result}` |
| `cmd` | `sb/write` | bus → adapter | `ecv1/{device}/ModbusAdapter/main/cmd/sb/write` | `{ok,result}` |
| `cmd` | `sb/status` | bus → adapter | `ecv1/{device}/ModbusAdapter/main/cmd/sb/status` | `{ok,result}` |
| `cmd` | `sb/signals` | bus → adapter | `ecv1/{device}/ModbusAdapter/main/cmd/sb/signals` | `{ok,result}` |
| `cmd` | `reconnect` | bus → adapter | `ecv1/{device}/ModbusAdapter/main/cmd/reconnect` | `{ok,result}` |
| `cmd` | `repoll` | bus → adapter | `ecv1/{device}/ModbusAdapter/main/cmd/repoll` | `{ok,result}` |
| `metric` | `southbound_health` | adapter → bus (auto) | `ecv1/{device}/ModbusAdapter/main/metric/southbound_health` | — |
| `state` | keepalive | adapter → bus (auto) | `ecv1/{device}/ModbusAdapter/main/state` | — |

Fleet consumers subscribe the six UNS wildcards — telemetry is one filter,
`ecv1/+/+/+/data/#`; events `ecv1/+/+/+/evt/#`; metrics `ecv1/+/+/+/metric/#`; state
`ecv1/+/+/+/state`. `state`/`metric`/`cfg`/`log` are library-owned **reserved** classes — a component
publish to them is rejected; the adapter only ever mints `data`/`evt`/`cmd` topics via the UNS builder.

## The command inbox

The read/write/control surface is served through the library's **command inbox** — a single
subscription `ecv1/{device}/ModbusAdapter/main/cmd/#` (the shared `main` instance; per-instance inboxes
are a later UNS phase). A request's **verb** is the topic channel after `cmd/` and must equal
`header.name`. Built-in verbs (`ping`, `reload-config`, `get-configuration`) ship with every component;
the adapter adds the `sb/*` + `reconnect`/`repoll` verbs below.

Because the inbox is `main`-only, a multi-instance adapter selects the target device with an
**`instance`** field in the request body (optional when only one device is configured). The reply body
is `{"ok": true, "result": <verb result>}` on success, or
`{"ok": false, "error": {"code", "message"}}` on failure (e.g. `WRITE_DISABLED`, `INSTANCE_NOT_FOUND`,
`RECONNECT_FAILED`).

## Sample object

| Field | Type | Notes |
|-------|------|-------|
| `value` | number \| boolean \| string | Per the signal's type (see [data-types.md](data-types.md)). |
| `quality` | string | Normalized `GOOD` \| `BAD` \| `UNCERTAIN`. |
| `qualityRaw` | string | `Good`, or the Modbus exception / timeout text on failure. |
| `sourceTs` | null | Modbus has no device timestamp. |
| `serverTs` | string | Adapter read time, ISO-8601 UTC. |

## Data plane

### `SouthboundSignalUpdate` (adapter → bus, `data` class)

Topic `ecv1/{device}/ModbusAdapter/{instance}/data/{signal}` — `{signal}` is the sanitized signal
name. The stable `signal.id` and protocol-native `signal.address` stay in the body (consumers key on
those, not the topic channel).

```jsonc
"body": {
  "device": { "adapter": "modbus", "instance": "plc1", "endpoint": "tcp://10.0.0.50:502 unit=1" },
  "signal": {
    "id": "u1/holding/0/float32",
    "name": "Temperature",
    "address": { "unitId": 1, "table": "holding", "address": 0, "type": "float32", "wordOrder": "big", "byteOrder": "big" }
  },
  "samples": [ { "value": 21.4, "quality": "GOOD", "qualityRaw": "Good", "sourceTs": null, "serverTs": "2026-07-03T01:48:00Z" } ]
}
```

Published when a polled value changes (`publishMode: onChange`, gated by the signal's `deadband`) or
every poll (`always`). One message carries one signal's `samples` (one, or many when
`publish.batchMs > 0`).

### `sb/write` (command)

Requires `write.enabled: true` (else the reply is a `WRITE_DISABLED` error). Body:

```jsonc
"body": { "instance": "plc1", "writes": [ { "name": "Setpoint", "value": 42.5 } ] }
// result: { "id": "plc1", "written": 1, "results": [ { "signal": "Setpoint", "value": 42.5, "ok": true } ] }
```

A single `{name,value}` object (no `writes` array) is also accepted. A **signal-ref** is either
`{ "name": "<configured signal>" }` (friendly; uses that signal's table/type/order) or explicit
`{ "unitId"?, "table", "address", "type", "wordOrder"?, "byteOrder"?, "scale"?, "offset"?, "count"? }`.
Entries without `value`, an unresolvable ref, a read-only table (`discrete`/`input`), or a `bit` signal
are reported per-entry as `{"ok": false, "error": …}`. Each write also emits an `evt/write` audit
event. Writes use FC5/FC15 (coil), FC6/FC16 (holding).

### `sb/read` (command, request/reply)

```jsonc
// request body
"body": { "instance": "plc1", "signals": [ { "name": "Temperature" }, { "unitId": 1, "table": "input", "address": 0, "type": "uint16" } ] }
// reply body: { "ok": true, "result": { "id": "plc1", "reads": [
//   { "signal": { "id": "...", "address": {...} }, "value": 21.4, "quality": "GOOD", "qualityRaw": "Good", "sourceTs": null, "serverTs": "..." } ] } }
```

Unresolvable refs are omitted (match by `signal`). A signal that errors returns an entry with
`quality: BAD` and the exception in `qualityRaw`.

## Control plane

- **`sb/status`** → `result = { "id", "connected", "metrics": { "read": {interval,total}, "write": {interval,total} } }`.
- **`sb/signals`** → `result = { "id", "signals": [ { "name", "unitId", "signalId", "address" }, ... ] }` — the configured/polled signals.
- **`reconnect`** (body `{instance}`) → drops and re-establishes the Modbus link (one bounded attempt); `result = { "id", "connected" }` or a `RECONNECT_FAILED` error.
- **`repoll`** (body `{instance}`) → forces one immediate poll cycle; `result = { "id", "polled": <groups> }`.

## Events (`evt` class)

- **`evt/connection`** — a Modbus link up/down transition per instance (`{instance, connected, endpoint}`), so a console can raise a device-offline alert immediately instead of waiting for the next metric tick.
- **`evt/write`** — a per-write audit record (`{instance, signal, value, ok, error, serverTs}`) for command-review.

### `southbound_health` (metric, reserved class — automatic)

The metric subsystem publishes it on the reserved `metric` class
(`ecv1/{device}/ModbusAdapter/main/metric/southbound_health`) — the component never addresses that
topic itself.

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
| `-t/--thing` | `<name>` | IoT Thing name; the `{device}` token of every UNS topic. |
