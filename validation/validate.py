"""Data-plane smoke test for the Modbus adapter against the pymodbus simulator over EMQX (UNS).

  A: poll -> SouthboundSignalUpdate on the UNS data class (GOOD, Modbus address shape, identity stamped).
  B: on-demand read by name via the command inbox (Scaled -> 25.0 via scale; Alarm3 -> True via bit).
  C: write round-trip via the command inbox (int16 / float32 / string / coil), confirmed by reading back,
     and the resulting evt/write audit event.
  D: control verbs (sb/status, sb/signals).

Telemetry rides ecv1/{device}/modbus-adapter/{instance}/data/#; commands ride the component-scope inbox
ecv1/{device}/modbus-adapter/cmd/{verb} (verbs sb/read, sb/write, sb/status, sb/signals), with the
target instance carried in the request body. Replies are {"ok":true,"result":...} on the request's
reply_to.
"""
import json
import sys
import time
import uuid
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

BROKER_HOST, BROKER_PORT = "localhost", 1883
REPLY_PREFIX = "modbusval/reply"
msgs = []
checks = []


def check(name, ok, detail=""):
    checks.append((name, bool(ok), detail))


def on_connect(c, u, f, rc, p=None):
    c.subscribe("ecv1/+/+/+/data/#")       # UNS telemetry (data class)
    c.subscribe("ecv1/+/+/+/evt/#")        # UNS events (evt class)
    c.subscribe(f"{REPLY_PREFIX}/#")       # command replies


def on_message(c, u, msg):
    try:
        msgs.append((msg.topic, json.loads(msg.payload.decode())))
    except Exception:
        pass


def updates():
    return [(t, p) for t, p in msgs if p.get("header", {}).get("name") == "SouthboundSignalUpdate"]


def events():
    return [(t, p) for t, p in msgs if "/evt/" in t]


def samples_for(name):
    out = []
    for _, p in updates():
        if p.get("body", {}).get("signal", {}).get("name") == name:
            out.extend(p["body"]["samples"])
    return out


def request(c, cmd_base, verb, body, timeout=5):
    """Send a command-inbox request and wait for its {"ok":..,"result":..} reply."""
    cid = str(uuid.uuid4())
    reply = f"{REPLY_PREFIX}/{cid}"
    h = {"name": verb, "version": "1.0", "timestamp": datetime.now(timezone.utc).isoformat(),
         "uuid": str(uuid.uuid4()), "correlation_id": cid, "reply_to": reply}
    c.publish(f"{cmd_base}/{verb}", json.dumps({"header": h, "tags": {}, "body": body}))
    deadline = time.time() + timeout
    while time.time() < deadline:
        for t, p in list(msgs):
            if t == reply and p.get("header", {}).get("correlation_id") == cid:
                return p
        time.sleep(0.1)
    return None


def result_of(reply):
    b = (reply or {}).get("body", {})
    return b.get("result", {}) if b.get("ok") else {}


def reads_by_addr(reply):
    out = {}
    for e in result_of(reply).get("reads", []):
        out.setdefault(e["signal"]["address"].get("address"), e)
    return out


def main():
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="modbus-validate")
    c.on_connect = on_connect
    c.on_message = on_message
    c.connect(BROKER_HOST, BROKER_PORT, 60)
    c.loop_start()

    print("[*] waiting up to 30s for SouthboundSignalUpdate on the UNS data class...", flush=True)
    deadline = time.time() + 30
    while time.time() < deadline and len(updates()) < 3:
        time.sleep(0.5)
    if not updates():
        print("FAIL: no updates; is the adapter running on validation/config.json against the sim?", flush=True)
        sys.exit(1)
    time.sleep(2)

    # UNS data topic: ecv1/{device}/{component}/{instance}/data/{signal}
    parts = updates()[0][0].split("/")
    device, comp, inst = parts[1], parts[2], parts[3]
    cmd_base = f"ecv1/{device}/{comp}/cmd"     # the component-scope command inbox
    print(f"[*] device={device} component={comp} instance={inst}", flush=True)
    check("data class topic", parts[4] == "data", updates()[0][0])

    # A: changing signal + envelope shape + top-level identity
    counter_vals = {json.dumps(s.get("value")) for s in samples_for("Counter16")}
    a_addr = next((p["body"]["signal"]["address"] for _, p in updates()
                   if p["body"]["signal"]["name"] == "Counter16"), {})
    a_dev = next((p["body"]["device"] for _, p in updates()), {})
    a_ident = next((p.get("identity") for _, p in updates()), {}) or {}
    check("changing signal (Counter16)", len(counter_vals) >= 2, f"{len(counter_vals)} distinct")
    check("envelope adapter=modbus", a_dev.get("adapter") == "modbus", f"{a_dev}")
    check("identity stamped (component/instance)",
          a_ident.get("component") == comp and a_ident.get("instance") == inst, f"{a_ident}")
    check("no tags.thing", "thing" not in (next((p.get("tags", {}) for _, p in updates()), {}) or {}))
    check("address shape", a_addr.get("table") == "holding" and a_addr.get("unitId") == 1
          and a_addr.get("type") == "uint16", f"{a_addr}")
    quals = {s.get("quality") for s in samples_for("Counter16")}
    check("quality GOOD", quals == {"GOOD"}, f"{quals}")

    # B: on-demand read by name (scale + bit) via the command inbox
    rp = request(c, cmd_base, "sb/read", {"instance": inst, "signals": [{"name": "Scaled"}, {"name": "Alarm3"}]})
    by_addr = reads_by_addr(rp)
    check("read Scaled == 25.0", abs((by_addr.get(40) or {}).get("value", 0) - 25.0) < 1e-6,
          f"{(by_addr.get(40) or {}).get('value')}")
    check("read Alarm3 (bit3) == True", (by_addr.get(41) or {}).get("value") is True,
          f"{(by_addr.get(41) or {}).get('value')}")

    # C: write round-trip via the command inbox + the evt/write audit event
    writes = [{"name": "RWInt16", "value": -1234}, {"name": "RWFloat32", "value": 12.5},
              {"name": "RWString", "value": "hello"}, {"name": "RunCmd", "value": True}]
    wr = request(c, cmd_base, "sb/write", {"instance": inst, "writes": writes})
    check("write reply ok", (wr or {}).get("body", {}).get("ok") is True and result_of(wr).get("written") == 4,
          f"{result_of(wr).get('written')}")
    time.sleep(1.5)
    rp = request(c, cmd_base, "sb/read", {"instance": inst, "signals": [
        {"name": "RWInt16"}, {"name": "RWFloat32"}, {"name": "RWString"}, {"name": "RunCmd"}]})
    got = {e["signal"]["address"]["address"]: e.get("value") for e in result_of(rp).get("reads", [])}
    check("write int16 -1234", got.get(10) == -1234, f"{got.get(10)}")
    check("write float32 12.5", abs((got.get(24) or 0) - 12.5) < 1e-6, f"{got.get(24)}")
    check("write string 'hello'", got.get(30) == "hello", f"{got.get(30)}")
    check("write coil True", got.get(0) is True, f"{got.get(0)}")  # RunCmd is coil address 0
    wevts = [p for t, p in events() if t.endswith("/evt/write")]
    check("evt/write emitted", any(e.get("body", {}).get("signal") == "RWInt16" for e in wevts),
          f"{len(wevts)} write events")

    # D: control verbs
    st = request(c, cmd_base, "sb/status", {"instance": inst})
    sb = result_of(st)
    check("status connected", bool(sb.get("connected")) and "metrics" in sb, f"{sb.get('connected')}")
    tg = request(c, cmd_base, "sb/signals", {"instance": inst})
    names = {t.get("name") for t in result_of(tg).get("signals", [])}
    check("signals query", {"Counter16", "Scaled", "RWInt16"}.issubset(names), f"{len(names)} signals")

    c.loop_stop()
    c.disconnect()
    print("\n================ MODBUS DATA-PLANE (UNS) ================", flush=True)
    npass = nfail = 0
    for name, ok, detail in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {name:34} {detail}", flush=True)
        npass += ok
        nfail += not ok
    print(f"\n========== {npass}/{npass + nfail} PASS ({'ALL PASS' if nfail == 0 else str(nfail) + ' FAIL'}) ==========", flush=True)
    sys.exit(0 if nfail == 0 else 1)


if __name__ == "__main__":
    main()
