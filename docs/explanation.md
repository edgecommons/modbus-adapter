# Explanation — How the Modbus adapter works, and why

This page is the mental model. For exact options see [reference/](reference/); for tasks, the
[how-to guides](how-to-guides.md).

## The southbound contract

The adapter is a *consumer* of the cross-language **southbound contract** (the same one the OPC UA
reference adapter implements): it publishes a normalized `SouthboundSignalUpdate` envelope, exposes a
read/write command surface, and emits a `southbound_health` metric. The cloud sees the same shape
regardless of protocol — only `device.adapter` and the opaque `signal.address` differ. This adapter is
the **poll-based** reference; OPC UA is the subscribe-based one.

## The Unified Namespace (UNS)

Addressing follows the UNS: every topic is `ecv1/{device}/{component}/{instance}/{class}[/channel]`,
built and validated by the library — never a hand-assembled string. Telemetry rides the `data` class
(`ecv1/{device}/ModbusAdapter/{instance}/data/{signal}`); discrete events ride `evt`; the on-demand
command surface rides the library's `cmd` inbox; and the library owns `state` (a keepalive),
`metric` (the health + system metrics), and `cfg` automatically. Every message carries a top-level
**`identity`** element (`{hier, path, component, instance}`) placing the reading in the enterprise
tree — routing and partitioning never parse the body or the topic. A fleet consumer needs one wildcard
per class (`ecv1/+/+/+/data/#`, `…/evt/#`, `…/metric/#`, `…/state`), not per-adapter topic templates.

## Poll, not subscribe

Modbus has no eventing — a device only answers requests. So instead of subscribing, the adapter
**polls**: each poll group has its own interval and its own worker thread that reads its signals, decides
what changed, and publishes. "Change" is decided client-side: with `publishMode: onChange` (the
default) a signal publishes only when its value moves past its `deadband`; with `always` it publishes
every poll. This is the Modbus equivalent of OPC UA's server-side monitored-item deadband — moved into
the adapter because the protocol can't do it for you.

## Coalescing: fewer reads

Reading each signal with its own request would be slow. The poller **coalesces** signals of the same table
into the fewest Modbus reads: it sorts by address, merges runs (bridging gaps up to `maxGap`), caps
each block at the protocol limit (125 registers, 2000 bits), issues one read per block, and slices
each signal's value out. So 20 nearby holding registers become one FC3 instead of 20. Wall-clock per
poll is the number of *blocks*, not the number of *signals*.

## The type & scaling layer — the heart of it

This is where Modbus differs most from a "typed" protocol like OPC UA. The wire only has **bits and
16-bit registers**; *types are a fiction the adapter maintains from config*. A `float32` is two
registers reassembled per the configured **byte and word order** and unpacked; a scaled `int16` is one
register times `scale` plus `offset`; a `bool` may be a coil or a single bit of a register. The codec
(`codec.py`) is the one place that converts both ways and is exhaustively unit-tested across every type
and all four order combinations.

Two consequences worth internalizing:
- **You must know the device's register map and encoding** — there is no discovery to fall back on.
  The map lives entirely in your config; get the address, type, and byte/word order right and the
  values are correct, get them wrong and you get plausible garbage.
- **Identity is explicit.** `signal.name` is your label, `signal.id` (`u<unit>/<table>/<addr>/<type>`) is a
  stable canonical key, and `signal.address` is the protocol-native handle for round-tripping reads/writes.

## Two planes

- **Data plane** — high-rate, fire-and-forget telemetry: `SouthboundSignalUpdate` out on the `data`
  class (through the library's `data()` facade); discrete events out on the `evt` class (through
  `events()`) — a `critical` connection alarm (`evt/critical/connection`, raised on drop / cleared on
  restore) and an `info`/`warning` write audit (`evt/{info|warning}/write`). Severity **derives** the
  channel, so the topic and the body can never disagree.
- **Control plane** — low-rate request/reply through the `cmd` inbox: `sb/write`, on-demand `sb/read`,
  and `sb/status` / `sb/signals` / `reconnect` / `repoll` verbs.

Keeping them separate means a consumer can fire a control verb without perturbing the telemetry
stream, and routing/partitioning can key on the data-plane topic alone. The command inbox is a single
`main`-instance subscription (`ecv1/{device}/ModbusAdapter/main/cmd/#`); a multi-instance adapter picks
the target device with an `instance` field in the request body.

## Quality

Every sample carries a normalized `quality` (`GOOD`/`BAD`/`UNCERTAIN`) plus `qualityRaw` (the native
detail). This is structural, not adapter discipline: the library's `data()` facade **requires** a
quality on every sample it constructs, defaulting an omitted one to `GOOD` (Modbus has no native
quality codes to report) and marking the synthesis `qualityRaw: "unspecified"` so a consumer can tell
a synthesized `GOOD` from a device-reported one. A Modbus exception or timeout publishes `BAD` for
every signal in the failed read block, with the exception text in `qualityRaw`, so a consumer sees an
outage rather than a stale value silently persisting. (A block failure has no value at all to report —
not even a "null" reading — so that one case goes through the facade's raw escape hatch rather than its
normal value-required builder; see `publisher.py`'s module docstring.)

## Instances are independent

One adapter process runs one worker per `component.instances[]` entry. Each connects (and *reconnects*,
retrying every 5s) on its own thread, so a device going offline only takes its own instance's signals to
`BAD` — the others keep streaming. This is the fault-isolation you want when one adapter fronts a fleet.

## A note on security

Classic Modbus has **no authentication or encryption** — it's plaintext on the wire. There is
deliberately no credential/cert handling in this adapter; secure Modbus at the **network** layer (a
dedicated VLAN, a VPN, firewalling the device subnet). (Modbus/TLS exists in the spec but is rarely
supported by devices; it's out of scope here.) This is the clean simplification versus the OPC UA
adapter, whose security layer does not carry over.
