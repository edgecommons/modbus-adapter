"""The single per-instance state model (D-SC-7) — one vocabulary, two surfaces.

An instance's condition is derived in exactly one place, :func:`device_state`, from the two facts
the adapter tracks: the ``sb/pause`` latch (:mod:`modbus_adapter.pause`) and the live Modbus
liveness (:meth:`modbus_adapter.connection.ModbusConnection.is_connected`). Both surfaces read it —
the ``sb/status`` reply (:meth:`modbus_adapter.command_service.CommandService.state`) and the
``state`` keepalive's ``instances[]`` (:func:`instance_connectivity`) — so a pulled answer can never
disagree with a pushed one, and a deliberately paused slave is distinguishable from one that has
gone quiet.

The vocabulary is the shared one (D-SC-7):

- ``ONLINE`` — the slave answers reads.
- ``PAUSED`` — ``sb/pause`` is latched: polling and publishing are suspended. Administrative state
  wins over connectivity, so a paused instance reads ``PAUSED`` whatever the link is doing (the
  normalized ``connected`` flag still carries the live liveness beside it).
- ``BACKOFF`` — the device is running but its link is down; the poll loop keeps retrying.
- ``CONNECTING`` — a configured instance whose device has not come up yet (the initial connect
  blocks and retries every few seconds), so there is nothing to poll or pause.
"""
from edgecommons.heartbeat.instance_connectivity import InstanceConnectivity

#: The slave answers reads.
ONLINE = "ONLINE"
#: The instance's device has not come up yet (initial connect retrying).
CONNECTING = "CONNECTING"
#: The device is running but the link is down and being retried.
BACKOFF = "BACKOFF"
#: ``sb/pause`` is latched — polling and publishing are suspended.
PAUSED = "PAUSED"


def device_state(paused: bool, connected: bool) -> str:
    """The state token for a running device: ``PAUSED`` beats connectivity, then
    ``ONLINE``/``BACKOFF``."""
    if paused:
        return PAUSED
    return ONLINE if connected else BACKOFF


def instance_connectivity(instance_ids, devices):
    """Build the ``state`` keepalive's ``instances[]`` sample — one entry per **configured**
    instance, carrying the normalized ``connected`` flag, the connection detail, and the
    :func:`device_state` token.

    :param instance_ids: the configured instance ids (``component.instances[]``)
    :param devices: the ready devices, ``{instance_id: ModbusDevice}``
    :returns: a list of :class:`~edgecommons.heartbeat.instance_connectivity.InstanceConnectivity`
    """
    out = []
    for iid in instance_ids:
        device = devices.get(iid)
        if device is None:
            out.append(InstanceConnectivity.of(iid, False).with_state(CONNECTING))
            continue
        out.append(
            InstanceConnectivity.of(iid, device.is_connected(), device.endpoint)
            .with_state(device.state()))
    return out
