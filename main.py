"""EdgeCommons Modbus adapter entry point.

Builds the framework, then spawns one worker thread per ``component.instances[]`` entry — each runs a
ModbusDevice (its connection blocks/retries independently, so one device down doesn't affect the
others). The library owns SIGTERM/SIGINT → graceful shutdown.

The on-demand command surface is served through the library's **command inbox**
(``runtime.get_commands()``): the verbs are registered once here on the component-scope inbox
(``ecv1/{device}/modbus-adapter/cmd/#``) and dispatched into the right device by the request
body's ``instance`` selector (the instance token is optional and present only for explicit
multi-instance addressing). Data (``data``), events (``evt``), the ``state`` keepalive, the ``southbound_health``
+ ``sys`` metrics, and the ``cfg`` publisher all ride the UNS classes automatically.
"""
import argparse
import logging
import sys
import threading

from edgecommons import EdgeCommonsBuilder
from edgecommons.command_inbox import CommandException
from edgecommons.heartbeat.instance_connectivity import InstanceConnectivity

from modbus_adapter.command_service import panels
from modbus_adapter.config.server_configuration import ServerConfiguration
from modbus_adapter.device import ModbusDevice

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

    def resolve_device(body):
        """Pick the target device by the request body's 'instance' selector. With a single configured
        device the selector is optional; otherwise it is required (a single command inbox serves all devices)."""
        inst = body.get("instance")
        if inst is None:
            if len(devices) == 1:
                return next(iter(devices.values()))
            raise CommandException("BAD_ARGS",
                                   f"body must specify 'instance' (configured: {sorted(devices)})")
        device = devices.get(inst)
        if device is None:
            raise CommandException("NO_SUCH_INSTANCE",
                                   f"no ready device instance '{inst}' (ready: {sorted(devices)})")
        return device

    # Register the Modbus command verbs on the component-scope command inbox (once). Handlers fan out to
    # the addressed device; each returns the verb result (wrapped as {"ok":true,"result":...}) or
    # raises CommandException for a coded error reply.
    commands = runtime.get_commands()
    if commands is not None:
        commands.register("sb/read", lambda req: resolve_device(_body(req)).commands.read(_body(req)))
        commands.register("sb/write", lambda req: resolve_device(_body(req)).commands.write(_body(req)))
        commands.register("sb/status", lambda req: resolve_device(_body(req)).commands.status())
        commands.register("sb/signals", lambda req: resolve_device(_body(req)).commands.signals())
        commands.register("sb/browse", lambda req: resolve_device(_body(req)).commands.browse(_body(req)))
        commands.register("sb/pause", lambda req: resolve_device(_body(req)).commands.pause())
        commands.register("sb/resume", lambda req: resolve_device(_body(req)).commands.resume())
        commands.register("reconnect", lambda req: resolve_device(_body(req)).commands.reconnect())
        commands.register("repoll", lambda req: resolve_device(_body(req)).commands.repoll())
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
