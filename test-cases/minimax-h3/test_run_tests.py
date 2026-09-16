#!/usr/bin/env python3
"""MiniMax H3 执行器测试。"""

from __future__ import annotations

import unittest

import run_tests as runner
from profiles import load_profiles


class RequestBuilderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profiles = load_profiles()
        cls.config, cls.cases = runner.load_cases()

    def test_endpoint_paths(self):
        self.assertEqual(
            runner.build_create_url("https://api.minimax.cn/"),
            "https://api.minimax.cn/v2/video_generation",
        )
        self.assertEqual(
            runner.build_query_url("https://api.minimax.cn", "task/id"),
            "https://api.minimax.cn/v2/query/video_generation/task%2Fid",
        )

    def test_all_content_scenarios(self):
        expected = {
            "text_to_video": [("text", None)],
            "image_to_video": [("text", None), ("image_url", "first_frame")],
            "start_end_to_video": [
                ("text", None),
                ("image_url", "first_frame"),
                ("image_url", "last_frame"),
            ],
            "multimodal_reference": [
                ("text", None),
                ("image_url", "reference_image"),
                ("video_url", "reference_video"),
                ("audio_url", "reference_audio"),
            ],
        }
        for scenario, shape in expected.items():
            content = runner.build_content(scenario, self.config)
            self.assertEqual(
                [(item["type"], item.get("role")) for item in content],
                shape,
                scenario,
            )

    def test_t2v_profile_overrides(self):
        case = next(item for item in self.cases if item["id"] == "t2v_full")
        h3 = runner.run_case(
            case, schemas={}, config=self.config,
            profile=self.profiles["minimax-h3"], model="MiniMax-H3",
            base_url="", api_key="", dry_run=True, no_poll=False,
        )
        h3_max = runner.run_case(
            case, schemas={}, config=self.config,
            profile=self.profiles["minimax-h3-max"], model="MiniMax-H3-Max",
            base_url="", api_key="", dry_run=True, no_poll=False,
        )
        self.assertEqual(h3.details["create_body"]["resolution"], "2K")
        self.assertEqual(h3.details["create_body"]["duration"], 4)
        self.assertEqual(h3_max.details["create_body"]["resolution"], "480P")
        self.assertEqual(
            h3_max.details["create_body"]["extra"]["prompt_expansion_mode"],
            "quality",
        )

        i2v_case = next(item for item in self.cases if item["id"] == "i2v_first_frame")
        for profile_name, model in (("minimax-h3", "MiniMax-H3"), ("minimax-h3-max", "MiniMax-H3-Max")):
            result = runner.run_case(
                i2v_case, schemas={}, config=self.config,
                profile=self.profiles[profile_name], model=model,
                base_url="", api_key="", dry_run=True, no_poll=False,
            )
            self.assertEqual(result.details["create_body"]["resolution"], "768P")
            self.assertEqual(result.details["create_body"]["duration"], 15)

    def test_missing_prompt_negative_body_keeps_empty_text(self):
        case = next(item for item in self.cases if item["id"] == "missing_prompt_error")
        result = runner.run_case(
            case, schemas={}, config=self.config,
            profile=self.profiles["minimax-h3"], model="MiniMax-H3",
            base_url="", api_key="", dry_run=True, no_poll=False,
        )
        self.assertEqual(result.details["create_body"]["content"][0]["text"], "")

    def test_h3_max_only_case_is_skipped_for_h3(self):
        case = next(item for item in self.cases if item["id"] == "h3_max_2k_error")
        result = runner.run_case(
            case, schemas={}, config=self.config,
            profile=self.profiles["minimax-h3"], model="MiniMax-H3",
            base_url="", api_key="", dry_run=True, no_poll=False,
        )
        self.assertEqual(result.status, "skipped")

class CheckTests(unittest.TestCase):
    def setUp(self):
        self.schemas = runner.load_schemas()
        self.create_body = {
            "model": "MiniMax-H3",
            "content": [{"type": "text", "text": "test"}],
            "resolution": "768P",
            "duration": 5,
            "ratio": "16:9",
        }
        self.response = {
            "task": {
                "id": "123",
                "model": "MiniMax-H3",
                "status": "succeeded",
                "created_at": 1,
                "updated_at": 2,
                "content": {"url": "https://example.test/output.mp4"},
                "resolution": "768P",
                "duration": 5,
                "usage": {
                    "total_seconds": 5,
                    "input_seconds": 0,
                    "output_seconds": 5,
                    "input_image_count": 0,
                    "total_tokens": 30,
                    "prompt_tokens": 10,
                    "completion_tokens": 20,
                },
                "ratio": "16:9",
                "task_type": "generation",
                "modality": "video",
            }
        }

    def test_success_checks_pass(self):
        checks = [
            "query_schema", "reached_succeeded", "succeeded_schema",
            "task_id_matches", "task_model_matches", "task_resolution_matches",
            "task_duration_matches", "task_generation_video",
            "usage_billing_matches_request",
        ]
        verdict = runner.run_checks(
            checks, self.schemas,
            create_status=200, create_response={"task_id": "123"},
            query_status=200, query_response=self.response,
            polled_to_terminal=True, create_body=self.create_body, task_id="123",
        )
        self.assertEqual(verdict[0], "pass")

    def test_query_schema_accepts_documented_optional_task_fields(self):
        """VideoTask 的基础 Schema 不能把文档未标注 required 的字段设为必填。"""
        for field in ("id", "model", "created_at", "updated_at", "task_type"):
            with self.subTest(field=field):
                response = {"task": dict(self.response["task"])}
                del response["task"][field]
                verdict = runner.run_checks(
                    ["query_schema"], self.schemas,
                    create_status=200, create_response={"task_id": "123"},
                    query_status=200, query_response=response,
                    polled_to_terminal=True, create_body=self.create_body, task_id="123",
                )
                self.assertEqual(verdict[0], "pass")

    def test_query_schema_only_validates_final_response(self):
        """中间态只用于轮询，不参与字段契约校验。"""
        verdict = runner.run_checks(
            ["query_schema", "succeeded_schema"], self.schemas,
            create_status=200, create_response={"task_id": "123"},
            query_status=200, query_response=self.response,
            polled_to_terminal=True, create_body=self.create_body, task_id="123",
        )
        self.assertEqual(verdict[0], "pass")

    def test_official_response_without_modality_passes(self):
        """官方视频查询响应可能省略可选的 modality 字段。"""
        del self.response["task"]["modality"]
        verdict = runner.run_checks(
            ["query_schema", "succeeded_schema", "task_generation_video"], self.schemas,
            create_status=200, create_response={"task_id": "123"},
            query_status=200, query_response=self.response,
            polled_to_terminal=True, create_body=self.create_body, task_id="123",
        )
        self.assertEqual(verdict[0], "pass")

    def test_non_video_modality_still_fails(self):
        self.response["task"]["modality"] = "text"
        verdict = runner.run_checks(
            ["task_generation_video"], self.schemas,
            create_status=200, create_response={"task_id": "123"},
            query_status=200, query_response=self.response,
            polled_to_terminal=True, create_body=self.create_body, task_id="123",
        )
        self.assertEqual(verdict[0], "fail")

    def test_ratio_semantics_for_text_frame_and_reference_scenarios(self):
        scenarios = [
            (self.create_body, "16:9", "pass"),
            ({**self.create_body, "content": [
                {"type": "text", "text": "test"},
                {"type": "image_url", "image_url": {"url": "x"}, "role": "first_frame"},
            ], "ratio": "16:9"}, "adaptive", "pass"),
            ({**self.create_body, "content": [
                {"type": "text", "text": "test"},
                {"type": "video_url", "video_url": {"url": "x"}, "role": "reference_video"},
            ], "ratio": "adaptive"}, "9:16", "pass"),
            (self.create_body, "adaptive", "fail"),
        ]
        for body, actual_ratio, expected_verdict in scenarios:
            with self.subTest(body=body, actual_ratio=actual_ratio):
                self.response["task"]["ratio"] = actual_ratio
                verdict = runner.run_checks(
                    ["task_ratio_matches_semantics"], self.schemas,
                    create_status=200, create_response={"task_id": "123"},
                    query_status=200, query_response=self.response,
                    polled_to_terminal=True, create_body=body, task_id="123",
                )
                self.assertEqual(verdict[0], expected_verdict)

    def test_usage_seconds_mismatch_fails(self):
        self.response["task"]["usage"]["total_seconds"] = 6
        verdict = runner.run_checks(
            ["usage_billing_matches_request"], self.schemas,
            create_status=200, create_response={"task_id": "123"},
            query_status=200, query_response=self.response,
            polled_to_terminal=True, create_body=self.create_body, task_id="123",
        )
        self.assertEqual(verdict[0], "fail")

    def test_billing_fields_match_reference_media(self):
        body = {
            **self.create_body,
            "content": [
                {"type": "text", "text": "test"},
                {"type": "image_url", "image_url": {"url": "image"}, "role": "reference_image"},
                {"type": "video_url", "video_url": {"url": "video"}, "role": "reference_video"},
            ],
        }
        self.response["task"]["usage"] = {
            "total_seconds": 11,
            "input_seconds": 6,
            "output_seconds": 5,
            "input_image_count": 1,
        }
        verdict = runner.run_checks(
            ["usage_billing_matches_request"], self.schemas,
            create_status=200, create_response={"task_id": "123"},
            query_status=200, query_response=self.response,
            polled_to_terminal=True, create_body=body, task_id="123",
        )
        self.assertEqual(verdict[0], "pass")

    def test_error_contract_and_http_code(self):
        response = {
            "error": {
                "message": "invalid params (2013)",
                "http_code": "400",
            }
        }
        verdict = runner.run_checks(
            ["create_error_status", "error_schema", "error_http_code_matches_status"],
            self.schemas,
            create_status=400, create_response=response,
            query_status=0, query_response={}, polled_to_terminal=False,
            create_body=self.create_body, task_id=None,
            expected_http_status=400,
        )
        self.assertEqual(verdict[0], "pass")
        self.assertEqual(verdict[2], "HTTP 400")
        self.assertEqual(verdict[3], "HTTP 400")

    def test_no_poll_removes_terminal_checks(self):
        case = {
            "id": "quick",
            "scenario": "text_to_video",
            "checks": ["create_status_200", "query_schema", "reached_succeeded"],
        }
        config = {
            "prompt": "test", "resolution": "768P", "duration": 5, "ratio": "16:9",
            "poll_interval": 1, "poll_timeout": 1,
        }
        result = runner.run_case(
            case, schemas={}, config=config, profile=load_profiles()["minimax-h3"],
            model="MiniMax-H3", base_url="", api_key="", dry_run=True, no_poll=True,
        )
        self.assertNotIn("reached_succeeded", result.details["checks"])
        self.assertIn("reached_succeeded", result.details["skipped_checks"])


if __name__ == "__main__":
    unittest.main()
