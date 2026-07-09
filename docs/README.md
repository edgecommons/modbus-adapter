# Modbus Adapter — Documentation

`com.mbreissi.edgecommons.ModbusAdapter` connects to Modbus devices (TCP, serial RTU, or RTU-over-TCP) and
bridges their registers onto a message bus: it polls a configured register map and republishes value
changes as structured messages, and serves on-demand reads, writes, and management queries. Built on
the `edgecommons` (`edgecommons`) library, it runs wherever you deploy it — a Greengrass v2
component, a standalone process, or a Kubernetes pod. It is the **Python reference** southbound
adapter and the poll-based counterpart to the (Java) OPC UA reference adapter.

| Doc | Start here when you want to… |
|-----|------------------------------|
| **[Tutorial](tutorial.md)** | learn by doing — bring the adapter up against a simulator, end to end |
| **[How-to guides](how-to-guides.md)** | accomplish a task — define signals, tune rates, read/write, add a second device, deploy |
| **[Reference](reference/)** | look up an exact option, topic, payload, or type |
| **[Explanation](explanation.md)** | understand how it works and why — polling, the type layer, the two planes |

## Quick routing

- **"I'm new here."** → [Tutorial](tutorial.md).
- **"What config option does X?"** → [Reference — Configuration](reference/configuration.md).
- **"How is a register turned into a value?"** → [Reference — Data Types](reference/data-types.md).
- **"What message on which topic?"** → [Reference — Messaging Interface](reference/messaging-interface.md).
- **"What does this metric mean?"** → [Reference — Metrics](reference/metrics.md).
- **"Why poll instead of subscribe?"** → [Explanation](explanation.md).

## Audience

These docs are for **integrators and operators** — people who deploy the adapter and write clients
that consume or command it. They do not cover modifying the adapter's own source.
