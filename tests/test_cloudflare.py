import unittest
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from com_domain_filter.cloudflare import (
    STATUS_AVAILABLE_MISMATCH,
    STATUS_EXACT_AVAILABLE,
    STATUS_EXACT_UNAVAILABLE,
    STATUS_NO_COM,
    CloudflareChecker,
    classify_response,
    find_system_chrome,
)


class CloudflareClassificationTests(unittest.TestCase):
    def test_system_chrome_override_is_detected(self):
        with tempfile.TemporaryDirectory() as temp:
            executable = Path(temp) / "Google Chrome"
            executable.write_text("#!/bin/sh\n", encoding="utf-8")
            executable.chmod(0o755)
            with patch.dict(os.environ, {"COM_DOMAIN_FILTER_CHROME_PATH": str(executable)}):
                self.assertEqual(find_system_chrome(), executable)

    def test_existing_dedicated_chrome_is_found_by_profile_and_debug_port(self):
        with tempfile.TemporaryDirectory() as temp:
            profile = Path(temp) / "profile with spaces"
            checker = CloudflareChecker("https://example.com/", profile)
            command = (
                f"4321 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome "
                f"--user-data-dir={profile} --remote-debugging-port=45678 --new-window"
            )
            with (
                patch(
                    "com_domain_filter.cloudflare.subprocess.run",
                    return_value=SimpleNamespace(stdout=command),
                ),
                patch.object(checker, "_debug_endpoint_available", return_value=True),
            ):
                self.assertEqual(checker._find_existing_chrome(), (4321, 45678))

    def test_existing_chrome_without_live_debug_endpoint_is_not_reused(self):
        with tempfile.TemporaryDirectory() as temp:
            profile = Path(temp) / "profile"
            checker = CloudflareChecker("https://example.com/", profile)
            command = (
                f"4321 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome "
                f"--user-data-dir={profile} --remote-debugging-port=45678 --new-window"
            )
            with (
                patch(
                    "com_domain_filter.cloudflare.subprocess.run",
                    return_value=SimpleNamespace(stdout=command),
                ),
                patch.object(checker, "_debug_endpoint_available", return_value=False),
            ):
                self.assertIsNone(checker._find_existing_chrome())

    def test_exact_available(self):
        payload = {
            "check_result": {"name": "abc123.com", "available": True, "can_register": True},
            "domains": [{"name": "abc123.com", "availability": "available"}],
        }
        self.assertEqual(classify_response("abc123.com", payload).status, STATUS_EXACT_AVAILABLE)

    def test_exact_unavailable(self):
        payload = {
            "check_result": {"name": "abc.com", "available": False, "can_register": False},
            "domains": [{"name": "abc.com", "availability": "registered"}],
        }
        self.assertEqual(classify_response("abc.com", payload).status, STATUS_EXACT_UNAVAILABLE)

    def test_available_but_mismatch(self):
        payload = {
            "check_result": {"name": "", "available": False, "can_register": False},
            "domains": [{"name": "abc-new.com", "availability": "available"}],
        }
        result = classify_response("abc.com", payload)
        self.assertEqual(result.status, STATUS_AVAILABLE_MISMATCH)
        self.assertEqual(result.returned_name, "abc-new.com")

    def test_no_com_result(self):
        payload = {
            "check_result": {"name": "", "available": False, "can_register": False},
            "domains": [{"name": "abc.net", "availability": "available"}],
        }
        self.assertEqual(classify_response("abc.com", payload).status, STATUS_NO_COM)


if __name__ == "__main__":
    unittest.main()
