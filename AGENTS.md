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
`southbound_health`, and reports per-slave connectivity **and state** in the `state` keepalive's
`instances[]`.
Richer types are synthesized from bits + 16-bit registers (byte/word order, scale/offset, bit
extraction) — Modbus has no eventing, discovery, or native quality.

## Layout

- `main.py` — builds `EdgeCommons`, spawns one `ModbusDevice` worker thread per
  `component.instances[]` entry, registers the `sb/*` verbs at `CommandScope.INSTANCE` + the three
  panels on the shared command inbox, dispatches each request into the addressed device through
  `modbus_adapter/routing.py`, and installs the keepalive connectivity provider.
- `modbus_adapter/routing.py` — the adapter-side half of instance resolution (`resolve_instance`,
  D-SC-4): the library resolves the addressing (topic token, body `instance`, conflict refusal);
  this applies the configured default (optional iff one device) and `NO_SUCH_INSTANCE`.
- `modbus_adapter/instance_state.py` — the single instance state model (D-SC-7): `device_state`
  (`ONLINE`/`PAUSED`/`BACKOFF`/`CONNECTING`) read by both `sb/status` and the keepalive, plus the
  `instances[]` sample builder.
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
- **`southbound_health`'s measure set is exact** (SOUTHBOUND.md §5, all eight): `connectionState`,
  `publishLatencyMs`, `pollLatencyMs`, `readErrors`, `staleSignals`, `reconnects`, `writeErrors`
  (device-path write failures only — policy refusals/unresolved refs/missing values/encode errors
  never count), `signalsSubscribed` (the served configured/polled inventory while connected, 0 while
  disconnected). `health.py`'s `HEALTH_MEASURES` is the parity anchor `tests/test_health.py` asserts
  against — move it, the builder, and `docs/reference/metrics.md` together.
- **Every sample carries a quality.** A failed read publishes a `BAD` sample (never omitted); a
  successful read leaves quality for the `data()` facade to default to `GOOD`/`qualityRaw:"unspecified"`
  (Modbus has no native quality).
- **`repoll` is refused while paused** with the top-level code `PAUSED` (a whole-operation refusal,
  not a `BAD_ARGS`); `sb/pause`/`sb/resume` are confirmed + idempotent, reply `{paused, changed}`.
- **Every verb declares `CommandScope.INSTANCE`** (SOUTHBOUND §2.2 / D-SC-2): all nine act on one
  slave. The library owns addressing — topic token, body `instance`, the conflict-first `BAD_ARGS` —
  and hands the handler the resolved instance; the adapter never parses a topic or reads
  `body["instance"]`. Only the two configuration-dependent policies stay here
  (`routing.resolve_instance`, D-SC-4): the configured default (optional iff exactly one device;
  otherwise `BAD_ARGS`) and `NO_SUCH_INSTANCE` for an unknown instance.
- **One instance state model** (D-SC-7): `instance_state.device_state` is the only place a state
  token is decided, and both surfaces read it — `sb/status`'s `state` field and the `state`
  keepalive's `instances[]` entries (`ONLINE`/`PAUSED`/`BACKOFF`/`CONNECTING`). Never add a second
  bookkeeping path; a paused instance must publish `PAUSED` so a fleet view can tell it from a stale
  one.
- **Standardized error codes:** `BAD_ARGS`, `PAUSED`, `NO_SUCH_INSTANCE`, `WRITE_NOT_ALLOWED`,
  `WRITE_FAILED`, `RECONNECT_FAILED`. No `WRITE_DISABLED`/`INSTANCE_REQUIRED`/`INSTANCE_NOT_FOUND`.

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
