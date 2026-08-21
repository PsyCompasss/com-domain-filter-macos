import unittest

from com_domain_filter.cloudflare import (
    STATUS_AVAILABLE_MISMATCH,
    STATUS_EXACT_AVAILABLE,
    STATUS_EXACT_UNAVAILABLE,
    STATUS_NO_COM,
    classify_response,
)


class CloudflareClassificationTests(unittest.TestCase):
    def test_exact_available(self):
        payload = {
            "check_result": {"name": "abc123.com", "available": True, "can_register": True},
            "domains": [{"name": "abc123.com", "availability": "available"}],
        }
        self.assertEqual(classify_response("abc123.com", payload).status, STATUS_EXACT_AVAILABLE)

    def test_exact_unavailable(self):
        payload = {
            "check_result": {"name": "musa.com", "available": False, "can_register": False},
            "domains": [{"name": "musa.com", "availability": "registered"}],
        }
        self.assertEqual(classify_response("musa.com", payload).status, STATUS_EXACT_UNAVAILABLE)

    def test_available_but_mismatch(self):
        payload = {
            "check_result": {"name": "", "available": False, "can_register": False},
            "domains": [{"name": "musa-new.com", "availability": "available"}],
        }
        result = classify_response("musa.com", payload)
        self.assertEqual(result.status, STATUS_AVAILABLE_MISMATCH)
        self.assertEqual(result.returned_name, "musa-new.com")

    def test_no_com_result(self):
        payload = {
            "check_result": {"name": "", "available": False, "can_register": False},
            "domains": [{"name": "musa.net", "availability": "available"}],
        }
        self.assertEqual(classify_response("musa.com", payload).status, STATUS_NO_COM)


if __name__ == "__main__":
    unittest.main()
