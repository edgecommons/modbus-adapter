# How-to Guides

Recipes for specific tasks. Each assumes the adapter builds and runs (see the [tutorial](tutorial.md)).
For concepts see [explanation.md](explanation.md); for exhaustive options see [reference/](reference/).

---

## Define a register map (tags)

Modbus has no discovery — you declare every tag. Put tags in a poll group on the instance:

```jsonc
"pollGroups": [
  { "id": "process", "pollIntervalMs": 500, "tags": [
    { "name": "Temperature", "table": "holding", "address": 0, "type": "float32", "scale": 0.1 },
    { "name": "Running",     "table": "coil",    "address": 0, "type": "bool" },
    { "name": "FaultBit2",   "table": "holding", "address": 10, "type": "bool", "bit": 2 }
  ] }
]
```

- `table`: `holding`/`input` (16-bit registers) or `coil`/`discrete` (bits).
- `address` is the **0-based** PDU address (not the 4xxxx/1-based convention).
- Pick `type` to match the device's encoding; set `wordOrder`/`byteOrder` if it isn't big/big
  (see [data-types](reference/data-types.md)). Use `scale`/`offset` for engineering units, `bit` to
  pull one bit of a register.

---

## Match the device's number format

If 32/64-bit values come out wrong (byte-swapped or word-swapped), set the order on the tag:

```jsonc
{ "name": "Energy", "table": "holding", "address": 20, "type": "uint32", "wordOrder": "little" }
```

The four combinations of `wordOrder` × `byteOrder` cover ABCD/BADC/CDAB/DCBA. The
[data-types table](reference/data-types.md#byte--word-order-multi-register-types) maps them out.

---

## Tune poll rate and reduce traffic

| You want… | Set |
|-----------|-----|
| Faster/slower polling | `pollGroups[].pollIntervalMs` |
| Publish only on real change | `publishMode: "onChange"` (default) + a `deadband` per tag |
| Publish every poll | `publishMode: "always"` |
| Drop sensor jitter | `deadband: { "type": "absolute", "value": 0.5 }` (or `percent`) |
| Fewer, larger messages | `publish.batchMs > 0` (coalesce a tag's samples per interval) |
| Fewer Modbus reads | raise `maxGap` so nearby tags merge into one read block |

The poller already merges contiguous tags of the same table into single reads (capped at 125
registers / 2000 bits); `maxGap` lets it bridge small holes between tags.

---

## Read and write tags from a client

**Write** (needs `write.enabled: true`) — fire-and-forget:
```
topic:   southbound/<ComponentName>/<InstanceId>/write
payload: { "writes": [ { "name": "Setpoint", "value": 42.5 } ] }
```

**Read** — request/reply (set `reply_to` + `correlation_id`):
```
publish   southbound/<ComponentName>/<InstanceId>/read
          { "header": { "reply_to": "app/r", "correlation_id": "7" }, "body": { "tags": [ { "name": "Temperature" } ] } }
subscribe app/r   → SouthboundReadResult
```

Address a tag by `name` (a configured tag) or explicitly by
`{ unitId?, table, address, type, wordOrder?, scale?, … }` for arbitrary access. Read-only tables
(`discrete`/`input`) reject writes. Full schemas: [messaging reference](reference/messaging-interface.md).

---

## Bridge several devices from one adapter

Add an instance per device under `component.instances[]` — each gets its own connection/worker, so one
device being down doesn't disturb the others:

```jsonc
"instances": [
  { "id": "plc1", "connection": { "transport": "tcp", "host": "10.0.0.50", "port": 502, "unitId": 1 }, "pollGroups": [ ... ] },
  { "id": "plc2", "connection": { "transport": "tcp", "host": "10.0.0.51", "port": 502, "unitId": 1 }, "pollGroups": [ ... ] }
]
```

Multiple **unit ids** behind one gateway: give each poll group its own `unitId` within a single
instance.

---

## Use serial RTU

```jsonc
"connection": { "transport": "rtu", "serialPort": "/dev/ttyUSB0", "baudRate": 9600,
                "parity": "N", "stopBits": 1, "byteSize": 8, "unitId": 1 }
```

For a serial-to-Ethernet gateway that speaks RTU framing over a socket, use
`"transport": "rtutcp"` with `host`/`port`. The tag/type/poll model is identical across transports.

---

## Deploy to a platform

**HOST:** `python main.py --platform HOST --transport MQTT ./messaging.json -c FILE ./config.json -t my-thing`

**Greengrass:** package per `gdk-config.json`/`recipe.yaml`; config comes from the deployment
(`--platform GREENGRASS -c GG_CONFIG`).

**Kubernetes:** build the image and apply the manifests (config from a mounted ConfigMap, identity from
the Downward API).

---

## Observe health and status

- **Metric** `southbound_health` (`connectionState`, `readErrors`) flows to `metricEmission.target`.
- **Status query:** request/reply on `…/control/status` → `{ connected, metrics }`.
- **Tags query:** request/reply on `…/control/tags` → the resolved tag list with addresses.
- **Logs:** each subsystem logs under its own name with the `[<instanceId>]` prefix.
