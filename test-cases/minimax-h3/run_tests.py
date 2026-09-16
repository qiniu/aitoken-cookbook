#!/usr/bin/env python3
"""MiniMax H3 视频生成 V2 兼容性测试执行入口。"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHARED = HERE.parent / "_shared"
SCHEMA_DIR = HERE / "schemas"
sys.path.insert(0, str(SHARED))

from http_common import USER_AGENT  # noqa: E402
from report import CaseResult, Report, mask_secret  # noqa: E402
from profiles import (  # noqa: E402
    MiniMaxH3Profile,
    apply_profile_overrides,
    case_skip_reason,
    load_profiles,
)

CREATE_PATH = "/v2/video_generation"
QUERY_PATH = "/v2/query/video_generation"
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}
RATIOS = {"adaptive", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16"}
MAX_STR_LEN = 500
NO_POLL_CHECKS = {
    "create_status_200",
    "create_schema",
    "query_status_200",
    "query_schema",
    "task_id_matches",
}


def truncate(value, max_len: int = MAX_STR_LEN):
    """递归截断报告中的长字符串。"""
    if isinstance(value, str):
        return value if len(value) <= max_len else value[:max_len] + f"...(已截断，共 {len(value)} 字符)"
    if isinstance(value, list):
        return [truncate(item, max_len) for item in value]
    if isinstance(value, dict):
        return {key: truncate(item, max_len) for key, item in value.items()}
    return value


def get_path(obj, path: str):
    """按点号路径读取嵌套字段。"""
    current = obj
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def load_schemas() -> dict:
    """加载基础 Schema，并派生视频成功态 Schema。"""
    try:
        import jsonschema
    except ImportError:
        print("error: 缺少依赖 jsonschema，请执行 pip install jsonschema", file=sys.stderr)
        raise SystemExit(1)

    create_schema = json.loads((SCHEMA_DIR / "create_response.schema.json").read_text(encoding="utf-8"))
    query_schema = json.loads((SCHEMA_DIR / "query_response.schema.json").read_text(encoding="utf-8"))
    error_schema = json.loads((SCHEMA_DIR / "error_response.schema.json").read_text(encoding="utf-8"))
    succeeded_schema = {
        "allOf": [
            query_schema,
            {
                "type": "object",
                "required": ["task"],
                "properties": {
                    "task": {
                        "type": "object",
                        "required": ["status", "content", "resolution", "duration", "usage", "ratio"],
                        "properties": {
                            "status": {"const": "succeeded"},
                            "content": {
                                "type": "object",
                                "required": ["url"],
                                "properties": {"url": {"type": "string", "minLength": 1}},
                            },
                            "usage": {
                                "type": "object",
                                "required": ["total_seconds", "input_seconds", "output_seconds", "input_image_count"],
                            },
                        },
                    }
                },
            },
        ]
    }
    return {
        "create": jsonschema.Draft202012Validator(create_schema),
        "query": jsonschema.Draft202012Validator(query_schema),
        "succeeded": jsonschema.Draft202012Validator(succeeded_schema),
        "error": jsonschema.Draft202012Validator(error_schema),
    }


def validate_schema(validator, instance) -> str | None:
    """通过返回 None，否则返回首个 Schema 错误。"""
    errors = sorted(validator.iter_errors(instance), key=lambda error: list(error.absolute_path))
    if not errors:
        return None
    error = errors[0]
    location = "/".join(str(part) for part in error.absolute_path) or "(root)"
    return f"{location}: {error.message}"


def load_cases() -> tuple[dict, list[dict]]:
    """读取 cases.yaml。"""
    try:
        import yaml
    except ImportError:
        print("error: 缺少依赖 pyyaml，请执行 pip install pyyaml", file=sys.stderr)
        raise SystemExit(1)

    data = yaml.safe_load((HERE / "cases.yaml").read_text(encoding="utf-8"))
    config = {
        key: data.get(key)
        for key in (
            "prompt", "first_frame_url", "last_frame_url", "reference_image_url",
            "reference_video_url", "reference_audio_url", "resolution", "duration", "ratio",
        )
    }
    config["poll_interval"] = int(data.get("poll_interval", 5))
    config["poll_timeout"] = int(data.get("poll_timeout", 600))
    return config, data.get("cases", [])


def _pick(key: str, config: dict, case: dict):
    return case[key] if key in case else config.get(key)


def build_content(scenario: str, config: dict, case: dict | None = None) -> list[dict]:
    """按场景构造 MiniMax 多模态 content 数组。"""
    case = case or {}
    text = {"type": "text", "text": _pick("prompt", config, case)}
    if scenario == "text_to_video":
        return [text]
    if scenario == "image_to_video":
        return [text, {
            "type": "image_url",
            "image_url": {"url": _pick("first_frame_url", config, case)},
            "role": "first_frame",
        }]
    if scenario == "start_end_to_video":
        return [
            text,
            {"type": "image_url", "image_url": {"url": _pick("first_frame_url", config, case)}, "role": "first_frame"},
            {"type": "image_url", "image_url": {"url": _pick("last_frame_url", config, case)}, "role": "last_frame"},
        ]
    if scenario == "multimodal_reference":
        return [
            text,
            {"type": "image_url", "image_url": {"url": _pick("reference_image_url", config, case)}, "role": "reference_image"},
            {"type": "video_url", "video_url": {"url": _pick("reference_video_url", config, case)}, "role": "reference_video"},
            {"type": "audio_url", "audio_url": {"url": _pick("reference_audio_url", config, case)}, "role": "reference_audio"},
        ]
    raise ValueError(f"未知 scenario：{scenario}")


def build_create_body(model: str, content: list[dict], config: dict, case: dict) -> dict:
    """构造创建任务请求体；负向用例也允许发送超出 profile 的参数。"""
    body: dict = {"model": model, "content": content}
    for key in ("resolution", "duration", "ratio"):
        value = _pick(key, config, case)
        if value is not None and value != "":
            body[key] = value
    for key in ("extra", "callback_url", "aigc_watermark"):
        if key in case:
            body[key] = case[key]
    return body


def build_create_url(base_url: str) -> str:
    return base_url.rstrip("/") + CREATE_PATH


def build_query_url(base_url: str, task_id: str) -> str:
    encoded_id = urllib.parse.quote(task_id, safe="")
    return base_url.rstrip("/") + QUERY_PATH + "/" + encoded_id


def parse_response(raw: str, content_type: str) -> dict:
    """解析 JSON；非 JSON 响应也保留在报告中。"""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {
            "_non_json_response": True,
            "_content_type": content_type,
            "_parse_error": str(exc),
            "_raw_body": raw,
        }
    if not isinstance(parsed, dict):
        return {"_non_object_response": True, "_content_type": content_type, "_raw_body": parsed}
    return parsed


def send_request(url: str, api_key: str, method: str, body: dict | None, timeout: int) -> tuple[int, dict]:
    """发送请求；HTTP 错误响应同样解析并返回。"""
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": USER_AGENT,
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return response.status, parse_response(raw, response.headers.get("Content-Type", ""))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        content_type = exc.headers.get("Content-Type", "") if exc.headers else ""
        return exc.code, parse_response(raw, content_type)


def poll_task(base_url: str, api_key: str, task_id: str, *, interval: int, timeout_total: int, no_poll: bool) -> tuple[int, dict, list[dict]]:
    """查询一次或轮询至终态，返回最后状态、响应和全部查询记录。"""
    url = build_query_url(base_url, task_id)
    deadline = time.monotonic() + timeout_total
    history: list[dict] = []
    last_status, last_response = 0, {}
    while True:
        last_status, last_response = send_request(url, api_key, "GET", None, timeout=60)
        history.append({"http_status": last_status, "response": last_response})
        task_status = get_path(last_response, "task.status")
        if no_poll or last_status != 200 or task_status in TERMINAL_STATUSES:
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(interval)
    return last_status, last_response, history



def run_checks(checks: list[str], schemas: dict, *, create_status: int, create_response: dict,
               query_status: int, query_response: dict,
               polled_to_terminal: bool, create_body: dict, task_id: str | None,
               expected_http_status: int | None = None) -> tuple[str, str, object, object]:
    """执行命名断言，任一失败即返回 fail。"""
    task = query_response.get("task", {}) if isinstance(query_response, dict) else {}
    task_status = task.get("status") if isinstance(task, dict) else None
    if "reached_succeeded" in checks:
        expected_display = "succeeded"
        actual_display = task_status
    elif "create_error_status" in checks:
        expected_status = str(expected_http_status) if expected_http_status is not None else "4xx"
        expected_display = f"HTTP {expected_status}"
        actual_display = f"HTTP {create_status}"
    else:
        expected_display = None
        actual_display = None

    for check in checks:
        if check == "create_status_200" and create_status != 200:
            return "fail", f"创建任务 status 期望 200，实际 {create_status}", 200, create_status
        if check == "create_schema":
            error = validate_schema(schemas["create"], create_response)
            if error:
                return "fail", f"创建响应不符合 schema：{error}", None, None
        elif check == "create_error_status":
            if expected_http_status is not None:
                if create_status != expected_http_status:
                    return "fail", f"创建任务 status 期望 {expected_http_status}，实际 {create_status}", expected_http_status, create_status
            elif not 400 <= create_status < 500:
                return "fail", f"创建任务期望 4xx，实际 {create_status}", "4xx", create_status
        elif check == "error_schema":
            error = validate_schema(schemas["error"], create_response)
            if error:
                return "fail", f"错误响应不符合 schema：{error}", None, None
        elif check == "error_http_code_matches_status":
            actual = get_path(create_response, "error.http_code")
            expected = create_status
            if actual is not None and str(actual) != str(expected):
                return "fail", f"error.http_code 期望 {expected}，实际 {actual}", expected, actual
        elif check == "query_status_200" and query_status != 200:
            return "fail", f"查询任务 status 期望 200，实际 {query_status}", 200, query_status
        elif check == "query_schema":
            error = validate_schema(schemas["query"], query_response)
            if error:
                return "fail", f"最终查询响应不符合 schema：{error}", None, None
        elif check == "reached_succeeded":
            if not polled_to_terminal:
                return "fail", "轮询超时或未到终态", "succeeded", task_status
            if task_status != "succeeded":
                return "fail", f"任务终态期望 succeeded，实际 {task_status}，error={task.get('error')}", "succeeded", task_status
        elif check == "succeeded_schema":
            error = validate_schema(schemas["succeeded"], query_response)
            if error:
                return "fail", f"成功态响应不符合 schema：{error}", None, None
        elif check == "task_id_matches":
            actual = task.get("id")
            if actual != task_id:
                return "fail", f"查询 task.id 与创建 task_id 不一致：{actual}", task_id, actual
        elif check == "task_model_matches":
            actual = task.get("model")
            expected = create_body.get("model")
            if actual != expected:
                return "fail", f"task.model 期望 {expected}，实际 {actual}", expected, actual
        elif check == "task_resolution_matches":
            actual = task.get("resolution")
            expected = create_body.get("resolution")
            if actual != expected:
                return "fail", f"task.resolution 期望 {expected}，实际 {actual}", expected, actual
        elif check == "task_duration_matches":
            actual = task.get("duration")
            expected = create_body.get("duration")
            if actual != expected:
                return "fail", f"task.duration 期望 {expected}，实际 {actual}", expected, actual
        elif check == "task_ratio_matches_semantics":
            actual = task.get("ratio")
            requested = create_body.get("ratio", "adaptive")
            roles = {
                item.get("role") for item in create_body.get("content", [])
                if isinstance(item, dict)
            }
            has_frame = bool(roles & {"first_frame", "last_frame"})
            has_reference = bool(roles & {"reference_image", "reference_video", "reference_audio"})
            if has_frame:
                expected = "adaptive"
                valid = actual == expected
            elif has_reference and requested == "adaptive":
                # 文档允许参考生视频使用 adaptive；查询端可能回显 adaptive 或最终具体比例。
                expected = sorted(RATIOS)
                valid = actual in RATIOS
            else:
                expected = requested
                valid = actual == expected
            if not valid:
                return "fail", f"task.ratio 与场景语义不符：期望 {expected}，实际 {actual}", expected, actual
        elif check == "task_generation_video":
            actual = (task.get("task_type"), task.get("modality"))
            # 官方 /v2/query/video_generation 响应当前可能不返回 modality。
            # 该字段出现时必须为 video；缺省时可由查询端点和 generation 类型确定为视频任务。
            if actual[0] != "generation" or actual[1] not in (None, "video"):
                return "fail", f"任务类型期望 generation/video（modality 可省略），实际 {actual}", ["generation", "video"], list(actual)
        elif check == "usage_billing_matches_request":
            usage = task.get("usage", {})
            values = (usage.get("total_seconds"), usage.get("input_seconds"), usage.get("output_seconds"))
            if any(value is None for value in values) or values[0] != values[1] + values[2]:
                return "fail", f"usage 秒数不一致：total/input/output={values}", "total=input+output", values
            actual = usage.get("output_seconds")
            expected = task.get("duration")
            if actual != expected:
                return "fail", f"usage.output_seconds 期望 {expected}，实际 {actual}", expected, actual
            expected = sum(1 for item in create_body.get("content", []) if item.get("type") == "image_url")
            actual = usage.get("input_image_count")
            if actual != expected:
                return "fail", f"usage.input_image_count 期望 {expected}，实际 {actual}", expected, actual
            has_reference_video = any(
                isinstance(item, dict) and item.get("type") == "video_url"
                for item in create_body.get("content", [])
            )
            input_seconds = usage.get("input_seconds")
            if has_reference_video and input_seconds <= 0:
                return "fail", f"有参考视频时 usage.input_seconds 应大于 0，实际 {input_seconds}", ">0", input_seconds
            if not has_reference_video and input_seconds != 0:
                expected = ">0" if has_reference_video else 0
                return "fail", f"usage.input_seconds 与参考视频输入不符：期望 {expected}，实际 {input_seconds}", expected, input_seconds
        elif check not in {
            "create_status_200", "query_status_200", "create_schema", "create_error_status",
            "error_schema", "error_http_code_matches_status", "query_schema",
            "reached_succeeded", "succeeded_schema", "task_id_matches", "task_model_matches",
            "task_resolution_matches", "task_duration_matches", "task_ratio_matches_semantics",
            "task_generation_video", "usage_billing_matches_request",
        }:
            return "fail", f"未知 check：{check}", None, None
    return "pass", "", expected_display, actual_display


def run_case(case: dict, *, schemas: dict, config: dict, profile: MiniMaxH3Profile,
             model: str, base_url: str, api_key: str, dry_run: bool, no_poll: bool) -> CaseResult:
    """执行单个用例。"""
    case_id = case["id"]
    name = case.get("name", case_id)
    scenario = case.get("scenario", "text_to_video")
    base_details = {"scenario": scenario, "profile": profile.name}

    try:
        reason = case_skip_reason(case, profile)
        if reason:
            return CaseResult(id=case_id, name=name, status="skipped", details={**base_details, "skip_reason": reason})
        effective_case = apply_profile_overrides(case, profile)
        case_model = effective_case.get("model", model)
        checks = list(effective_case.get("checks", []))
        negative_case = "create_error_status" in checks
        content = build_content(scenario, config, effective_case)
        create_body = build_create_body(case_model, content, config, effective_case)
    except Exception as exc:  # noqa: BLE001
        return CaseResult(id=case_id, name=name, status="error", error=f"构造请求失败：{exc!r}", details=base_details)

    skipped_checks: list[str] = []
    if no_poll and not negative_case:
        skipped_checks = [check for check in checks if check not in NO_POLL_CHECKS]
        checks = [check for check in checks if check in NO_POLL_CHECKS]

    create_url = build_create_url(base_url) if base_url else ""
    details = {
        **base_details,
        "model": case_model,
        "create_url": create_url,
        "create_body": truncate(create_body),
        "checks": checks,
        "skipped_checks": skipped_checks,
    }
    if dry_run:
        return CaseResult(id=case_id, name=name, status="pass", details={**details, "dry_run": True})

    start = time.monotonic()
    try:
        create_status, create_response = send_request(create_url, api_key, "POST", create_body, timeout=120)
    except Exception as exc:  # noqa: BLE001
        return CaseResult(
            id=case_id, name=name, status="error", error=f"创建请求异常：{exc!r}",
            duration_ms=int((time.monotonic() - start) * 1000), details=details,
        )

    task_id = create_response.get("task_id") if isinstance(create_response, dict) else None
    query_status, query_response, history = 0, {}, []
    if create_status == 200 and task_id:
        try:
            query_status, query_response, history = poll_task(
                base_url, api_key, task_id,
                interval=config["poll_interval"], timeout_total=config["poll_timeout"],
                no_poll=no_poll,
            )
        except Exception as exc:  # noqa: BLE001
            return CaseResult(
                id=case_id, name=name, status="error", error=f"查询请求异常：{exc!r}",
                duration_ms=int((time.monotonic() - start) * 1000),
                details={**details, "task_id": task_id, "create_response": truncate(create_response)},
            )

    task_status = get_path(query_response, "task.status")
    polled_to_terminal = task_status in TERMINAL_STATUSES
    verdict, error, expected, actual = run_checks(
        checks, schemas,
        create_status=create_status, create_response=create_response,
        query_status=query_status, query_response=query_response,
        polled_to_terminal=polled_to_terminal, create_body=create_body, task_id=task_id,
        expected_http_status=effective_case.get("expected_http_status"),
    )
    elapsed = int((time.monotonic() - start) * 1000)
    task = query_response.get("task", {}) if isinstance(query_response, dict) else {}
    return CaseResult(
        id=case_id, name=name, status=verdict, error=error or None,
        expected=expected, actual=actual, duration_ms=elapsed,
        details={
            **details,
            "task_id": task_id,
            "polls": len(history),
            "task_status": task_status,
            "usage": task.get("usage") if isinstance(task, dict) else None,
            "video_url": get_path(query_response, "task.content.url"),
            "create_response": truncate(create_response),
            "query_response": truncate(query_response),
        },
    )


def main() -> int:
    profiles = load_profiles()
    parser = argparse.ArgumentParser(description="运行 MiniMax H3 视频生成兼容性测试")
    parser.add_argument("--profile", required=True, choices=sorted(profiles), help="模型能力档案")
    parser.add_argument("--model", default=os.environ.get("MINIMAX_H3_MODEL"), help="覆盖请求体中的模型名称")
    parser.add_argument("--dry-run", action="store_true", help="只构造请求和加载 Schema，不访问接口")
    parser.add_argument("--no-poll", action="store_true", help="创建后只查询一次，不等待终态")
    parser.add_argument("--out", default=str(HERE / "reports"), help="报告输出目录")
    args = parser.parse_args()

    profile = profiles[args.profile]
    model = args.model or profile.default_model
    schemas = load_schemas()
    config, cases = load_cases()
    if not cases:
        print("error: cases.yaml 中没有用例", file=sys.stderr)
        return 1

    base_url = os.environ.get("API_BASE_URL", "")
    api_key = os.environ.get("API_KEY", "")
    if not args.dry_run:
        missing = [name for name, value in (("API_BASE_URL", base_url), ("API_KEY", api_key)) if not value]
        if missing:
            print(f"error: 未设置 {' / '.join(missing)}；本地自测可使用 --dry-run", file=sys.stderr)
            return 1

    def work(case: dict) -> CaseResult:
        return run_case(
            case, schemas=schemas, config=config, profile=profile, model=model,
            base_url=base_url, api_key=api_key, dry_run=args.dry_run, no_poll=args.no_poll,
        )

    if args.dry_run:
        results = [work(case) for case in cases]
    else:
        with ThreadPoolExecutor(max_workers=len(cases)) as pool:
            results = list(pool.map(work, cases))

    report = Report(
        model=model,
        cases=results,
        env={
            "API_BASE_URL": base_url,
            "API_KEY": mask_secret(api_key),
            "MINIMAX_H3_MODEL": model,
            "MINIMAX_H3_PROFILE": profile.name,
        },
    )
    paths = report.write(args.out)
    summary = report.summary()
    verdict = "PASS" if report.passed else "FAIL"
    print(
        f"{model}: {verdict} total={summary['total']} pass={summary['passed']} "
        f"fail={summary['failed']} error={summary['errored']} skip={summary['skipped']} "
        f"warn={summary['warned']} ({summary['duration_ms']}ms)"
    )
    print("报告已写入：" + "、".join(str(path) for path in paths.values()))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
