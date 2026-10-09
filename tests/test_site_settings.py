"""Verify private configuration and public client template rendering."""
import json
import pathlib
import socket
import tempfile
import unittest
from export_client_package import client_network_settings
from site_settings import LOOPBACK, site_port

class NetworkSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        (self.root / 'config').mkdir()

    def write(self, values):
        (self.root / 'config/private-site.json').write_text(json.dumps(values), encoding='utf-8')

    def test_missing_port_requires_configuration(self):
        with self.assertRaisesRegex(ValueError, 'gateway_port'):
            site_port('gateway', self.root)

    def test_invalid_ports_are_rejected(self):
        for value in [0, -1, 1 << 16, True, '__GATEWAY_PORT__']:
            with self.subTest(value=value):
                self.write({'gateway_port': value})
                with self.assertRaises(ValueError):
                    site_port('gateway', self.root)

    def test_client_template_has_consistent_resolved_ports(self):
        with socket.socket() as listener:
            listener.bind((LOOPBACK, 0))
            port = listener.getsockname()[1]
        self.write({'server_address': 'localhost', 'gateway_port': port, 'bridge_port': port})
        template = json.loads((pathlib.Path(__file__).resolve().parents[1] / 'client-package/clientsettings.template.json').read_text())
        settings = client_network_settings(template, self.root)
        self.assertEqual(settings['gatewayBaseUrl'], f'https://localhost:{port}')
        self.assertEqual(settings['bridgePort'], port)
        self.assertIs(type(settings['bridgePort']), int)
        self.assertEqual(settings['clientCertificateThumbprint'], template['clientCertificateThumbprint'])
        self.assertEqual(template['bridgePort'], '__BRIDGE_PORT__')
