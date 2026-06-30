# Sample Configurations

Complete, copy-paste-ready configurations for the Modbus adapter
(`com.mbreissi.modbus.ModbusAdapter`), one per realistic deployment scenario, with an explanation of
**what every option does and how it changes runtime behavior**.

These are worked examples. For the exhaustive option list see [reference/configuration.md](reference/configuration.md);
for the type/byte-order system see [reference/data-types.md](reference/data-types.md); for task recipes
see [how-to-guides.md](how-to-guides.md); for the message envelopes see
[reference/messaging-interface.md](reference/messaging-interface.md).

The adapter loads **one JSON document** from `-c/--config`. The top level may contain `component`
(required — the adapter), and the standard ggcommons sections `tags`, `messaging`, `metricEmission`,
`logging`, and `heartbeat`. Timing values resolve **tag/group ▸ instance `defaults` ▸
`global.defaults` ▸ built-in**.

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
            "tags": [
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
| `messaging.local.type/host/port` | The transport target for published `SouthboundTagUpdate` messages. On `HOST` this is the local MQTT broker the adapter connects to (and the same broker your consumers subscribe on). |
| `messaging.local.clientId` | MQTT client id used for the broker session. Make it unique per process so two adapters don't fight over the same session. |
| `instances[].id` | Stable instance id. Appears as `{InstanceId}` in topic templates, as `device.instance` in every message, and as the `[plc1]` prefix in logs. |
| `connection.transport: tcp` | Opens a Modbus/TCP socket to `host:port`. |
| `connection.host` / `port` | The device endpoint. Default `127.0.0.1:502`. |
| `connection.unitId` | Default Modbus unit/slave id used for reads/writes unless a poll group or tag-ref overrides it. |
| `pollGroups[].pollIntervalMs` | How often this group is read end-to-end. `1000` = once per second. Lower = fresher data but more bus traffic. |
| tag `name` | Required human name; the `{tagId}` topic variable and the friendly handle for reads/writes. |
| tag `table` | Which Modbus space + function code: `holding`(FC3) / `input`(FC4) registers, `coil`(FC1) / `discrete`(FC2) bits. |
| tag `address` | **0-based PDU address** (not the 4xxxx/1-based convention). |
| tag `type` | How raw registers/bits decode. Register tables default to `uint16`; bit tables are always `bool`. |

With `publishMode` unset it defaults to `onChange` and (no `deadband`) republishes whenever a value
differs from the last published one. With no `messaging` connection the adapter still polls but cannot
publish.

---

## 2. Serial RTU and RTU-over-TCP

The adapter supports three transports — `tcp`, `rtu` (serial line), and `rtutcp` (RTU framing over a
TCP socket, for serial-to-Ethernet gateways). The tag/type/poll model is identical across all three;
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
            "tags": [
              { "name": "Voltage", "table": "input", "address": 0, "type": "float32" },
              { "name": "Energy",  "table": "input", "address": 12, "type": "uint32", "wordOrder": "little" }
            ] }
        ]
      }
    ]
  }
}
```

**RTU-over-TCP** (same RTU framing, but reached through a gateway's IP:port — swap only the
`connection`):

```jsonc
"connection": { "transport": "rtutcp", "host": "10.0.0.200", "port": 502, "unitId": 5, "timeoutMs": 1500 }
```

**What each option does at runtime**

| Option | Effect |
|--------|--------|
| `transport: rtu` | Builds a `ModbusSerialClient` with the RTU framer. `host`/`port` are ignored; `serialPort` is required. |
| `transport: rtutcp` | Builds a `ModbusTcpClient` but with the **RTU** framer over the socket — the right choice for a serial-to-Ethernet gateway that wraps raw RTU frames. Uses `host`/`port`, ignores `serialPort`. |
| `serialPort` | OS serial device path/name (`/dev/ttyUSB0`, `COM3`). RTU only. |
| `baudRate` | Line speed (default `9600`). Must match the device exactly or every frame fails to decode. |
| `parity` | `N`/`E`/`O` (default `N`). Must match the device. |
| `stopBits` | `1` or `2` (default `1`). Must match the device. |
| `byteSize` | Bits per character (default `8`). |
| `unitId` | The RTU slave address on the bus. On a multidrop RTU line each device has a distinct id; set it per instance, or per poll group when several slaves share one line/gateway. |
| `timeoutMs` | Per-request response timeout (default `1000`). Serial lines are slow — raise it (e.g. `1500`) so a legitimate but slow reply isn't counted as an error. A read that exceeds the timeout marks that block's tags `BAD` and increments `readErrors`. |

Because a serial line is a single shared medium, **only one request is in flight at a time** and poll
groups effectively serialize on it. Keep `pollIntervalMs` realistic for the baud rate and the number
of registers — over-aggressive polling on RTU just queues reads and inflates latency.

---

## 3. Greengrass v2 deployment (IPC)

On Greengrass the config is the component's `ComponentConfig` and messaging uses Greengrass IPC — no
`messaging` section and no broker are needed. The config below is the `recipe.yaml`
`DefaultConfiguration.ComponentConfig`; override `connection` and `pollGroups` for your device in the
deployment. The component runs `main.py --platform GREENGRASS` (config source defaults to `GG_CONFIG`,
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
            publish: { topic: "southbound/{ComponentName}/{InstanceId}/{tagId}", batchMs: 0 }
            write:   { enabled: true, topic: "southbound/{ComponentName}/{InstanceId}/write" }
            read:    { topic: "southbound/{ComponentName}/{InstanceId}/read" }
            pollGroups:
              - id: "main"
                pollIntervalMs: 1000
                tags:
                  - { name: "Counter16", table: "holding", address: 0, type: "uint16" }
                  - { name: "Scaled",    table: "holding", address: 40, type: "uint16", scale: 0.1 }
```

**What each option does at runtime**

| Option | Effect |
|--------|--------|
| `--platform GREENGRASS` (in the recipe `Run`) | Selects IPC messaging and `GG_CONFIG` as the config source; publishes route through the Nucleus rather than a broker. The recipe's `accessControl` grants pub/sub on IPC and IoT Core. |
| `heartbeat.*` | Standard ggcommons heartbeat — periodic CPU/memory/disk system metrics on the given topic via IPC. Independent of Modbus polling. |
| `metricEmission.target: log` | Routes the adapter's `southbound_health` metric to a rotating log file (vs `messaging`/`cloudwatch`/`prometheus`). `{ComponentFullName}` resolves to the deployed component name. |
| `global.defaults` | Instance/group defaults inherited when a group omits `pollIntervalMs` / `publishMode` / `maxGap`. |
| `adapter: "modbus"` | Informational only; echoed as `device.adapter` in every message. |
| `connection.timeoutMs` | Per-request timeout (default `1000`). |
| `publish.topic` | Template for published value updates. `{ComponentName}` / `{InstanceId}` / `{tagId}` are substituted (and sanitized) per message; `{tagId}` is the tag `name`. |
| `publish.batchMs: 0` | Publish each sample immediately (see [§4](#batching-batchms) for batching). |
| `write.enabled: true` | Subscribes the write topic so external clients can command the device. With `false` (default) the write topic is **not** subscribed and writes are impossible. |
| `write.topic` / `read.topic` | Topics for the command surface (fire-and-forget writes; request/reply reads). A `…/control/+` topic is always subscribed for `status`/`tags` queries. |
| tag `scale` | `Scaled` publishes `raw × 0.1` (e.g. raw `123` → `12.3`); applying a scale turns an integer type into a float on the wire. |

On startup each instance's `connect()` **blocks and retries every 5 s** until the device answers, so a
device that is down at deploy time does not crash the component — it logs and keeps trying, and the
instance becomes ready once connected.

---

## 4. Multiple poll groups, register maps, intervals, function codes, deadband

The real workhorse pattern: split a device's register map into poll groups by **how fast each set of
values changes** and **how it should be published**. Fast-changing process values poll often with a
deadband; slow totalizers/diagnostics poll rarely; status bits get their own cadence. Different groups
can target different `unitId`s behind one gateway.

```jsonc
{
  "tags": { "site": "plant1", "line": "5" },
  "messaging": { "local": { "type": "mqtt", "host": "localhost", "port": 1883 } },
  "metricEmission": { "target": "messaging", "targetConfig": { "topic": "metrics/{ThingName}/{ComponentName}" } },
  "component": {
    "global": { "defaults": { "pollIntervalMs": 1000, "publishMode": "onChange", "maxGap": 4, "batchMs": 0 } },
    "instances": [
      {
        "id": "plc1",
        "connection": { "transport": "tcp", "host": "10.0.0.50", "port": 502, "unitId": 1, "timeoutMs": 1000 },
        "publish": { "topic": "southbound/{site}/{ComponentName}/{InstanceId}/{tagId}" },
        "write":   { "enabled": true },
        "pollGroups": [

          { "id": "process", "pollIntervalMs": 250, "publishMode": "onChange", "maxGap": 2,
            "tags": [
              { "name": "Temperature", "table": "holding", "address": 0, "type": "float32", "scale": 0.1,
                "deadband": { "type": "absolute", "value": 0.2 } },
              { "name": "Pressure",    "table": "holding", "address": 2, "type": "float32",
                "deadband": { "type": "percent", "value": 1.0 } },
              { "name": "FlowRate",    "table": "holding", "address": 4, "type": "float32" }
            ] },

          { "id": "totals", "pollIntervalMs": 5000, "publishMode": "always", "maxGap": 8,
            "tags": [
              { "name": "EnergyTotal", "table": "input",  "address": 0,  "type": "uint32", "wordOrder": "little" },
              { "name": "RunHours",    "table": "input",  "address": 2,  "type": "uint32" },
              { "name": "SerialNo",    "table": "input",  "address": 10, "type": "string", "count": 8 }
            ] },

          { "id": "status", "pollIntervalMs": 1000, "unitId": 2,
            "tags": [
              { "name": "Running",   "table": "coil",     "address": 0, "type": "bool" },
              { "name": "RemoteMode","table": "discrete", "address": 0, "type": "bool" },
              { "name": "AlarmHigh", "table": "holding",  "address": 20, "type": "bool", "bit": 0,
                "topic": "southbound/{site}/alarms/{InstanceId}/{tagId}" },
              { "name": "AlarmLow",  "table": "holding",  "address": 20, "type": "bool", "bit": 1 }
            ] }
        ]
      }
    ]
  }
}
```

### How the groups behave

Each poll group runs on **its own daemon thread**, so the `250 ms` `process` loop, the `5000 ms`
`totals` loop, and the `1000 ms` `status` loop run concurrently and independently.

**`process` (fast, deadband-gated).** Read every 250 ms for fresh control data.
- `Temperature` decodes a `float32` (registers 0–1), multiplies by `scale 0.1`, and only republishes
  when it moves at least **0.2** engineering units from the last published value (`deadband absolute`).
- `Pressure` republishes only when it changes by at least **1.0 percent** of the previous value
  (`deadband percent`). When the previous value is `0`, percent can't be computed so any change
  publishes.
- `FlowRate` has no deadband, so under `onChange` it republishes on any change.
- The three tags are contiguous (`0–5`), so with `maxGap 2` the coalescer merges them into **one**
  `read_holding_registers(0, count=6)` per poll instead of three reads.

**`totals` (slow, always).** Read every 5 s; `publishMode: always` republishes every poll regardless
of change — useful for monotonic counters and a steady "still alive" signal.
- `EnergyTotal` is a `uint32` (registers 0–1) with `wordOrder: little` because this meter stores the
  low-order register first; without it the value would be word-swapped and wildly wrong.
- `SerialNo` is a `string` spanning `count: 8` registers (16 UTF-8 bytes, null-trimmed).
- `RunHours` ends at register 4 and `SerialNo` starts at 10; the gap of 6 is `≤ maxGap 8`, so all
  three coalesce into **one** `read_input_registers(0, count=18)` — one bus round-trip for the whole
  group.

**`status` (bits + bit-extraction, different unit).** `unitId: 2` overrides the connection's
`unitId: 1`, so this group reads a second slave behind the same TCP gateway.
- `Running` (coil) and `RemoteMode` (discrete) decode single bits to booleans; they live on different
  tables so they are read with separate function-code calls (coalescing is per-table).
- `AlarmHigh`/`AlarmLow` extract bits 0 and 1 of the **same** holding register 20 with `bit`, surfacing
  packed status word bits as individual booleans. Both reads come from one register read.
- `AlarmHigh` overrides its publish topic with a per-tag `topic`, so it goes to an alarms topic while
  the rest of the group uses the instance `publish.topic`.

### Option → runtime effect

| Option | Effect on runtime behavior |
|--------|---------------------------|
| `pollGroups[].pollIntervalMs` | The cadence of one full read-decode-publish pass for the group. Set per the data's rate of change; faster = fresher but more bus load and more messages. The loop subtracts its own work time so the period is honored (it never drifts later by the read duration). |
| `pollGroups[].publishMode: onChange` | A decoded value is published only if it passes its `deadband` vs the last published value (first read always publishes). Cuts message volume on steady signals. |
| `pollGroups[].publishMode: always` | Every poll publishes, change or not. Use for counters/totalizers or a heartbeat-style feed. |
| `pollGroups[].unitId` | Overrides `connection.unitId` for this group — the way to address multiple slaves behind one TCP/RTU-TCP gateway or one RTU line from a single instance. |
| `pollGroups[].maxGap` | Largest address gap (in registers/bits) the coalescer will bridge to merge two tags into one read. `0` = only strictly contiguous tags merge; higher = fewer, larger reads (less protocol overhead) at the cost of reading some unused registers. Each merged block is still capped at the protocol max (125 registers, 2000 bits). |
| tag `type` (`float32`,`uint32`,`int16`,…) | Determines how many registers the tag spans and how the raw words are interpreted (see [data-types](reference/data-types.md)). |
| tag `wordOrder` / `byteOrder` | Reorder the registers/bytes before decode. `big`/`big` (default) = ABCD; the four combinations cover ABCD/BADC/CDAB/DCBA. Wrong order = correct magnitude class but garbled value. |
| tag `scale` / `offset` | Linear transform `value = raw × scale + offset` on read (inverted on write). Converts raw counts to engineering units; a scaled integer is emitted as a float. |
| tag `bit` (0–15) | Publishes a single bit of a holding/input register as a boolean. Only valid with `type: bool` on a register table. |
| tag `count` | Number of registers a `string` spans (2 UTF-8 bytes each). Required for `string`. |
| tag `deadband` | Per-tag change filter applied under `onChange`: `none` (any change), `absolute` (`|new−old| ≥ value`), `percent` (`|new−old| ≥ value%` of old). Non-numeric tags (bool/string) publish on any change regardless of type. |
| tag `topic` | Per-tag override of `publish.topic` — route specific tags (e.g. alarms) to their own topic. |

### Batching (`batchMs`)

`batchMs` (under `publish`, or `global.defaults`) controls message coalescing across time:

```jsonc
"publish": { "topic": "southbound/{site}/{ComponentName}/{InstanceId}/{tagId}", "batchMs": 1000 }
```

- `batchMs: 0` (default) — every sample publishes immediately as its own `SouthboundTagUpdate`.
- `batchMs > 0` — samples are buffered per tag and flushed together on a timer every `batchMs`, so one
  message can carry several `samples` for a tag. This trades freshness/latency for far fewer, larger
  messages — useful on constrained uplinks. The device's flush tick is `batchMs` (or 5 s when batching
  is off); it also drives the periodic `southbound_health` emission.

---

## 5. Kubernetes (ConfigMap)

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
      "component": {
        "global": { "defaults": { "pollIntervalMs": 1000, "publishMode": "onChange", "maxGap": 8 } },
        "instances": [
          {
            "id": "plc1",
            "connection": { "transport": "tcp", "host": "modbus-sim.default.svc.cluster.local", "port": 5020, "unitId": 1, "timeoutMs": 1000 },
            "publish": { "topic": "southbound/{ComponentName}/{InstanceId}/{tagId}", "batchMs": 0 },
            "write":   { "enabled": true, "topic": "southbound/{ComponentName}/{InstanceId}/write" },
            "pollGroups": [
              { "id": "main", "pollIntervalMs": 1000,
                "tags": [
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
| Config source `CONFIGMAP` | Reads `config.json` from the mounted ConfigMap directory and hot-reloads when you `kubectl apply` a new ConfigMap (the `..data` swap). Editing the map re-applies config without a pod restart. |
| `messaging.local.host` | Point at an **in-cluster** broker Service DNS name (`emqx.default.svc.cluster.local`). |
| `connection.host` | Point at the device/gateway's **Service** or reachable address (`modbus-sim.default.svc.cluster.local`) — the adapter runs in-cluster, so the device must be reachable from the pod network. |
| Identity (no `-t`) | The Thing name resolves from the Downward API (`GGCOMMONS_THING_NAME` ▸ `POD_NAME`), so `{ThingName}` in topics is the pod name unless overridden. |
| Health/metrics ports | The Deployment exposes the library's HTTP health endpoint (`/startupz`, `/livez`, `/readyz`) for k8s probes; `metricEmission.target: prometheus` can expose `/metrics` for scraping. |

The polling, type, deadband, and command behavior are identical to the other platforms — only the
config source, broker/device addressing, and identity differ.

---

## How the cross-cutting options affect runtime

These behaviors apply to **every** configuration above.

### Poll interval, coalescing, and bus load

The poll manager turns each group's tags into the **fewest Modbus reads** possible: tags on the same
table are sorted by address and merged into contiguous read blocks, bridging gaps up to `maxGap` and
capping each block at the protocol limit (125 holding/input registers, 2000 coil/discrete bits). Net
bus load ≈ `(read blocks per group) × (1000 / pollIntervalMs)` requests/second per group. Two levers:

- **Lower `pollIntervalMs`** → fresher data, proportionally more requests and messages.
- **Raise `maxGap`** → nearby tags collapse into one larger read (fewer round-trips, lower per-request
  overhead) at the cost of reading some unused registers in between. Coalescing is per table, so mixing
  tables in a group means at least one read per table.

A poll-group thread measures its own work time and waits `pollIntervalMs − elapsed`, so a slow read
shortens (never lengthens) the next sleep — the configured cadence is the ceiling, not an addition.

### Decoding raw registers (`type` / `wordOrder` / `byteOrder` / `scale` / `bit`)

Modbus carries only bits and 16-bit registers; richer types are synthesized in `codec.py`. A read
block's registers are sliced per tag, then assembled into the value: `wordOrder` orders the registers
(big = most-significant first; little = reversed), `byteOrder` orders the bytes within each register,
and the `type`'s width decides how many registers are consumed. `scale`/`offset` then apply the linear
transform; `bit` extracts a single bit. If a decode raises (e.g. a malformed string), that tag is
published with quality `BAD` and the rest of the block continues.

### Deadband and publish mode (data freshness vs message volume)

Under `publishMode: onChange`, every decoded value is compared to the **last published** value via the
tag's `deadband` and only republished if it passes — the first reading after start always publishes.
This suppresses noise/jitter so steady signals don't flood the bus. `publishMode: always` bypasses the
deadband and publishes every poll. `batchMs` is orthogonal: it coalesces whatever was published in a
window into fewer messages.

### Reconnect, timeout, and read failures

At startup each instance's `connect()` **blocks and retries every 5 seconds** until the device answers,
so a device down at launch doesn't crash the adapter — other instances keep running (each instance has
its own worker/connection). `connection.timeoutMs` bounds each individual request; a read that times
out, errors, or returns a Modbus exception marks **every tag in that read block** with quality `BAD`
(value `null`) and increments the `readErrors` counter, while the loop stays alive and retries on the
next interval. The `southbound_health` metric's `connectionState` (1/0) and `readErrors` reflect this;
it is emitted to `metricEmission.target` and queryable on the `…/control/status` topic.

### Reads vs writes (the command surface)

Polling is the read **plane**. The command surface is separate:

- **Writes** require `write.enabled: true` (otherwise the write topic is never subscribed). A write is
  fire-and-forget to `write.topic` with `{ "writes": [ { "name": "Setpoint", "value": 42.5 } ] }` (or a
  single `{ "name": …, "value": … }`). Only **writable tables** accept writes — `coil` and `holding`;
  `discrete`/`input` are rejected with a warning, and `bit` (single-bit) writes are skipped (the
  read-modify-write is not implemented). `scale`/`offset` are inverted on the way down.
- **Reads** are request/reply on `read.topic` (set `reply_to`/`correlation_id`) and return a
  `SouthboundReadResult` — on-demand, independent of the poll loop.
- **Control** queries (`…/control/status`, `…/control/tags`) return connection state + counters and the
  resolved tag list.

A tag-ref in any command is either `{ "name": "<configured tag>" }` or an explicit
`{ unitId?, table, address, type, wordOrder?, scale?, … }` for arbitrary access. See
[reference/messaging-interface.md](reference/messaging-interface.md) for the full payloads.
