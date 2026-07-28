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
- ``PAUSED`` — ``sb/pause`` is latched **and the link is up**: the instance is deliberately quiet
  rather than stale.
- ``BACKOFF`` — the device is running but its link is down; the poll loop keeps retrying.
- ``CONNECTING`` — a configured instance whose device has not come up yet (the initial connect
  blocks and retries every few seconds), so there is nothing to poll or pause.

**Link truth wins** (the fleet-wide precedence, shared with the OPC UA and EtherNet/IP adapters and
the scaffold templates): a paused instance whose link is down reports ``BACKOFF`` — or ``CONNECTING``
before its first connect — never ``PAUSED``. The pause stays visible beside it, in ``sb/status``'s
``paused`` field, so no surface hides either fact.
"""
from edgecommons.heartbeat.instance_connectivity import InstanceConnectivity

#: The slave answers reads.
ONLINE = "ONLINE"
#: The instance's device has not come up yet (initial connect retrying).
CONNECTING = "CONNECTING"
#: The device is running but the link is down and being retried.
BACKOFF = "BACKOFF"
#: ``sb/pause`` is latched and the link is up — polling and publishing are suspended.
PAUSED = "PAUSED"


def device_state(paused: bool, connected: bool) -> str:
    """The state token for a running device. Link truth wins: a down link is ``BACKOFF`` whether or
    not the instance is paused; ``PAUSED`` is reported only while the link is up."""
    if not connected:
        return BACKOFF
    return PAUSED if paused else ONLINE


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
