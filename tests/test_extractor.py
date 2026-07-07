import unittest
from email.message import EmailMessage
from email import message_from_bytes
from email.policy import default as default_policy

from src.extractor import build_sms_text, extract_body, EMPTY_BODY_PLACEHOLDER


def parse(raw: bytes):
    return message_from_bytes(raw, policy=default_policy)


class TestExtractBody(unittest.TestCase):
    def test_plain_text(self):
        msg = EmailMessage()
        msg.set_content("Battery low on GXT UPS.")
        self.assertIn("Battery low", extract_body(msg))

    def test_multipart_prefers_plain(self):
        msg = EmailMessage()
        msg.set_content("plain version")
        msg.add_alternative("<html><b>html version</b></html>", subtype="html")
        self.assertIn("plain version", extract_body(msg))

    def test_html_only_converted(self):
        msg = EmailMessage()
        msg.set_content("<html><body><p>Power <b>restored</b></p></body></html>",
                        subtype="html")
        body = extract_body(msg)
        self.assertIn("Power", body)
        self.assertIn("restored", body)
        self.assertNotIn("<b>", body)

    def test_empty_body(self):
        msg = parse(b"Subject: x\r\n\r\n")
        self.assertEqual(extract_body(msg).strip(), "")

    def test_non_utf8_charset(self):
        raw = (b"Subject: t\r\n"
               b"Content-Type: text/plain; charset=iso-8859-1\r\n\r\n"
               b"temp 25\xb0C over threshold\r\n")
        self.assertIn("over threshold", extract_body(parse(raw)))

    def test_unknown_charset_falls_back(self):
        raw = (b"Subject: t\r\n"
               b"Content-Type: text/plain; charset=x-no-such-charset\r\n\r\n"
               b"hello\r\n")
        self.assertIn("hello", extract_body(parse(raw)))


class TestBuildSmsText(unittest.TestCase):
    def test_subject_colon_body(self):
        self.assertEqual(build_sms_text("ALARM", "UPS on battery"),
                         "ALARM: UPS on battery")

    def test_whitespace_collapsed(self):
        self.assertEqual(build_sms_text("A  B", "line1\n\n  line2\t x"),
                         "A B: line1 line2 x")

    def test_truncated_to_160(self):
        out = build_sms_text("S", "x" * 500)
        self.assertEqual(len(out), 160)

    def test_empty_gives_placeholder(self):
        self.assertEqual(build_sms_text("", "   \n "), EMPTY_BODY_PLACEHOLDER)

    def test_no_subject(self):
        self.assertEqual(build_sms_text("", "body only"), "body only")

    def test_no_body_keeps_subject(self):
        self.assertEqual(build_sms_text("Subject only", ""), "Subject only:")


if __name__ == "__main__":
    unittest.main()
