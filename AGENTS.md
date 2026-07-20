# modbus-adapter — agent guidance

This is the **Python reference southbound protocol-adapter** (`com.mbreissi.edgecommons.ModbusAdapter`),
built on the `edgecommons` library and the cross-language **southbound contract**
(`core/docs/SOUTHBOUND.md`). It follows the org-wide conventions in the parent EdgeCommons workspace's
`AGENTS.md` — read that first if you have it; what follows is scoped to this repo. It is the
**poll-based** counterpart to the subscribe-based OPC UA reference adapter, and much of the
CLI-scaffold-parity baseline was modelled on it.

## What this component is

The adapter connects to Modbus slaves (TCP, serial RTU, RTU-over-TCP), polls a config-declared
register map, detects change client-side, and republishes value changes as `SouthboundSignalUpdate`
messages on the UNS `data` class. It serves the standardized `sb/*` command surface plus
`southbound_health`, and reports per-slave connectivity in the `state` keepalive's `instances[]`.
Richer types are synthesized from bits + 16-bit registers (byte/word order, scale/offset, bit
extraction) — Modbus has no eventing, discovery, or native quality.

## Layout

- `main.py` — builds `EdgeCommons`, spawns one `ModbusDevice` worker thread per
  `component.instances[]` entry, registers the `sb/*` verbs + the three panels on the shared command
  inbox, and dispatches each request into the addressed device by its body `instance` selector.
- `modbus_adapter/device.py` — coordinates one instance: connection + poll manager + publisher +
  command service + health + the pause latch + a tick that flushes batched publishes and emits health.
- `modbus_adapter/connection.py` — **the protocol seam**: the pymodbus client, connect/retry, and
  table read/write helpers with live liveness tracking. Live-infra (`.coveragerc`-omitted).
- `modbus_adapter/poll_manager.py` — coalesces contiguous addresses into the fewest reads, decodes,
  applies change/deadband, feeds the publisher; one daemon thread per poll group; suspends while paused.
- `modbus_adapter/publisher.py` — batches/publishes reads through the instance's `data()` facade
  (never a hand-assembled body or topic).
- `modbus_adapter/command_service.py` — the `sb/*` command surface (`read`/`write`/`status`/`signals`/
  `browse`/`pause`/`resume`/`reconnect`/`repoll`) + the three edge-console panel descriptors.
- `modbus_adapter/health.py` — the canonical `southbound_health` metric (SOUTHBOUND.md §5).
- `modbus_adapter/metrics.py` — `ClientMetrics` (the health-feeding counters/trackers) + the Modbus
  operational-metric families (`ModbusConnection/Inventory/Poll/Publish/Command`).
- `modbus_adapter/pause.py` — the per-instance pause latch shared by the poll manager, tick, and
  command surface.
- `modbus_adapter/codec.py`, `modbus_adapter/config/` — decode/encode + the config resolvers
  (`ServerConfiguration`, `ConnectionInfo`, `PollGroup`, `SignalSpec`, `DeadbandSpec`).
- `config.schema.json` — the config this component understands (validated against `component.global`
  by `edgecommons component validate`). Keep `additionalProperties: false`.
- `tests/` — unit tests against `tests/_fakes.py` (an in-memory `FakeConn` + recording messaging bound
  to a real `EdgeCommonsInstance`); no broker/PLC needed.
- `validation/` — the live HOST smoke (a pymodbus sim + MQTT validators).
- `docs/` — Diátaxis documentation describing the component; keep it in sync with the code.

## Non-negotiable invariants (do not remove)

- **The write allow-list is checked BEFORE any device I/O.** `command_service.py`'s `write()` gates
  every entry on `config.permits(signal_id)` — matched on the stable `signal.id` (`writes.allow[]`,
  SOUTHBOUND.md §2.2 / D-U16) — before the write reaches the device. There is no boolean write toggle.
- **`southbound_health`'s measure set is exact** (SOUTHBOUND.md §5): `connectionState`,
  `publishLatencyMs`, `pollLatencyMs`, `readErrors`, `staleSignals`, plus the §5-optional
  `reconnects`. `health.py`'s `HEALTH_MEASURES` is the parity anchor `tests/test_health.py` asserts
  against — move it, the builder, and `docs/reference/metrics.md` together.
- **Every sample carries a quality.** A failed read publishes a `BAD` sample (never omitted); a
  successful read leaves quality for the `data()` facade to default to `GOOD`/`qualityRaw:"unspecified"`
  (Modbus has no native quality).
- **`repoll` is refused while paused** (`BAD_ARGS`); `sb/pause`/`sb/resume` are confirmed + idempotent,
  reply `{paused, changed}`.
- **Instance routing** (D-EIP-13): the body `instance` selector is optional iff exactly one device is
  configured; otherwise a missing id is `BAD_ARGS` and an unknown id is `NO_SUCH_INSTANCE`.
- **Standardized error codes:** `BAD_ARGS`, `NO_SUCH_INSTANCE`, `WRITE_NOT_ALLOWED`, `WRITE_FAILED`,
  `RECONNECT_FAILED`. No `WRITE_DISABLED`/`INSTANCE_REQUIRED`/`INSTANCE_NOT_FOUND`.

## Validation expectations

- `python -m pytest` must pass with no broker, no device, and no cloud credentials.
- The org coverage gate is **90% line coverage** (`.github/workflows/ci.yml`'s reusable
  `component-ci.yml` runs `python -m pytest`; the gate rides `pyproject.toml` addopts). `.coveragerc`
  scopes it to the CI-testable surface — the only exclusions are the live-infra seams
  (`connection.py` pymodbus socket I/O, `device.py` orchestration), validated by the `validation/`
  HOST smoke. Add tests rather than lowering the gate or excluding testable code.
- A metric family or command verb you add needs a template test asserting its measure names / verb
  behavior (`tests/test_metrics.py`, `tests/test_operational_metrics.py`, `tests/test_commands.py`).
- Wire/behavior changes reachable through Greengrass are validated on `lab-5950x` per the org matrix.

## Docs stay in sync with code

Any change that adds/removes a command verb, a metric family/measure, or a config key must update the
matching page under `docs/` in the same change (`docs/reference/{messaging-interface,metrics,
configuration}.md` describe exact topics, measures, and config). Treat stale docs as a defect.

## Building against the library

`requirements.txt`/`pyproject.toml` pin `edgecommons` by git ref. For local dev against the sibling
monorepo checkout, run `pip install -e ../core/libs/python` after the initial install (see `CLAUDE.md`).
