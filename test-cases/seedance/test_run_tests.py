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
        cls.suite_config, cls.suite_cases = runner.load_cases()
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

    def test_profile_max_reference_content_combines_image_video_and_audio(self):
        case = next(
            item
            for item in self.suite_cases
            if item["id"] == "reference_images_profile_max"
        )
        expected_video_urls = [
            "https://arkdocs.tos-cn-beijing.volces.com/videos/video-generation/seedance2.5_reference2.mp4",
            "https://arkdocs.tos-cn-beijing.volces.com/videos/video-generation/seedance2.5_reference3.mp4",
            "https://arkdocs.tos-cn-beijing.volces.com/videos/video-generation/seedance2.5_reference4.mp4",
            "https://arkdocs.tos-cn-beijing.volces.com/videos/video-generation/seedance2.5_reference5.mp4",
            "https://arkdocs.tos-cn-beijing.volces.com/videos/video-generation/seedance2.5_reference6.mp4",
            "https://arkdocs.tos-cn-beijing.volces.com/videos/video-generation/seedance2.5_reference7.mp4",
        ]
        results = {
            profile_name: runner.run_case(
                case,
                schemas={},
                config=self.suite_config,
                profile=self.profiles[profile_name],
                model="ep-custom",
                base_url="",
                api_key="",
                dry_run=True,
                no_poll=False,
            )
            for profile_name in self.profiles
        }

        for profile_name, result in results.items():
            self.assertEqual(result.status, "pass", profile_name)
            content = result.details["create_body"]["content"]
            expected_image_count = 30 if profile_name == "seedance-2.5" else 9
            self.assertEqual(
                len(
                    [item for item in content if item.get("role") == "reference_image"]
                ),
                expected_image_count,
                profile_name,
            )
            videos = [
                item["video_url"]["url"]
                for item in content
                if item.get("role") == "reference_video"
            ]
            self.assertEqual(
                videos,
                expected_video_urls
                if profile_name == "seedance-2.5"
                else [self.suite_config["reference_video_url"]],
                profile_name,
            )
            audios = [
                item["audio_url"]["url"]
                for item in content
                if item.get("role") == "reference_audio"
            ]
            self.assertEqual(
                audios,
                [self.suite_config["reference_audio_url"]],
                profile_name,
            )

    def test_t2v_full_combines_25_duration_mov_and_common_checks(self):
        case = next(item for item in self.suite_cases if item["id"] == "t2v_full")

        result = runner.run_case(
            case,
            schemas={},
            config=self.suite_config,
            profile=self.profiles["seedance-2.5"],
            model="ep-custom",
            base_url="",
            api_key="",
            dry_run=True,
            no_poll=False,
        )

        self.assertEqual(result.details["create_body"]["duration"], 30)
        self.assertEqual(result.details["create_body"]["output_format"], "mov")
        self.assertIn("query_duration_matches_request", result.details["checks"])
        self.assertIn(
            "succeeded_video_format_matches_request", result.details["checks"]
        )
        self.assertIn("succeeded_has_last_frame", result.details["checks"])

    def test_t2v_full_combines_20_standard_4k_and_common_checks(self):
        case = next(item for item in self.suite_cases if item["id"] == "t2v_full")

        result = runner.run_case(
            case,
            schemas={},
            config=self.suite_config,
            profile=self.profiles["seedance-2.0"],
            model="ep-custom",
            base_url="",
            api_key="",
            dry_run=True,
            no_poll=False,
        )

        self.assertEqual(result.details["create_body"]["resolution"], "4k")
        self.assertIn("query_resolution_matches_request", result.details["checks"])
        self.assertIn("succeeded_has_last_frame", result.details["checks"])

    def test_default_suite_limits_expected_video_jobs_per_profile(self):
        expected_counts = {
            "seedance-2.5": 6,
            "seedance-2.0": 5,
            "seedance-2.0-fast": 5,
            "seedance-2.0-mini": 5,
        }

        for profile_name, expected_count in expected_counts.items():
            profile = self.profiles[profile_name]
            results = [
                runner.run_case(
                    case,
                    schemas={},
                    config=self.suite_config,
                    profile=profile,
                    model="ep-custom",
                    base_url="",
                    api_key="",
                    dry_run=True,
                    no_poll=False,
                )
                for case in self.suite_cases
            ]
            expected_video_jobs = [
                result
                for result in results
                if result.status == "pass"
                and "create_error_status" not in result.details.get("checks", [])
            ]
            self.assertEqual(
                len(expected_video_jobs),
                expected_count,
                profile_name,
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

    def test_i2v_first_frame_applies_25_adaptive_duration(self):
        case = next(item for item in self.suite_cases if item["id"] == "i2v_first_frame")
        result = runner.run_case(
            case,
            schemas={},
            config=self.suite_config,
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
