# Reference — Messaging Interface & CLI

Every topic and message the adapter publishes or accepts, and the CLI flags. Addressing follows the
**Unified Namespace (UNS)**: `ecv1/{device}/{component}/{instance}/{class}[/channel]`. For the
data/control plane model, see [explanation.md](../explanation.md); for client recipes, the
[how-to guides](../how-to-guides.md).

- `{device}` — the resolved Thing name (the last `hierarchy` level).
- `{component}` — the component UNS token, `modbus-adapter`.
- `{instance}` — a device instance id (`plc1`, …) for `data`/`evt`; `main` for the shared command
  inbox, the `state` keepalive, and `metric`.

## Envelope

All messages use the EdgeCommons JSON envelope: `{header, identity, tags, body}`.
The library stamps the top-level **`identity`** (`{hier, path, component, instance}`) on every message
built from config. `tags` is arbitrary business metadata.
Request/reply carries `header.reply_to` + `header.correlation_id`; the reply is published to
`reply_to` with the same `correlation_id`.

```jsonc
"identity": {
  "hier": [ { "level": "site", "value": "lab" }, { "level": "shop", "value": "s1" },
            { "level": "line", "value": "l1" }, { "level": "device", "value": "gw-01" } ],
  "path": "lab/s1/l1/gw-01", "component": "modbus-adapter", "instance": "plc1"
}
```

## Topics

| Class | Message | Direction | Topic | Reply |
|-------|---------|-----------|-------|-------|
| `data` | `SouthboundSignalUpdate` | adapter → bus | `ecv1/{device}/modbus-adapter/{instance}/data/{signal}` | — |
| `evt` | `evt` | adapter → bus | `ecv1/{device}/modbus-adapter/{instance}/evt/{severity}/{connection\|write}` | — |
| `cmd` | `sb/read` | bus → adapter | `ecv1/{device}/modbus-adapter/main/cmd/sb/read` | `{ok,result}` |
| `cmd` | `sb/write` | bus → adapter | `ecv1/{device}/modbus-adapter/main/cmd/sb/write` | `{ok,result}` |
| `cmd` | `sb/status` | bus → adapter | `ecv1/{device}/modbus-adapter/main/cmd/sb/status` | `{ok,result}` |
| `cmd` | `sb/signals` | bus → adapter | `ecv1/{device}/modbus-adapter/main/cmd/sb/signals` | `{ok,result}` |
| `cmd` | `reconnect` | bus → adapter | `ecv1/{device}/modbus-adapter/main/cmd/reconnect` | `{ok,result}` |
| `cmd` | `repoll` | bus → adapter | `ecv1/{device}/modbus-adapter/main/cmd/repoll` | `{ok,result}` |
| `metric` | `southbound_health` | adapter → bus (auto) | `ecv1/{device}/modbus-adapter/main/metric/southbound_health` | — |
| `state` | keepalive | adapter → bus (auto) | `ecv1/{device}/modbus-adapter/main/state` | — |

Fleet consumers subscribe the six UNS wildcards — telemetry is one filter,
`ecv1/+/+/+/data/#`; events `ecv1/+/+/+/evt/#`; metrics `ecv1/+/+/+/metric/#`; state
`ecv1/+/+/+/state`. `state`/`metric`/`cfg`/`log` are library-owned **reserved** classes — a component
publish to them is rejected; the adapter only ever mints `data`/`evt` topics via the `data()`/`events()`
facades and `cmd` replies via the command inbox — never a hand-assembled topic string.

## The command inbox

The read/write/control surface is served through the library's **command inbox** — a single
subscription `ecv1/{device}/modbus-adapter/main/cmd/#` (the shared `main` instance; there are no
per-instance inboxes). A request's **verb** is the topic channel after `cmd/` and must equal
`header.name`. Built-in verbs (`ping`, `reload-config`, `get-configuration`) ship with every component;
the adapter adds the `sb/*` + `reconnect`/`repoll` verbs below.

Because the inbox is `main`-only, a multi-instance adapter selects the target device with an
**`instance`** field in the request body (optional when only one device is configured). The reply body
is `{"ok": true, "result": <verb result>}` on success, or
`{"ok": false, "error": {"code", "message"}}` on failure (e.g. `WRITE_DISABLED`, `INSTANCE_NOT_FOUND`,
`RECONNECT_FAILED`).

## Sample object

The `sb/read` reply's `reads[]` entries (below) always carry all five fields explicitly:

| Field | Type | Notes |
|-------|------|-------|
| `value` | number \| boolean \| string | Per the signal's type (see [data-types.md](data-types.md)). |
| `quality` | string | Normalized `GOOD` \| `BAD` \| `UNCERTAIN`. |
| `qualityRaw` | string | `Good`, or the Modbus exception / timeout text on failure. |
| `sourceTs` | null | Modbus has no device timestamp. |
| `serverTs` | string | Adapter read time, ISO-8601 UTC. |

`data`-class samples (below) go through the `data()` facade instead, which **omits** a field rather
than emitting it `null`, and defaults an omitted `quality` to `GOOD` with `qualityRaw: "unspecified"`
(Modbus has no native quality codes) rather than the literal string `"Good"`.

## Data plane

### `SouthboundSignalUpdate` (adapter → bus, `data` class)

Published through the library's `data()` facade (`gg.instance(id).data()`), which constructs the body, sanitizes the channel, mints
the topic, and stamps the envelope identity — the adapter only ever calls
`.signal(id).name(n).address(a).device(...).add_samples(...).signal_path(p).publish()`. Topic
`ecv1/{device}/modbus-adapter/{instance}/data/{signal}` — `{signal}` is the sanitized signal name. The
stable `signal.id` and protocol-native `signal.address` stay in the body (consumers key on those, not
the topic channel). Quality has no Modbus-native meaning, so a successful read omits it and the facade
defaults it to `GOOD` with `qualityRaw: "unspecified"` (a synthesized-vs-device-reported marker); a
failed read passes an explicit `BAD` with the exception text as `qualityRaw`.

```jsonc
"body": {
  "device": { "adapter": "modbus", "instance": "plc1", "endpoint": "tcp://10.0.0.50:502 unit=1" },
  "signal": {
    "id": "u1/holding/0/float32",
    "name": "Temperature",
    "address": { "unitId": 1, "table": "holding", "address": 0, "type": "float32", "wordOrder": "big", "byteOrder": "big" }
  },
  "samples": [ { "value": 21.4, "quality": "GOOD", "qualityRaw": "unspecified", "serverTs": "2026-07-03T01:48:00Z" } ]
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
are reported per-entry as `{"ok": false, "error": …}`. Each write also emits an
`evt/info/write`/`evt/warning/write` audit event. Writes use FC5/FC15 (coil), FC6/FC16 (holding).

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

Published through the library's `events()` facade (`gg.instance(id).events()`): severity **derives** the channel
`evt/{severity}/{type}`, so the topic and the body can never disagree — identical in shape to the
OPC UA reference adapter.

```jsonc
"body": {
  "severity": "critical", "type": "connection", "message": "Modbus link down",
  "timestamp": "2026-07-03T01:48:00Z", "context": { "endpoint": "tcp://10.0.0.50:502 unit=1" },
  "alarm": true, "active": true
}
```

- **`evt/critical/connection`** — a Modbus link up/down transition per instance, modeled as a
  stateful alarm: `raise_alarm("connection", ...)` on drop (`alarm:true, active:true`),
  `clear_alarm("connection", ...)` on restore (`active:false`) — both ride the *same*
  `evt/critical/connection` channel, so a console tracking `evt/critical/#` sees both ends. Context
  carries `{endpoint}` (the connection description, e.g. slave address).
- **`evt/info/write`** / **`evt/warning/write`** — a per-write audit record, `info` on success and
  `warning` on failure — `emit("write", message, {signal, value, error?}, severity)`.

A fleet consumer subscribing `ecv1/+/+/+/evt/critical/#` sees only alarm-grade events without
per-adapter knowledge of the channel shape.

### `southbound_health` (metric, reserved class — automatic)

The metric subsystem publishes it on the reserved `metric` class
(`ecv1/{device}/modbus-adapter/main/metric/southbound_health`) — the component never addresses that
topic itself.

| Measure | Unit | Meaning |
|---------|------|---------|
| `connectionState` | Count | `1` connected, `0` down |
| `readErrors` | Count | read errors over the interval |

Dimension: `instance` (plus auto `coreName`/`component`).

## State keepalive (`state` class, reserved — automatic)

The library's heartbeat publishes the `state` keepalive on the reserved `state` class
(`ecv1/{device}/modbus-adapter/main/state`) every ~5 s — the component never addresses that topic
itself. The RUNNING keepalive also carries an **`instances`** array: one entry per configured slave
(`component.instances[]`), so a fleet consumer sees every slave's up/down state under the one component
without a separate UNS instance per slave (identity, data, and lifecycle stay under `main`).

```jsonc
"body": {
  "status": "RUNNING",
  "uptimeSecs": 3600,
  "instances": [
    { "instance": "plc1", "connected": true,  "detail": "tcp://10.0.0.50:502 unit=1" },
    { "instance": "plc2", "connected": false }
  ]
}
```

- `connected` — **live liveness**, driven by the poll reads themselves: any response that arrives (data,
  or even a slave exception for e.g. an illegal address) marks the link up; a transport/IO error, a
  `ModbusIOException`, or no response marks it down. It is *not* pymodbus's cached `client.connected`
  (which reflects intent and lags a socket that died mid-session), so a mid-session southbound loss shows
  up promptly as `connected: false` on the next keepalive.
- `detail` — the connection describe string (`tcp://host:port unit=N` / `rtu://COM@baud unit=N`); omitted
  before that slave's device has connected (`connected` is then `false`).
- `instances` is present **only** on the RUNNING keepalive (the best-effort `STOPPED` shutdown state, and
  a keepalive with no configured slaves, omit it).

## CLI

| Flag | Values | Notes |
|------|--------|-------|
| `--platform` | `GREENGRASS` \| `HOST` \| `KUBERNETES` \| `auto` | Default `auto`. |
| `--transport` | `MQTT [path]` \| `IPC` | HOST/K8s use MQTT; the path is the messaging config. |
| `-c/--config` | `FILE <path>` \| `ENV` \| `GG_CONFIG` \| `CONFIGMAP` \| … | Default from the platform. |
| `-t/--thing` | `<name>` | IoT Thing name; the `{device}` token of every UNS topic. |
