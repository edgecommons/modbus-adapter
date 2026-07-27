"""EdgeCommons Modbus adapter entry point.

Builds the framework, then spawns one worker thread per ``component.instances[]`` entry — each runs a
ModbusDevice (its connection blocks/retries independently, so one device down doesn't affect the
others). The library owns SIGTERM/SIGINT → graceful shutdown.

The on-demand command surface is served through the library's **command inbox**
(``runtime.get_commands()``): the nine verbs are registered once here, each declaring
``CommandScope.INSTANCE`` (they all act on one slave). The inbox subscribes both D-U28 command
scopes (``ecv1/{device}/modbus-adapter/cmd/#`` and ``ecv1/{device}/modbus-adapter/+/cmd/#``), owns
the addressing — topic token, body ``instance``, and the conflict refusal — and hands each handler
the resolved ``addressed_instance``. ``modbus_adapter.routing.resolve_instance`` then applies the
two policies that need this adapter's configuration (D-SC-4): the sole configured device when no
instance is addressed, and ``NO_SUCH_INSTANCE`` for one it does not serve. Data (``data``), events
(``evt``), the ``state`` keepalive, the ``southbound_health``
+ ``sys`` metrics, and the ``cfg`` publisher all ride the UNS classes automatically.
"""
import argparse
import logging
import sys
import threading

from edgecommons import EdgeCommonsBuilder
from edgecommons.command_inbox import CommandScope

from modbus_adapter.command_service import panels
from modbus_adapter.config.server_configuration import ServerConfiguration
from modbus_adapter.device import ModbusDevice
from modbus_adapter.instance_state import instance_connectivity
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

    # Register the Modbus command verbs on the shared command inbox (once). Every verb acts on one
    # slave, so each declares CommandScope.INSTANCE: the inbox enforces that addressing before
    # dispatch and hands the handler the resolved instance, and routing.resolve_instance applies the
    # adapter-side configured-default / existence policy. Each handler returns the verb result
    # (wrapped as {"ok":true,"result":...}) or raises CommandException for a coded error reply.
    commands = runtime.get_commands()
    if commands is not None:
        def instance_verb(call):
            """Bind a CommandService call into an INSTANCE-scoped inbox handler."""
            def handler(request, addressed_instance):
                device = resolve_instance(devices, addressed_instance)
                return call(device.commands, _body(request))
            return handler

        commands.register("sb/read", CommandScope.INSTANCE, instance_verb(lambda c, b: c.read(b)))
        commands.register("sb/write", CommandScope.INSTANCE, instance_verb(lambda c, b: c.write(b)))
        commands.register("sb/status", CommandScope.INSTANCE, instance_verb(lambda c, b: c.status()))
        commands.register("sb/signals", CommandScope.INSTANCE, instance_verb(lambda c, b: c.signals()))
        commands.register("sb/browse", CommandScope.INSTANCE, instance_verb(lambda c, b: c.browse(b)))
        commands.register("sb/pause", CommandScope.INSTANCE, instance_verb(lambda c, b: c.pause()))
        commands.register("sb/resume", CommandScope.INSTANCE, instance_verb(lambda c, b: c.resume()))
        commands.register("reconnect", CommandScope.INSTANCE, instance_verb(lambda c, b: c.reconnect()))
        commands.register("repoll", CommandScope.INSTANCE, instance_verb(lambda c, b: c.repoll()))
        # The edge-console panel trio (overview/signals/diagnostics) for the descriptor surface.
        for panel in panels():
            commands.register_panel(panel)
        logger.info("Command verbs registered: %s", sorted(commands.verbs()))
    else:
        logger.warning("No command inbox (unresolved identity) — command surface disabled")

    # Report each configured slave's connectivity AT THE INSTANCE LEVEL via the component's state
    # keepalive's instances[] (the #1c surface): the normalized connected flag, the endpoint detail,
    # and the instance state token from the single state model that also answers sb/status (D-SC-7),
    # so a paused slave reads PAUSED instead of looking silently stale. Identity and the
    # state/lifecycle keepalive stay at component scope; this is the per-slave view.
    runtime.set_instance_connectivity_provider(
        lambda: instance_connectivity(config_manager.get_instance_ids(), devices))

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
