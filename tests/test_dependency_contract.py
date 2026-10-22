"""Validate the declared runtime used by public TLS/Gateway qualification."""
import importlib.metadata
from pathlib import Path
import unittest

class DependencyContractTests(unittest.TestCase):
    def test_gateway_crypto_pin_matches_the_qualified_runtime(self):
        requirements=(Path(__file__).resolve().parents[1]/'requirements-gateway.txt').read_text(encoding='utf8')
        pins=[line.strip() for line in requirements.splitlines() if line.strip().startswith('cryptography==')]
        self.assertEqual(pins,['cryptography=='+importlib.metadata.version('cryptography')])

if __name__=='__main__':unittest.main()