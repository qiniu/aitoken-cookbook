#!/usr/bin/env python3
"""Seedance profile 感知执行器测试。"""

from __future__ import annotations

import unittest
import urllib.error
from unittest.mock import patch

import run_tests as runner
from profiles import load_profiles


class RunnerProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profiles = load_profiles()
        cls.config = {
            "prompt": "生成测试视频",
            "first_frame_url": "https://example.test/first.png",
            "last_frame_url": "https://example.test/last.png",
            "reference_image_url": "https://example.test/reference.png",
            "reference_video_url": "https://example.test/reference.mp4",
            "reference_audio_url": "https://example.test/reference.mp3",
            "resolution": "720p",
            "ratio": "adaptive",
            "duration": 5,
            "poll_interval": 1,
            "poll_timeout": 10,
        }

    def test_profile_max_image_content_count(self):
        case = {
            "prompt": "依次参考图像1至图像30的构图",
            "reference_image_url": "https://example.test/product.png",
        }

        v25_content = runner.build_content(
            "reference_images_profile_max",
            self.config,
            case,
            self.profiles["seedance-2.5"],
        )
        v20_content = runner.build_content(
            "reference_images_profile_max",
            self.config,
            case,
            self.profiles["seedance-2.0"],
        )

        self.assertEqual(
            len([item for item in v25_content if item.get("role") == "reference_image"]),
            30,
        )
        self.assertEqual(
            len([item for item in v20_content if item.get("role") == "reference_image"]),
            9,
        )

    def test_audio_only_content_has_no_image_or_video(self):
        content = runner.build_content(
            "audio_only_reference",
            self.config,
            {"reference_audio_url": "https://example.test/audio.mp3"},
            self.profiles["seedance-2.5"],
        )

        self.assertEqual([item["type"] for item in content], ["text", "audio_url"])
        self.assertEqual(content[1]["role"], "reference_audio")

    def test_six_video_content_uses_all_declared_urls(self):
        urls = [f"https://example.test/reference-{number}.mp4" for number in range(1, 7)]
        content = runner.build_content(
            "multimodal_reference_6_videos",
            self.config,
            {
                "reference_image_url": "https://example.test/product.png",
                "reference_video_urls": urls,
            },
            self.profiles["seedance-2.5"],
        )

        videos = [item for item in content if item.get("role") == "reference_video"]
        self.assertEqual([item["video_url"]["url"] for item in videos], urls)

    def test_content_over_profile_reference_limit_is_rejected_locally(self):
        with self.assertRaisesRegex(ValueError, "参考图片数量 10 超过 profile 上限 9"):
            runner.build_content(
                "reference_to_video",
                self.config,
                {
                    "reference_image_urls": [
                        f"https://example.test/reference-{number}.png"
                        for number in range(10)
                    ]
                },
                self.profiles["seedance-2.0"],
            )

    def test_output_format_is_sent_in_create_body(self):
        body = runner.build_create_body(
            "ep-custom",
            [{"type": "text", "text": "test"}],
            self.config,
            {"output_format": "mov"},
        )

        self.assertEqual(body["output_format"], "mov")

    def test_unsupported_case_is_skipped_without_schemas_or_url(self):
        result = runner.run_case(
            {
                "id": "t2v_4k",
                "name": "4K",
                "scenario": "text_to_video",
                "requires": {"resolution": "4k"},
            },
            schemas={},
            config=self.config,
            profile=self.profiles["seedance-2.5"],
            model="ep-custom",
            base_url="",
            api_key="",
            dry_run=True,
            no_poll=False,
        )

        self.assertEqual(result.status, "skipped")
        self.assertIn("4k", result.details["skip_reason"])

    def test_dry_run_applies_25_edit_constraints(self):
        result = runner.run_case(
            {
                "id": "video_edit",
                "name": "视频编辑",
                "scenario": "video_edit",
                "ratio": "16:9",
                "duration": 5,
                "reference_video_url": "https://example.test/edit.mov",
            },
            schemas={},
            config=self.config,
            profile=self.profiles["seedance-2.5"],
            model="ep-custom",
            base_url="",
            api_key="",
            dry_run=True,
            no_poll=False,
        )

        self.assertEqual(result.status, "pass")
        self.assertEqual(result.details["create_body"]["ratio"], "adaptive")
        self.assertEqual(result.details["create_body"]["duration"], -1)


class OutputCapabilityCheckTests(unittest.TestCase):
    def test_quicktime_major_brand_is_required(self):
        self.assertTrue(
            runner.is_quicktime_mov_header(
                b"\x00\x00\x00\x14ftypqt  \x00\x00\x00\x00"
            )
        )
        self.assertFalse(
            runner.is_quicktime_mov_header(
                b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00"
            )
        )

    def test_resolution_check_fails_when_query_differs_from_request(self):
        verdict = runner.run_checks(
            ["query_resolution_matches_request"],
            {},
            create_status=200,
            create_resp={},
            query_status=200,
            query_resp={"resolution": "720p"},
            polled=True,
            create_body={"resolution": "4k"},
        )

        self.assertEqual(verdict[0], "fail")
        self.assertEqual(verdict[2:], ("4k", "720p"))

    def test_duration_check_accepts_matching_query_value(self):
        verdict = runner.run_checks(
            ["query_duration_matches_request"],
            {},
            create_status=200,
            create_resp={},
            query_status=200,
            query_resp={"duration": 30},
            polled=True,
            create_body={"duration": 30},
        )

        self.assertEqual(verdict[0], "pass")

    def test_duration_check_fails_when_query_value_is_missing(self):
        verdict = runner.run_checks(
            ["query_duration_matches_request"],
            {},
            create_status=200,
            create_resp={},
            query_status=200,
            query_resp={},
            polled=True,
            create_body={"duration": 30},
        )

        self.assertEqual(verdict[0], "fail")
        self.assertEqual(verdict[2:], (30, None))

    def test_mov_probe_uses_bounded_range_request(self):
        response = _FakeBinaryResponse(
            b"\x00\x00\x00\x14ftypqt  \x00\x00\x00\x00" + b"x" * 512,
            content_type="video/quicktime",
        )

        with patch.object(runner.urllib.request, "urlopen", return_value=response) as open_url:
            matched, metadata = runner.probe_mov_url(
                "https://example.test/output.mov", timeout=7
            )

        request = open_url.call_args.args[0]
        self.assertEqual(request.get_header("Range"), "bytes=0-255")
        self.assertEqual(open_url.call_args.kwargs["timeout"], 7)
        self.assertEqual(response.read_sizes, [256])
        self.assertTrue(matched)
        self.assertEqual(metadata["content_type"], "video/quicktime")

    def test_mov_probe_reports_network_error_without_raising(self):
        with patch.object(
            runner.urllib.request,
            "urlopen",
            side_effect=urllib.error.URLError("offline"),
        ):
            matched, metadata = runner.probe_mov_url(
                "https://example.test/output.mov"
            )

        self.assertFalse(matched)
        self.assertIn("offline", metadata["error"])

    def test_mov_check_probes_succeeded_video_url(self):
        response = _FakeBinaryResponse(
            b"\x00\x00\x00\x14ftypqt  \x00\x00\x00\x00",
            content_type="video/quicktime",
        )

        with patch.object(runner.urllib.request, "urlopen", return_value=response):
            verdict = runner.run_checks(
                ["succeeded_video_format_matches_request"],
                {},
                create_status=200,
                create_resp={},
                query_status=200,
                query_resp={
                    "status": "succeeded",
                    "content": {"video_url": "https://example.test/output.mov"},
                },
                polled=True,
                create_body={"output_format": "mov"},
            )

        self.assertEqual(verdict[0], "pass")


class _FakeBinaryResponse:
    def __init__(self, data: bytes, *, content_type: str):
        self._data = data
        self.headers = {"Content-Type": content_type}
        self.read_sizes: list[int] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        return self._data[:size]


if __name__ == "__main__":
    unittest.main()
