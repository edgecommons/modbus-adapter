# Validation harness (Modbus adapter smoke tests)

Reproducible end-to-end tests: a Python **pymodbus** Modbus/TCP simulator + an MQTT test client drive
the adapter and verify poll→publish, on-demand read, write round-trip, multi-server operation, and the
full data-type/feature matrix — over a local MQTT broker.

## Prerequisites

- `pip install -e . -r requirements-test.txt` (brings `pymodbus`, `paho-mqtt`, `pytest`).
- A local MQTT broker on `localhost:1883` (e.g. `docker run -d -p 1883:1883 emqx/emqx`).

Run commands from the repo root.

## Unit tests (no broker/sim needed)

```bash
python -m pytest          # codec (all types x byte/word orders), config, coalescing
```

## Data-plane + suite (sim + adapter + MQTT)

```bash
python validation/modbus_sim_server.py --port 5020 &
python main.py --platform HOST --transport MQTT validation/messaging-local.json \
       -c FILE validation/config.json -t modbus-thing &
python validation/validate.py            # poll->publish, read (scale+bit), write round-trip, control
python validation/validate_suite.py      # every type write->read-back, word order, explicit ref, BAD quality
```

## Multiple servers at once

```bash
python validation/modbus_sim_server.py --port 5020 &
python validation/modbus_sim_server.py --port 5021 &
python main.py --platform HOST --transport MQTT validation/messaging-local.json \
       -c FILE validation/config-multi.json -t modbus-thing &
python validation/validate_multi.py      # plc1->:5020, plc2->:5021 stream concurrently + route independently
```

## The simulator

`modbus_sim_server.py` is a pymodbus Modbus/TCP slave (unit id 1) with a known register map (see its
docstring). A background **loopback client** drives a changing counter + temperature ramp and
re-asserts the fixed seeds each tick — pymodbus 3.13's deprecated in-process datastore mis-serves
sparse construction-seeded values, but client writes are reliable, so the sim seeds itself the same
way the adapter writes.

## RTU coverage

Serial RTU needs a virtual COM pair (com0com on Windows / `socat` PTYs on Linux) — documented, not
gating here. **RTU-over-TCP** (`"transport": "rtutcp"`) exercises the RTU framing + codec against a
TCP sim with no serial hardware: start the sim and point an instance's `connection.transport` at
`rtutcp` (the sim would need the RTU framer too; the codec/poll/command paths are transport-agnostic).

## Notes

- Modbus is **plaintext** (no auth/TLS in classic Modbus); security is network-level. There is no
  credential/cert handling in this adapter.
- The pymodbus deprecation warnings from the sim are harmless.
