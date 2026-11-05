#!/usr/bin/env python3
"""Kling 4.0 官方新版协议的生成能力与接口契约测试。"""

from __future__ import annotations

import argparse
import base64
import copy
import fnmatch
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, InvalidOperation
from pathlib import Path

import jsonschema
import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "_shared"))
from http_common import USER_AGENT  # noqa: E402
from report import CaseResult, Report, mask_secret  # noqa: E402

ENDPOINTS = {"text-to-video", "image-to-video", "omni-video"}
TERMINAL = {"succeeded", "failed"}


class MissingFixture(ValueError):
    """选中的场景尚未配置足量素材。"""


class ContractError(ValueError):
    """接口响应或生成产物不满足断言。"""


def require(condition, message):
    if not condition:
        raise ContractError(message)


def load_cases(path=HERE / "cases.yaml", fixtures_path=None):
    config = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if fixtures_path:
        overrides = yaml.safe_load(Path(fixtures_path).read_text(encoding="utf-8"))
        config["fixtures"].update(overrides.get("fixtures", overrides))
    cases = config.pop("cases")
    ids = [case["id"] for case in cases]
    if not ids or len(set(ids)) != len(ids):
        raise ValueError("用例不能为空，且 ID 必须唯一")
    for case in cases:
        if case["endpoint"] not in ENDPOINTS:
            raise ValueError(f"未知端点：{case['endpoint']}")
    return config, cases


def load_schemas():
    schema = json.loads((HERE / "schemas" / "responses.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    return {name: jsonschema.Draft202012Validator(
        {"$ref": f"#/$defs/{name}", "$defs": schema["$defs"]}
    ) for name in ("create", "query", "task", "succeeded_task", "video", "error")}


def validate(schemas, name, response):
    errors = sorted(schemas[name].iter_errors(response),
                    key=lambda error: str(list(error.absolute_path)))
    if errors:
        error = errors[0]
        location = ".".join(map(str, error.absolute_path)) or "根节点"
        raise ContractError(f"{name} 响应结构错误，{location}: {error.message}")



def edit_path(body, path, value=None, *, remove=False):
    """按点分隔路径修改负向请求，数字路径段为数组下标；非法路径直接报错。"""
    parts = path.split(".")
    current = body
    for part in parts[:-1]:
        current = current[int(part)] if isinstance(current, list) else current[part]
    key = int(parts[-1]) if isinstance(current, list) else parts[-1]
    if remove:
        del current[key]
    else:
        current[key] = value


def material_values(spec, fixtures):
    value = fixtures.get(spec["fixture"])
    values = value if isinstance(value, list) else [value] if value else []
    count = spec.get("count", 1)
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise ValueError("素材数量必须为正整数")
    if not values or any(not isinstance(item, str) or not item for item in values):
        raise MissingFixture(f"缺少素材 {spec['fixture']}")
    if count > len(values) and not spec.get("repeat"):
        raise MissingFixture(f"{spec['fixture']} 需要 {count} 份素材，当前 {len(values)} 份")
    return [values[index % len(values)] for index in range(count)]


def image_base64(source, dry_run):
    if dry_run:
        return f"<执行时将素材编码为Base64：{source}>"
    parsed = urllib.parse.urlsplit(source)
    if parsed.scheme in {"https", "http"}:
        request = urllib.request.Request(source, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=60) as response:
            content = response.read(30 * 1024 * 1024 + 1)
    else:
        with Path(source).open("rb") as handle:
            content = handle.read(30 * 1024 * 1024 + 1)
    if not content or len(content) > 30 * 1024 * 1024:
        raise ValueError("Base64 输入图片必须非空且不超过30MB")
    return base64.b64encode(content).decode("ascii")


def build_body(case, config, run_id, dry_run=False):
    prompt = case.get("prompt", "")
    body = {"prompt": prompt} if case["endpoint"] == "text-to-video" else {
        "contents": [{"type": "prompt", "text": prompt}]
    }
    counters = {}
    # 先检查全部素材，缺失配置时不会先下载前面的 Base64 图片。
    materials = [(spec, material_values(spec, config["fixtures"])) for spec in case.get("inputs", [])]
    for spec, values in materials:
        kind = spec["type"]
        for value in values:
            counters[kind] = counters.get(kind, 0) + 1
            if spec.get("encoding") == "base64":
                value = image_base64(value, dry_run)
            body["contents"].append({"type": kind, "url": value, "id": f"{kind}_{counters[kind]}"})
    for key in ("settings", "options"):
        if key in case:
            body[key] = copy.deepcopy(case[key])
    body.setdefault("options", {})["external_task_id"] = f"{run_id}-{case['id']}"
    # 负向参数必须原样发到被测服务，不能由本地校验替服务端拒绝。
    for path, value in case.get("patch", {}).items():
        edit_path(body, path, value)
    for path in case.get("omit", []):
        edit_path(body, path, remove=True)
    return body


def create_url(base_url, endpoint, model):
    return f"{base_url.rstrip('/')}/{endpoint}/{urllib.parse.quote(model, safe='')}"


def query_url(base_url, ids):
    return base_url.rstrip("/") + "/tasks?" + urllib.parse.urlencode({"task_ids": ",".join(ids)})


def parse_response(raw):
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {"_non_json_response": raw}
    return value if isinstance(value, dict) else {"_non_object_response": value}


def send_request(url, api_key, method, body, timeout=60):
    """创建操作不自动重试，避免网络超时后重复生成收费任务。"""
    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(url, method=method, headers=headers,
                                    data=None if body is None else json.dumps(body, ensure_ascii=False).encode())
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, parse_response(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        return exc.code, parse_response(exc.read().decode("utf-8", errors="replace"))


def check_success(status, response, schemas, name):
    require(status == 200, f"预期 HTTP 200，实际 {status}：{response.get('message', response)}")
    validate(schemas, name, response)


def check_error(status, response, expected, schemas):
    """负向通过须同时满足HTTP状态、错误结构和业务错误码。"""
    require(status == expected["http_status"],
            f"预期 HTTP {expected['http_status']}，实际 {status}")
    validate(schemas, "error", response)
    require(response["code"] in expected["codes"],
            f"预期业务码 {expected['codes']}，实际 {response['code']}：{response['message']}")
    data = response.get("data")
    require(not (isinstance(data, dict) and data.get("id")), "参数错误响应不应创建视频任务")


def check_task(task, task_id, external_id=None):
    require(task["id"] == task_id, f"任务ID不匹配：预期 {task_id}，实际 {task['id']}")
    require(task["update_time"] >= task["create_time"], "更新时间早于创建时间")
    if external_id:
        require(task.get("external_id") == external_id, "external_id 未正确回显")


def get_task(response, task_id):
    tasks = response["data"]
    require(len(tasks) == 1, f"单任务查询应返回1项，实际 {len(tasks)} 项")
    task = tasks[0]
    check_task(task, task_id)
    return task


def poll_task(base_url, api_key, task_id, external_id, config, schemas, details, no_poll=False):
    deadline = time.monotonic() + config["poll_timeout"]
    previous = details.get("create_response", {}).get("data")
    while True:
        remaining = deadline - time.monotonic()
        require(remaining > 0, f"轮询超时，任务 {task_id} 尚未完成")
        status, response = send_request(query_url(base_url, [task_id]), api_key, "GET", None,
                                        timeout=min(config["request_timeout"], remaining))
        details["query_response"] = response
        details.setdefault("poll_history", []).append({
            "http_status": status, "code": response.get("code"),
            "elapsed_seconds": round(config["poll_timeout"] - remaining, 2),
            "statuses": [item.get("status") for item in response.get("data", [])
                         if isinstance(item, dict)] if isinstance(response.get("data"), list) else [],
        })
        check_success(status, response, schemas, "query")
        task = get_task(response, task_id)
        check_task(task, task_id, external_id)
        if previous:
            require(task["update_time"] >= previous["update_time"], "轮询中的更新时间倒退")
            require(not (previous["status"] == "processing" and task["status"] == "submitted"),
                    "任务状态从 processing 回退到 submitted")
            require(previous["status"] not in TERMINAL or task["status"] == previous["status"],
                    "任务终态发生变化")
        previous = task
        if task["status"] in TERMINAL or no_poll:
            return task
        remaining = deadline - time.monotonic()
        require(remaining > 0, f"轮询超时，任务 {task_id} 尚未完成")
        time.sleep(min(config["poll_interval"], remaining))


def number(value, label):
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ContractError(f"{label} 不是有效数字：{value!r}") from exc
    require(result.is_finite(), f"{label} 不能为 NaN 或无穷大")
    return result


def check_outputs(task, case, body, schemas):
    require(task["status"] == "succeeded", f"任务未成功：{task['status']}，{task.get('message', '')}")
    validate(schemas, "succeeded_task", task)
    videos = [item for item in task.get("outputs", []) if item.get("type") == "video"]
    require(bool(videos), "成功任务没有视频产物")
    expected = case.get("expected_duration", body.get("settings", {}).get("duration"))
    if expected is None and not any(item.get("type") == "base_video" for item in body.get("contents", [])):
        expected = 10
    duration_tolerance = Decimal("0.1")
    for video in videos:
        validate(schemas, "video", video)
        duration = number(video["duration"], "视频时长")
        # 编码产物可能多出或少于一帧，边界与请求时长匹配使用同一容差。
        require(Decimal(3) - duration_tolerance <= duration <= Decimal(30) + duration_tolerance,
                f"产物时长超出3～30秒范围（允许0.1秒偏差）：{duration}")
        if expected is not None:
            require(abs(duration - Decimal(str(expected))) <= duration_tolerance,
                    f"视频时长不匹配：预期 {expected}，实际 {duration}")
        if body.get("options", {}).get("watermark_info", {}).get("enabled"):
            require(is_http_url(video.get("watermark_url", "")), "开启水印后未返回有效 watermark_url")
    # 文档规定 usage 在有用量时返回，不为无视频输入任务编造 token 或秒数公式。
    usage = task.get("usage")
    if usage and "input_duration" in usage:
        require(number(usage["input_duration"], "输入视频时长") > 0, "输入视频时长应大于0")
    for billing in task.get("billing", []):
        require(number(billing["amount"], "扣减数额") >= 0, "扣减数额不能为负")
        if "list_price" in billing:
            require(number(billing["list_price"], "刊例价") >= 0, "刊例价不能为负")
    return videos


def is_http_url(value):
    if not isinstance(value, str):
        return False
    parsed = urllib.parse.urlsplit(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def run_case(case, *, config, schemas, base_url, api_key, model, run_id,
             dry_run=False, no_poll=False):
    started = time.monotonic()
    details = {"endpoint": case["endpoint"], "manual_review": {
        "status": "pending" if case.get("review") else "not_applicable",
        "items": case.get("review", [])}, "dry_run": dry_run,
        "case_type": "negative" if "expected_error" in case else "positive"}
    result = CaseResult(id=case["id"], name=case["name"], status="error", details=details,
                        expected=case.get("expected_error", "创建和查询成功，产物满足响应契约"))
    try:
        body = build_body(case, config, run_id, dry_run)
        url = create_url(base_url, case["endpoint"], model)
        details.update(create_url=url, create_body=body)
        prompt = body.get("prompt", next((item.get("text", "") for item in body.get("contents", [])
                                          if item.get("type") == "prompt"), ""))
        details["prompt_characters"] = {"total": len(prompt),
                                        "english_ascii": sum(ord(char) < 128 for char in prompt),
                                        "non_english": sum(ord(char) >= 128 for char in prompt)}
        if dry_run:
            result.status = "pass"
            result.actual = "请求已构造；未访问接口、未验证模型能力"
            return result
        status, response = send_request(url, api_key, "POST", body, config["request_timeout"])
        details.update(create_http_status=status, create_response=response)
        # 在后续校验之前保留任务ID，便于异常后追踪。
        data = response.get("data")
        if isinstance(data, dict):
            details["task_id"] = data.get("id")
        if "expected_error" in case:
            result.actual = {"http_status": status, "code": response.get("code")}
            check_error(status, response, case["expected_error"], schemas)
            result.status = "pass"
            return result
        check_success(status, response, schemas, "create")
        task_id = data["id"]
        external_id = body["options"]["external_task_id"]
        check_task(data, task_id, external_id)
        task = poll_task(base_url, api_key, task_id, external_id, config, schemas, details, no_poll)
        details.update(task_status=task["status"], billing=task.get("billing"), usage=task.get("usage"))
        if no_poll and task["status"] not in TERMINAL:
            result.status = "skipped"
            result.actual = "创建与首次查询通过；尚未验证生成终态"
            return result
        videos = check_outputs(task, case, body, schemas)
        for video in videos:
            require(is_http_url(video["url"]), "生成结果不是HTTP(S)视频链接")
        details["videos"] = [{"video_url": item["url"], "duration": item["duration"],
                              "watermark_video_url": item.get("watermark_url")} for item in videos]
        details["media_checks"] = (
            "自动校验接口返回的视频链接与时长；实际分辨率、画幅、帧率、时长、可播放性及画面效果待人工验收"
        )
        result.status = "pass"
        result.actual = "生成及接口契约通过；画面效果待人工复核"
    except MissingFixture as exc:
        result.status, result.error = "error", str(exc)
    except ContractError as exc:
        result.status, result.error = "fail", str(exc)
    except Exception as exc:  # 网络与执行异常保留现场。
        result.status, result.error = "error", f"{type(exc).__name__}: {exc}"
    finally:
        result.duration_ms = round((time.monotonic() - started) * 1000)
    return result



def report_value(value, api_key):
    """先脱敏再截断，防止错误响应或回显请求泄露密钥。"""
    if isinstance(value, str):
        if api_key:
            value = value.replace(api_key, "[密钥已隐藏]")
        return value if len(value) <= 1000 else value[:1000] + f"…（共{len(value)}字符）"
    if isinstance(value, dict):
        return {key: report_value(item, api_key) for key, item in value.items()}
    if isinstance(value, list):
        return [report_value(item, api_key) for item in value]
    return value


def positive_float(value):
    result = float(value)
    if not 0 < result < float("inf"):
        raise argparse.ArgumentTypeError("必须为有限正数")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Kling 4.0 官方新版协议视频生成能力测试；默认执行全部用例并严格校验")
    parser.add_argument("--model", default=os.environ.get("KLING_MODEL", "kling-4.0"), help="模型路径段，可使用兼容网关别名")
    parser.add_argument("--cases", default=str(HERE / "cases.yaml"), help="用例文件")
    parser.add_argument("--fixtures", help="覆盖 fixtures 的 YAML 文件")
    parser.add_argument("--case", action="append", default=[], help="按ID或通配符选择，可重复")
    parser.add_argument("--list", action="store_true", help="列出用例与素材缺口，不访问网络")
    parser.add_argument("--dry-run", action="store_true", help="只构造请求并加载响应契约，不访问网络")
    parser.add_argument("--no-poll", action="store_true", help="创建后只查询一次，未完成任务标记跳过")
    parser.add_argument("--workers", type=int, default=2, help="最大并发数，默认2")
    parser.add_argument("--poll-interval", type=positive_float)
    parser.add_argument("--poll-timeout", type=positive_float)
    parser.add_argument("--out", default=str(HERE / "reports"), help="报告目录")
    args = parser.parse_args(argv)
    if args.workers < 1:
        parser.error("--workers 必须大于0")
    config, cases = load_cases(args.cases, args.fixtures)
    for name in ("poll_interval", "poll_timeout", "request_timeout"):
        override = getattr(args, name, None)
        config[name] = positive_float(override if override is not None else config[name])
    if args.case:
        for pattern in args.case:
            if not any(fnmatch.fnmatchcase(case["id"], pattern) for case in cases):
                parser.error(f"没有匹配用例：{pattern}")
        cases = [case for case in cases if any(fnmatch.fnmatchcase(case["id"], pattern) for pattern in args.case)]
    schemas = load_schemas()
    if args.list:
        for case in cases:
            try:
                build_body(case, config, "preview", dry_run=True)
                state = "可执行"
            except MissingFixture as exc:
                state = str(exc)
            print(f"{case['id']} | {case['name']} | {state}")
        return 0
    base_url = os.environ.get("API_BASE_URL", "")
    api_key = os.environ.get("API_KEY", "")
    if not args.dry_run:
        if not is_http_url(base_url) or not api_key:
            parser.error("真实调用需要设置有效 API_BASE_URL 和 API_KEY；本地检查使用 --dry-run")
        if urllib.parse.urlsplit(base_url).query or urllib.parse.urlsplit(base_url).fragment:
            parser.error("API_BASE_URL 不能包含查询参数或片段")
    run_id = "kv4-" + uuid.uuid4().hex[:12]
    missing = []
    # 默认预检全部所选用例，预演与真实执行都不能因缺素材缩减覆盖范围。
    for case in cases:
        try:
            build_body(case, config, run_id, dry_run=True)
        except MissingFixture as exc:
            missing.append((case["id"], str(exc)))
    if missing:
        parser.error("素材预检未通过：" + "；".join(f"{cid}: {reason}" for cid, reason in missing))
    positive_count = sum("expected_error" not in case for case in cases)
    negative_count = len(cases) - positive_count
    print(f"准备执行 {len(cases)} 个用例：正向 {positive_count} 个、负向 {negative_count} 个，"
          f"预期生成 {positive_count} 段视频；全部通过才返回成功。"
          + ("当前为离线预演。" if args.dry_run else ""), flush=True)

    def work(case):
        result = run_case(case, config=config, schemas=schemas, base_url=base_url, api_key=api_key,
                          model=args.model, run_id=run_id, dry_run=args.dry_run,
                          no_poll=args.no_poll)
        print(f"[{result.status}] {result.id}" +
              (f"：{report_value(result.error, api_key)}" if result.error else ""), flush=True)
        return result

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(work, cases))
    for result in results:
        result.details = report_value(result.details, api_key)
        result.error = report_value(result.error, api_key)
    report = Report(model=args.model, cases=results, env={
        "API_BASE_URL": base_url, "API_KEY": mask_secret(api_key), "KLING_MODEL": args.model,
        "run_id": run_id, "dry_run": args.dry_run,
        "manual_review": "实际媒体参数、可播放性和画面效果需按各用例清单人工确认",
    })
    paths = report.write(args.out)
    print(json.dumps(report.summary(), ensure_ascii=False))
    print("报告已写入：" + "、".join(str(path) for path in paths.values()))
    return 0 if all(result.status == "pass" for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
