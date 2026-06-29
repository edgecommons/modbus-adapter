"""Full type & feature matrix for the Modbus adapter against the simulator over EMQX.

Covers: write->read-back for every supported type; scale + bit decode; a non-default word-order
round-trip; addressing by explicit {table,address,type} ref; BAD quality on an illegal address;
the changing-tag stream; and the control queries. Run the sim + adapter on validation/config.json
first.
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

# name -> (holding address, written value)
WRITES = {
    "RWInt16": (10, -1234), "RWUInt16": (11, 50000),
    "RWInt32": (12, -100000), "RWUInt32": (14, 3000000000),
    "RWInt64": (16, -5000000000), "RWUInt64": (20, 10000000000),
    "RWFloat32": (24, 12.5), "RWFloat64": (26, 1234.5),
    "RWString": (30, "modbus!"),
}


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
    return [(t, p) for t, p in msgs if p.get("header", {}).get("name") == "SouthboundTagUpdate"]


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


def read_entries(c, read_topic, refs):
    rp = request(c, read_topic, {"tags": refs})
    return {e["tag"]["address"]["address"]: e for e in (rp.get("body", {}).get("reads", []) if rp else [])}


def main():
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="modbus-suite")
    c.on_connect = on_connect
    c.on_message = on_message
    c.connect(BROKER_HOST, BROKER_PORT, 60)
    c.loop_start()

    deadline = time.time() + 30
    while time.time() < deadline and len(updates()) < 3:
        time.sleep(0.5)
    if not updates():
        print("FAIL: no updates; adapter not running on validation/config.json?", flush=True)
        sys.exit(1)
    parts = updates()[0][0].split("/")
    comp, inst = parts[2], parts[3]
    read_topic = f"southbound/{comp}/{inst}/read"
    write_topic = f"southbound/{comp}/{inst}/write"

    # --- write every type, then read back -------------------------------------------------
    writes = [{"name": n, "value": v} for n, (_, v) in WRITES.items()]
    c.publish(write_topic, json.dumps({"header": {"name": "w", "correlation_id": str(uuid.uuid4())},
                                       "tags": {}, "body": {"writes": writes}}))
    time.sleep(1.5)
    got = read_entries(c, read_topic, [{"name": n} for n in WRITES])
    for name, (addr, val) in WRITES.items():
        e = got.get(addr)
        gv = e.get("value") if e else None
        ok = (abs(gv - val) < 1e-3) if isinstance(val, float) else (gv == val)
        check(f"type {name}", ok, f"wrote {val!r} -> read {gv!r}")

    # --- scale + bit decode ----------------------------------------------------------------
    dec = read_entries(c, read_topic, [{"name": "Scaled"}, {"name": "Alarm3"}])
    check("scale (Scaled==25.0)", abs((dec.get(40) or {}).get("value", 0) - 25.0) < 1e-6)
    check("bit (Alarm3==True)", (dec.get(41) or {}).get("value") is True)

    # --- non-default word order round-trip (explicit ref at a free address) ---------------
    lo = {"unitId": 1, "table": "holding", "address": 50, "type": "float32", "wordOrder": "little"}
    c.publish(write_topic, json.dumps({"header": {"name": "w", "correlation_id": str(uuid.uuid4())},
                                       "tags": {}, "body": {"writes": [dict(lo, value=7.25)]}}))
    time.sleep(1.0)
    e = read_entries(c, read_topic, [lo]).get(50)
    check("word-order little round-trip", e is not None and abs(e.get("value", 0) - 7.25) < 1e-6,
          f"{(e or {}).get('value')}")

    # --- explicit-address read (by table+address, not name) -------------------------------
    e = read_entries(c, read_topic, [{"unitId": 1, "table": "input", "address": 0, "type": "uint16"}]).get(0)
    check("explicit-ref read (input)", e is not None and e.get("quality") == "GOOD", f"{(e or {}).get('value')}")

    # --- BAD quality on an illegal address -------------------------------------------------
    rp = request(c, read_topic, {"tags": [{"unitId": 1, "table": "holding", "address": 9999, "type": "uint16"}]})
    reads = rp.get("body", {}).get("reads", []) if rp else []
    check("BAD on illegal address", len(reads) == 1 and reads[0].get("quality") == "BAD",
          f"{reads[0].get('quality') if reads else 'no reply'}")

    # --- changing stream + control ---------------------------------------------------------
    cvals = {json.dumps(s.get("value")) for _, p in updates() if p["body"]["tag"]["name"] == "Counter16"
             for s in p["body"]["samples"]}
    check("changing stream", len(cvals) >= 2, f"{len(cvals)} distinct")
    st = request(c, f"southbound/{comp}/{inst}/control/status", {})
    check("status connected", bool((st.get("body", {}) if st else {}).get("connected")))
    tg = request(c, f"southbound/{comp}/{inst}/control/tags", {})
    names = {t.get("name") for t in (tg.get("body", {}).get("tags", []) if tg else [])}
    check("tags query complete", set(WRITES).issubset(names), f"{len(names)} tags")

    c.loop_stop()
    c.disconnect()
    print("\n================ MODBUS SUITE ================", flush=True)
    npass = nfail = 0
    for name, ok, detail in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {name:30} {detail}", flush=True)
        npass += ok
        nfail += not ok
    print(f"\n========== {npass}/{npass + nfail} PASS ({'ALL PASS' if nfail == 0 else str(nfail) + ' FAIL'}) ==========", flush=True)
    sys.exit(0 if nfail == 0 else 1)


if __name__ == "__main__":
    main()
