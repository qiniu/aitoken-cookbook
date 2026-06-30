#!/usr/bin/env python3
"""gpt-image-2 兼容性测试执行入口。

把接口地址指向被测的 gpt-image-2 兼容服务，运行本脚本即可测试该接口，
并在 reports/ 下生成报告。

读取 cases.yaml，逐个 case 调用图片生成接口，按声明的 checks 校验响应，
最终复用 _shared/report.py 产出 json / md / html 三份报告到 reports/ 目录。

output_tokens 的预期值不写死：按 quality + size 调用
_shared/gpt_image_2_token_calculator.py 按 gpt-image-2 官方算法动态算出，
这是被测接口需对齐的计费契约。

环境变量：
  API_BASE_URL    必填，被测接口的基础地址，如 https://your-domain.com/v1
  API_KEY         必填，被测接口的鉴权密钥
  GPT_IMAGE_MODEL 选填，默认 gpt-image-2

所有 case 默认并发请求（生图较慢，串行会很耗时）。

用法：
  python run_tests.py            # 并发请求接口
  python run_tests.py --dry-run  # 跳过真实请求，仅自测预期值计算链路
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHARED = HERE.parent / "_shared"

# 复用公共报告模块
sys.path.insert(0, str(SHARED))
from report import CaseResult, Report, mask_secret  # noqa: E402

# 默认配置（base_url 无默认，必须通过 API_BASE_URL 指定）
DEFAULT_MODEL = "gpt-image-2"
# 各 endpoint 对应的接口路径
ENDPOINT_PATHS = {
    "generations": "/v1/images/generations",
    "edits": "/v1/images/edits",
}


def load_calculator():
    """以文件路径方式加载计算器模块（文件名含连字符，不能直接 import）。"""
    path = SHARED / "gpt_image_2_token_calculator.py"
    spec = importlib.util.spec_from_file_location("gpt_image_2_token_calculator", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_cases() -> tuple[dict, list[dict]]:
    """读取 cases.yaml，返回（全局配置 dict, 用例列表）。

    全局配置含 prompt（generations 提示词）、edit_prompt（edits 编辑指令）、
    edit_image（edits 默认输入图片路径）。
    """
    try:
        import yaml
    except ImportError:
        print("error: 缺少依赖 pyyaml，请执行 pip install pyyaml", file=sys.stderr)
        raise SystemExit(1)

    data = yaml.safe_load((HERE / "cases.yaml").read_text(encoding="utf-8"))
    config = {
        "prompt": data.get("prompt", ""),
        "edit_prompt": data.get("edit_prompt", ""),
        "edit_image": data.get("edit_image", ""),
    }
    return config, data.get("cases", [])


def parse_size(size: str) -> tuple[int, int]:
    """把 "1024x1024" 解析为 (width, height)。"""
    width, height = size.lower().split("x", 1)
    return int(width), int(height)


def get_path(obj, path: str):
    """按点号路径取嵌套字段，缺失返回 None。"""
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


# 报告中单个字符串字段保留的最大长度（base64 图片等会被截断）
MAX_STR_LEN = 500


def truncate(value, max_len: int = MAX_STR_LEN):
    """递归截断过长字符串，避免 base64 图片等把报告撑爆。"""
    if isinstance(value, str):
        if len(value) > max_len:
            return value[:max_len] + f"...(已截断，共 {len(value)} 字符)"
        return value
    if isinstance(value, list):
        return [truncate(v, max_len) for v in value]
    if isinstance(value, dict):
        return {k: truncate(v, max_len) for k, v in value.items()}
    return value


def build_url(base_url: str, endpoint: str) -> str:
    """由 base_url + endpoint 拼出完整接口地址（兼容是否带 /v1 与尾斜杠）。"""
    return base_url.rstrip("/").removesuffix("/v1") + ENDPOINT_PATHS[endpoint]


def build_gen_body(model: str, prompt: str, quality: str, size: str) -> dict:
    """构造文生图（generations）的 JSON 请求体。"""
    return {
        "model": model,
        "prompt": prompt,
        "quality": quality,
        "size": size,
        "n": 1,
    }


def build_edit_fields(model: str, prompt: str, quality: str, size: str) -> dict:
    """构造图生图（edits）的表单字段（不含图片本身）。"""
    return {
        "model": model,
        "prompt": prompt,
        "quality": quality,
        "size": size,
        "n": "1",
    }


def encode_multipart(fields: dict, image_path: Path) -> tuple[bytes, str]:
    """把表单字段与图片编码为 multipart/form-data，返回 (body, content_type)。"""
    # 固定 boundary（无需随机；脚本不依赖 Date/random）
    boundary = "----gptimage2testboundary7MA4YWxkTrZu0gW"
    crlf = b"\r\n"
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(b"--" + boundary.encode())
        parts.append(f'Content-Disposition: form-data; name="{name}"'.encode())
        parts.append(b"")
        parts.append(str(value).encode("utf-8"))
    # 图片字段（OpenAI 图片编辑约定字段名为 image）
    image_bytes = image_path.read_bytes()
    parts.append(b"--" + boundary.encode())
    parts.append(
        f'Content-Disposition: form-data; name="image"; filename="{image_path.name}"'.encode()
    )
    parts.append(b"Content-Type: image/png")
    parts.append(b"")
    parts.append(image_bytes)
    parts.append(b"--" + boundary.encode() + b"--")
    parts.append(b"")
    body = crlf.join(parts)
    return body, f"multipart/form-data; boundary={boundary}"


def send_request(url: str, api_key: str, data: bytes, content_type: str,
                 timeout: int) -> tuple[int, dict]:
    """发送 POST 请求并解析 JSON 响应，返回 (状态码, 响应 JSON)。"""
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": content_type,
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
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


def run_checks(checks: list[str], status: int, resp: dict,
               req: dict, expected_tokens: int) -> tuple[str, str, object, object]:
    """执行校验项，返回 (status, error, expected, actual)。

    status: pass / fail；任一 check 不通过即 fail，error 记录首个失败原因。
    expected / actual 优先反映 output_tokens 校验，便于报告直观展示。
    """
    actual_tokens = get_path(resp, "usage.output_tokens")
    expected_display = None
    actual_display = None

    for check in checks:
        if check == "status_200":
            if status != 200:
                return "fail", f"status_code 期望 200，实际 {status}", 200, status

        elif check == "data_exists":
            data = resp.get("data")
            if not data:
                return "fail", "data 字段缺失或为空", None, None

        elif check == "usage_exists":
            if get_path(resp, "usage.input_tokens") is None:
                return "fail", "usage.input_tokens 缺失", None, None
            if get_path(resp, "usage.output_tokens") is None:
                return "fail", "usage.output_tokens 缺失", None, None

        elif check == "output_tokens_typed":
            if not isinstance(actual_tokens, (int, float)) or isinstance(actual_tokens, bool):
                return "fail", f"usage.output_tokens 不是数字：{actual_tokens!r}", "number", type(actual_tokens).__name__

        elif check == "output_tokens_exact":
            expected_display, actual_display = expected_tokens, actual_tokens
            if actual_tokens != expected_tokens:
                return "fail", f"output_tokens 期望 {expected_tokens}，实际 {actual_tokens}", expected_tokens, actual_tokens

        elif check == "size_echo":
            actual_size = resp.get("size")
            if actual_size != req["size"]:
                return "fail", f"size 期望 {req['size']}，实际 {actual_size}", req["size"], actual_size

        elif check == "quality_echo":
            actual_quality = resp.get("quality")
            if actual_quality != req["quality"]:
                return "fail", f"quality 期望 {req['quality']}，实际 {actual_quality}", req["quality"], actual_quality

        else:
            return "fail", f"未知 check：{check}", None, None

    return "pass", "", expected_display, actual_display


def run_case(case: dict, *, calc, config: dict, model: str, base_url: str,
             api_key: str, dry_run: bool) -> CaseResult:
    """执行单个 case，返回 CaseResult。无共享可变状态，可安全并发调用。"""
    cid = case["id"]
    name = case.get("name", cid)
    quality = case["quality"]
    size = case["size"]
    checks = case.get("checks", [])
    endpoint = case.get("endpoint", "generations")

    base_details = {"endpoint": endpoint, "quality": quality, "size": size}

    if endpoint not in ENDPOINT_PATHS:
        return CaseResult(
            id=cid, name=name, status="error",
            error=f"未知 endpoint：{endpoint}", details=base_details,
        )

    # 按 quality + size 动态算出预期 output_tokens
    width, height = parse_size(size)
    size_errors = calc.validate_size(width, height)
    if size_errors:
        return CaseResult(
            id=cid, name=name, status="error",
            error="尺寸非法：" + "; ".join(size_errors),
            details=base_details,
        )
    expected_tokens = calc.calculate_output_tokens(width, height, quality)

    req = {"quality": quality, "size": size}
    api_url = build_url(base_url, endpoint) if base_url else ""

    if dry_run:
        # 干跑：不打接口，只确认预期值算得出来
        return CaseResult(
            id=cid, name=name, status="pass",
            expected=expected_tokens, actual=None, duration_ms=0,
            details={
                **base_details,
                "dry_run": True,
                "expected_output_tokens": expected_tokens,
                "checks": checks,
            },
        )

    # 按 endpoint 构造请求（generations 用 JSON，edits 用 multipart）
    timeout = int(case.get("timeout", 300))
    try:
        if endpoint == "generations":
            sent_body = build_gen_body(model, config["prompt"], quality, size)
            data = json.dumps(sent_body).encode("utf-8")
            content_type = "application/json"
            request_detail = {"url": api_url, "body": sent_body}
        else:  # edits
            fields = build_edit_fields(model, config["edit_prompt"], quality, size)
            image_path = (HERE / case.get("image", config["edit_image"])).resolve()
            if not image_path.is_file():
                return CaseResult(
                    id=cid, name=name, status="error",
                    error=f"输入图片不存在：{image_path}", details=base_details,
                )
            data, content_type = encode_multipart(fields, image_path)
            request_detail = {
                "url": api_url,
                "fields": fields,
                "image": str(image_path.relative_to(HERE)),
            }
    except Exception as exc:  # noqa: BLE001
        return CaseResult(
            id=cid, name=name, status="error", error=f"构造请求失败：{exc!r}",
            details=base_details,
        )

    start = time.monotonic()
    try:
        status, resp = send_request(api_url, api_key, data, content_type, timeout)
    except Exception as exc:  # noqa: BLE001
        elapsed = int((time.monotonic() - start) * 1000)
        return CaseResult(
            id=cid, name=name, status="error", error=f"请求异常：{exc!r}",
            duration_ms=elapsed,
            details={
                **base_details,
                "expected_output_tokens": expected_tokens,
                "request": request_detail,
                "checks": checks,
            },
        )
    elapsed = int((time.monotonic() - start) * 1000)

    verdict, error, expected, actual = run_checks(checks, status, resp, req, expected_tokens)
    return CaseResult(
        id=cid, name=name, status=verdict, error=error or None,
        expected=expected, actual=actual, duration_ms=elapsed,
        details={
            **base_details,
            "http_status": status,
            "expected_output_tokens": expected_tokens,
            "usage": resp.get("usage"),
            "checks": checks,
            # 完整记录请求与响应，便于失败时定位（长字符串如 base64 图片已截断）
            "request": request_detail,
            "response": truncate(resp),
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="运行 gpt-image-2 测试用例")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="跳过真实请求，仅自测预期值计算链路（无需 API Key）",
    )
    parser.add_argument(
        "--out",
        default=str(HERE / "reports"),
        help="报告输出目录，默认 ./reports",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("GPT_IMAGE_MODEL", DEFAULT_MODEL),
        help=f"被测模型 id，默认取环境变量 GPT_IMAGE_MODEL 或 {DEFAULT_MODEL}",
    )
    args = parser.parse_args()

    calc = load_calculator()
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

    # 默认并发请求所有 case；dry-run 无 I/O，直接串行即可
    def work(case):
        return run_case(case, calc=calc, config=config, model=model,
                        base_url=base_url, api_key=api_key, dry_run=args.dry_run)

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
        "GPT_IMAGE_MODEL": model,
    }
    report = Report(model=model, cases=results, env=env)
    paths = report.write(args.out)
    s = report.summary()
    verdict = "PASS" if report.passed else "FAIL"
    print(f"{model}: {verdict}  total={s['total']} pass={s['passed']} "
          f"fail={s['failed']} error={s['errored']} ({s['duration_ms']}ms)")
    print("报告已写入：" + "、".join(str(p) for p in paths.values()))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
