from __future__ import annotations

import unittest

from omarchy_cast.wfd_fixture import INCOMPATIBLE_VIDEO_FIXTURE, MAX_FIXTURE_HEADERS, MAX_FIXTURE_MESSAGE_BYTES, SUCCESS_FIXTURE, TIMEOUT_FIXTURE, WFDProtocolError, parse_rtsp_fixture, run_wfd_fixture


class WFDProtocolFixtureTest(unittest.TestCase):
    def test_success_fixture_reaches_play_and_always_cleans_up(self) -> None:
        result = run_wfd_fixture(SUCCESS_FIXTURE)
        self.assertEqual(result.status, "completed")
        self.assertTrue(result.video_advertised)
        self.assertIn("streaming", result.trace)
        self.assertEqual(result.cleanup[-1], "p2p-cleanup-skipped-offline")

    def test_incompatible_video_and_timeout_are_distinguished(self) -> None:
        incompatible = run_wfd_fixture(INCOMPATIBLE_VIDEO_FIXTURE)
        timeout = run_wfd_fixture(TIMEOUT_FIXTURE)
        self.assertEqual(incompatible.status, "incompatible")
        self.assertIn("video", incompatible.detail)
        self.assertEqual(timeout.status, "timeout")
        self.assertNotIn("streaming", timeout.trace)

    def test_parser_refuses_malformed_or_length_mismatched_messages(self) -> None:
        with self.assertRaisesRegex(WFDProtocolError, "CSeq"):
            parse_rtsp_fixture("RTSP/1.0 200 OK\r\n\r\n")
        with self.assertRaisesRegex(WFDProtocolError, "length"):
            parse_rtsp_fixture("RTSP/1.0 200 OK\r\nCSeq: 1\r\nContent-Length: 4\r\n\r\nx")

    def test_parser_bounds_message_headers_and_protocol_numbers(self) -> None:
        cases = (
            "x" * (MAX_FIXTURE_MESSAGE_BYTES + 1),
            "RTSP/1.0 200 OK\r\nCSeq: " + "9" * 10_000 + "\r\n\r\n",
            "RTSP/1.0 200 OK\r\nCSeq: 1\r\nContent-Length: " + "9" * 10_000 + "\r\n\r\n",
            "RTSP/1.0 200 OK\r\nCSeq: 1\r\nCSeq: 2\r\n\r\n",
            "RTSP/1.0 200 OK\r\nCSeq: 1\r\n" + "".join(f"X-{index}: value\r\n" for index in range(MAX_FIXTURE_HEADERS)) + "\r\n",
        )
        for raw in cases:
            with self.subTest(size=len(raw)), self.assertRaises(WFDProtocolError):
                parse_rtsp_fixture(raw)
