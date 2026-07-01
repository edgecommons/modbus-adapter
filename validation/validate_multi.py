"""One adapter, two Modbus servers at once (config-multi.json): plc1 -> :5020, plc2 -> :5021.

Confirms both instances stream concurrently with the correct per-instance identity (device.instance /
endpoint), publish to distinct {InstanceId} topics, and that an on-demand read on each instance's topic
routes to that server only. Start two sims (ports 5020, 5021) and the adapter on config-multi.json.
"""
import json
import sys
import time
import uuid
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

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
    return [p for _, p in msgs if p.get("header", {}).get("name") == "SouthboundSignalUpdate"]


def by_instance(inst):
    return [p for p in updates() if p.get("body", {}).get("device", {}).get("instance") == inst]


def request(c, topic, body, timeout=5):
    cid = str(uuid.uuid4())
    reply = f"southbound/reply/{cid}"
    h = {"name": "req", "correlation_id": cid, "reply_to": reply,
         "timestamp": datetime.now(timezone.utc).isoformat(), "uuid": str(uuid.uuid4()), "version": "1.0"}
    c.publish(topic, json.dumps({"header": h, "tags": {}, "body": body}))
    deadline = time.time() + timeout
    while time.time() < deadline:
        for t, p in list(msgs):
            if t == reply and p.get("header", {}).get("correlation_id") == cid:
                return p
        time.sleep(0.1)
    return None


def main():
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="modbus-multi")
    c.on_connect = on_connect
    c.on_message = on_message
    c.connect("localhost", 1883, 60)
    c.loop_start()

    print("[*] waiting up to 40s for both instances to stream...", flush=True)
    deadline = time.time() + 40
    while time.time() < deadline:
        if by_instance("plc1") and by_instance("plc2"):
            break
        time.sleep(0.5)
    time.sleep(2)

    p1, p2 = by_instance("plc1"), by_instance("plc2")
    check("plc1 streaming", len(p1) > 0, f"{len(p1)} updates")
    check("plc2 streaming", len(p2) > 0, f"{len(p2)} updates")
    ep1 = {p["body"]["device"]["endpoint"] for p in p1}
    ep2 = {p["body"]["device"]["endpoint"] for p in p2}
    check("plc1 endpoint :5020", any(":5020" in e for e in ep1), f"{ep1}")
    check("plc2 endpoint :5021", any(":5021" in e for e in ep2), f"{ep2}")
    check("distinct endpoints", ep1 and ep2 and ep1.isdisjoint(ep2), f"{ep1} vs {ep2}")

    comp = None
    for t, p in msgs:
        parts = t.split("/")
        if len(parts) >= 5 and parts[3] == "plc1":
            comp = parts[2]
            break
    if comp:
        r1 = request(c, f"southbound/{comp}/plc1/read", {"signals": [{"name": "Counter16"}]})
        r2 = request(c, f"southbound/{comp}/plc2/read", {"signals": [{"name": "Counter16"}]})
        check("plc1 read routes", bool(r1) and r1["body"]["reads"][0]["quality"] == "GOOD")
        check("plc2 read routes", bool(r2) and r2["body"]["reads"][0]["quality"] == "GOOD")
    else:
        check("component derivable", False)

    c.loop_stop()
    c.disconnect()
    print("\n================ MODBUS MULTI-SERVER ================", flush=True)
    npass = nfail = 0
    for name, ok, detail in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {name:24} {detail}", flush=True)
        npass += ok
        nfail += not ok
    print(f"\n========== {npass}/{npass + nfail} PASS ({'ALL PASS' if nfail == 0 else str(nfail) + ' FAIL'}) ==========", flush=True)
    sys.exit(0 if nfail == 0 else 1)


if __name__ == "__main__":
    main()
