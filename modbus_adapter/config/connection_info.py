"""Per-instance Modbus connection parameters (TCP / serial RTU / RTU-over-TCP)."""

TCP = "tcp"
RTU = "rtu"
RTU_TCP = "rtutcp"
TRANSPORTS = {TCP, RTU, RTU_TCP}


class ConnectionInfo:
    """Parses the instance ``connection`` block. Defaults to Modbus TCP on :502, unit 1."""

    def __init__(self, o):
        o = o or {}
        self.raw = o
        self.transport = o.get("transport", TCP)
        if self.transport not in TRANSPORTS:
            raise ValueError(f"connection.transport must be one of {sorted(TRANSPORTS)}, got {self.transport!r}")
        # TCP / RTU-over-TCP
        self.host = o.get("host", "127.0.0.1")
        self.port = int(o.get("port", 502))
        # serial RTU
        self.serial_port = o.get("serialPort")
        self.baud_rate = int(o.get("baudRate", 9600))
        self.parity = o.get("parity", "N")
        self.stop_bits = int(o.get("stopBits", 1))
        self.byte_size = int(o.get("byteSize", 8))
        # common
        self.unit_id = int(o.get("unitId", 1))
        self.timeout_ms = int(o.get("timeoutMs", 1000))

    @property
    def timeout_s(self) -> float:
        return self.timeout_ms / 1000.0

    def describe(self) -> str:
        if self.transport == RTU:
            return f"rtu://{self.serial_port}@{self.baud_rate} unit={self.unit_id}"
        return f"{self.transport}://{self.host}:{self.port} unit={self.unit_id}"
