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
  python run_tests.py --profile seedance-2.0
  python run_tests.py --profile seedance-2.5 --model ep-custom
  python run_tests.py --profile seedance-2.0-mini --no-poll
  python run_tests.py --profile seedance-2.5 --dry-run

--model 只决定请求体中的模型标识，可传 Model ID、Endpoint ID 或自定义别名；
必填的 --profile 独立声明其能力，用于选择合法用例和请求参数。
"""

from __future__ import annotations

import argparse
import json
import os
import re
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
from report import CaseResult, Report, mask_secret  # noqa: E402
from profiles import (  # noqa: E402
    SeedanceProfile,
    apply_profile_overrides,
    load_profiles,
    unmet_requirement,
)

# 默认配置（base_url 无默认，必须通过 API_BASE_URL 指定）
DEFAULT_MODEL = "doubao-seedance-2-0-260128"

# 视频生成任务的接口路径（与火山方舟一致）
CREATE_PATH = "/contents/generations/tasks"

# 任务终态：轮询到这些状态即停止
TERMINAL_STATUSES = {"succeeded", "failed", "expired", "cancelled"}

# 报告中单个字符串字段保留的最大长度（base64 等会被截断）
MAX_STR_LEN = 500

# 火山方舟任务 ID 格式：cgt- 前缀 + 14 位时间戳 + - + 短随机串，如 cgt-20260420145835-68j7n
VOLC_TASK_ID_RE = re.compile(r"^cgt-\d{14}-[a-z0-9]+$")


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


def _validate_reference_limits(content: list[dict], profile: SeedanceProfile) -> None:
    """在发请求前校验参考素材数量没有超过 profile 上限。"""
    image_count = sum(1 for item in content if item.get("role") == "reference_image")
    video_count = sum(1 for item in content if item.get("role") == "reference_video")
    audio_count = sum(1 for item in content if item.get("role") == "reference_audio")
    limits = (
        ("参考图片", image_count, profile.max_reference_images),
        ("参考视频", video_count, profile.max_reference_videos),
        ("参考音频", audio_count, profile.max_reference_audios),
    )
    for label, actual, limit in limits:
        if actual > limit:
            raise ValueError(f"{label}数量 {actual} 超过 profile 上限 {limit}")
    total = image_count + video_count + audio_count
    if total > profile.max_total_reference_assets:
        raise ValueError(
            f"参考素材总数 {total} 超过 profile 上限 {profile.max_total_reference_assets}"
        )


def build_content(
    scenario: str,
    cfg: dict,
    case: dict | None,
    profile: SeedanceProfile,
) -> list[dict]:
    """按场景拼 content[] 数组（type/role 符合火山方舟格式）。

    prompt / first_frame_url / last_frame_url 支持 case 级覆盖：case 显式声明时
    优先于全局配置（与 resolution/ratio/duration 的 case 优先模式一致），
    便于负向用例指定特定素材（如真人图片）而不影响其他用例。
    """
    case = case or {}

    def pick(key: str) -> str:
        return case.get(key, cfg.get(key, ""))

    prompt = pick("prompt")
    text_item = {"type": "text", "text": prompt}

    if scenario == "text_to_video":
        content = [text_item]

    elif scenario == "image_to_video":
        content = [
            text_item,
            {"type": "image_url", "image_url": {"url": pick("first_frame_url")}, "role": "first_frame"},
        ]

    elif scenario == "reference_to_video":
        # 参考图生视频：content=[text, reference_image, ...]。
        # case 声明 reference_image_urls（列表）时按多图参考展开，每个 URL 一个
        # role=reference_image 项（Seedance 2.0 多图参考用法）；未声明时回退到
        # 单个 reference_image_url，保持向后兼容。
        ref_urls = case.get("reference_image_urls")
        if not ref_urls:
            ref_urls = [pick("reference_image_url")]
        content = [
            text_item,
            *[
                {"type": "image_url", "image_url": {"url": url}, "role": "reference_image"}
                for url in ref_urls
            ],
        ]

    elif scenario == "start_end_to_video":
        content = [
            text_item,
            {"type": "image_url", "image_url": {"url": pick("first_frame_url")}, "role": "first_frame"},
            {"type": "image_url", "image_url": {"url": pick("last_frame_url")}, "role": "last_frame"},
        ]

    elif scenario == "multimodal_reference":
        content = [
            text_item,
            {"type": "image_url", "image_url": {"url": pick("reference_image_url")}, "role": "reference_image"},
            {
                "type": "video_url",
                "video_url": {"url": pick("reference_video_url")},
                "role": "reference_video",
            },
            {
                "type": "audio_url",
                "audio_url": {"url": pick("reference_audio_url")},
                "role": "reference_audio",
            },
        ]

    elif scenario == "audio_only_reference":
        content = [
            text_item,
            {"type": "audio_url", "audio_url": {"url": pick("reference_audio_url")}, "role": "reference_audio"},
        ]

    elif scenario in {"video_edit", "video_extend"}:
        content = [
            text_item,
            {"type": "video_url", "video_url": {"url": pick("reference_video_url")}, "role": "reference_video"},
        ]

    elif scenario == "reference_images_profile_max":
        url = pick("reference_image_url")
        content = [
            text_item,
            *[
                {"type": "image_url", "image_url": {"url": url}, "role": "reference_image"}
                for _ in range(profile.max_reference_images)
            ],
            {
                "type": "video_url",
                "video_url": {"url": pick("reference_video_url")},
                "role": "reference_video",
            },
            {
                "type": "audio_url",
                "audio_url": {"url": pick("reference_audio_url")},
                "role": "reference_audio",
            },
        ]

    elif scenario == "multimodal_reference_6_videos":
        video_urls = case.get("reference_video_urls") or []
        content = [
            text_item,
            {"type": "image_url", "image_url": {"url": pick("reference_image_url")}, "role": "reference_image"},
            *[
                {"type": "video_url", "video_url": {"url": url}, "role": "reference_video"}
                for url in video_urls
            ],
        ]

    else:
        raise ValueError(f"未知 scenario：{scenario}")

    _validate_reference_limits(content, profile)
    return content


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
    for key in (
        "seed",
        "camera_fixed",
        "watermark",
        "generate_audio",
        "return_last_frame",
        "output_format",
    ):
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


def parse_response(raw: str, content_type: str) -> dict:
    """把原始响应体解析为 dict；非 JSON（如返回 HTML）时不抛异常，
    而是返回一个标记 dict，原样保留原始 body 与 content-type，便于报告定位。
    """
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {
            "_non_json_response": True,
            "_content_type": content_type,
            "_parse_error": str(exc),
            "_raw_body": raw,
        }
    # 顶层不是对象（如返回裸数组/字符串）时也包一层，保证下游 .get 安全
    if not isinstance(parsed, dict):
        return {"_non_object_response": True, "_content_type": content_type, "_raw_body": parsed}
    return parsed


def send_request(url: str, api_key: str, method: str, body: dict | None,
                 timeout: int) -> tuple[int, dict]:
    """发送请求并解析响应，返回 (状态码, 响应 dict)。

    无论成功响应还是 HTTP 错误，都先读出原始 body 再解析；非 JSON 响应
    （如网关返回 HTML 首页）不抛异常，而是返回带原始内容的标记 dict，
    使原始响应能完整记录到报告中。
    """
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
            raw = resp.read().decode("utf-8", errors="replace")
            ctype = resp.headers.get("Content-Type", "")
            return resp.status, parse_response(raw, ctype)
    except urllib.error.HTTPError as exc:
        # HTTP 错误也读出 body，便于报告里展示错误详情
        raw = exc.read().decode("utf-8", errors="replace")
        ctype = exc.headers.get("Content-Type", "") if exc.headers else ""
        return exc.code, parse_response(raw, ctype)


def poll_task(base_url: str, api_key: str, task_id: str, *, interval: int,
              timeout_total: int, no_poll: bool) -> tuple[int, dict, int, dict]:
    """轮询查询任务状态，返回 (最后一次 HTTP 状态, 最后一次响应, 轮询次数, 首次响应)。

    no_poll=True 时只查询一次即返回；否则轮询直到终态或总超时。
    使用 time.monotonic 计时，不依赖 time.time/random。

    首次响应单独返回：创建后的第一次查询天然处于进行中态（queued/running），
    status_queued_or_running 据此校验，无需为「进行中态」单独建一个任务。
    """
    url = build_query_url(base_url, task_id)
    polls = 0
    deadline = time.monotonic() + timeout_total
    last_status, last_resp = 0, {}
    first_resp: dict = {}
    while True:
        status, resp = send_request(url, api_key, "GET", None, timeout=60)
        polls += 1
        last_status, last_resp = status, resp
        if polls == 1:
            first_resp = resp
        if no_poll:
            break
        task_status = resp.get("status") if isinstance(resp, dict) else None
        if status != 200 or task_status in TERMINAL_STATUSES:
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(interval)
    return last_status, last_resp, polls, first_resp


# ==================== 校验 ====================


def is_quicktime_mov_header(data: bytes) -> bool:
    """文件头是否包含 major brand 为 ``qt  `` 的 ISO BMFF ftyp box。"""
    offset = data.find(b"ftyp")
    return offset >= 0 and data[offset + 4:offset + 8] == b"qt  "


def probe_mov_url(url: str, timeout: int = 60) -> tuple[bool, dict[str, object]]:
    """只读取视频 URL 的前 256 字节并判断是否为 QuickTime MOV 容器。"""
    request = urllib.request.Request(url, headers={"Range": "bytes=0-255"})
    metadata: dict[str, object] = {"url": url}
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            prefix = response.read(256)
            metadata.update({
                "content_type": response.headers.get("Content-Type", ""),
                "bytes_read": len(prefix),
                "prefix_hex": prefix[:32].hex(),
            })
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        metadata["error"] = repr(exc)
        return False, metadata
    return is_quicktime_mov_header(prefix), metadata


def run_checks(checks: list[str], schemas: dict, *, create_status: int,
               create_resp: dict, query_status: int, query_resp: dict,
               polled: bool, first_query_resp: dict | None = None,
               create_body: dict | None = None,
               expected_error_code: str | None = None) -> tuple[str, str, object, object]:
    """执行校验项，返回 (status, error, expected, actual)。

    任一 check 不通过即 fail，error 记录首个失败原因。
    expected/actual 反映任务最终状态，便于报告直观展示。
    """
    create_body = create_body or {}
    task_status = query_resp.get("status") if isinstance(query_resp, dict) else None
    # 进行中态取首次查询响应：轮询到终态的用例也能顺带校验 queued/running
    first_resp = first_query_resp if first_query_resp is not None else query_resp
    first_status = first_resp.get("status") if isinstance(first_resp, dict) else None
    if "reached_succeeded" in checks:
        expected_display = "succeeded"
        actual_display = task_status
    else:
        expected_display = None
        actual_display = None

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

        elif check == "error_code_matches":
            # 负向用例：断言 error.code 精确等于 case 声明的 expected_error_code。
            # 用于校验被测服务透传了火山方舟的特定错误码（如真人图片隐私检测）。
            if not expected_error_code:
                return "fail", "error_code_matches 需在 case 中声明 expected_error_code", None, None
            actual_code = get_path(create_resp, "error.code")
            if actual_code != expected_error_code:
                return "fail", f"error.code 期望 {expected_error_code}，实际 {actual_code}", expected_error_code, actual_code

        elif check == "query_status_200":
            if query_status != 200:
                return "fail", f"查询任务 status 期望 200，实际 {query_status}", 200, query_status

        elif check == "query_schema":
            err = validate_schema(schemas["query"], query_resp)
            if err:
                return "fail", f"查询响应不符合 schema：{err}", None, None

        elif check == "status_queued_or_running":
            # 判首次查询响应而非最终响应：轮询到终态的用例也能校验进行中态，
            # 无需为此单独创建一个任务。
            if first_status not in {"queued", "running"}:
                return (
                    "fail",
                    f"首次查询 status 期望 queued 或 running，实际 {first_status}",
                    "queued|running",
                    first_status,
                )

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

        elif check == "succeeded_has_last_frame":
            # 开启 return_last_frame:true 后，成功态响应应在 content.last_frame_url 返回尾帧图 URL。
            # 是否返回取决于被测实现是否透传该参数：若成功响应仅含 video_url 而缺 last_frame_url，
            # 该 check 失败，用于暴露不支持 return_last_frame 透传的实现。
            last_frame = get_path(query_resp, "content.last_frame_url")
            if not last_frame:
                return ("fail",
                        "成功响应缺 content.last_frame_url（被测实现未透传 return_last_frame 或丢弃了尾帧字段）",
                        "content.last_frame_url", last_frame)

        elif check == "query_resolution_matches_request":
            expected_resolution = create_body.get("resolution")
            actual_resolution = query_resp.get("resolution")
            if actual_resolution != expected_resolution:
                return (
                    "fail",
                    f"查询响应 resolution 期望 {expected_resolution}，实际 {actual_resolution}",
                    expected_resolution,
                    actual_resolution,
                )

        elif check == "query_duration_matches_request":
            expected_duration = create_body.get("duration")
            actual_duration = query_resp.get("duration")
            if actual_duration != expected_duration:
                return (
                    "fail",
                    f"查询响应 duration 期望 {expected_duration}，实际 {actual_duration}",
                    expected_duration,
                    actual_duration,
                )

        elif check == "succeeded_video_format_matches_request":
            expected_format = create_body.get("output_format")
            video_url = get_path(query_resp, "content.video_url")
            if expected_format != "mov":
                return (
                    "fail",
                    f"当前仅支持校验 output_format=mov，实际 {expected_format}",
                    "mov",
                    expected_format,
                )
            if not video_url:
                return (
                    "fail",
                    "成功响应缺 content.video_url，无法验证 MOV 容器",
                    "content.video_url",
                    video_url,
                )
            matched, metadata = probe_mov_url(video_url)
            if not matched:
                return (
                    "fail",
                    f"生成视频不是 QuickTime MOV 容器或文件头读取失败：{metadata}",
                    "ftyp major brand qt  ",
                    metadata,
                )

        else:
            return "fail", f"未知 check：{check}", None, None

    return "pass", "", expected_display, actual_display


def run_warn_checks(warn_checks: list[str], *, create_resp: dict, query_resp: dict) -> list[str]:
    """执行软校验（警告级），返回警告消息列表（每条不满足项一条）。

    与 run_checks 不同：软校验不影响 case 的 pass/fail，仅在响应不符合「火山原生格式」
    时累积一条警告用于提示。值缺失（如任务未成功、无 video_url）时跳过对应软校验，
    交由硬 schema check 处理，避免重复报噪音。
    """
    warnings: list[str] = []
    for check in warn_checks:
        if check == "id_volc_format":
            # 任务 ID 应为火山方舟格式（cgt-14位时间戳-随机串）；创建响应与查询响应的 id 都检查。
            # 用于提示被测实现自行生成了非火山格式 ID（如 UUID）。
            create_id = create_resp.get("id") if isinstance(create_resp, dict) else None
            query_id = query_resp.get("id") if isinstance(query_resp, dict) else None
            for where, task_id in (("创建响应", create_id), ("查询响应", query_id)):
                if task_id is not None and not (isinstance(task_id, str) and VOLC_TASK_ID_RE.fullmatch(task_id)):
                    warnings.append(
                        f"{where} id 非火山格式（期望形如 cgt-20260420145835-68j7n），实际 {task_id!r}"
                    )

        elif check == "video_url_is_volc":
            # 成功态生成视频链接应为火山域名（host 以 volces.com 结尾）；
            # 用于提示被测实现把视频转存到自有 CDN、未透传火山原始链接。
            video_url = get_path(query_resp, "content.video_url")
            if isinstance(video_url, str) and video_url:
                host = urllib.parse.urlparse(video_url).hostname
                if not (host and (host == "volces.com" or host.endswith(".volces.com"))):
                    warnings.append(
                        f"content.video_url 非火山链接（host 期望以 volces.com 结尾），实际 {video_url!r}"
                    )

        else:
            warnings.append(f"未知 warn_check：{check}")
    return warnings


# ==================== 单个 case 执行 ====================


def run_case(case: dict, *, schemas: dict, config: dict, profile: SeedanceProfile,
             model: str, base_url: str, api_key: str, dry_run: bool,
             no_poll: bool) -> CaseResult:
    """执行单个 case，返回 CaseResult。无共享可变状态，可安全并发调用。"""
    cid = case["id"]
    name = case.get("name", cid)
    scenario = case.get("scenario", "text_to_video")
    case_model = case.get("model", model)

    base_details = {
        "scenario": scenario,
        "model": case_model,
        "profile": profile.name,
    }
    if case.get("poll"):
        base_details["poll"] = case["poll"]

    try:
        reason = unmet_requirement(case.get("requires", {}), profile)
    except Exception as exc:  # noqa: BLE001
        return CaseResult(
            id=cid, name=name, status="error",
            error=f"解析 profile 要求失败：{exc!r}", details=base_details,
        )
    if reason:
        return CaseResult(
            id=cid, name=name, status="skipped", duration_ms=0,
            details={**base_details, "skip_reason": reason},
        )

    try:
        effective_case = apply_profile_overrides(case, profile)
    except Exception as exc:  # noqa: BLE001
        return CaseResult(
            id=cid, name=name, status="error",
            error=f"应用 profile 覆盖失败：{exc!r}", details=base_details,
        )

    checks = effective_case.get("checks", [])
    warn_checks = effective_case.get("warn_checks", [])
    case_no_poll = no_poll or effective_case.get("poll") == "once"

    # 构造创建请求体
    try:
        content = build_content(scenario, config, effective_case, profile)
        create_body = build_create_body(
            case_model, content, config, effective_case
        )
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
                "warn_checks": warn_checks,
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
    query_status, query_resp, polls, first_query_resp = 0, {}, 0, {}
    polled = False
    if create_status == 200 and task_id:
        try:
            query_status, query_resp, polls, first_query_resp = poll_task(
                base_url, api_key, task_id,
                interval=config["poll_interval"],
                timeout_total=config["poll_timeout"],
                no_poll=case_no_poll,
            )
            polled = not case_no_poll
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
        first_query_resp=first_query_resp,
        create_body=create_body,
        expected_error_code=effective_case.get("expected_error_code"),
    )

    # 软校验：火山原生格式提示，不影响 verdict，仅累积为警告
    warnings = run_warn_checks(warn_checks, create_resp=create_resp, query_resp=query_resp)

    return CaseResult(
        id=cid, name=name, status=verdict, error=error or None,
        expected=expected, actual=actual, duration_ms=elapsed,
        warnings=warnings,
        details={
            **base_details,
            "task_id": task_id,
            "polls": polls,
            "task_status": query_resp.get("status") if isinstance(query_resp, dict) else None,
            "first_task_status": first_query_resp.get("status") if isinstance(first_query_resp, dict) else None,
            "usage": query_resp.get("usage") if isinstance(query_resp, dict) else None,
            "checks": checks,
            "warn_checks": warn_checks,
            # 完整记录请求与响应，便于失败时定位（长字符串已截断）
            "create_url": create_url,
            "create_body": truncate(create_body),
            "create_response": truncate(create_resp),
            "query_response": truncate(query_resp),
        },
    )


def main() -> int:
    profiles = load_profiles()
    parser = argparse.ArgumentParser(description="运行 Seedance 视频生成测试用例")
    parser.add_argument(
        "--profile", required=True, choices=sorted(profiles),
        help="模型能力档案；与 --model 独立，用于选择合法测试用例",
    )
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
    profile = profiles[args.profile]
    api_key = os.environ.get("API_KEY", "")

    if not args.dry_run:
        # 真实请求时，接口地址与密钥都必须提供
        missing = [n for n, v in (("API_BASE_URL", base_url), ("API_KEY", api_key)) if not v]
        if missing:
            print(f"error: 未设置 {' / '.join(missing)}；如需本地自测可加 --dry-run",
                  file=sys.stderr)
            return 1

    def work(case):
        return run_case(case, schemas=schemas, config=config, profile=profile,
                        model=model,
                        base_url=base_url, api_key=api_key,
                        dry_run=args.dry_run, no_poll=args.no_poll)

    if args.dry_run:
        results = [work(c) for c in cases]
    else:
        # 一次性并发全部 case；executor.map 按输入顺序返回，报告顺序与 cases.yaml 一致
        with ThreadPoolExecutor(max_workers=len(cases)) as pool:
            results = list(pool.map(work, cases))

    # 记录本次运行的环境变量到报告（密钥脱敏，便于复现与排查）
    env = {
        "API_BASE_URL": base_url,
        "API_KEY": mask_secret(api_key),
        "SEEDANCE_MODEL": model,
        "SEEDANCE_PROFILE": profile.name,
    }
    report = Report(model=model, cases=results, env=env)
    paths = report.write(args.out)
    s = report.summary()
    verdict = "PASS" if report.passed else "FAIL"
    print(f"{model}: {verdict}  total={s['total']} pass={s['passed']} "
          f"fail={s['failed']} error={s['errored']} skip={s['skipped']} "
          f"warn={s['warned']} ({s['duration_ms']}ms)")
    print("报告已写入：" + "、".join(str(p) for p in paths.values()))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
