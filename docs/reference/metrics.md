# Reference - Metrics

The Modbus adapter emits health and operational metrics through the EdgeCommons metric service. With
`metricEmission.target: messaging`, metrics are published on the reserved UNS `metric` class:

```text
ecv1/{device}/modbus-adapter/metric/{metricName}
```

The adapter never writes reserved `metric` topics directly. It defines metrics through `MetricEmitter`,
so the same names, measures, units, and dimensions are used by messaging, CloudWatch, and Prometheus
targets.

## Dimension model

Dimensions are intentionally low-cardinality and CloudWatch-friendly. The adapter uses `instance`,
`connectionType`, `pollGroup`, `table`, `result`, `publishMode`, and `verb`, plus runtime-injected
component dimensions.

Signal names, Modbus addresses, endpoint URLs, and raw error text are not metric dimensions. Use data
messages, events, logs, or command replies for those details.

## `southbound_health`

The canonical southbound health metric every adapter emits (SOUTHBOUND.md §5), per instance.

Dimensions: `instance`.

| Measure | Unit | Purpose |
|---|---:|---|
| `connectionState` | Count | `1` connected, `0` disconnected. Drives simple health alarms. |
| `publishLatencyMs` | Milliseconds | Latency of the most recent northbound `data` publish. Detects a slow local broker or IPC. |
| `pollLatencyMs` | Milliseconds | Round-trip of the most recent poll cycle. Detects slow slaves or overloaded links. |
| `readErrors` | Count | Read errors observed during the reporting interval. Identifies polling failures without inspecting logs. |
| `staleSignals` | Count | Configured signals with no successful read for longer than `component.global.healthThresholds.staleSignalSecs` (default 30). Surfaces silently-stuck signals a flat value cannot reveal. |
| `reconnects` | Count | Link recoveries observed during the interval. Flags an unstable field network. |

## `ModbusConnection`

Connection lifecycle and liveness for one Modbus instance.

Dimensions: `instance`, `connectionType`.

| Measure | Unit | Purpose |
|---|---:|---|
| `connectionState` | Count | `1` connected, `0` disconnected. Helps determine current PLC/slave reachability. |
| `connectAttempts` | Count | Initial connection attempts in the interval. Helps identify startup or reconnect loops. |
| `connectFailures` | Count | Failed initial connection attempts in the interval. Helps separate unavailable devices from quiet devices. |
| `reconnectAttempts` | Count | Explicit reconnect command attempts. Helps audit operator or automation recovery actions. |
| `reconnectFailures` | Count | Failed explicit reconnect attempts. Helps detect persistent southbound failure. |
| `connectionDrops` | Count | Live links marked down by transport, IO, or no-response reads. Helps identify unstable field networks. |
| `connectedDurationMs` | Milliseconds | Time spent connected since the previous emission. Helps calculate availability and duty cycle. |

## `ModbusInventory`

Static poll inventory and coalescing shape.

Dimensions: `instance`, `pollGroup`, `table`.

| Measure | Unit | Purpose |
|---|---:|---|
| `configuredSignals` | Count | Signals configured in the poll group/table. Helps confirm configuration size. |
| `readBlocks` | Count | Coalesced Modbus read blocks. Helps measure whether address layout is efficient. |
| `configuredPollIntervalMs` | Milliseconds | Poll interval configured for the group. Helps correlate load and freshness. |
| `coalescingRatio` | None | Signals divided by read blocks. Helps spot sparse maps that create excess protocol calls. |
| `writableSignals` | Count | Writable signals in writable tables when writes are enabled. Helps confirm the write surface exposed by configuration. |

## `ModbusPoll`

Polling work and sample production by group, table, and result.

Dimensions: `instance`, `pollGroup`, `table`, `result` (`success` or `error`).

| Measure | Unit | Purpose |
|---|---:|---|
| `pollCycles` | Count | Poll cycles observed. Helps confirm each group is being scheduled. |
| `pollDurationMs` | Milliseconds | Accumulated poll work time. Helps detect slow slaves or overloaded serial links. |
| `protocolReadRequests` | Count | Modbus protocol reads issued. Helps estimate southbound load. |
| `protocolReadErrors` | Count | Failed protocol reads. Helps identify address, network, or slave errors. |
| `registersRead` | Count | Registers or bits read from successful blocks. Helps quantify field data volume. |
| `signalsDecoded` | Count | Signals decoded successfully. Helps detect codec/config mismatches. |
| `samplesGood` | Count | GOOD samples produced. Helps measure usable telemetry output. |
| `samplesBad` | Count | BAD samples produced from read/decode failures. Helps quantify data quality problems. |
| `samplesChanged` | Count | Samples offered for publishing after publish-mode/deadband checks. Helps explain emitted data volume. |
| `samplesSuppressed` | Count | Decoded samples suppressed by `onChange` or deadband. Helps validate deadband behavior. |
| `pollOverruns` | Count | Poll loops whose work exceeded the configured interval. Helps identify overloaded poll schedules. |

## `ModbusPublish`

Data-message publication after polling.

Dimensions: `instance`, `publishMode`.

| Measure | Unit | Purpose |
|---|---:|---|
| `dataMessagesPublished` | Count | `SouthboundSignalUpdate` messages published. Helps measure outbound bus volume. |
| `samplesPublished` | Count | Samples included in published messages. Helps compare batching to message count. |
| `publishFailures` | Count | Data publish failures swallowed by the adapter. Helps identify local bus problems. |
| `batchFlushes` | Count | Buffered signal batches flushed. Helps verify batching behavior. |
| `batchSize` | Count | Samples in flushed or published batches. Helps tune `publish.batchMs`. |
| `publishLatencyMs` | Milliseconds | Accumulated publish call latency. Helps detect local broker or IPC slowdown. |

## `ModbusCommand`

Command-plane activity and results.

Dimensions: `instance`, `verb`, `result` (`success` or `error`).

| Measure | Unit | Purpose |
|---|---:|---|
| `commandRequests` | Count | Command handler invocations. Helps measure operator/API demand. |
| `commandLatencyMs` | Milliseconds | Accumulated command handler latency. Helps detect slow reads, writes, or control actions. |
| `commandErrors` | Count | Command handlers that returned a coded error. Helps alert on failed command use. |
| `readSignals` | Count | Signals returned by `sb/read`. Helps measure explicit read volume. |
| `writeSignals` | Count | Write entries supplied to `sb/write`. Helps audit write demand. |
| `writeFailures` | Count | Write entries reported as failed. Helps detect denied or failed control writes. |
| `reconnectRequests` | Count | Explicit reconnect command requests. Helps audit recovery actions. |
| `repollRequests` | Count | Explicit repoll command requests. Helps audit forced polling actions. |
