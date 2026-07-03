# Tutorial — From zero to live values

By the end you'll have the adapter polling a Modbus simulator and publishing value changes onto MQTT,
and you'll have read and written a signal from a client. No hardware required.

## 1. Prerequisites

- Python 3.9+, and a local MQTT broker on `localhost:1883`
  (`docker run -d -p 1883:1883 emqx/emqx`).
- From the repo root: `pip install -e . -r requirements-test.txt`.

## 2. Start the simulator

```bash
python validation/modbus_sim_server.py --port 5020
```

It serves a Modbus/TCP slave (unit 1) with a known register map and a counter/ramp that change every
half second (see the script's docstring for the map).

## 3. Run the adapter

In another shell:

```bash
python main.py --platform HOST --transport MQTT validation/messaging-local.json \
       -c FILE validation/config.json -t modbus-thing
```

You should see it connect, coalesce the configured signals into read blocks, and start. The config
(`validation/config.json`) defines one instance (`plc1`) polling holding/coil/discrete/input signals.

## 4. Watch values flow

Subscribe to the UNS data class (any MQTT client) — one wildcard covers the whole fleet:

```bash
mosquitto_sub -t 'ecv1/+/+/+/data/#' -v
```

You'll see `SouthboundSignalUpdate` messages on `ecv1/modbus-thing/ModbusAdapter/plc1/data/{signal}`
for the changing signals (e.g. `Counter16`, `Temp`), each with a `value`, normalized `quality`, a
Modbus `address` (`{unitId, table, address, type}`), and the top-level `identity`. (Also try
`ecv1/+/+/+/state` for the keepalive and `ecv1/+/+/+/metric/#` for `southbound_health`.)

## 5. Read a signal on demand

Read/write go through the command inbox (`ecv1/{device}/ModbusAdapter/main/cmd/{verb}`); set
`header.name` to the verb and `reply_to` to a topic you subscribe. With a GGCommons client this is one
`request()` call; raw MQTT:

```
publish ecv1/modbus-thing/ModbusAdapter/main/cmd/sb/read
  {"header":{"name":"sb/read","reply_to":"app/r","correlation_id":"1"},"body":{"signals":[{"name":"Scaled"}]}}
subscribe app/r   →  { "ok": true, "result": { "reads": [ { "value": 25.0, ... } ] } }   # raw 250 × scale 0.1
```

## 6. Write a signal

```
publish ecv1/modbus-thing/ModbusAdapter/main/cmd/sb/write
  {"header":{"name":"sb/write","reply_to":"app/r","correlation_id":"2"},
   "body":{"writes":[{"name":"RWFloat32","value":42.5}]}}      # a writable holding/coil signal
```

Read it back to confirm. (In `config.json`, `RWFloat32` / `RWInt16` / `RWString` / `RunCmd` are
writable scratch signals.)

## 7. Prove it end-to-end

```bash
python validation/validate.py        # poll→publish, read, write round-trip, control — ALL PASS
```

Next: the [how-to guides](how-to-guides.md) for defining your own register map, tuning rates, and
deploying; the [reference](reference/) for every option; the [explanation](explanation.md) for the
model.
