#!/usr/bin/env python3
"""Seedance 视频生成兼容性测试执行入口（火山方舟兼容格式）。

把接口地址指向被测的 Seedance 兼容服务，运行本脚本即可测试该接口，
并在 reports/ 下生成报告。

校验目标：被测端点的 path、请求体、响应体能完全兼容火山方舟视频生成格式。
  创建任务  POST {API_BASE_URL}/contents/generations/tasks   （JSON 请求，返回 {id}）
  查询任务  GET  {API_BASE_URL}/contents/generations/tasks/{id}（轮询直到终态）

视频生成是异步流程：创建任务拿到 id 后，轮询查询接口直到终态
（succeeded / failed / expired / cancelled）。响应体结构用 schemas/ 下的
JSON Schema（draft 2020-12）校验，跨字段与流程语义保留为少量命名 check。

环境变量：
  API_BASE_URL    必填，被测接口的基础地址，如 https://your-domain.com/api/v3
  API_KEY         必填，被测接口的鉴权密钥
  SEEDANCE_MODEL  选填，默认 doubao-seedance-2-0-260128

所有 case 默认并发执行（视频生成较慢，串行会很耗时），各 case 内部独立轮询。

用法：
  python run_tests.py            # 并发请求接口，轮询到终态
  python run_tests.py --no-poll  # 仅创建 + 单次查询，快速冒烟（不等待终态）
  python run_tests.py --dry-run  # 跳过真实请求，仅自测请求体构造与 schema 加载
"""

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

# 复用公共报告模块
sys.path.insert(0, str(SHARED))
from report import CaseResult, Report  # noqa: E402

# 默认配置（base_url 无默认，必须通过 API_BASE_URL 指定）
DEFAULT_MODEL = "doubao-seedance-2-0-260128"

# 视频生成任务的接口路径（与火山方舟一致）
CREATE_PATH = "/contents/generations/tasks"

# 任务终态：轮询到这些状态即停止
TERMINAL_STATUSES = {"succeeded", "failed", "expired", "cancelled"}

# 报告中单个字符串字段保留的最大长度（base64 等会被截断）
MAX_STR_LEN = 500


def truncate(value, max_len: int = MAX_STR_LEN):
    """递归截断过长字符串，避免 base64 等把报告撑爆。"""
    if isinstance(value, str):
        if len(value) > max_len:
            return value[:max_len] + f"...(已截断，共 {len(value)} 字符)"
        return value
    if isinstance(value, list):
        return [truncate(v, max_len) for v in value]
    if isinstance(value, dict):
        return {k: truncate(v, max_len) for k, v in value.items()}
    return value


def get_path(obj, path: str):
    """按点号路径取嵌套字段，缺失返回 None。"""
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


# ==================== schema 加载与校验 ====================


def load_schemas() -> dict:
    """加载 schemas/ 下的 JSON Schema，并派生成功态 schema。

    返回 {"create", "query", "succeeded", "error"} 各自的校验器。
    成功态 schema 在查询基础 schema 上 allOf 追加：必有 content.video_url 与 usage。
    """
    try:
        import jsonschema
    except ImportError:
        print("error: 缺少依赖 jsonschema，请执行 pip install jsonschema", file=sys.stderr)
        raise SystemExit(1)

    create_schema = json.loads((SCHEMA_DIR / "create_response.schema.json").read_text(encoding="utf-8"))
    query_schema = json.loads((SCHEMA_DIR / "query_response.schema.json").read_text(encoding="utf-8"))
    error_schema = json.loads((SCHEMA_DIR / "error_response.schema.json").read_text(encoding="utf-8"))

    # 成功态：在基础 query schema 上叠加约束（必有 content.video_url 与 usage）
    succeeded_schema = {
        "allOf": [
            query_schema,
            {
                "type": "object",
                "required": ["content", "usage"],
                "properties": {
                    "content": {
                        "type": "object",
                        "required": ["video_url"],
                        "properties": {"video_url": {"type": "string", "minLength": 1}},
                    },
                    "usage": {
                        "type": "object",
                        "required": ["completion_tokens", "total_tokens"],
                    },
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
    """用 validator 校验 instance，通过返回 None，否则返回首个错误的可读描述。"""
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))
    if not errors:
        return None
    err = errors[0]
    loc = "/".join(str(p) for p in err.absolute_path) or "(root)"
    return f"{loc}: {err.message}"


# ==================== 用例加载与请求构造 ====================


def load_cases() -> tuple[dict, list[dict]]:
    """读取 cases.yaml，返回（全局配置 dict, 用例列表）。"""
    try:
        import yaml
    except ImportError:
        print("error: 缺少依赖 pyyaml，请执行 pip install pyyaml", file=sys.stderr)
        raise SystemExit(1)

    data = yaml.safe_load((HERE / "cases.yaml").read_text(encoding="utf-8"))
    config = {
        "prompt": data.get("prompt", ""),
        "first_frame_url": data.get("first_frame_url", ""),
        "last_frame_url": data.get("last_frame_url", ""),
        "reference_image_url": data.get("reference_image_url", ""),
        "reference_video_url": data.get("reference_video_url", ""),
        "reference_audio_url": data.get("reference_audio_url", ""),
        "resolution": data.get("resolution", ""),
        "ratio": data.get("ratio", ""),
        "duration": data.get("duration"),
        "poll_interval": int(data.get("poll_interval", 5)),
        "poll_timeout": int(data.get("poll_timeout", 600)),
    }
    return config, data.get("cases", [])


def build_content(scenario: str, cfg: dict) -> list[dict]:
    """按场景拼 content[] 数组（type/role 符合火山方舟格式）。"""
    prompt = cfg["prompt"]
    text_item = {"type": "text", "text": prompt}

    if scenario == "text_to_video":
        return [text_item]

    if scenario == "image_to_video":
        return [
            text_item,
            {"type": "image_url", "image_url": {"url": cfg["first_frame_url"]}, "role": "first_frame"},
        ]

    if scenario == "start_end_to_video":
        return [
            text_item,
            {"type": "image_url", "image_url": {"url": cfg["first_frame_url"]}, "role": "first_frame"},
            {"type": "image_url", "image_url": {"url": cfg["last_frame_url"]}, "role": "last_frame"},
        ]

    if scenario == "multimodal_reference":
        return [
            text_item,
            {"type": "image_url", "image_url": {"url": cfg["reference_image_url"]}, "role": "reference_image"},
            {"type": "video_url", "video_url": {"url": cfg["reference_video_url"]}, "role": "reference_video"},
            {"type": "audio_url", "audio_url": {"url": cfg["reference_audio_url"]}, "role": "reference_audio"},
        ]

    raise ValueError(f"未知 scenario：{scenario}")


def build_create_body(model: str, content: list[dict], cfg: dict, case: dict) -> dict:
    """构造创建任务的 JSON 请求体（顶层可选参数留空则不发送）。"""
    body: dict = {"model": model, "content": content}

    # 顶层生成参数：case 优先于全局配置
    resolution = case.get("resolution", cfg.get("resolution"))
    ratio = case.get("ratio", cfg.get("ratio"))
    duration = case.get("duration", cfg.get("duration"))
    if resolution:
        body["resolution"] = resolution
    if ratio:
        body["ratio"] = ratio
    if duration is not None:
        body["duration"] = duration

    # 其余可选开关（仅在 case 显式声明时发送）
    for key in ("seed", "camera_fixed", "watermark", "generate_audio", "return_last_frame"):
        if key in case:
            body[key] = case[key]

    return body


# ==================== HTTP 请求 ====================


def build_create_url(base_url: str) -> str:
    """创建任务 URL：base_url 去尾斜杠 + 固定 path。"""
    return base_url.rstrip("/") + CREATE_PATH


def build_query_url(base_url: str, task_id: str) -> str:
    """查询任务 URL：创建 URL + /{id}。"""
    return build_create_url(base_url) + "/" + urllib.parse.quote(task_id, safe="")


def send_request(url: str, api_key: str, method: str, body: dict | None,
                 timeout: int) -> tuple[int, dict]:
    """发送请求并解析 JSON 响应，返回 (状态码, 响应 JSON)。"""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # HTTP 错误也读出 body，便于报告里展示错误详情
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"error": raw}


def poll_task(base_url: str, api_key: str, task_id: str, *, interval: int,
              timeout_total: int, no_poll: bool) -> tuple[int, dict, int]:
    """轮询查询任务状态，返回 (最后一次 HTTP 状态, 最后一次响应, 轮询次数)。

    no_poll=True 时只查询一次即返回；否则轮询直到终态或总超时。
    使用 time.monotonic 计时，不依赖 time.time/random。
    """
    url = build_query_url(base_url, task_id)
    polls = 0
    deadline = time.monotonic() + timeout_total
    last_status, last_resp = 0, {}
    while True:
        status, resp = send_request(url, api_key, "GET", None, timeout=60)
        polls += 1
        last_status, last_resp = status, resp
        if no_poll:
            break
        task_status = resp.get("status") if isinstance(resp, dict) else None
        if status != 200 or task_status in TERMINAL_STATUSES:
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(interval)
    return last_status, last_resp, polls


# ==================== 校验 ====================


def run_checks(checks: list[str], schemas: dict, *, create_status: int,
               create_resp: dict, query_status: int, query_resp: dict,
               polled: bool) -> tuple[str, str, object, object]:
    """执行校验项，返回 (status, error, expected, actual)。

    任一 check 不通过即 fail，error 记录首个失败原因。
    expected/actual 反映任务最终状态，便于报告直观展示。
    """
    task_status = query_resp.get("status") if isinstance(query_resp, dict) else None
    expected_display = "succeeded" if "reached_succeeded" in checks else None
    actual_display = task_status if "reached_succeeded" in checks else None

    for check in checks:
        if check == "create_status_200":
            if create_status != 200:
                return "fail", f"创建任务 status 期望 200，实际 {create_status}", 200, create_status

        elif check == "create_schema":
            err = validate_schema(schemas["create"], create_resp)
            if err:
                return "fail", f"创建响应不符合 schema：{err}", None, None

        elif check == "create_error_status":
            # 负向用例：创建任务应返回 4xx（非法模型/参数等）
            if not (400 <= create_status < 500):
                return "fail", f"创建任务期望 4xx 错误，实际 {create_status}", "4xx", create_status

        elif check == "error_schema":
            # 负向用例：错误响应应符合 {error:{code,message,...}} 结构
            err = validate_schema(schemas["error"], create_resp)
            if err:
                return "fail", f"错误响应不符合 schema：{err}", None, None

        elif check == "query_status_200":
            if query_status != 200:
                return "fail", f"查询任务 status 期望 200，实际 {query_status}", 200, query_status

        elif check == "query_schema":
            err = validate_schema(schemas["query"], query_resp)
            if err:
                return "fail", f"查询响应不符合 schema：{err}", None, None

        elif check == "reached_succeeded":
            if not polled:
                return "fail", "未轮询到终态（--no-poll 模式下不应声明 reached_succeeded）", "succeeded", task_status
            if task_status != "succeeded":
                err_obj = query_resp.get("error") if isinstance(query_resp, dict) else None
                hint = f"，error={err_obj}" if err_obj else ""
                return "fail", f"任务终态期望 succeeded，实际 {task_status}{hint}", "succeeded", task_status

        elif check == "succeeded_schema":
            err = validate_schema(schemas["succeeded"], query_resp)
            if err:
                return "fail", f"成功态响应不符合 schema：{err}", None, None

        elif check == "usage_total_equals_completion":
            completion = get_path(query_resp, "usage.completion_tokens")
            total = get_path(query_resp, "usage.total_tokens")
            if completion is None or total is None:
                return "fail", "usage.completion_tokens / total_tokens 缺失", None, None
            if total != completion:
                return "fail", f"total_tokens({total}) != completion_tokens({completion})", completion, total

        else:
            return "fail", f"未知 check：{check}", None, None

    return "pass", "", expected_display, actual_display


# ==================== 单个 case 执行 ====================


def run_case(case: dict, *, schemas: dict, config: dict, model: str, base_url: str,
             api_key: str, dry_run: bool, no_poll: bool) -> CaseResult:
    """执行单个 case，返回 CaseResult。无共享可变状态，可安全并发调用。"""
    cid = case["id"]
    name = case.get("name", cid)
    scenario = case.get("scenario", "text_to_video")
    checks = case.get("checks", [])
    case_model = case.get("model", model)

    base_details = {"scenario": scenario, "model": case_model}

    # 构造创建请求体
    try:
        content = build_content(scenario, config)
        create_body = build_create_body(case_model, content, config, case)
    except Exception as exc:  # noqa: BLE001
        return CaseResult(
            id=cid, name=name, status="error",
            error=f"构造请求失败：{exc!r}", details=base_details,
        )

    create_url = build_create_url(base_url) if base_url else ""

    if dry_run:
        # 干跑：不打接口，只确认请求体构造与 schema 已加载
        return CaseResult(
            id=cid, name=name, status="pass",
            expected=None, actual=None, duration_ms=0,
            details={
                **base_details,
                "dry_run": True,
                "create_url": create_url,
                "create_body": truncate(create_body),
                "checks": checks,
            },
        )

    start = time.monotonic()
    # 1) 创建任务
    try:
        create_status, create_resp = send_request(
            create_url, api_key, "POST", create_body, timeout=120
        )
    except Exception as exc:  # noqa: BLE001
        elapsed = int((time.monotonic() - start) * 1000)
        return CaseResult(
            id=cid, name=name, status="error", error=f"创建请求异常：{exc!r}",
            duration_ms=elapsed,
            details={**base_details, "create_url": create_url,
                     "create_body": truncate(create_body), "checks": checks},
        )

    task_id = create_resp.get("id") if isinstance(create_resp, dict) else None

    # 创建失败（无 task_id）：直接对创建响应跑校验，不进入轮询
    query_status, query_resp, polls = 0, {}, 0
    polled = False
    if create_status == 200 and task_id:
        try:
            query_status, query_resp, polls = poll_task(
                base_url, api_key, task_id,
                interval=config["poll_interval"],
                timeout_total=config["poll_timeout"],
                no_poll=no_poll,
            )
            polled = not no_poll
        except Exception as exc:  # noqa: BLE001
            elapsed = int((time.monotonic() - start) * 1000)
            return CaseResult(
                id=cid, name=name, status="error", error=f"查询请求异常：{exc!r}",
                duration_ms=elapsed,
                details={**base_details, "task_id": task_id,
                         "create_response": truncate(create_resp), "checks": checks},
            )

    elapsed = int((time.monotonic() - start) * 1000)

    verdict, error, expected, actual = run_checks(
        checks, schemas,
        create_status=create_status, create_resp=create_resp,
        query_status=query_status, query_resp=query_resp, polled=polled,
    )

    return CaseResult(
        id=cid, name=name, status=verdict, error=error or None,
        expected=expected, actual=actual, duration_ms=elapsed,
        details={
            **base_details,
            "task_id": task_id,
            "polls": polls,
            "task_status": query_resp.get("status") if isinstance(query_resp, dict) else None,
            "usage": query_resp.get("usage") if isinstance(query_resp, dict) else None,
            "checks": checks,
            # 完整记录请求与响应，便于失败时定位（长字符串已截断）
            "create_url": create_url,
            "create_body": truncate(create_body),
            "create_response": truncate(create_resp),
            "query_response": truncate(query_resp),
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="运行 Seedance 视频生成测试用例")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="跳过真实请求，仅自测请求体构造与 schema 加载（无需 API Key）",
    )
    parser.add_argument(
        "--no-poll", action="store_true",
        help="仅创建 + 单次查询，不等待终态（快速冒烟）",
    )
    parser.add_argument(
        "--out", default=str(HERE / "reports"),
        help="报告输出目录，默认 ./reports",
    )
    parser.add_argument(
        "--model", default=os.environ.get("SEEDANCE_MODEL", DEFAULT_MODEL),
        help=f"被测模型 id，默认取环境变量 SEEDANCE_MODEL 或 {DEFAULT_MODEL}",
    )
    args = parser.parse_args()

    schemas = load_schemas()
    config, cases = load_cases()
    if not cases:
        print("error: cases.yaml 中没有用例", file=sys.stderr)
        return 1

    base_url = os.environ.get("API_BASE_URL", "")
    model = args.model
    api_key = os.environ.get("API_KEY", "")

    if not args.dry_run:
        # 真实请求时，接口地址与密钥都必须提供
        missing = [n for n, v in (("API_BASE_URL", base_url), ("API_KEY", api_key)) if not v]
        if missing:
            print(f"error: 未设置 {' / '.join(missing)}；如需本地自测可加 --dry-run",
                  file=sys.stderr)
            return 1

    def work(case):
        return run_case(case, schemas=schemas, config=config, model=model,
                        base_url=base_url, api_key=api_key,
                        dry_run=args.dry_run, no_poll=args.no_poll)

    if args.dry_run:
        results = [work(c) for c in cases]
    else:
        # 一次性并发全部 case；executor.map 按输入顺序返回，报告顺序与 cases.yaml 一致
        with ThreadPoolExecutor(max_workers=len(cases)) as pool:
            results = list(pool.map(work, cases))

    report = Report(model=model, cases=results)
    paths = report.write(args.out)
    s = report.summary()
    verdict = "PASS" if report.passed else "FAIL"
    print(f"{model}: {verdict}  total={s['total']} pass={s['passed']} "
          f"fail={s['failed']} error={s['errored']} ({s['duration_ms']}ms)")
    print("报告已写入：" + "、".join(str(p) for p in paths.values()))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
