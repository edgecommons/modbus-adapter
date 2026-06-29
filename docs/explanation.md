# Explanation — How the Modbus adapter works, and why

This page is the mental model. For exact options see [reference/](reference/); for tasks, the
[how-to guides](how-to-guides.md).

## The southbound contract

The adapter is a *consumer* of the cross-language **southbound contract** (the same one the OPC UA
reference adapter implements): it publishes a normalized `SouthboundTagUpdate` envelope, exposes a
read/write command surface, and emits a `southbound_health` metric. The cloud sees the same shape
regardless of protocol — only `device.adapter` and the opaque `tag.address` differ. This adapter is
the **poll-based** reference; OPC UA is the subscribe-based one.

## Poll, not subscribe

Modbus has no eventing — a device only answers requests. So instead of subscribing, the adapter
**polls**: each poll group has its own interval and its own worker thread that reads its tags, decides
what changed, and publishes. "Change" is decided client-side: with `publishMode: onChange` (the
default) a tag publishes only when its value moves past its `deadband`; with `always` it publishes
every poll. This is the Modbus equivalent of OPC UA's server-side monitored-item deadband — moved into
the adapter because the protocol can't do it for you.

## Coalescing: fewer reads

Reading each tag with its own request would be slow. The poller **coalesces** tags of the same table
into the fewest Modbus reads: it sorts by address, merges runs (bridging gaps up to `maxGap`), caps
each block at the protocol limit (125 registers, 2000 bits), issues one read per block, and slices
each tag's value out. So 20 nearby holding registers become one FC3 instead of 20. Wall-clock per
poll is the number of *blocks*, not the number of *tags*.

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
- **Identity is explicit.** `tag.name` is your label, `tag.id` (`u<unit>/<table>/<addr>/<type>`) is a
  stable canonical key, and `tag.address` is the protocol-native handle for round-tripping reads/writes.

## Two planes

- **Data plane** — high-rate, fire-and-forget telemetry: `SouthboundTagUpdate` out, `write` in.
- **Control plane** — low-rate request/reply: on-demand `read`, and `status` / `tags` queries.

Keeping them separate means a consumer can fire a control query without perturbing the telemetry
stream, and routing/partitioning can key on the data-plane topic alone.

## Quality

Every sample carries a normalized `quality` (`GOOD`/`BAD`/`UNCERTAIN`) plus `qualityRaw` (the native
detail). A successful read is `GOOD`; a Modbus exception or timeout publishes `BAD` for every tag in
the failed read block, with the exception text in `qualityRaw`, so a consumer sees an outage rather
than a stale value silently persisting.

## Instances are independent

One adapter process runs one worker per `component.instances[]` entry. Each connects (and *reconnects*,
retrying every 5s) on its own thread, so a device going offline only takes its own instance's tags to
`BAD` — the others keep streaming. This is the fault-isolation you want when one adapter fronts a fleet.

## A note on security

Classic Modbus has **no authentication or encryption** — it's plaintext on the wire. There is
deliberately no credential/cert handling in this adapter; secure Modbus at the **network** layer (a
dedicated VLAN, a VPN, firewalling the device subnet). (Modbus/TLS exists in the spec but is rarely
supported by devices; it's out of scope here.) This is the clean simplification versus the OPC UA
adapter, whose security layer does not carry over.
