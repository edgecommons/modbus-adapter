"""GGCommons Modbus adapter entry point.

Builds the framework, then spawns one worker thread per ``component.instances[]`` entry — each runs a
ModbusDevice (its connection blocks/retries independently, so one device down doesn't affect the
others). The library owns SIGTERM/SIGINT → graceful shutdown.
"""
import argparse
import logging
import sys
import threading

from ggcommons import GGCommonsBuilder

from modbus_adapter.config.server_configuration import ServerConfiguration
from modbus_adapter.device import ModbusDevice

logger = logging.getLogger("main")


def main():
    arg_parser = argparse.ArgumentParser(description="GGCommons Modbus adapter")
    gg = (
        GGCommonsBuilder.create("com.mbreissi.modbus.ModbusAdapter")
        .with_args(sys.argv[1:])
        .with_app_options(arg_parser)
        .build()
    )
    config_manager = gg.get_config_manager()
    messaging = gg.get_messaging()
    metrics = gg.get_metrics()
    credentials = gg.get_credentials()

    logger.info("Starting Modbus adapter (thing=%s)", config_manager.get_thing_name())
    gg.set_ready(False)

    global_config = config_manager.get_global_config()
    devices = []

    def worker(instance_id):
        try:
            server_config = ServerConfiguration(config_manager, global_config, instance_id)
            device = ModbusDevice(config_manager, messaging, metrics, credentials, server_config)
            devices.append(device)
            gg.set_ready(True)            # ready once at least one device is connected + polling
        except Exception:                 # noqa: BLE001
            logger.exception("[%s] failed to start device", instance_id)

    for instance_id in config_manager.get_instance_ids():
        threading.Thread(target=worker, args=(instance_id,),
                         name=f"adapter-{instance_id}", daemon=True).start()

    try:
        threading.Event().wait()          # block until the lib's signal hook exits the process
    finally:
        for d in devices:
            try:
                d.stop()
            except Exception:             # noqa: BLE001
                pass
        gg.shutdown()


if __name__ == "__main__":
    main()
