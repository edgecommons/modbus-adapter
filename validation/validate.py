"""Data-plane smoke test for the Modbus adapter against the pymodbus simulator over EMQX.

  A: poll -> SouthboundSignalUpdate for a changing signal (GOOD, Modbus address shape).
  B: on-demand read by name (Scaled -> 25.0 via scale; Alarm3 -> True via bit extract).
  C: write round-trip (int16 / float32 / string / coil), confirmed by reading back.
  D: control plane (status, signals).
"""
import json
import sys
import time
import uuid
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

BROKER_HOST, BROKER_PORT = "localhost", 1883
msgs = []
checks = []


def check(name, ok, detail=""):
    checks.append((name, bool(ok), detail))


def on_connect(c, u, f, rc, p=None):
    c.subscribe("southbound/#")


def on_message(c, u, msg):
    try:
        msgs.append((msg.topic, json.loads(msg.payload.decode())))
    except Exception:
        pass


def updates():
    return [(t, p) for t, p in msgs if p.get("header", {}).get("name") == "SouthboundSignalUpdate"]


def samples_for(name):
    out = []
    for _, p in updates():
        if p.get("body", {}).get("signal", {}).get("name") == name:
            out.extend(p["body"]["samples"])
    return out


def request(c, topic, body, timeout=5):
    cid = str(uuid.uuid4())
    reply = f"southbound/reply/{cid}"
    h = {"name": "req", "version": "1.0", "timestamp": datetime.now(timezone.utc).isoformat(),
         "uuid": str(uuid.uuid4()), "correlation_id": cid, "reply_to": reply}
    c.publish(topic, json.dumps({"header": h, "tags": {}, "body": body}))
    deadline = time.time() + timeout
    while time.time() < deadline:
        for t, p in list(msgs):
            if t == reply and p.get("header", {}).get("correlation_id") == cid:
                return p
        time.sleep(0.1)
    return None


def reads_by_name(reply):
    out = {}
    for e in (reply.get("body", {}).get("reads", []) if reply else []):
        # signal.id is "u1/holding/40/uint16"; key by the address tuple via id is fine, but we match by name
        out.setdefault(e["signal"]["address"].get("address"), e)
    return out


def main():
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="modbus-validate")
    c.on_connect = on_connect
    c.on_message = on_message
    c.connect(BROKER_HOST, BROKER_PORT, 60)
    c.loop_start()

    print("[*] waiting up to 30s for SouthboundSignalUpdate...", flush=True)
    deadline = time.time() + 30
    while time.time() < deadline and len(updates()) < 3:
        time.sleep(0.5)
    if not updates():
        print("FAIL: no updates; is the adapter running on validation/config.json against the sim?", flush=True)
        sys.exit(1)
    time.sleep(2)

    parts = updates()[0][0].split("/")
    comp, inst = parts[2], parts[3]
    read_topic = f"southbound/{comp}/{inst}/read"
    write_topic = f"southbound/{comp}/{inst}/write"
    print(f"[*] component={comp} instance={inst}", flush=True)

    # A: changing signal + envelope shape
    counter_vals = {json.dumps(s.get("value")) for s in samples_for("Counter16")}
    a_addr = next((p["body"]["signal"]["address"] for _, p in updates()
                   if p["body"]["signal"]["name"] == "Counter16"), {})
    a_dev = next((p["body"]["device"] for _, p in updates()), {})
    check("changing signal (Counter16)", len(counter_vals) >= 2, f"{len(counter_vals)} distinct")
    check("envelope adapter=modbus", a_dev.get("adapter") == "modbus", f"{a_dev}")
    check("address shape", a_addr.get("table") == "holding" and a_addr.get("unitId") == 1
          and a_addr.get("type") == "uint16", f"{a_addr}")
    quals = {s.get("quality") for s in samples_for("Counter16")}
    check("quality GOOD", quals == {"GOOD"}, f"{quals}")

    # B: on-demand read by name (scale + bit)
    rp = request(c, read_topic, {"signals": [{"name": "Scaled"}, {"name": "Alarm3"}]})
    by_addr = reads_by_name(rp)
    check("read Scaled == 25.0", abs((by_addr.get(40) or {}).get("value", 0) - 25.0) < 1e-6,
          f"{(by_addr.get(40) or {}).get('value')}")
    check("read Alarm3 (bit3) == True", (by_addr.get(41) or {}).get("value") is True,
          f"{(by_addr.get(41) or {}).get('value')}")

    # C: write round-trip
    writes = [{"name": "RWInt16", "value": -1234}, {"name": "RWFloat32", "value": 12.5},
              {"name": "RWString", "value": "hello"}, {"name": "RunCmd", "value": True}]
    c.publish(write_topic, json.dumps({"header": {"name": "w", "correlation_id": str(uuid.uuid4())},
                                       "tags": {}, "body": {"writes": writes}}))
    time.sleep(1.5)
    rp = request(c, read_topic, {"signals": [{"name": "RWInt16"}, {"name": "RWFloat32"},
                                             {"name": "RWString"}, {"name": "RunCmd"}]})
    got = {e["signal"]["address"]["address"]: e.get("value") for e in (rp.get("body", {}).get("reads", []) if rp else [])}
    check("write int16 -1234", got.get(10) == -1234, f"{got.get(10)}")
    check("write float32 12.5", abs((got.get(24) or 0) - 12.5) < 1e-6, f"{got.get(24)}")
    check("write string 'hello'", got.get(30) == "hello", f"{got.get(30)}")
    check("write coil True", got.get(0) is True, f"{got.get(0)}")  # RunCmd is coil address 0

    # D: control
    st = request(c, f"southbound/{comp}/{inst}/control/status", {})
    sb = st.get("body", {}) if st else {}
    check("status connected", bool(sb.get("connected")) and "metrics" in sb, f"{sb.get('connected')}")
    tg = request(c, f"southbound/{comp}/{inst}/control/signals", {})
    names = {t.get("name") for t in (tg.get("body", {}).get("signals", []) if tg else [])}
    check("signals query", {"Counter16", "Scaled", "RWInt16"}.issubset(names), f"{len(names)} signals")

    c.loop_stop()
    c.disconnect()
    print("\n================ MODBUS DATA-PLANE ================", flush=True)
    npass = nfail = 0
    for name, ok, detail in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {name:28} {detail}", flush=True)
        npass += ok
        nfail += not ok
    print(f"\n========== {npass}/{npass + nfail} PASS ({'ALL PASS' if nfail == 0 else str(nfail) + ' FAIL'}) ==========", flush=True)
    sys.exit(0 if nfail == 0 else 1)


if __name__ == "__main__":
    main()
