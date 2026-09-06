# Tutorial — From zero to live values

By the end you'll have the adapter polling a Modbus simulator and publishing value changes onto MQTT,
and you'll have read and written a signal from a client. No hardware required.

## 1. Prerequisites

- Python 3.9+, and a local MQTT broker on `localhost:1883`
  (`docker run -d -p 1883:1883 emqx/emqx`).
- From the repo root: `pip install -e . -r requirements-test.txt`. In this organization workspace, use `pip install -e ../core/libs/python paho-mqtt` for the matching protobuf client.
- Install [ec-uns-cmd](https://github.com/edgecommons/ec-uns-cmd) and put it on `PATH` for commands. The Python here-documents below use a Bash-compatible shell; in PowerShell, save their contents as `.py` files and run `python <file>.py`.

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

Normal MQTT and Greengrass IPC messaging carries EdgeCommons protobuf bytes. Decode the data
messages before displaying their human-readable JSON projection:

```bash
python - <<'PY'
import json
import paho.mqtt.client as mqtt
from edgecommons.messaging.message import Message

c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
c.on_connect = lambda c, u, f, rc, p: c.subscribe("ecv1/modbus-thing/modbus-adapter/plc1/data/#", qos=1)
def on_message(c, u, m):
    message = Message.from_bytes(m.payload)
    print(m.topic, json.dumps(message.to_diagnostic_json(), indent=2))
c.on_message = on_message
c.connect("localhost", 1883)
try:
    c.loop_forever()
except KeyboardInterrupt:
    pass
finally:
    c.unsubscribe("ecv1/modbus-thing/modbus-adapter/plc1/data/#")
    c.disconnect()
PY
```

Each update has `body.signal`, `body.samples`, protocol address metadata, and the publisher's
top-level `identity`. This filter covers this simulator instance. Component keepalive and metrics
use `ecv1/modbus-thing/modbus-adapter/state` and
`ecv1/modbus-thing/modbus-adapter/metric/#`, without an instance segment.

## 5. Read a signal on demand

The instance is selected by the topic through `--instance`. The body below is a native JSON command
argument object; `ec-uns-cmd` builds the protobuf message and prints the correlated reply's `result`.

```bash
ec-uns-cmd --broker localhost:1883 --device modbus-thing --component modbus-adapter --instance plc1 sb/read --body '{"signals":[{"name":"Scaled"}]}'
```

The `reads` entries report current values and per-entry outcomes; `Scaled` applies the configured scale.

## 6. Write a signal

`RWFloat32` is a writable scratch signal in the supplied simulator configuration:

```bash
ec-uns-cmd --broker localhost:1883 --device modbus-thing --component modbus-adapter --instance plc1 sb/write --body '{"writes":[{"name":"RWFloat32","value":42.5}]}'
ec-uns-cmd --broker localhost:1883 --device modbus-thing --component modbus-adapter --instance plc1 sb/read --body '{"signals":[{"name":"RWFloat32"}]}'
```

Check the write's per-entry result and that the read returns `42.5`.

## 7. Validate the result

The live checks above cover polling, protobuf publication and a read/write round trip. For the local
unit suite run `python -m pytest`. The older JSON-wire clients in `validation/` are not current
protobuf conformance gates; see the [validation guide](../validation/README.md) for their status.
Stop the watcher, adapter and simulator with Ctrl-C when finished.

Next: the [how-to guides](how-to-guides.md) for defining your own register map, tuning rates, and
deploying; the [reference](reference/) for every option; the [explanation](explanation.md) for the
model.
