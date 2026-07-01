# Reference — Data Types

Modbus carries only **bits** (coils, discrete inputs) and **16-bit registers** (holding, input). This
adapter *synthesizes* richer types from those primitives. Every signal declares its `type` (and, for
multi-register types, byte/word order); the adapter assembles registers into the value on read and
splits the value back into registers on write. All conversion is in `modbus_adapter/codec.py`.

## Tables

| Table | FC (read) | Access | Element |
|-------|-----------|--------|---------|
| `coil` | 1 | read/write | 1 bit → bool |
| `discrete` | 2 | read-only | 1 bit → bool |
| `holding` | 3 | read/write | 16-bit register |
| `input` | 4 | read-only | 16-bit register |

## Types

| `type` | Registers | On-wire JSON (read) | Write input | Notes |
|--------|-----------|---------------------|-------------|-------|
| `bool` | 1 bit, or 1 register + `bit` | boolean | boolean | coil/discrete; or a single bit of a holding/input register via `bit` |
| `int16` | 1 | number | number (int) | signed |
| `uint16` | 1 | number | number (int) | |
| `int32` | 2 | number | number (int) | |
| `uint32` | 2 | number | number (int) | |
| `int64` | 4 | number | number (int) | |
| `uint64` | 4 | number | number (int) | |
| `float32` | 2 | number | number | IEEE-754 single |
| `float64` | 4 | number | number | IEEE-754 double |
| `string` | `count` | string | string | UTF-8, null-padded; `count` registers (2 chars each) |

## Byte & word order (multi-register types)

A register is 16 bits (2 bytes). A 32/64-bit value spans 2–4 registers; devices differ in how the
bytes are laid out. Two independent knobs cover the four real-world layouts:

- **`wordOrder`** — `big` (default): most-significant register first; `little`: registers reversed (word swap).
- **`byteOrder`** — `big` (default): high byte first within each register; `little`: bytes swapped.

| `wordOrder` / `byteOrder` | Layout (32-bit ABCD) |
|---|---|
| big / big | ABCD |
| big / little | BADC |
| little / big | CDAB |
| little / little | DCBA |

## Scaling & bit extraction

- **`scale` / `offset`** (numeric types): read `value = raw·scale + offset`; write inverts it
  (`raw = round((value − offset) / scale)` for integer types). With no scale/offset an integer type
  stays an integer on the wire; **with** a scale it becomes a float.
- **`bit`** (0–15, holding/input + `type: bool`): publishes a single bit of a register as a boolean.
  Bit *writes* (read-modify-write) are not supported in v1 and are skipped with a warning.

## Published identity

Each `SouthboundSignalUpdate` / read result carries:

- `signal.name` — the configured signal name (also the `{signalId}` topic variable).
- `signal.id` — a stable canonical id, `u<unitId>/<table>/<address>/<type>` (e.g. `u1/holding/24/float32`).
- `signal.address` — the protocol-native handle: `{ unitId, table, address, type, wordOrder?, byteOrder?, bit?, count? }`.

## Value typing notes

- Integers use the full 64-bit range; consumers whose JSON parser uses IEEE-754 doubles (e.g.
  JavaScript) may lose precision above 2^53.
- `bool` is a JSON boolean; `string` a JSON string; everything else a JSON number.
- Modbus has no device-side timestamp, so `sourceTs` is `null` and `serverTs` is the adapter's read time.
