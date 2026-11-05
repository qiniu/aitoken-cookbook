#!/usr/bin/env python3
"""离线验证错误判定、轮询状态机、产物响应契约和真实 HTTP 请求格式。"""

from __future__ import annotations

import copy
import html
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import run_tests as runner


def task(status="succeeded", task_id="task-1", external_id="run-sample"):
    result = {"id": task_id, "status": status, "create_time": 1, "update_time": 2,
              "external_id": external_id}
    if status == "succeeded":
        result["outputs"] = [{"type": "video", "id": "video-1",
                              "url": "https://example.test/result.mp4", "duration": "3"}]
    return result


def envelope(data):
    return {"code": 0, "message": "成功", "request_id": "request-1", "data": data}


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.config, self.cases = runner.load_cases()
        self.schemas = runner.load_schemas()
        self.config.update(poll_interval=0.001, poll_timeout=10, request_timeout=2)
        self.case = {"id": "sample", "name": "测试样例", "endpoint": "text-to-video",
                     "prompt": "一只猫走过草地", "settings": {"duration": 3, "resolution": "720p"},
                     "review": ["动作自然"]}

    def run_case(self, case=None, **kwargs):
        return runner.run_case(case or self.case, config=self.config, schemas=self.schemas,
                               base_url="https://example.test", api_key="secret", model="kling-4.0",
                               run_id="run", **kwargs)

    def test_all_seven_cases_build_with_default_fixtures_without_network(self):
        self.assertEqual(len(self.cases), 7)
        self.assertEqual(sum("expected_error" not in case for case in self.cases), 5)
        with patch.object(runner.urllib.request, "urlopen", side_effect=AssertionError("离线模式访问网络")):
            for case in self.cases:
                with self.subTest(case=case["id"]):
                    self.assertEqual(self.run_case(case, dry_run=True).status, "pass")

    def test_negative_parameters_reach_server_and_do_not_poll(self):
        for case in self.cases:
            if "expected_error" not in case:
                continue
            for code in (1200, 1201):
                with self.subTest(case=case["id"], code=code), patch.object(runner, "send_request", return_value=(
                    400, {"code": code, "message": "参数不合法", "request_id": "error-r"}
                )) as send:
                    result = self.run_case(case)
                    self.assertEqual(result.status, "pass", result.error)
                    self.assertEqual(result.actual, {"http_status": 400, "code": code})
                    self.assertEqual(result.details["manual_review"]["status"], "not_applicable")
                    self.assertEqual(send.call_count, 1)
                    body = send.call_args.args[3]
                    if case["id"] == "missing_prompt_error":
                        self.assertNotIn("prompt", body)
                    elif case["id"] == "invalid_duration_error":
                        self.assertEqual(body["settings"]["duration"], 31)

    def test_unrelated_errors_cannot_pass_negative_case(self):
        case = next(case for case in self.cases if case["id"] == "missing_prompt_error")
        for status, code in [(401, 1002), (429, 1102), (429, 1302), (500, 5000),
                             (400, 1301), (400, 1203), (200, 1201)]:
            with self.subTest(status=status, code=code), patch.object(runner, "send_request", return_value=(
                status, {"code": code, "message": "错误", "request_id": "r"}
            )):
                self.assertEqual(self.run_case(case).status, "fail")

    def test_error_response_requires_typed_code_message_and_request_id(self):
        case = next(case for case in self.cases if case["id"] == "missing_prompt_error")
        valid = {"code": 1201, "message": "参数错误", "request_id": "r"}
        invalid = [{key: value for key, value in valid.items() if key != missing}
                   for missing in valid]
        invalid += [{**valid, "code": "1201"}, {**valid, "code": 0},
                    {**valid, "message": ""}, {**valid, "message": None},
                    {**valid, "request_id": ""}, runner.parse_response("<html>错误</html>"),
                    runner.parse_response("[]")]
        for response in invalid:
            with self.subTest(response=response), patch.object(runner, "send_request", return_value=(400, response)):
                self.assertEqual(self.run_case(case).status, "fail")

    def test_unexpected_created_task_in_negative_case_is_retained_without_polling(self):
        case = next(case for case in self.cases if case["id"] == "invalid_duration_error")
        for status, response in [
            (200, envelope(task("submitted"))),
            (400, {"code": 1201, "message": "错误", "request_id": "r", "data": {"id": "task-1"}}),
        ]:
            with self.subTest(status=status), patch.object(runner, "send_request", return_value=(status, response)) as send:
                result = self.run_case(case, no_poll=True)
                self.assertEqual(result.status, "fail")
                self.assertEqual(result.details["task_id"], "task-1")
                self.assertEqual(send.call_count, 1)

    def test_negative_transport_error_is_not_a_pass(self):
        case = next(case for case in self.cases if case["id"] == "invalid_duration_error")
        with patch.object(runner, "send_request", side_effect=TimeoutError("连接超时")) as send:
            self.assertEqual(self.run_case(case).status, "error")
        self.assertEqual(send.call_count, 1)

    def test_create_envelope_and_task_required_fields(self):
        valid = envelope(task("submitted"))
        invalid = [{key: value for key, value in valid.items() if key != missing}
                   for missing in ("code", "message", "request_id", "data")]
        invalid += [{**valid, "data": {key: value for key, value in valid["data"].items() if key != missing}}
                    for missing in ("id", "status", "create_time", "update_time")]
        invalid += [{**valid, "code": 1201}, {**valid, "code": "0"}, {**valid, "request_id": ""},
                    {**valid, "data": []}, {**valid, "data": None}]
        for response in invalid:
            with self.subTest(response=response), patch.object(runner, "send_request", return_value=(200, response)) as send:
                self.assertEqual(self.run_case().status, "fail")
                self.assertEqual(send.call_count, 1)

    def test_query_envelope_and_array_shape_checked_on_every_poll(self):
        invalid = [envelope(value) for value in (None, {}, [], [None], [task(), task()])]
        invalid += [{"code": 0, "message": "成功", "data": [task()]},
                    {**envelope([task()]), "code": 1201}]
        for response in invalid:
            with self.subTest(response=response), patch.object(runner, "send_request", side_effect=[
                (200, envelope(task("submitted"))), (200, response),
            ]):
                self.assertEqual(self.run_case().status, "fail")

    def test_three_distinct_keyframes_have_unique_ids(self):
        case = next(case for case in self.cases if case["id"] == "multi_keyframes")
        inputs = runner.build_body(case, self.config, "run", True)["contents"][1:]
        self.assertEqual([item["type"] for item in inputs], ["first_frame", "middle_frame", "last_frame"])
        self.assertEqual(len({item["id"] for item in inputs}), 3)
        self.assertEqual(len({item["url"] for item in inputs}), 3)

    def test_reference_limits_require_all_materials_before_submission(self):
        case = next(case for case in self.cases if case["id"] == "omni_reference")
        inputs = runner.build_body(case, self.config, "run", True)["contents"][1:]
        self.assertEqual(sum(item["type"] == "refer_image" for item in inputs), 10)
        self.assertEqual(sum(item["type"] == "feature_video" for item in inputs), 5)
        self.assertEqual(len({item["url"] for item in inputs}), 15)
        for key in ("limit_reference_images", "limit_reference_videos"):
            incomplete = self.config["fixtures"][key][:-1]
            with self.subTest(fixture=key), patch.dict(self.config["fixtures"], {key: incomplete}), \
                    patch.object(runner, "send_request") as send:
                result = self.run_case(case)
                self.assertEqual(result.status, "error")
                send.assert_not_called()

    def test_missing_fixture_never_submits(self):
        case = next(case for case in self.cases if case["id"] == "start_end_frames")
        self.config["fixtures"]["last_frame"] = ""
        with patch.object(runner, "send_request") as send:
            result = self.run_case(case)
        self.assertEqual(result.status, "error")
        send.assert_not_called()


    def test_auth_billing_server_and_html_errors_cannot_pass_generation(self):
        for http, code in [(401, 1002), (429, 1102), (500, 5000), (400, 1301), (200, 0)]:
            with self.subTest(http=http, code=code), patch.object(runner, "send_request", return_value=(
                http, {"code": code, "message": "错误", "request_id": "r"}
            )):
                self.assertEqual(self.run_case().status, "fail")
        with patch.object(runner, "send_request", return_value=(400, {"_non_json_response": "<html>错误</html>"})):
            self.assertEqual(self.run_case().status, "fail")


    def test_create_transport_failure_is_not_retried(self):
        with patch.object(runner, "send_request", side_effect=TimeoutError("请求超时")) as send:
            self.assertEqual(self.run_case().status, "error")
        self.assertEqual(send.call_count, 1)

    def test_full_polling_success(self):
        replies = [(200, envelope(task("submitted"))), (200, envelope([task("processing")])),
                   (200, envelope([task()]))]
        with patch.object(runner, "send_request", side_effect=replies):
            result = self.run_case()
        self.assertEqual(result.status, "pass", result.error)
        self.assertEqual(len(result.details["poll_history"]), 2)
        self.assertEqual(result.details["manual_review"]["status"], "pending")

    def test_no_poll_distinguishes_pending_failed_and_completed(self):
        for state, verdict in [("processing", "skipped"), ("failed", "fail"), ("succeeded", "pass")]:
            with self.subTest(state=state), patch.object(runner, "send_request", side_effect=[
                (200, envelope(task("submitted"))), (200, envelope([task(state)])),
            ]) as send:
                result = self.run_case(no_poll=True)
                self.assertEqual(result.status, verdict, result.error)
                self.assertEqual(send.call_count, 2)

    def test_every_poll_checks_schema_identity_and_state(self):
        wrong_id = task("processing", task_id="another")
        missing_status = task("processing")
        del missing_status["status"]
        wrong_external = task("processing", external_id="other")
        backwards_time = {**task("processing"), "update_time": 0}
        for invalid in (wrong_id, missing_status, wrong_external, backwards_time):
            with self.subTest(invalid=invalid), patch.object(runner, "send_request", side_effect=[
                (200, envelope(task("submitted"))), (200, envelope([invalid])),
            ]):
                self.assertEqual(self.run_case().status, "fail")
        with patch.object(runner, "send_request", side_effect=[
            (200, envelope(task("submitted"))), (200, envelope([task("processing")])),
            (200, envelope([task("submitted")])),
        ]):
            self.assertEqual(self.run_case().status, "fail")
        with patch.object(runner, "send_request", side_effect=[
            (200, envelope(task("succeeded"))), (200, envelope([task("processing")])),
        ]):
            self.assertEqual(self.run_case(no_poll=True).status, "fail")

    def test_default_rejects_missing_materials_before_submission(self):
        self.config["fixtures"]["last_frame"] = ""
        for args in ([], ["--dry-run"]):
            with self.subTest(args=args), \
                    patch.dict(runner.os.environ, {"API_BASE_URL": "https://example.test", "API_KEY": "key"}), \
                    patch.object(runner, "send_request") as send, patch("sys.stderr"), \
                    patch.object(runner, "run_case") as run, \
                    patch.object(runner, "load_cases", return_value=(self.config, self.cases)):
                with self.assertRaises(SystemExit) as caught:
                    runner.main(args)
                self.assertEqual(caught.exception.code, 2)
                run.assert_not_called()
                send.assert_not_called()

    def test_default_runs_all_cases_and_requires_every_result_to_pass(self):
        for status, expected_exit in (("pass", 0), ("fail", 1), ("error", 1), ("skipped", 1)):
            def fake_run(case, **kwargs):
                return runner.CaseResult(id=case["id"], name=case["name"],
                    status=status if case["id"] == "omni_edit" else "pass")

            with self.subTest(status=status), \
                    patch.dict(runner.os.environ, {"API_BASE_URL": "https://example.test", "API_KEY": "key"}), \
                    patch.object(runner, "run_case", side_effect=fake_run) as run, \
                    patch.object(runner.Report, "write", return_value={}), patch("sys.stdout"):
                self.assertEqual(runner.main([]), expected_exit)
                self.assertEqual({call.args[0]["id"] for call in run.call_args_list},
                                 {case["id"] for case in self.cases})
                self.assertEqual(run.call_count, 7)

    def test_poll_deadline_does_not_start_an_extra_request(self):
        config = {**self.config, "poll_timeout": 1, "poll_interval": 5}
        with patch.object(runner.time, "monotonic", side_effect=[0, 0, 0.8, 1.1]), \
             patch.object(runner.time, "sleep") as sleep, \
             patch.object(runner, "send_request", return_value=(200, envelope([task("processing")]))) as send:
            with self.assertRaisesRegex(runner.ContractError, "轮询超时"):
                runner.poll_task("https://example.test", "key", "task-1", "run-sample",
                                 config, self.schemas, {})
        self.assertEqual(send.call_count, 1)
        self.assertAlmostEqual(sleep.call_args.args[0], 0.2)

    def test_bad_outputs_and_billing_fail(self):
        for update in ({"outputs": []}, {"outputs": [{"type": "video", "id": "v", "url": "x", "duration": "NaN"}]},
                       {"billing": [{"charge_type": "unit", "amount": "-1", "package_type": "video"}]},
                       {"billing": [{"charge_type": "cash", "amount": "1"}]}):
            with self.subTest(update=update), patch.object(runner, "send_request", side_effect=[
                (200, envelope(task("submitted"))), (200, envelope([{**task(), **update}])),
            ]):
                self.assertEqual(self.run_case().status, "fail")

    def test_succeeded_response_requires_complete_video_output(self):
        valid = task()
        invalid = [{key: value for key, value in valid.items() if key != "outputs"}]
        for missing in ("id", "url", "duration", "type"):
            response = copy.deepcopy(valid)
            del response["outputs"][0][missing]
            invalid.append(response)
        for field, value in (("url", ""), ("url", "file:///tmp/result.mp4"), ("duration", 3)):
            response = copy.deepcopy(valid)
            response["outputs"][0][field] = value
            invalid.append(response)
        for response in invalid:
            with self.subTest(response=response), patch.object(runner, "send_request", side_effect=[
                (200, envelope(task("submitted"))), (200, envelope([response])),
            ]):
                self.assertEqual(self.run_case().status, "fail")

    def test_usage_only_validates_official_fields(self):
        updates = [{}, {"usage": {}}, {"usage": {"input_duration": "8.03"}}]
        updates += [{"usage": {"input_duration": "8.03", "output_duration": value}}
                    for value in (None, {}, "-1", "30")]
        for update in updates:
            with self.subTest(update=update), patch.object(runner, "send_request", side_effect=[
                (200, envelope(task("submitted"))), (200, envelope([{**task(), **update}])),
            ]):
                result = self.run_case()
                self.assertEqual(result.status, "pass", result.error)
                self.assertEqual(result.details["usage"], update.get("usage"))

    def test_invalid_official_input_duration_fails(self):
        for value in (None, {}, 8.03, "8", "8.0", "-1.00", "0.00"):
            completed = {**task(), "usage": {"input_duration": value}}
            with self.subTest(value=value), patch.object(runner, "send_request", side_effect=[
                (200, envelope(task("submitted"))), (200, envelope([completed])),
            ]):
                self.assertEqual(self.run_case().status, "fail")

    def test_duration_mismatch_and_missing_watermark_fail(self):
        for case in ({**self.case, "settings": {"duration": 5}},
                     {**self.case, "options": {"watermark_info": {"enabled": True}}}):
            with self.subTest(case=case), patch.object(runner, "send_request", side_effect=[
                (200, envelope(task("submitted"))), (200, envelope([task()])),
            ]):
                self.assertEqual(self.run_case(case).status, "fail")

    def test_output_duration_tolerance_applies_at_both_boundaries(self):
        for requested, returned, passes in (
            (3, "2.96", True), (3, "3.041", True), (3, "2.8", False),
            (30, "30.041", True), (30, "30.2", False), (30, "29.8", False),
        ):
            completed = task()
            completed["outputs"][0]["duration"] = returned
            body = {"settings": {"duration": requested}}
            with self.subTest(requested=requested, returned=returned):
                if passes:
                    runner.check_outputs(completed, self.case, body, self.schemas)
                else:
                    with self.assertRaises(runner.ContractError):
                        runner.check_outputs(completed, self.case, body, self.schemas)

    def test_success_uses_api_response_and_keeps_media_review_pending(self):
        case = {**self.case, "options": {"watermark_info": {"enabled": True}}}
        completed = task()
        completed["outputs"][0]["watermark_url"] = "https://example.test/watermarked.mp4"
        with patch.object(runner, "send_request", side_effect=[
            (200, envelope(task("submitted"))), (200, envelope([completed])),
        ]), patch.object(runner.urllib.request, "urlopen", side_effect=AssertionError("不应下载输出视频")), \
                patch("subprocess.run", side_effect=AssertionError("不应调用外部媒体程序")):
            result = self.run_case(case)
        self.assertEqual(result.status, "pass", result.error)
        self.assertEqual(result.details["videos"][0]["duration"], "3")
        self.assertEqual(result.details["videos"][0]["watermark_video_url"],
                         "https://example.test/watermarked.mp4")
        self.assertEqual(result.details["manual_review"]["status"], "pending")
        self.assertIn("待人工验收", result.details["media_checks"])

    def test_report_redacts_before_truncating(self):
        secret = "key-" + "x" * 1200
        value = runner.report_value({"error": "echo:" + secret, "nested": [secret]}, secret)
        self.assertNotIn("key-", json.dumps(value))
        self.assertIn("密钥已隐藏", value["error"])
        media = runner.report_value({"video_url": "https://example.test/video.mp4?key=" + secret}, secret)
        self.assertNotIn(secret, media["video_url"])
        self.assertIn("密钥已隐藏", media["video_url"])

    def test_report_preserves_long_media_urls_and_renders_previews(self):
        case = {**self.case, "options": {"watermark_info": {"enabled": True}}}
        completed = task()
        completed["outputs"] = [
            {"type": "video", "id": f"video-{index}", "duration": "3",
             "url": f"https://example.test/video-{index}.mp4?signature=" + "a" * 1200 + "&expires=123",
             "watermark_url": f"https://example.test/watermark-{index}.mp4?signature=" + "b" * 1200 + "&expires=123"}
            for index in (1, 2)
        ]
        with patch.object(runner, "send_request", side_effect=[
            (200, envelope(task("submitted"))), (200, envelope([completed])),
        ]):
            result = self.run_case(case)
        self.assertEqual(result.status, "pass", result.error)
        result.details["create_body"]["data"] = "data:image/png;base64," + "c" * 2000
        result.details = runner.report_value(result.details, "secret")
        report = runner.Report(model="kling-4.0", cases=[result])
        with tempfile.TemporaryDirectory() as directory:
            paths = report.write(directory)
            details = json.loads(paths["json"].read_text())["cases"][0]["details"]
            rendered = paths["html"].read_text()
        self.assertEqual(rendered.count("<video "), 4)
        self.assertLess(len(details["create_body"]["data"]), 1100)
        for index, video in enumerate(completed["outputs"], start=1):
            suffix = "" if index == 1 else f"_{index}"
            for field, source in (("video_url", "url"), ("watermark_video_url", "watermark_url")):
                self.assertEqual(details[field + suffix], video[source])
                self.assertEqual(details["videos"][index - 1][field], video[source])
                self.assertIn(f'<video src="{html.escape(video[source])}"', rendered)


class LocalHTTPTests(unittest.TestCase):
    def test_real_http_round_trip_and_error_envelope(self):
        received = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                received.append((self.path, self.headers.get("Authorization"), json.loads(raw)))
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"code": 1201, "message": "参数错误", "request_id": "r"}).encode())

            def do_GET(self):
                received.append((self.path, self.headers.get("Authorization"), None))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(envelope([task()])).encode())

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        try:
            status, response = runner.send_request(runner.create_url(base_url, "text-to-video", "kling-4.0"),
                                                    "test-key", "POST", {"prompt": "测试中文"})
            self.assertEqual((status, response["code"]), (400, 1201))
            self.assertEqual(received[0], ("/text-to-video/kling-4.0", "Bearer test-key", {"prompt": "测试中文"}))
            url = runner.query_url(base_url, ["task/a&b"])
            self.assertIn("task_ids=task%2Fa%26b", url)
            self.assertEqual(runner.send_request(url, "", "GET", None)[0], 200)
            self.assertIsNone(received[-1][1])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
