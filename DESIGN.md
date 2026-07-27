# modbus-adapter — design decisions

Internal decision register for the Modbus reference adapter. Status/history/roadmap live here, not in
the user-facing `docs/` (which describe current behavior only). See `core/docs/SOUTHBOUND.md` for the
cross-language contract this component implements, and `core/docs/platform/DESIGN-cli-scaffold-parity.md`
for the component baseline.

## Architecture

One `EdgeCommons` runtime; one `ModbusDevice` worker thread per `component.instances[]` entry (each
slave's connection lifecycle is independent, so one device down does not affect the others). The
protocol I/O sits behind `connection.py` (the pymodbus client + live liveness); everything above it —
poll manager, publisher, command surface, health, metrics — is written against in-memory fakes and
unit-tested without a broker or PLC. The on-demand command surface is served through the library
command inbox (`gg.get_commands()`), registered once on the component-scope inbox and dispatched into
the addressed device by the request body's `instance` selector.

## Decision register

- **D-M1 — `writes.allow[]` replaces the boolean write toggle (hard rename, no alias).** Writes are
  gated per-entry by an allow-list matched on the stable `signal.id` (`ServerConfiguration.permits`),
  checked in `command_service.write()` **before any device I/O** (SOUTHBOUND.md §2.2 / D-U16). The
  prior `write.enabled` boolean is removed outright — there is no fallback or deprecation alias. This
  is a breaking **config + wire** change: existing configs carrying `write.enabled` must migrate to
  `writes.allow[]` (the old key is silently ignored → read-only). The Modbus table-writability check
  (`coil`/`holding` writable; `discrete`/`input` read-only) and the bit-write restriction are kept as
  per-entry failures. Rationale: an adapter that writes whatever it is asked to is a control-system
  vulnerability; an allow-list on a stable id is the contract's posture. The shipped
  `test-configs`/`validation` + `recipe.yaml` + `k8s/configmap.yaml` are migrated in the same change.

- **D-M2 — standardized error codes.** `WRITE_DISABLED` → `WRITE_NOT_ALLOWED` (whole batch refused by
  the allow-list); `INSTANCE_REQUIRED` → `BAD_ARGS`; `INSTANCE_NOT_FOUND` → `NO_SUCH_INSTANCE`. Added:
  `WRITE_FAILED` (every *attempted* allowed write was rejected by the device), `BAD_ARGS` (a malformed
  `sb/browse` cursor/ref, or mixing the paged and hierarchical browse forms), and `PAUSED` (`repoll`
  while paused — a whole-operation refusal per the amended SOUTHBOUND §2.2; originally shipped here as
  `BAD_ARGS`, migrated in the 2026-07 southbound conformance pass — a wire-visible code change).
  `RECONNECT_FAILED` is unchanged. Breaking
  wire change (§2.2 standardized set); pre-1.0/experimental, so a documented break is acceptable.
  Per-entry write failures remain reported in `results[]`; only an all-failed batch raises
  `WRITE_FAILED`, preserving per-entry granularity in mixed batches.

- **D-M3 — `sb/pause`/`sb/resume`.** A per-instance `PauseState` latch (`pause.py`) shared by the poll
  manager (skips polling while paused, loop stays alive), the device tick (skips the batched-publish
  flush), and the command surface. Confirmed + idempotent, reply `{paused, changed}`. `repoll` is
  refused while paused (`PAUSED`, see D-M2) — a paused instance publishes nothing. The paused flag is surfaced
  in `sb/status`. *Deviation from the template:* it is **not** added to the `state` keepalive's
  `instances[]` connectivity, because `modbus-adapter` pins `edgecommons python-lib/v0.3.0`, whose
  `InstanceConnectivity` may predate `with_state`/`with_attributes`; surfacing it there would risk an
  API-version mismatch for no contract benefit. `sb/status` is the authoritative paused surface.

- **D-M4 — `southbound_health` to the exact §5 eight-measure set.** `health.py` emits
  `connectionState`, `publishLatencyMs`, `pollLatencyMs`, `readErrors`, `staleSignals`, `reconnects`,
  `writeErrors`, and `signalsSubscribed` (the last two added in the 2026-07 southbound conformance
  pass, when the amended §5 fixed all eight as the set).
  Latencies are the last observed poll-cycle / publish-call durations (surfaced from the poll manager
  and publisher onto the shared `ClientMetrics`); `staleSignals` counts configured signals with no
  successful read for longer than `component.global.healthThresholds.staleSignalSecs` (default 30) —
  refreshed on every successful decode, not only on publish, so a stable value is not counted stale;
  `reconnects` counts link recoveries observed by the device tick; `writeErrors` (Count, 60, drained
  on emit like `readErrors`) counts DEVICE-PATH write failures only — the entry passed validation +
  the allow-list and then failed at the device (`command_service._write_one` splits the encode step
  from the device write so a caller-side encode error never counts); `signalsSubscribed` (Count, 1,
  gauge) is the served configured/polled inventory while connected and 0 while disconnected — Modbus
  is a polling protocol, so "subscribed" is the register map the session serves. The pre-existing
  `ModbusPublish.publishLatencyMs` / `ModbusPoll.pollDurationMs` operational measures are unchanged —
  §5 surfaces them on the canonical metric additionally.

- **D-M5 — `sb/browse` as a configured-inventory walk, paged + hierarchical.** Modbus has no
  address-space discovery (signals are declared explicitly), so `sb/browse` serves the *configured*
  inventory rather than returning `BROWSE_UNSUPPORTED` — the verb answers usefully and stays distinct
  from `sb/signals` (single-shot full inventory). Two mutually exclusive request forms over the same
  inventory: **paged** (`{cursor?, max?}` → `{entries:[{id,name,type}], cursor?}`; the cursor is an
  opaque offset token) and — added in the 2026-07 southbound conformance pass for the `treeBrowser`
  panel — **hierarchical**, selected by the presence of `ref` (`{ref, depth?, maxRefs?}` →
  `{id, mode, root:{nodeId,name,nodeClass,dataType,refs}, refCount, depth, truncated}`; `"root"` is
  the device node whose `contains` refs are the flat signal inventory, a signal id is a known leaf,
  an unknown ref is `BAD_ARGS`; `depth`/`maxRefs` clamp to 1..4 / 1..1000; mixing `ref`/`depth`/
  `maxRefs` with `cursor`/`max`, or `depth`/`maxRefs` without `ref`, is `BAD_ARGS`).

- **D-M6 — edge-console panel trio, at the renderable descriptor floor.** `overview`/`signals`/
  `diagnostics` registered via `commands.register_panel` (order 10/20/30, `scope: "instance"`), each
  bound only to verbs this adapter serves. Defined next to the command surface
  (`command_service.panels()`), registered in `main.py`. The widgets carry what the current console
  renderer requires (floor raised in the 2026-07 southbound conformance pass): the overview
  `summary` has `rows` and the `commandSummary` a `verbs` list (the pre-conformance `fields`/
  `actions` keys are gone); the `signalGrid` names `signalsVerb` **and** `subscriptionsVerb` (both →
  `sb/signals`; the second is a descriptor-compat alias the shipped console reads — no
  `sb/subscriptions` wire verb exists) plus `readVerb`; the `treeBrowser` names `browseVerb`,
  `mode: "hierarchical"`, `rootRef: "root"`, and widget-level `scope: "instance"` (repeated on every
  command-backed widget). No widget advertises a `writeVerb` — the guarded-write console flow does
  not exist; writes stay on the command surface behind the allow-list. The diagnostics
  `keyValueList` widget is kept as this repo's own extra view.

- **D-M7 — Greengrass recipe + kebab artifact.** The recipe previously bundled a pre-rebrand
  `greengrass_commons-*.whl` that is never produced, so a fresh GG deploy failed at install. Fixed to
  install directly from `requirements.txt` (which carries the `edgecommons` git pin), mirroring the
  `python-protocol-adapter` template. The GG artifact is kebab-cased `ModbusAdapter.zip` →
  `modbus-adapter.zip` with matching `{artifacts:decompressedPath}/modbus-adapter/` paths; the
  Greengrass **component name** stays PascalCase reverse-DNS (`com.mbreissi.edgecommons.ModbusAdapter`).

## Known consumers of the breaking changes (grepped, not assumed)

- `edge-console` (`ui/`, `protocol/`, `gateway/`): no literal use of the old modbus error codes or
  `write.enabled` — command replies are rendered descriptor-generically.
- `bottling-company-test`: the two shipped modbus device configs
  (`sites/dallas-site/configs/{filling-line,packaging-line}/…`) carry `write: {enabled: false}`. Under
  the hard rename that key is ignored and the device stays read-only (empty `writes.allow`), so
  behavior is unchanged; the dead key should be removed on the next bottling-company-test update. Not
  edited from this repo.

## License

Business Source License 1.1 (`LICENSE`). No Apache-2.0 mismatch exists in this repo (no manifest
declares a conflicting license), so there is nothing to reconcile here.
