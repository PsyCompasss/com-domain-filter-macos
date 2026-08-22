import unittest
import os
import tempfile
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from com_domain_filter.cloudflare import (
    STATUS_AVAILABLE_MISMATCH,
    STATUS_EXACT_AVAILABLE,
    STATUS_EXACT_UNAVAILABLE,
    STATUS_NO_COM,
    CloudflareChecker,
    chrome_app_bundle,
    classify_response,
    find_system_chrome,
)


class CloudflareClassificationTests(unittest.TestCase):
    class FakeResponse:
        def __init__(self, payload):
            self.payload = json.dumps(payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return self.payload

    def test_system_chrome_override_is_detected(self):
        with tempfile.TemporaryDirectory() as temp:
            executable = Path(temp) / "Google Chrome"
            executable.write_text("#!/bin/sh\n", encoding="utf-8")
            executable.chmod(0o755)
            with patch.dict(os.environ, {"COM_DOMAIN_FILTER_CHROME_PATH": str(executable)}):
                self.assertEqual(find_system_chrome(), executable)

    def test_chrome_app_bundle_is_derived_from_executable(self):
        executable = Path("/Volumes/Musa/App/Google Chrome.app/Contents/MacOS/Google Chrome")
        self.assertEqual(chrome_app_bundle(executable), Path("/Volumes/Musa/App/Google Chrome.app"))

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

    def test_recorded_session_outside_chrome_profile_is_reused_first(self):
        with tempfile.TemporaryDirectory() as temp:
            profile = Path(temp) / "profiles" / "wanwang.aliyun.com"
            checker = CloudflareChecker("https://wanwang.aliyun.com/domain", profile)
            checker._chrome_pid = 4321
            checker._write_session_files(45678)
            self.assertNotEqual(checker._pid_file.parent, profile)
            with (
                patch.object(checker, "_debug_endpoint_available", return_value=True),
                patch(
                    "com_domain_filter.cloudflare.subprocess.run",
                    return_value=SimpleNamespace(stdout=f"Google Chrome --user-data-dir={profile}"),
                ),
            ):
                self.assertEqual(checker._find_existing_chrome(), (4321, 45678))

    def test_wait_for_existing_chrome_retries_transient_port_gap(self):
        with tempfile.TemporaryDirectory() as temp:
            checker = CloudflareChecker("https://example.com/", Path(temp) / "profile")
            with (
                patch.object(checker, "_find_existing_chrome", side_effect=[None, (4321, 45678)]),
                patch("com_domain_filter.cloudflare.time.sleep"),
            ):
                self.assertEqual(checker._wait_for_existing_chrome(2), (4321, 45678))

    def test_empty_retained_chrome_gets_new_page_before_reconnect(self):
        with tempfile.TemporaryDirectory() as temp:
            checker = CloudflareChecker("https://example.com/", Path(temp) / "profile")
            responses = [self.FakeResponse([]), self.FakeResponse({"type": "page"})]
            with patch("com_domain_filter.cloudflare.urlopen", side_effect=responses) as mocked:
                checker._ensure_page_target(45678)
            self.assertEqual(mocked.call_count, 2)
            request = mocked.call_args_list[1].args[0]
            self.assertEqual(request.method, "PUT")
            self.assertIn("https%3A%2F%2Fexample.com%2F", request.full_url)

    def test_retained_chrome_with_page_is_left_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            checker = CloudflareChecker("https://example.com/", Path(temp) / "profile")
            response = self.FakeResponse([{"type": "page"}])
            with patch("com_domain_filter.cloudflare.urlopen", return_value=response) as mocked:
                checker._ensure_page_target(45678)
            self.assertEqual(mocked.call_count, 1)

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
