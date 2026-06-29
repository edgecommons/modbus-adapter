# Tutorial — From zero to live values

By the end you'll have the adapter polling a Modbus simulator and publishing value changes onto MQTT,
and you'll have read and written a tag from a client. No hardware required.

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

You should see it connect, coalesce the configured tags into read blocks, and start. The config
(`validation/config.json`) defines one instance (`plc1`) polling holding/coil/discrete/input tags.

## 4. Watch values flow

Subscribe to the bus (any MQTT client):

```bash
mosquitto_sub -t 'southbound/#' -v
```

You'll see `SouthboundTagUpdate` messages for the changing tags (e.g. `Counter16`, `Temp`), each with
a `value`, normalized `quality`, and a Modbus `address` (`{unitId, table, address, type}`).

## 5. Read a tag on demand

Publish a read request and watch the reply (set `reply_to` to a topic you subscribe to). With a
GGCommons client this is one `request()` call; raw MQTT:

```
publish southbound/ModbusAdapter/plc1/read
  {"header":{"reply_to":"app/r","correlation_id":"1"},"body":{"tags":[{"name":"Scaled"}]}}
subscribe app/r   →  SouthboundReadResult with Scaled = 25.0 (raw 250 × scale 0.1)
```

## 6. Write a tag

```
publish southbound/ModbusAdapter/plc1/write
  {"body":{"writes":[{"name":"Setpoint?","value":42.5}]}}     # use a writable holding/coil tag
```

Read it back to confirm. (In `config.json`, `RWFloat32` / `RWInt16` / `RWString` / `RunCmd` are
writable scratch tags.)

## 7. Prove it end-to-end

```bash
python validation/validate.py        # poll→publish, read, write round-trip, control — ALL PASS
```

Next: the [how-to guides](how-to-guides.md) for defining your own register map, tuning rates, and
deploying; the [reference](reference/) for every option; the [explanation](explanation.md) for the
model.
