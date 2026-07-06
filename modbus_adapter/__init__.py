"""EdgeCommons Modbus adapter — the Python reference southbound adapter.

Bridges Modbus TCP / RTU / RTU-over-TCP devices onto the EdgeCommons messaging bus using the
southbound contract (docs/SOUTHBOUND.md): polls register maps and republishes value changes as
``SouthboundSignalUpdate`` messages, serves on-demand batch reads and batch writes, and emits the
``southbound_health`` metric.
"""

__version__ = "1.0.0"
