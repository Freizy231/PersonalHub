"""Which resolved addresses an outbound request may connect to."""
import unittest

from backend.app.core.outbound import is_public_address


class PublicAddressTests(unittest.TestCase):
    def test_public_addresses_are_allowed(self):
        for value in ('8.8.8.8', '1.1.1.1', '2606:4700:4700::1111', '64:ff9b::808:808', '2002:808:808::1'):
            self.assertTrue(is_public_address(value), value)

    def test_private_loopback_and_link_local_are_refused(self):
        for value in ('127.0.0.1', '10.0.0.5', '192.168.1.1', '169.254.1.1', '::1', 'fc00::1', 'fe80::1', '::ffff:127.0.0.1'):
            self.assertFalse(is_public_address(value), value)

    def test_translation_prefixes_cannot_smuggle_a_private_ipv4(self):
        for value in ('64:ff9b::7f00:1', '64:ff9b::a00:1', '64:ff9b::c0a8:101', '2002:7f00:1::1', '2002:a00:1::1', '2002:c0a8:101::1'):
            self.assertFalse(is_public_address(value), value)

    def test_teredo_is_refused(self):
        self.assertFalse(is_public_address('2001:0:4136:e378:8000:63bf:3fff:fdd2'))


if __name__ == '__main__':
    unittest.main()
