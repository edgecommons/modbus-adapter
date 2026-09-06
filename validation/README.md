# Modbus validation support

Current manual validation starts with the [simulator tutorial](../docs/tutorial.md): run the
shipped TCP simulator and adapter configuration, decode protobuf data, and use `ec-uns-cmd` to
verify correlated read/write outcomes. The register map is documented in `modbus_sim_server.py`.

```bash
python -m pytest
python validation/modbus_sim_server.py --port 5020
```

Run the simulator separately from the unit suite. The unit suite exercises local adapter logic;
it does not certify hardware, serial wiring, TLS, or deployed Greengrass behavior.

## Legacy clients requiring repair

`validate.py`, `validate_suite.py`, and `validate_multi.py` construct or decode JSON message
envelopes directly. Current MQTT and Greengrass IPC messaging carries protobuf bytes. These
clients are retained as historical scenario references and **must not be used as current
conformance gates** until their wire handling and assertions are updated. Old ALL PASS output
does not establish current release acceptance.

Use the current tutorial for a bounded live command round trip. Cross-repository validation is
owned by the Dallas [bottling-company-test](https://github.com/edgecommons/bottling-company-test)
harness; deployed Greengrass validation is a separate lab gate.
