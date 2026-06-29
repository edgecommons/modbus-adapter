"""A pymodbus Modbus/TCP slave for adapter smoke testing (the asyncua-equivalent).

Exposes a known register map across all four tables, seeded at construction. A background loopback
client mutates a counter + a temperature ramp (holding registers) every 0.5s so onChange polling has
live data. (pymodbus 3.13's in-process datastore set API is unreliable, but client writes work — so
the updater simply writes to itself, the same path the adapter uses.)

Run:  python validation/modbus_sim_server.py [--host 0.0.0.0] [--port 5020]

Register map (0-based PDU addresses; unit/device id 1):
  holding (R/W):
    0      Counter16   uint16   changing (++ each tick)
    1-2    Temp        float32  changing (ramp 20.0 .. ~70.0)
    10..33 RW scratch  (int16/uint16/int32/uint32/int64/uint64/float32/float64/string) for write tests
    40     ScaledRaw   uint16   seeded 250 (scale 0.1 -> 25.0)
    41     BitSource   uint16   seeded 0b1000 (bit 3 set)
  input (R/O):   0  InCounter  uint16  seeded 12345
  coil (R/W):    0  RunCmd     bool    seeded False
  discrete (R/O):0  StatusBit  bool    seeded True
"""
import argparse
import struct
import threading
import time

from pymodbus.client import ModbusTcpClient
from pymodbus.datastore import ModbusDeviceContext, ModbusSequentialDataBlock, ModbusServerContext
from pymodbus.server import StartTcpServer

UNIT = 1


def f32_regs(v):
    b = struct.pack(">f", float(v))
    return [int.from_bytes(b[0:2], "big"), int.from_bytes(b[2:4], "big")]


def build_context():
    hr = [0] * 100
    hr[40] = 250            # ScaledRaw
    hr[41] = 0b0000_1000    # BitSource (bit 3)
    ir = [0] * 100
    ir[0] = 12345           # InCounter
    di = [0] * 100
    di[0] = 1               # StatusBit = True
    device = ModbusDeviceContext(
        co=ModbusSequentialDataBlock(1, [0] * 100),
        di=ModbusSequentialDataBlock(1, di),
        hr=ModbusSequentialDataBlock(1, hr),
        ir=ModbusSequentialDataBlock(1, ir),
    )
    return ModbusServerContext(devices=device, single=True)


def updater(host, port):
    client = ModbusTcpClient(host if host != "0.0.0.0" else "127.0.0.1", port=port, timeout=2)
    while not client.connect():
        time.sleep(0.5)
    counter = 0
    while True:
        counter = (counter + 1) & 0xFFFF
        try:
            client.write_register(0, counter, device_id=UNIT)                          # Counter16
            client.write_registers(1, f32_regs(20.0 + (counter % 100) * 0.5), device_id=UNIT)  # Temp ramp
            # Re-assert the fixed seeds each tick via the (reliable) client-write path. pymodbus
            # 3.13's deprecated datastore does not serve sparse construction-seeded values at higher
            # addresses, so we keep them present this way.
            client.write_registers(40, [250, 0b0000_1000], device_id=UNIT)             # ScaledRaw, BitSource
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=5020)
    args = ap.parse_args()

    context = build_context()
    threading.Thread(target=updater, args=(args.host, args.port), daemon=True).start()
    print(f"[sim] Modbus/TCP slave on {args.host}:{args.port} (unit {UNIT})", flush=True)
    StartTcpServer(context, address=(args.host, args.port))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
