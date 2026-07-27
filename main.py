"""EdgeCommons Modbus adapter entry point.

Builds the framework, then spawns one worker thread per ``component.instances[]`` entry — each runs a
ModbusDevice (its connection blocks/retries independently, so one device down doesn't affect the
others). The library owns SIGTERM/SIGINT → graceful shutdown.

The on-demand command surface is served through the library's **command inbox**
(``runtime.get_commands()``): the verbs are registered once here through the scope-aware
``register_scoped`` form, so each handler receives the topic-addressed instance token beside the
request (the inbox subscribes both D-U28 command scopes, ``ecv1/{device}/modbus-adapter/cmd/#`` and
``ecv1/{device}/modbus-adapter/+/cmd/#``). ``modbus_adapter.routing.resolve_instance`` dispatches
each request into the addressed device per SOUTHBOUND §2.2: the topic-addressed instance is
authoritative (a conflicting body ``instance`` is ``BAD_ARGS``); a component-scoped delivery routes
by the body selector, optional iff exactly one device is configured. Data (``data``), events
(``evt``), the ``state`` keepalive, the ``southbound_health``
+ ``sys`` metrics, and the ``cfg`` publisher all ride the UNS classes automatically.
"""
import argparse
import logging
import sys
import threading

from edgecommons import EdgeCommonsBuilder
from edgecommons.heartbeat.instance_connectivity import InstanceConnectivity

from modbus_adapter.command_service import panels
from modbus_adapter.config.server_configuration import ServerConfiguration
from modbus_adapter.device import ModbusDevice
from modbus_adapter.routing import resolve_instance

logger = logging.getLogger("main")


def _body(request):
    b = request.get_body()
    return b if isinstance(b, dict) else {}


def main():
    arg_parser = argparse.ArgumentParser(description="EdgeCommons Modbus adapter")
    runtime = (
        EdgeCommonsBuilder.create("com.mbreissi.edgecommons.ModbusAdapter")
        .with_args(sys.argv[1:])
        .with_app_options(arg_parser)
        .build()
    )
    config_manager = runtime.get_config_manager()

    logger.info("Starting Modbus adapter (thing=%s)", config_manager.get_thing_name())
    runtime.set_ready(False)

    global_config = config_manager.get_global_config()
    devices = {}                              # instance_id -> ModbusDevice (populated as each connects)

    # Register the Modbus command verbs on the shared command inbox (once), through the scope-aware
    # registration form: the inbox hands each handler the topic-addressed instance token (None for a
    # component-scoped delivery), and routing.resolve_instance picks the addressed device per §2.2.
    # Each handler returns the verb result (wrapped as {"ok":true,"result":...}) or raises
    # CommandException for a coded error reply.
    commands = runtime.get_commands()
    if commands is not None:
        def scoped(call):
            """Bind a CommandService call into a §2.2 scope-aware inbox handler."""
            def handler(request, addressed_instance):
                body = _body(request)
                device = resolve_instance(devices, body, addressed_instance)
                return call(device.commands, body)
            return handler

        commands.register_scoped("sb/read", scoped(lambda c, b: c.read(b)))
        commands.register_scoped("sb/write", scoped(lambda c, b: c.write(b)))
        commands.register_scoped("sb/status", scoped(lambda c, b: c.status()))
        commands.register_scoped("sb/signals", scoped(lambda c, b: c.signals()))
        commands.register_scoped("sb/browse", scoped(lambda c, b: c.browse(b)))
        commands.register_scoped("sb/pause", scoped(lambda c, b: c.pause()))
        commands.register_scoped("sb/resume", scoped(lambda c, b: c.resume()))
        commands.register_scoped("reconnect", scoped(lambda c, b: c.reconnect()))
        commands.register_scoped("repoll", scoped(lambda c, b: c.repoll()))
        # The edge-console panel trio (overview/signals/diagnostics) for the descriptor surface.
        for panel in panels():
            commands.register_panel(panel)
        logger.info("Command verbs registered: %s", sorted(commands.verbs()))
    else:
        logger.warning("No command inbox (unresolved identity) — command surface disabled")

    # Report each configured slave's connectivity AT THE INSTANCE LEVEL via the component's state
    # keepalive's instances[] (the #1c surface): a slave whose device has not (re)connected reads
    # disconnected. Identity and the state/lifecycle keepalive stay at component scope; this is the
    # per-slave connectivity view.
    def _instance_connectivity():
        out = []
        for iid in config_manager.get_instance_ids():
            device = devices.get(iid)
            connected = device is not None and device.is_connected()
            detail = device.endpoint if device is not None else None
            out.append(InstanceConnectivity.of(iid, connected, detail))
        return out

    runtime.set_instance_connectivity_provider(_instance_connectivity)

    def worker(instance_id):
        try:
            server_config = ServerConfiguration(config_manager, global_config, instance_id)
            device = ModbusDevice(runtime, server_config)
            devices[server_config.id] = device
            runtime.set_ready(True)            # ready once at least one device is connected + polling
        except Exception:                 # noqa: BLE001
            logger.exception("[%s] failed to start device", instance_id)

    for instance_id in config_manager.get_instance_ids():
        threading.Thread(target=worker, args=(instance_id,),
                         name=f"adapter-{instance_id}", daemon=True).start()

    try:
        threading.Event().wait()          # block until the lib's signal hook exits the process
    finally:
        for d in list(devices.values()):
            try:
                d.stop()
            except Exception:             # noqa: BLE001
                pass
        runtime.shutdown()


if __name__ == "__main__":
    main()
