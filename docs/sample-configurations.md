# Sample Configurations

Complete, copy-paste-ready configurations for the Modbus adapter
(`com.mbreissi.modbus.ModbusAdapter`), built up from a trivial dev loop to a realistic,
multi-table device map with northbound topic mapping, plus how data reaches the cloud —
with an explanation of **what every option does and how it changes runtime behavior**.

These are worked examples. For the exhaustive option list see [reference/configuration.md](reference/configuration.md);
for the type/byte-order system see [reference/data-types.md](reference/data-types.md); for task recipes
see [how-to-guides.md](how-to-guides.md); for the message envelopes see
[reference/messaging-interface.md](reference/messaging-interface.md); for the data/control plane model
see [explanation.md](explanation.md).

The adapter loads **one JSON document** from `-c/--config`. The top level may contain `component`
(required — the adapter) and the standard ggcommons sections `tags`, `messaging`, `metricEmission`,
`logging`, `heartbeat`, and (opt-in) `streaming`. Timing values resolve **signal/group ▸ instance
`defaults` ▸ `global.defaults` ▸ built-in**.

---

## Addressing convention (read this first)

Every register table maps to a Modbus function code, and the adapter always uses the **0-based PDU
address** — *not* the 1-based 4xxxx/3xxxx/1xxxx/0xxxx convention printed in most vendor manuals. The
two relate by a fixed offset per table:

| Table | `table` value | FC (read) | FC (write) | Element | Vendor convention | `address` (config) |
|-------|---------------|-----------|------------|---------|-------------------|--------------------|
| Coil | `coil` | 1 | 5 / 15 | 1 bit → bool | `0xxxx` (00001…) | vendor − 1 |
| Discrete input | `discrete` | 2 | — (read-only) | 1 bit → bool | `1xxxx` (10001…) | vendor − 10001 |
| Holding register | `holding` | 3 | 6 / 16 | 16-bit register | `4xxxx` (40001…) | vendor − 40001 |
| Input register | `input` | 4 | — (read-only) | 16-bit register | `3xxxx` (30001…) | vendor − 30001 |

So holding register **40001** is `address: 0`, **40003** is `address: 2`, and so on — multi-register
values (a `float32`/`uint32` spans 2 registers) advance the vendor number by 2 each. The tables below
list both columns so you can transcribe a vendor map without arithmetic mistakes.

---

## 1. Minimal local / dev (HOST + MQTT, Modbus TCP)

The smallest config that polls a Modbus TCP slave/simulator and republishes to a local MQTT broker.
Pair it with the local broker (`docker compose -f test-infra/compose.yaml up -d`) and a TCP simulator
(e.g. the bundled `validation/modbus_sim_server.py` on `:5020`).

Run it:

```bash
python main.py --platform HOST --transport MQTT ./messaging.json -c FILE ./config.json -t dev-thing
```

`config.json`:

```jsonc
{
  "logging": { "level": "INFO" },
  "messaging": {
    "local": { "type": "mqtt", "host": "localhost", "port": 1883, "clientId": "modbus-adapter" }
  },
  "component": {
    "instances": [
      {
        "id": "plc1",
        "connection": { "transport": "tcp", "host": "127.0.0.1", "port": 5020, "unitId": 1 },
        "pollGroups": [
          { "id": "main", "pollIntervalMs": 1000,
            "signals": [
              { "name": "Counter16", "table": "holding", "address": 0, "type": "uint16" },
              { "name": "Running",   "table": "coil",    "address": 0, "type": "bool" }
            ] }
        ]
      }
    ]
  }
}
```

You can drop the `messaging` section entirely and pass the broker inline instead:
`--transport MQTT ./messaging.json`, where `messaging.json` holds the same `{ "messaging": { "local": … } }`.

**What each option does at runtime**

| Option | Effect |
|--------|--------|
| `logging.level` | Standard ggcommons log level. `INFO` logs connect/poll-group summaries and errors; `DEBUG` adds per-call detail. |
| `messaging.local.type/host/port` | The transport target for published `SouthboundSignalUpdate` messages. On `HOST` this is the local MQTT broker the adapter connects to (and the same broker your consumers subscribe on). |
| `messaging.local.clientId` | MQTT client id used for the broker session. Make it unique per process so two adapters don't fight over the same session. |
| `instances[].id` | Stable instance id. Appears as `{InstanceId}` in topic templates, as `device.instance` in every message, and as the `[plc1]` prefix in logs. |
| `connection.transport: tcp` | Opens a Modbus/TCP socket to `host:port`. |
| `connection.host` / `port` | The device endpoint. Default `127.0.0.1:502`. |
| `connection.unitId` | Default Modbus unit/slave id used for reads/writes unless a poll group or signal-ref overrides it. |
| `pollGroups[].pollIntervalMs` | How often this group is read end-to-end. `1000` = once per second. Lower = fresher data but more bus traffic. |
| signal `name` | Required human name; the `{signalId}` topic variable and the friendly handle for reads/writes. |
| signal `table` | Which Modbus space + function code: `holding`(FC3) / `input`(FC4) registers, `coil`(FC1) / `discrete`(FC2) bits. |
| signal `address` | **0-based PDU address** (see the convention table above). |
| signal `type` | How raw registers/bits decode. Register tables default to `uint16`; bit tables are always `bool`. |

With `publishMode` unset it defaults to `onChange` and (no `deadband`) republishes whenever a value
differs from the last published one. With no `messaging` connection the adapter still polls but cannot
publish.

---

## 2. A realistic multi-table device map

This is the centerpiece. A single instance talks to a **pump skid** behind one TCP gateway at
`10.0.0.50:502`, addressing **two unit ids on the same socket**: the skid **PLC** (`unitId 1`,
control/status/alarms) and an integrated **power/energy meter** (`unitId 2`, totalizers/diagnostics).
The map below is representative of a real device — contiguous ranged blocks, mixed data types, a
word-swapped energy counter, a byte-swapped float, and a packed status word with bit-extracted
alarms.

### Device map

**Holding registers — `unitId 1`, FC3 read / FC6·FC16 write** (process values, setpoints, status word)

| Vendor | `address` | Signal | `type` | `scale` | R/W | Engineering meaning |
|--------|-----------|-----|--------|---------|-----|---------------------|
| 40001–40002 | `0` | `Temperature` | `float32` | — | R | Process temperature °C |
| 40003–40004 | `2` | `Pressure` | `float32` | — | R | Header pressure bar |
| 40005–40006 | `4` | `FlowRate` | `float32` | — | R | Flow m³/h |
| 40007–40008 | `6` | `Setpoint` | `float32` | — | R/W | Temperature setpoint °C |
| 40009 | `8` | `PumpSpeedCmd` | `uint16` | `0.1` | R/W | Pump speed command % (0.1 resolution) |
| 40010 | `9` | `TempTrim` | `int16` | `0.1` | R/W | **Signed** trim °C (can be negative) |
| 40017 | `16` | `StatusWord` | `uint16` | — | R | Packed status/alarm bits (see below) |

**Input registers — `unitId 2`, FC4 read-only** (meter totalizers and diagnostics)

| Vendor | `address` | Signal | `type` | order / scale | Engineering meaning |
|--------|-----------|-----|--------|---------------|---------------------|
| 30001–30002 | `0` | `EnergyImport` | `uint32` | `wordOrder: little`, `scale: 0.001` | Imported energy kWh (low word first) |
| 30003–30004 | `2` | `EnergyExport` | `uint32` | `wordOrder: little`, `scale: 0.001` | Exported energy kWh |
| 30005–30006 | `4` | `NetPower` | `int32` | `scale: 0.001` | **Signed** net power kW (− = export) |
| 30007–30008 | `6` | `RunHours` | `uint32` | — | Run-time hours |
| 30009 | `8` | `FaultCount` | `uint16` | — | Lifetime fault count |
| 30011–30018 | `10` | `SerialNo` | `string` | `count: 8` | Meter serial (16 UTF-8 bytes) |
| 30019 | `18` | `FirmwareVer` | `uint16` | — | Firmware revision |
| 30021–30022 | `20` | `PhaseAngle` | `float32` | `byteOrder: little` | Phase angle ° (**byte-swapped** device) |

**Coils — `unitId 1`, FC1 read / FC5·FC15 write** (command bits, polled for read-back)

| Vendor | `address` | Signal | Meaning |
|--------|-----------|-----|---------|
| 00001 | `0` | `RunCmd` | Start/stop command |
| 00002 | `1` | `ResetCmd` | Fault reset |
| 00003 | `2` | `RemoteEnable` | Remote-control enable |

**Discrete inputs — `unitId 1`, FC2 read-only** (status bits)

| Vendor | `address` | Signal | Meaning |
|--------|-----------|-----|---------|
| 10001 | `0` | `Running` | Pump running |
| 10002 | `1` | `Fault` | Fault active |
| 10003 | `2` | `LocalMode` | Local (not remote) |
| 10004 | `3` | `HighLevelSwitch` | High-level float |
| 10005 | `4` | `LowLevelSwitch` | Low-level float |
| 10006 | `5` | `DoorOpen` | Enclosure door |

**Status word bit extraction — `StatusWord` (holding `address 16`, `unitId 1`)**

A single 16-bit register packs six status flags. Each is surfaced as its own boolean signal with
`type: bool` + `bit: N` on the **same** address `16`, so all six come from one register read:

| `bit` | Signal | Meaning |
|-------|-----|---------|
| 0 | `AlarmHigh` | High-process alarm |
| 1 | `AlarmLow` | Low-process alarm |
| 2 | `OverTemp` | Over-temperature |
| 3 | `MotorFault` | Motor fault |
| 4 | `CommError` | Field-bus comm error |
| 5 | `MaintenanceDue` | Maintenance due |

### The config

```jsonc
{
  "tags": { "site": "plant1", "area": "pumphouse", "line": "5" },
  "logging": { "level": "INFO" },
  "messaging": {
    "local": { "type": "mqtt", "host": "localhost", "port": 1883, "clientId": "modbus-skid1" }
  },
  "metricEmission": { "target": "messaging", "targetConfig": { "topic": "metrics/{ThingName}/{ComponentName}" } },
  "component": {
    "global": { "defaults": { "pollIntervalMs": 1000, "publishMode": "onChange", "maxGap": 8, "batchMs": 0 } },
    "instances": [
      {
        "id": "skid1",
        "adapter": "modbus",
        "connection": { "transport": "tcp", "host": "10.0.0.50", "port": 502, "unitId": 1, "timeoutMs": 1000 },
        "publish": { "topic": "southbound/{site}/{ComponentName}/{InstanceId}/{signalId}", "batchMs": 0 },
        "write":   { "enabled": true, "topic": "southbound/{ComponentName}/{InstanceId}/write" },
        "read":    { "topic": "southbound/{ComponentName}/{InstanceId}/read" },
        "pollGroups": [

          { "id": "process", "pollIntervalMs": 250, "unitId": 1, "publishMode": "onChange", "maxGap": 8,
            "signals": [
              { "name": "Temperature",  "table": "holding", "address": 0, "type": "float32",
                "deadband": { "type": "absolute", "value": 0.2 } },
              { "name": "Pressure",     "table": "holding", "address": 2, "type": "float32",
                "deadband": { "type": "percent", "value": 1.0 } },
              { "name": "FlowRate",     "table": "holding", "address": 4, "type": "float32" },
              { "name": "Setpoint",     "table": "holding", "address": 6, "type": "float32" },
              { "name": "PumpSpeedCmd", "table": "holding", "address": 8, "type": "uint16", "scale": 0.1 },
              { "name": "TempTrim",     "table": "holding", "address": 9, "type": "int16",  "scale": 0.1 },

              { "name": "AlarmHigh",      "table": "holding", "address": 16, "type": "bool", "bit": 0,
                "topic": "southbound/{site}/alarms/{InstanceId}/{signalId}" },
              { "name": "AlarmLow",       "table": "holding", "address": 16, "type": "bool", "bit": 1,
                "topic": "southbound/{site}/alarms/{InstanceId}/{signalId}" },
              { "name": "OverTemp",       "table": "holding", "address": 16, "type": "bool", "bit": 2,
                "topic": "southbound/{site}/alarms/{InstanceId}/{signalId}" },
              { "name": "MotorFault",     "table": "holding", "address": 16, "type": "bool", "bit": 3,
                "topic": "southbound/{site}/alarms/{InstanceId}/{signalId}" },
              { "name": "CommError",      "table": "holding", "address": 16, "type": "bool", "bit": 4 },
              { "name": "MaintenanceDue", "table": "holding", "address": 16, "type": "bool", "bit": 5 }
            ] },

          { "id": "totals", "pollIntervalMs": 5000, "unitId": 2, "publishMode": "always", "maxGap": 8,
            "signals": [
              { "name": "EnergyImport", "table": "input", "address": 0,  "type": "uint32", "wordOrder": "little", "scale": 0.001 },
              { "name": "EnergyExport", "table": "input", "address": 2,  "type": "uint32", "wordOrder": "little", "scale": 0.001 },
              { "name": "NetPower",     "table": "input", "address": 4,  "type": "int32",  "scale": 0.001 },
              { "name": "RunHours",     "table": "input", "address": 6,  "type": "uint32" },
              { "name": "FaultCount",   "table": "input", "address": 8,  "type": "uint16" },
              { "name": "SerialNo",     "table": "input", "address": 10, "type": "string", "count": 8 },
              { "name": "FirmwareVer",  "table": "input", "address": 18, "type": "uint16" },
              { "name": "PhaseAngle",   "table": "input", "address": 20, "type": "float32", "byteOrder": "little" }
            ] },

          { "id": "status", "pollIntervalMs": 1000, "unitId": 1, "publishMode": "onChange",
            "signals": [
              { "name": "RunCmd",       "table": "coil", "address": 0, "type": "bool" },
              { "name": "ResetCmd",     "table": "coil", "address": 1, "type": "bool" },
              { "name": "RemoteEnable", "table": "coil", "address": 2, "type": "bool" },

              { "name": "Running",         "table": "discrete", "address": 0, "type": "bool" },
              { "name": "Fault",           "table": "discrete", "address": 1, "type": "bool" },
              { "name": "LocalMode",       "table": "discrete", "address": 2, "type": "bool" },
              { "name": "HighLevelSwitch", "table": "discrete", "address": 3, "type": "bool" },
              { "name": "LowLevelSwitch",  "table": "discrete", "address": 4, "type": "bool" },
              { "name": "DoorOpen",        "table": "discrete", "address": 5, "type": "bool" }
            ] }
        ]
      }
    ]
  }
}
```

### How the groups behave

Each poll group runs on **its own daemon thread**, so the `250 ms` `process` loop, the `5000 ms`
`totals` loop, and the `1000 ms` `status` loop run concurrently and independently. The poller
coalesces each group's signals **per table** into the fewest Modbus reads, sorting by address and
bridging gaps up to `maxGap`.

**`process` (fast control data, `unitId 1`, every 250 ms).** All twelve signals are holding registers,
so they coalesce into **one read**. The numeric signals occupy registers `0–9` (contiguous), and the six
alarm bits all read register `16`. With `maxGap 8` the poller bridges the 6-register gap (registers
`10–15` are read but unused) and issues a single `read_holding_registers(0, count=17)`:

```
0  1   2  3   4  5   6  7   8     9      10 11 12 13 14 15   16
└Temperature┘└Pressure┘└FlowRate┘└Setpnt┘Pump  Trim  └── unused (gap) ──┘ Status
```

- `Temperature` decodes a `float32`, and only republishes when it moves at least **0.2** °C from the
  last published value (`deadband absolute`).
- `Pressure` republishes only when it changes by at least **1.0 percent** of the previous value
  (`deadband percent`). When the previous value is `0`, percent can't be computed so any change publishes.
- `FlowRate` has no deadband, so under `onChange` it republishes on any change.
- `PumpSpeedCmd`/`TempTrim` are writable; reading them here gives a command **read-back**. `TempTrim`
  is `int16`, so it carries negative trims correctly. Both apply `scale 0.1`, so raw `455` → `45.5`.
- `AlarmHigh…MaintenanceDue` extract bits `0–5` of the one `StatusWord` register, surfacing packed
  bits as individual booleans; the four true alarms also override their publish topic (see mapping below).
- Had `maxGap` been `< 6` (e.g. the contiguous-only `maxGap: 0`), the `StatusWord` block would not
  merge and the group would issue **two** reads — `read_holding_registers(0, count=10)` and
  `read_holding_registers(16, count=1)`.

**`totals` (slow meter counters, `unitId 2`, every 5 s, `always`).** `unitId: 2` overrides the
connection's `unitId: 1`, addressing the meter behind the same socket. `publishMode: always`
republishes every poll regardless of change — right for monotonic counters and a steady "still alive"
signal. All eight signals are input registers spanning `0–21`; the two 1-register gaps (`9`, `19`) are
`≤ maxGap 8`, so they coalesce into a single `read_input_registers(0, count=22)`.

- `EnergyImport`/`EnergyExport` are `uint32` with `wordOrder: little` because this meter stores the
  low-order register first; without it the value is word-swapped and wildly wrong. `scale 0.001`
  converts Wh counts to kWh (a scaled integer is emitted as a float).
- `NetPower` is `int32` so it can go negative when the site exports.
- `SerialNo` is a `string` spanning `count: 8` registers (16 UTF-8 bytes, null-trimmed).
- `PhaseAngle` is a `float32` with `byteOrder: little` (a BADC device): the bytes within each register
  are swapped while the register order stays big — see the four-layout table in
  [reference/data-types.md](reference/data-types.md).

**`status` (command read-back + status bits, `unitId 1`, every 1 s).** Coils and discretes are
**different tables**, so coalescing produces one read each: `read_coils(0, count=3)` and
`read_discrete_inputs(0, count=6)`. Single bits decode straight to booleans.

**Net bus load** ≈ `process` 1 read × 4/s + `status` 2 reads × 1/s + `totals` 1 read × 0.2/s ≈ **6.2
requests/second** across both unit ids on the one socket.

### Northbound topic mapping

Every published `SouthboundSignalUpdate` resolves its topic from `publish.topic` (or a per-signal `topic`
override), substituting template tokens and **sanitizing** each value (`/`, `+`, `#`, and whitespace →
`_`). With `publish.topic = southbound/{site}/{ComponentName}/{InstanceId}/{signalId}`, `tags.site =
plant1`, component `com.mbreissi.modbus.ModbusAdapter`, and instance `skid1`:

| Signal (register) | Effective template | Resolved topic |
|----------------|--------------------|----------------|
| `Temperature` (holding 0, u1) | instance `publish.topic` | `southbound/plant1/com.mbreissi.modbus.ModbusAdapter/skid1/Temperature` |
| `EnergyImport` (input 0, u2) | instance `publish.topic` | `southbound/plant1/com.mbreissi.modbus.ModbusAdapter/skid1/EnergyImport` |
| `RunCmd` (coil 0, u1) | instance `publish.topic` | `southbound/plant1/com.mbreissi.modbus.ModbusAdapter/skid1/RunCmd` |
| `AlarmHigh` (holding 16 bit 0, u1) | per-signal `topic` override | `southbound/plant1/alarms/skid1/AlarmHigh` |

Tokens: `{ThingName}` (the `-t`/identity), `{ComponentName}` and `{ComponentFullName}` (dots are kept —
only `/ + #` and whitespace are sanitized), `{InstanceId}` (the instance `id`), `{signalId}` (the signal
`name`), plus any key under top-level `tags` (here `{site}`).

**Worked example — `AlarmHigh`.** It reads bit 0 of `StatusWord` on `unitId 1`, and its per-signal
override routes it to the alarms topic. The resolved topic is
`southbound/plant1/alarms/skid1/AlarmHigh`, and the body carries the canonical signal identity:

```jsonc
"body": {
  "device": { "adapter": "modbus", "instance": "skid1", "endpoint": "tcp://10.0.0.50:502 unit=1" },
  "signal": {
    "id": "u1/holding/16/bool",
    "name": "AlarmHigh",
    "address": { "unitId": 1, "table": "holding", "address": 16, "type": "bool", "bit": 0 }
  },
  "samples": [ { "value": true, "quality": "GOOD", "qualityRaw": "Good", "sourceTs": null, "serverTs": "2026-06-29T01:48:00Z" } ]
}
```

`signal.id` is the stable canonical id `u<unitId>/<table>/<address>/<type>`; `signal.address` is the
protocol-native handle. Both are independent of the topic, so a consumer can key on identity even if
you re-template topics.

### Option → runtime effect

| Option | Effect on runtime behavior |
|--------|---------------------------|
| `global.defaults` / instance `defaults` | Fallback `pollIntervalMs` / `publishMode` / `maxGap` / `batchMs` inherited when a group/instance omits them. Resolution order is **group ▸ instance `defaults` ▸ `global.defaults` ▸ built-in**. |
| `connection.unitId` | Default unit id for the instance; overridden here per-group (`totals` → unit 2). |
| `connection.timeoutMs` | Per-request response timeout (default `1000`). A read that exceeds it marks that block's signals `BAD` and increments `readErrors`. |
| `publish.topic` | Template for `SouthboundSignalUpdate` topics; `{…}` tokens are substituted and sanitized per message. |
| `publish.batchMs` | `0` = publish each sample immediately; `>0` buffers per signal and flushes together (see [batching](#batching-batchms)). Set under `publish` or `defaults`; **not** per poll group. |
| `write.enabled` | `true` subscribes the write topic so external clients can command the device. `false` (default) leaves it unsubscribed — writes are impossible. |
| `write.topic` / `read.topic` | The command surface (fire-and-forget writes; request/reply reads). A `…/control/+` topic is always subscribed for `status`/`signals`. |
| `pollGroups[].pollIntervalMs` | One full read-decode-publish pass for the group. The loop subtracts its own work time, so a slow read shortens (never lengthens) the next sleep — the configured cadence is the ceiling. |
| `pollGroups[].unitId` | Overrides `connection.unitId` for this group — addresses multiple slaves behind one TCP/RTU-TCP gateway or RTU line from one instance. |
| `pollGroups[].publishMode: onChange` | A decoded value publishes only if it passes its `deadband` vs the last published value (the first read always publishes). Cuts message volume on steady signals. |
| `pollGroups[].publishMode: always` | Every poll publishes, change or not. Use for counters/totalizers or a heartbeat-style feed. |
| `pollGroups[].maxGap` | Largest address gap (registers/bits) the coalescer bridges to merge two signals into one read. `0` = strictly contiguous only; higher = fewer, larger reads (less overhead) at the cost of reading unused registers. Each block is capped at the protocol max (125 registers, 2000 bits). Coalescing is **per table**. |
| signal `type` (`int16`/`uint16`/`int32`/`uint32`/`int64`/`uint64`/`float32`/`float64`/`string`/`bool`) | Determines how many registers the signal spans and how the raw words are interpreted (see [data-types](reference/data-types.md)). |
| signal `wordOrder` | Order of the registers in a multi-register value. `big` (default) = most-significant first; `little` = reversed (word swap). |
| signal `byteOrder` | Order of bytes within each register. `big` (default)/`little`. The two knobs cover ABCD/BADC/CDAB/DCBA. Wrong order = right magnitude class, garbled value. |
| signal `scale` / `offset` | Linear transform `value = raw × scale + offset` on read (inverted on write). Converts raw counts to engineering units; a scaled integer is emitted as a float. |
| signal `count` | Registers a `string` spans (2 UTF-8 bytes each). Required for `string`. |
| signal `bit` (0–15) | Publishes a single bit of a holding/input register as a boolean. Only valid with `type: bool` on a register table. Bit *writes* (read-modify-write) are not implemented. |
| signal `deadband` | Per-signal change filter under `onChange`: `none` (any change), `absolute` (`|new−old| ≥ value`), `percent` (`|new−old| ≥ value%` of old; any change when old is `0`). Non-numeric signals (bool/string) publish on any change. |
| signal `topic` | Per-signal override of `publish.topic` — route specific signals (e.g. alarms) to their own topic. |

### Batching (`batchMs`)

`batchMs` (under `publish`, or `global`/instance `defaults`) coalesces messages across time:

```jsonc
"publish": { "topic": "southbound/{site}/{ComponentName}/{InstanceId}/{signalId}", "batchMs": 1000 }
```

- `batchMs: 0` (default) — every sample publishes immediately as its own `SouthboundSignalUpdate`.
- `batchMs > 0` — samples are buffered per signal and flushed together on a timer every `batchMs`, so one
  message can carry several `samples` for a signal. This trades freshness/latency for far fewer, larger
  messages — useful on constrained uplinks. The device's flush tick is `batchMs` (or 5 s when batching
  is off); it also drives the periodic `southbound_health` emission.

---

## 3. Northbound: from the local bus to the cloud

Everything above publishes to the **local bus** — Greengrass IPC on the `GREENGRASS` platform, the
local MQTT broker on `HOST`/`KUBERNETES`. That is the adapter's data plane: `SignalUpdatePublisher` sends
every `SouthboundSignalUpdate` with the plain `publish` call, and `CommandService` subscribes the
write/read/control surface with the plain `subscribe` call — both on the default provider channel
(the local broker on HOST, IPC on Greengrass). On-box consumers read those topics.

**What the adapter sends to the cloud itself.** The one northbound path the adapter wires directly is
its own *operational* telemetry — the heartbeat and the health metric. The library can deliver them
straight to AWS IoT Core alongside the local bus: on `HOST`/`KUBERNETES` the dual-MQTT provider holds
the IoT Core mTLS session next to the local one. Opt in with `messaging.iotCore` plus a heartbeat /
metric target set to `destination: "iotcore"`:

```jsonc
{
  "messaging": {
    "local":   { "type": "mqtt", "host": "localhost", "port": 1883, "clientId": "modbus-skid1" },
    "iotCore": {
      "endpoint": "a1b2c3d4e5f6g7-ats.iot.us-east-1.amazonaws.com",
      "port": 8883,
      "clientId": "modbus-skid1",
      "credentials": {
        "certPath": "/greengrass/v2/thingCert.crt",
        "keyPath":  "/greengrass/v2/privKey.key",
        "caPath":   "/greengrass/v2/rootCA.pem"
      }
    }
  },

  // Heartbeat and health go to IoT Core (low rate); register data stays on the local bus.
  "heartbeat": {
    "intervalSecs": 30,
    "targets": [ { "type": "messaging", "config": { "destination": "iotcore", "topic": "heartbeat/{ThingName}/{ComponentName}" } } ],
    "measures": { "cpu": true, "memory": true }
  },
  "metricEmission": {
    "target": "messaging",
    "targetConfig": { "topic": "southbound/{ThingName}/{ComponentName}/health", "destination": "iotcore" }
  }
}
```

On `GREENGRASS` the same `destination: "iotcore"` routes through the Nucleus' IoT Core connection, so
`messaging.iotCore` is not needed there.

**Forwarding the register data itself.** The adapter does **not** push polled register telemetry
off-box — it publishes locally and stops there. Getting that data to the cloud is a deployment
choice, handled by a separate consumer of the local topics:

- **Low-rate, actionable** items (state, alarms, a setpoint readback someone acts on) — re-publish
  to AWS IoT Core via a Greengrass/IoT-Core **topic bridge** (rules engine) or a small on-box
  subscriber. IoT Core is priced per message, so keep this sparse.
- **High-rate, high-volume** process data for analytics or a historian — the library's streaming
  subsystem, `gg.streams()`, which batches and compresses into a durable on-disk buffer that drains to
  Kinesis or Kafka and survives WAN outages. See the [Streaming guide](/guides/streaming/) and the
  [streaming reference](/reference/streaming/) for its configuration; it is a `ggcommons` subsystem you
  run in a forwarding component, not a `modbus-adapter` option.

---

## 4. Serial RTU and RTU-over-TCP

The adapter supports three transports — `tcp`, `rtu` (serial line), and `rtutcp` (RTU framing over a
TCP socket, for serial-to-Ethernet gateways). The signal/type/poll model is identical across all three;
only the `connection` block changes.

**Serial RTU** (`/dev/ttyUSB0`, `COM3`, …):

```jsonc
{
  "messaging": { "local": { "type": "mqtt", "host": "localhost", "port": 1883 } },
  "component": {
    "instances": [
      {
        "id": "meter1",
        "connection": {
          "transport": "rtu",
          "serialPort": "/dev/ttyUSB0",
          "baudRate": 19200, "parity": "E", "stopBits": 1, "byteSize": 8,
          "unitId": 5, "timeoutMs": 1500
        },
        "pollGroups": [
          { "id": "energy", "pollIntervalMs": 2000,
            "signals": [
              { "name": "Voltage", "table": "input", "address": 0, "type": "float32" },
              { "name": "Energy",  "table": "input", "address": 12, "type": "uint32", "wordOrder": "little" }
            ] }
        ]
      }
    ]
  }
}
```

**RTU-over-TCP** (same RTU framing, reached through a gateway's IP:port — swap only the `connection`):

```jsonc
"connection": { "transport": "rtutcp", "host": "10.0.0.200", "port": 502, "unitId": 5, "timeoutMs": 1500 }
```

**What each option does at runtime**

| Option | Effect |
|--------|--------|
| `transport: rtu` | Builds a `ModbusSerialClient` with the RTU framer. `host`/`port` are ignored; `serialPort` is required. |
| `transport: rtutcp` | Builds a `ModbusTcpClient` with the **RTU** framer over the socket — the right choice for a serial-to-Ethernet gateway that wraps raw RTU frames. Uses `host`/`port`, ignores `serialPort`. |
| `serialPort` | OS serial device path/name (`/dev/ttyUSB0`, `COM3`). RTU only. |
| `baudRate` | Line speed (default `9600`). Must match the device exactly or every frame fails to decode. |
| `parity` | `N`/`E`/`O` (default `N`). Must match the device. |
| `stopBits` | `1` or `2` (default `1`). Must match the device. |
| `byteSize` | Bits per character (default `8`). |
| `unitId` | The RTU slave address on the bus. On a multidrop RTU line each device has a distinct id; set it per instance, or per poll group when several slaves share one line/gateway. |
| `timeoutMs` | Per-request response timeout (default `1000`). Serial lines are slow — raise it (e.g. `1500`). A read that exceeds it marks the block's signals `BAD` and increments `readErrors`. |

Because a serial line is a single shared medium, **only one request is in flight at a time** and poll
groups effectively serialize on it. Keep `pollIntervalMs` realistic for the baud rate and register
count — over-aggressive polling on RTU just queues reads and inflates latency.

---

## 5. Greengrass v2 deployment (IPC)

On Greengrass the config is the component's `ComponentConfig` and messaging uses Greengrass IPC — no
`messaging` section and no broker. The config below is the `recipe.yaml`
`DefaultConfiguration.ComponentConfig`; override `connection` and `pollGroups` for your device at
deploy time. The component runs `main.py --platform GREENGRASS` (config source defaults to `GG_CONFIG`,
transport to `IPC`).

```yaml
ComponentConfiguration:
  DefaultConfiguration:
    ComponentConfig:
      logging:
        level: "INFO"
        python_format: "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
      heartbeat:
        intervalSecs: 5
        targets:
          - type: "messaging"
            config: { destination: "ipc", topic: "heartbeat/{ThingName}/{ComponentName}" }
        measures: { cpu: true, memory: true, disk: false }
      tags: {}
      metricEmission:
        target: "log"
        targetConfig: { logFileName: "/greengrass/v2/logs/{ComponentFullName}.metric.log" }
      component:
        global:
          defaults: { pollIntervalMs: 1000, publishMode: "onChange", maxGap: 8 }
        instances:
          - id: "plc1"
            adapter: "modbus"
            connection: { transport: "tcp", host: "10.0.0.50", port: 502, unitId: 1, timeoutMs: 1000 }
            publish: { topic: "southbound/{ComponentName}/{InstanceId}/{signalId}", batchMs: 0 }
            write:   { enabled: true, topic: "southbound/{ComponentName}/{InstanceId}/write" }
            read:    { topic: "southbound/{ComponentName}/{InstanceId}/read" }
            pollGroups:
              - id: "main"
                pollIntervalMs: 1000
                signals:
                  - { name: "Counter16", table: "holding", address: 0, type: "uint16" }
                  - { name: "Scaled",    table: "holding", address: 40, type: "uint16", scale: 0.1 }
```

**What changes vs the other platforms**

| Option | Effect |
|--------|--------|
| `--platform GREENGRASS` (in the recipe `Run`) | Selects IPC messaging and `GG_CONFIG` as the config source; publishes route through the Nucleus rather than a broker. The recipe's `accessControl` grants pub/sub on IPC and IoT Core. |
| `heartbeat.*` | Standard ggcommons heartbeat — periodic CPU/memory/disk system metrics on the given topic via IPC. Independent of Modbus polling. `destination: ipc` is the local channel. |
| `metricEmission.target: log` | Routes the `southbound_health` metric to a rotating log file (vs `messaging`/`cloudwatch`/`prometheus`). `{ComponentFullName}` resolves to the deployed component name. |
| `adapter: "modbus"` | Informational only; echoed as `device.adapter` in every message. |
| signal `scale` | `Scaled` publishes `raw × 0.1` (raw `123` → `12.3`); a scaled integer is emitted as a float. |

On startup each instance's `connect()` **blocks and retries every 5 s** until the device answers, so a
device down at deploy time does not crash the component — it logs and keeps trying, and the instance
becomes ready once connected.

---

## 6. Kubernetes (ConfigMap)

On Kubernetes the config is mounted as a **directory** (the whole ConfigMap volume) at `/etc/ggcommons`;
the `CONFIGMAP` source watches the kubelet `..data` symlink swap and **hot-reloads in process** on
`kubectl apply`. With `--platform auto`, the library detects KUBERNETES from the ServiceAccount token,
picks the `CONFIGMAP` source and `MQTT` transport (broker from the config), and takes identity from the
Downward API — so the container needs **no CLI args**.

`k8s/configmap.yaml`:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: modbus-adapter-config
data:
  config.json: |-
    {
      "logging": { "level": "INFO" },
      "messaging": {
        "local": { "type": "mqtt", "host": "emqx.default.svc.cluster.local", "port": 1883, "clientId": "modbus-adapter" }
      },
      "metricEmission": { "target": "prometheus", "targetConfig": { "port": 9090, "path": "/metrics" } },
      "component": {
        "global": { "defaults": { "pollIntervalMs": 1000, "publishMode": "onChange", "maxGap": 8 } },
        "instances": [
          {
            "id": "plc1",
            "connection": { "transport": "tcp", "host": "modbus-sim.default.svc.cluster.local", "port": 5020, "unitId": 1, "timeoutMs": 1000 },
            "publish": { "topic": "southbound/{ComponentName}/{InstanceId}/{signalId}", "batchMs": 0 },
            "write":   { "enabled": true, "topic": "southbound/{ComponentName}/{InstanceId}/write" },
            "pollGroups": [
              { "id": "main", "pollIntervalMs": 1000,
                "signals": [
                  { "name": "Counter16", "table": "holding", "address": 0,  "type": "uint16" },
                  { "name": "Scaled",    "table": "holding", "address": 40, "type": "uint16", "scale": 0.1 }
                ] }
            ]
          }
        ]
      }
    }
```

**What changes vs the other platforms**

| Aspect | Effect |
|--------|--------|
| Config source `CONFIGMAP` | Reads `config.json` from the mounted ConfigMap directory and hot-reloads when you `kubectl apply` a new ConfigMap (the `..data` swap) — no pod restart. |
| `messaging.local.host` | Point at an **in-cluster** broker Service DNS name (`emqx.default.svc.cluster.local`). |
| `connection.host` | Point at the device/gateway's **Service** or reachable address — the adapter runs in-cluster, so the device must be reachable from the pod network. |
| Identity (no `-t`) | The Thing name resolves from the Downward API (`GGCOMMONS_THING_NAME` ▸ `POD_NAME`), so `{ThingName}` in topics is the pod name unless overridden. |
| `metricEmission.target: prometheus` | Exposes the `southbound_health` metric as OpenMetrics text at `:9090/metrics` for scraping (the default metric target on KUBERNETES). |
| Health/probes | The Deployment exposes the library's HTTP health endpoint (`/startupz`, `/livez`, `/readyz`) for k8s probes. |

Polling, type, deadband, and command behavior are identical to the other platforms — only the config
source, broker/device addressing, identity, and metric target differ.

---

## How the cross-cutting options affect runtime

These behaviors apply to **every** configuration above.

### Poll interval, coalescing, and bus load

The poll manager turns each group's signals into the **fewest Modbus reads** possible: signals on the same
table are sorted by address and merged into contiguous read blocks, bridging gaps up to `maxGap` and
capping each block at the protocol limit (125 holding/input registers, 2000 coil/discrete bits). Net
bus load ≈ `(read blocks per group) × (1000 / pollIntervalMs)` requests/second per group. Two levers:

- **Lower `pollIntervalMs`** → fresher data, proportionally more requests and messages.
- **Raise `maxGap`** → nearby signals collapse into one larger read (fewer round-trips, lower per-request
  overhead) at the cost of reading some unused registers in between. Coalescing is per table, so mixing
  tables in a group means at least one read per table.

A poll-group thread measures its own work time and waits `pollIntervalMs − elapsed`, so a slow read
shortens (never lengthens) the next sleep — the configured cadence is the ceiling, not an addition.

### Decoding raw registers (`type` / `wordOrder` / `byteOrder` / `scale` / `bit`)

Modbus carries only bits and 16-bit registers; richer types are synthesized in `codec.py`. A read
block's registers are sliced per signal, then assembled: `wordOrder` orders the registers (big =
most-significant first; little = reversed), `byteOrder` orders the bytes within each register, and the
`type`'s width decides how many registers are consumed. `scale`/`offset` then apply the linear
transform; `bit` extracts a single bit. If a decode raises (e.g. a malformed string), that signal is
published with quality `BAD` and the rest of the block continues.

### Deadband and publish mode (data freshness vs message volume)

Under `publishMode: onChange`, every decoded value is compared to the **last published** value via the
signal's `deadband` and only republished if it passes — the first reading after start always publishes.
This suppresses noise/jitter so steady signals don't flood the bus. `publishMode: always` bypasses the
deadband and publishes every poll. `batchMs` is orthogonal: it coalesces whatever was published in a
window into fewer messages.

### Topic resolution, sanitization, and precedence

A publish topic is `signal.topic` (if set) else the instance `publish.topic` else the built-in
`southbound/{ComponentName}/{InstanceId}/{signalId}`. The library resolves `{ThingName}`,
`{ComponentName}`, `{ComponentFullName}`, and custom `tags` keys; the adapter then substitutes
`{InstanceId}` and `{signalId}`. Every substituted value is **sanitized** — `/`, `+`, `#`, and whitespace
become `_` — to block topic injection and stray MQTT wildcards. Timing/coalescing keys
(`pollIntervalMs`, `publishMode`, `maxGap`) resolve **group ▸ instance `defaults` ▸ `global.defaults` ▸
built-in**; `batchMs` resolves from `publish` ▸ instance/`global` `defaults`.

### Reconnect, timeout, and read failures

At startup each instance's `connect()` **blocks and retries every 5 seconds** until the device answers,
so a device down at launch doesn't crash the adapter — other instances keep running (each instance has
its own worker/connection). `connection.timeoutMs` bounds each individual request; a read that times
out, errors, or returns a Modbus exception marks **every signal in that read block** with quality `BAD`
(value `null`) and increments the `readErrors` counter, while the loop stays alive and retries on the
next interval. The `southbound_health` metric's `connectionState` (1/0) and `readErrors` reflect this;
it is emitted to `metricEmission.target` and queryable on the `…/control/status` topic.

### Reads vs writes (the command surface)

Polling is the read **plane**. The command surface is separate:

- **Writes** require `write.enabled: true` (otherwise the write topic is never subscribed). A write is
  fire-and-forget to `write.topic` with `{ "writes": [ { "name": "Setpoint", "value": 42.5 } ] }` (or a
  single `{ "name": …, "value": … }`). Only **writable tables** accept writes — `coil` (FC5/FC15) and
  `holding` (FC6/FC16); `discrete`/`input` are rejected with a warning, and `bit` (single-bit) writes
  are skipped (read-modify-write is not implemented). `scale`/`offset` are inverted on the way down.
- **Reads** are request/reply on `read.topic` (set `reply_to`/`correlation_id`) and return a
  `SouthboundReadResult` — on-demand, independent of the poll loop.
- **Control** queries (`…/control/status`, `…/control/signals`) return connection state + counters and the
  resolved signal list.

A signal-ref in any command is either `{ "name": "<configured signal>" }` or an explicit
`{ unitId?, table, address, type, wordOrder?, scale?, … }` for arbitrary access. See
[reference/messaging-interface.md](reference/messaging-interface.md) for the full payloads.
