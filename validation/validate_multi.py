"""One adapter, two Modbus servers at once (config-multi.json): plc1 -> :5020, plc2 -> :5021 (UNS).

Confirms both instances stream concurrently on their own UNS data topics with the correct per-instance
identity (top-level identity.instance + body device.instance/endpoint), and that an on-demand read
addressed (by the request-body 'instance' selector) to each instance routes to that server only. The
command inbox is a single component-scope inbox; the instance selector fans it out. Start two sims
(ports 5020, 5021) and the adapter on config-multi.json.
"""
import json
import sys
import time
import uuid
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

REPLY_PREFIX = "modbusval/reply"
msgs = []
checks = []


def check(name, ok, detail=""):
    checks.append((name, bool(ok), detail))


def on_connect(c, u, f, rc, p=None):
    c.subscribe("ecv1/+/+/+/data/#")
    c.subscribe(f"{REPLY_PREFIX}/#")


def on_message(c, u, msg):
    try:
        msgs.append((msg.topic, json.loads(msg.payload.decode())))
    except Exception:
        pass


def updates():
    return [(t, p) for t, p in msgs if p.get("header", {}).get("name") == "SouthboundSignalUpdate"]


def by_instance(inst):
    return [p for _, p in updates() if p.get("body", {}).get("device", {}).get("instance") == inst]


def request(c, cmd_base, verb, body, timeout=5):
    cid = str(uuid.uuid4())
    reply = f"{REPLY_PREFIX}/{cid}"
    h = {"name": verb, "correlation_id": cid, "reply_to": reply,
         "timestamp": datetime.now(timezone.utc).isoformat(), "uuid": str(uuid.uuid4()), "version": "1.0"}
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
    check("plc1 endpoint reported", len(ep1) == 1 and all(e.startswith("tcp://") for e in ep1), f"{ep1}")
    check("plc2 endpoint reported", len(ep2) == 1 and all(e.startswith("tcp://") for e in ep2), f"{ep2}")
    check("distinct endpoints", ep1 and ep2 and ep1.isdisjoint(ep2), f"{ep1} vs {ep2}")

    # distinct per-instance data topics (ecv1/{device}/{comp}/{instance}/data/...)
    topics1 = {t for t, p in updates() if p["body"]["device"]["instance"] == "plc1"}
    topics2 = {t for t, p in updates() if p["body"]["device"]["instance"] == "plc2"}
    check("distinct data topics", all("/plc1/data/" in t for t in topics1)
          and all("/plc2/data/" in t for t in topics2), f"{len(topics1)}|{len(topics2)}")

    # command routing: derive device+component from an observed data topic, address each instance by body
    device = comp = None
    for t, p in updates():
        parts = t.split("/")
        if parts[3] == "plc1":
            device, comp = parts[1], parts[2]
            break
    if device:
        cmd_base = f"ecv1/{device}/{comp}/cmd"
        r1 = request(c, cmd_base, "sb/read", {"instance": "plc1", "signals": [{"name": "Counter16"}]})
        r2 = request(c, cmd_base, "sb/read", {"instance": "plc2", "signals": [{"name": "Counter16"}]})
        check("plc1 read routes", bool(result_of(r1).get("reads")) and result_of(r1)["reads"][0]["quality"] == "GOOD")
        check("plc2 read routes", bool(result_of(r2).get("reads")) and result_of(r2)["reads"][0]["quality"] == "GOOD")
        # unknown instance -> coded error
        rbad = request(c, cmd_base, "sb/read", {"instance": "plc9", "signals": [{"name": "Counter16"}]})
        check("unknown instance errors", rbad is not None and rbad.get("body", {}).get("ok") is False
              and rbad["body"]["error"]["code"] == "INSTANCE_NOT_FOUND", f"{(rbad or {}).get('body')}")
    else:
        check("component derivable", False)

    c.loop_stop()
    c.disconnect()
    print("\n================ MODBUS MULTI-SERVER (UNS) ================", flush=True)
    npass = nfail = 0
    for name, ok, detail in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {name:24} {detail}", flush=True)
        npass += ok
        nfail += not ok
    print(f"\n========== {npass}/{npass + nfail} PASS ({'ALL PASS' if nfail == 0 else str(nfail) + ' FAIL'}) ==========", flush=True)
    sys.exit(0 if nfail == 0 else 1)


if __name__ == "__main__":
    main()
