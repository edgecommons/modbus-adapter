"""GGCommons Modbus adapter entry point.

Builds the framework, then spawns one worker thread per ``component.instances[]`` entry — each runs a
ModbusDevice (its connection blocks/retries independently, so one device down doesn't affect the
others). The library owns SIGTERM/SIGINT → graceful shutdown.

The on-demand command surface is served through the library's **command inbox**
(``gg.get_commands()``): the verbs are registered once here on the shared ``main``-instance inbox
(``ecv1/{device}/ModbusAdapter/main/cmd/#``) and dispatched into the right device by the request
body's ``instance`` selector (the shipped inbox is ``main``-instance only; per-instance inboxes are a
later UNS phase). Data (``data``), events (``evt``), the ``state`` keepalive, the ``southbound_health``
+ ``sys`` metrics, and the ``cfg`` publisher all ride the UNS classes automatically.
"""
import argparse
import logging
import sys
import threading

from ggcommons import GGCommonsBuilder
from ggcommons.command_inbox import CommandException
from ggcommons.heartbeat.instance_connectivity import InstanceConnectivity

from modbus_adapter.config.server_configuration import ServerConfiguration
from modbus_adapter.device import ModbusDevice

logger = logging.getLogger("main")


def _body(request):
    b = request.get_body()
    return b if isinstance(b, dict) else {}


def main():
    arg_parser = argparse.ArgumentParser(description="GGCommons Modbus adapter")
    gg = (
        GGCommonsBuilder.create("com.mbreissi.modbus.ModbusAdapter")
        .with_args(sys.argv[1:])
        .with_app_options(arg_parser)
        .build()
    )
    config_manager = gg.get_config_manager()

    logger.info("Starting Modbus adapter (thing=%s)", config_manager.get_thing_name())
    gg.set_ready(False)

    global_config = config_manager.get_global_config()
    devices = {}                              # instance_id -> ModbusDevice (populated as each connects)

    def resolve_device(body):
        """Pick the target device by the request body's 'instance' selector. With a single configured
        device the selector is optional; otherwise it is required (the shared inbox is main-only)."""
        inst = body.get("instance")
        if inst is None:
            if len(devices) == 1:
                return next(iter(devices.values()))
            raise CommandException("INSTANCE_REQUIRED",
                                   f"body must specify 'instance' (configured: {sorted(devices)})")
        device = devices.get(inst)
        if device is None:
            raise CommandException("INSTANCE_NOT_FOUND",
                                   f"no ready device instance '{inst}' (ready: {sorted(devices)})")
        return device

    # Register the Modbus command verbs on the shared main-instance inbox (once). Handlers fan out to
    # the addressed device; each returns the verb result (wrapped as {"ok":true,"result":...}) or
    # raises CommandException for a coded error reply.
    commands = gg.get_commands()
    if commands is not None:
        commands.register("sb/read", lambda req: resolve_device(_body(req)).commands.read(_body(req)))
        commands.register("sb/write", lambda req: resolve_device(_body(req)).commands.write(_body(req)))
        commands.register("sb/status", lambda req: resolve_device(_body(req)).commands.status())
        commands.register("sb/signals", lambda req: resolve_device(_body(req)).commands.signals())
        commands.register("reconnect", lambda req: resolve_device(_body(req)).commands.reconnect())
        commands.register("repoll", lambda req: resolve_device(_body(req)).commands.repoll())
        logger.info("Command verbs registered: %s", sorted(commands.verbs()))
    else:
        logger.warning("No command inbox (unresolved identity) — command surface disabled")

    # Report each configured slave's connectivity AT THE INSTANCE LEVEL via the component's main state
    # keepalive's instances[] (the #1c surface): a slave whose device has not (re)connected reads
    # disconnected. Identity/data/lifecycle stay under `main`; this is the per-slave connectivity view.
    def _instance_connectivity():
        out = []
        for iid in config_manager.get_instance_ids():
            device = devices.get(iid)
            connected = device is not None and device.is_connected()
            detail = device.endpoint if device is not None else None
            out.append(InstanceConnectivity.of(iid, connected, detail))
        return out

    gg.set_instance_connectivity_provider(_instance_connectivity)

    def worker(instance_id):
        try:
            server_config = ServerConfiguration(config_manager, global_config, instance_id)
            device = ModbusDevice(gg, server_config)
            devices[server_config.id] = device
            gg.set_ready(True)            # ready once at least one device is connected + polling
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
        gg.shutdown()


if __name__ == "__main__":
    main()
