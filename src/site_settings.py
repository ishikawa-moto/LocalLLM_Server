"""Load private deployment values without embedding personal paths or LAN addresses."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def site_value(name, default, root=None):
    path = Path(root or ROOT) / 'config' / 'private-site.json'
    settings = json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
    if not isinstance(settings, dict):
        raise ValueError('Private site settings must be an object')
    value = settings.get(name, default)
    if not isinstance(value, type(default)):
        raise ValueError('Private site setting has an invalid type: ' + name)
    return value

# Bind scope is expressed by platform loopback/unspecified addresses, never a LAN address.
import ipaddress
import socket
LOOPBACK = str(ipaddress.IPv4Address(socket.INADDR_LOOPBACK))
if not ipaddress.ip_address(LOOPBACK).is_loopback:
    raise ValueError('localhost must resolve to a loopback address')
LOOPBACK_SUBNET = str(ipaddress.ip_network(f'{LOOPBACK}/8', strict=False))
ANY_ADDRESS = str(ipaddress.IPv4Address(0))

def site_port(service, root=None):
    """Read a numeric deployment port from ignored local settings."""
    port = site_value(service + '_port', 0, root)
    if isinstance(port, bool) or not 0 < port <= 65535:
        raise ValueError('Configure ' + service + '_port in config/private-site.json')
    return port
