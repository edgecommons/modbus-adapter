# GGCommons Modbus Adapter

The **Python reference** southbound protocol adapter, built on the `ggcommons`
(`greengrass-commons`) library and the southbound contract (`docs/SOUTHBOUND.md` in the ggcommons
monorepo). It bridges **Modbus** devices — TCP, serial RTU, and RTU-over-TCP — onto a message bus:
it polls register maps and republishes value changes as `SouthboundSignalUpdate` messages on the
**Unified Namespace** `data` class (`ecv1/{device}/ModbusAdapter/{instance}/data/{signal}`), serves
on-demand reads/writes/control through the library **command inbox** (`sb/read`, `sb/write`,
`sb/status`, `sb/signals`, `reconnect`, `repoll`), emits `evt`-class connection/write events (through
the library's `data()`/`events()` facades), and reports the `southbound_health` metric. It runs
wherever you deploy it — a Greengrass v2 component, a standalone process, or a Kubernetes pod.

Sibling of the Java OPC UA reference adapter; same southbound contract, the poll-based counterpart to
OPC UA's subscribe-based model.

## Status

In development. See `docs/` for the operator/integrator guide and `validation/` for the reproducible
end-to-end test harness (a pymodbus simulator + MQTT validators).

## Quick start (local, against the simulator)

```bash
pip install -e . -r requirements-test.txt
python validation/modbus_sim_server.py &          # pymodbus TCP slave
python main.py --platform HOST --transport MQTT validation/messaging-local.json \
       -c FILE validation/config.json -t modbus-thing
```

Subscribe to `ecv1/+/+/+/data/#` to watch telemetry (and `ecv1/+/+/+/state`, `ecv1/+/+/+/metric/#`
for the keepalive + health). For local dev against the **sibling** ggcommons UNS library, install it
editable into the venv first: `pip install -e ../ggcommons/libs/python`, then `pip install -e .
-r requirements-test.txt`.

## Tests

```bash
python -m pytest          # unit suite (codec / config / coalescing / commands / publisher / events),
                          # with the org 90% coverage gate (scoped to the CI-testable surface; see .coveragerc)
```
