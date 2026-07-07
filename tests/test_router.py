import unittest

from src.router import route


class TestRoute(unittest.TestCase):
    def test_e164_with_plus(self):
        self.assertEqual(route("+61412345678@sms.local"), ("sms", "+61412345678"))

    def test_e164_without_plus(self):
        self.assertEqual(route("61412345678@sms.local"), ("sms", "+61412345678"))

    def test_angle_brackets_stripped(self):
        self.assertEqual(route("<+61412345678@sms.local>"), ("sms", "+61412345678"))

    def test_valid_email(self):
        self.assertEqual(route("admin@example.com"), ("email", "admin@example.com"))

    def test_phone_too_short_is_not_sms(self):
        # 7 digits fails E.164; falls through to email check, domain has dot
        self.assertEqual(route("1234567@sms.local"), ("email", "1234567@sms.local"))

    def test_letters_in_localpart_is_email(self):
        self.assertEqual(route("ups-alerts@example.com"),
                         ("email", "ups-alerts@example.com"))

    def test_no_at_raises(self):
        with self.assertRaises(ValueError):
            route("garbage")

    def test_empty_localpart_raises(self):
        with self.assertRaises(ValueError):
            route("@example.com")

    def test_dotless_domain_non_phone_raises(self):
        with self.assertRaises(ValueError):
            route("garbage@nothing")

    def test_leading_zero_not_e164(self):
        # 0412... is not E.164 (must not start with 0); domain has a dot so email
        self.assertEqual(route("0412345678@sms.local")[0], "email")

    def test_max_length_e164(self):
        self.assertEqual(route("+123456789012345@s.l"), ("sms", "+123456789012345"))

    def test_too_long_not_e164(self):
        self.assertEqual(route("1234567890123456@s.l")[0], "email")


if __name__ == "__main__":
    unittest.main()
