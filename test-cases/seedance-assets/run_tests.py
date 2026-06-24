#!/usr/bin/env python3
"""Seedance 素材资产（Assets）API 兼容性测试执行入口（火山方舟兼容格式）。

把接口地址指向被测的素材资产兼容服务，运行本脚本即可测试该接口，
并在 reports/ 下生成报告。

校验目标：被测端点的 path、请求体、响应体、鉴权方式能完全兼容火山方舟
素材资产 API 格式。
  端点    POST {API_BASE_URL}/?Action=<Action>&Version=2024-01-01
  鉴权    AK/SK 火山 Signature V4 签名（见 volc_sign.py）
  响应    火山信封 {ResponseMetadata:{...,Error}, Result:{...}}

与视频生成套件不同，素材资产是「有依赖的生命周期链」，必须串行执行：
  建组 CreateAssetGroup → 上传 CreateAsset → 轮询 GetAsset 至 Active
  → 查询 ListAssets / ListAssetGroups → 真人会话 CreateVisualValidateSession。
后续 step 通过 ${group_id} / ${asset_id} 占位符引用前序 step 的输出。

环境变量：
  API_BASE_URL   必填，被测接口的基础地址，如 https://your-domain.com/api/v3
  ACCESS_KEY     必填，AK（火山签名格式的 Access Key）
  SECRET_KEY     必填，SK（火山签名格式的 Secret Key）
  PROJECT_NAME   选填，项目名；需与 AK/SK 有权限的项目一致。
                 优先级：环境变量 > cases.yaml 的 project_name > default。

用法：
  python run_tests.py            # 串行执行整条生命周期链
  python run_tests.py --dry-run  # 跳过真实请求，仅自测请求体构造、占位符替换与 schema 加载
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
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHARED = HERE.parent / "_shared"
SCHEMA_DIR = HERE / "schemas"

# 复用公共报告模块与同目录签名模块
sys.path.insert(0, str(SHARED))
sys.path.insert(0, str(HERE))
from report import CaseResult, Report  # noqa: E402
import volc_sign  # noqa: E402

# 固定 API 版本（拼到 ?Version=）
API_VERSION = "2024-01-01"

# 报告中单个字符串字段保留的最大长度（带签名的 URL 等会被截断）
MAX_STR_LEN = 500


def truncate(value, max_len: int = MAX_STR_LEN):
    """递归截断过长字符串，避免带签名 URL 等把报告撑爆。"""
    if isinstance(value, str):
        if len(value) > max_len:
            return value[:max_len] + f"...(已截断，共 {len(value)} 字符)"
        return value
    if isinstance(value, list):
        return [truncate(v, max_len) for v in value]
    if isinstance(value, dict):
        return {k: truncate(v, max_len) for k, v in value.items()}
    return value


# ==================== schema 加载与校验 ====================


def load_schemas() -> dict:
    """加载 schemas/ 下的所有 JSON Schema，编译为 jsonschema 校验器。

    返回 {schema 文件名: validator}，外加键 "envelope" / "error" 便于直接取用。
    各 step 的 result_schema 通过文件名取对应校验器。
    """
    try:
        import jsonschema
    except ImportError:
        print("error: 缺少依赖 jsonschema，请先运行 test-cases/setup.sh", file=sys.stderr)
        raise SystemExit(1)

    validators: dict = {}
    for path in SCHEMA_DIR.glob("*.schema.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        validators[path.name] = jsonschema.Draft202012Validator(schema)
    # 便捷别名
    validators["envelope"] = validators["envelope.schema.json"]
    validators["error"] = validators["error.schema.json"]
    return validators


def validate_schema(validator, instance) -> str | None:
    """用 validator 校验 instance，通过返回 None，否则返回首个错误的可读描述。"""
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))
    if not errors:
        return None
    err = errors[0]
    loc = "/".join(str(p) for p in err.absolute_path) or "(root)"
    return f"{loc}: {err.message}"


# ==================== 用例加载 ====================


def load_config_and_steps() -> tuple[dict, list[dict]]:
    """读取 cases.yaml，返回（全局配置 dict, 有序 step 列表）。"""
    try:
        import yaml
    except ImportError:
        print("error: 缺少依赖 pyyaml，请先运行 test-cases/setup.sh", file=sys.stderr)
        raise SystemExit(1)

    data = yaml.safe_load((HERE / "cases.yaml").read_text(encoding="utf-8"))
    config = {
        "asset_image_url": data.get("asset_image_url", ""),
        "project_name": data.get("project_name", "default"),
        "poll_interval": int(data.get("poll_interval", 5)),
        "poll_timeout": int(data.get("poll_timeout", 600)),
        "liveness_callback_url": data.get("liveness_callback_url", ""),
    }
    return config, data.get("steps", [])


def substitute(value, variables: dict):
    """递归替换 ${var} 占位符。

    若字符串整体就是单个 ${var}，则替换为变量原值（保留类型）；
    否则按文本插值（用于 URL 等含占位符的字符串）。
    """
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("${") and stripped.endswith("}") and stripped.count("${") == 1:
            key = stripped[2:-1]
            return variables.get(key, value)
        # 文本插值
        out = value
        for k, v in variables.items():
            out = out.replace("${" + k + "}", str(v))
        return out
    if isinstance(value, list):
        return [substitute(v, variables) for v in value]
    if isinstance(value, dict):
        return {k: substitute(v, variables) for k, v in value.items()}
    return value


# ==================== HTTP 请求（火山 Action 风格 + V4 签名）====================


def build_url(base_url: str, action: str) -> str:
    """拼接 Action 风格 URL：base_url 去尾斜杠 + /?Action=<action>&Version=<ver>。"""
    base = base_url.rstrip("/")
    return f"{base}/?Action={action}&Version={API_VERSION}"


def parse_response(raw: str, content_type: str) -> dict:
    """把原始响应体解析为 dict；非 JSON（如网关返回 HTML）时不抛异常，
    返回带原始 body 与 content-type 的标记 dict，便于报告定位。
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
    if not isinstance(parsed, dict):
        return {"_non_object_response": True, "_content_type": content_type, "_raw_body": parsed}
    return parsed


def send_signed_request(base_url: str, action: str, body: dict, *,
                        access_key: str, secret_key: str,
                        timeout: int) -> tuple[int, dict]:
    """对请求做火山 V4 签名后发送，返回 (HTTP 状态码, 响应 dict)。"""
    url = build_url(base_url, action)
    parsed = urllib.parse.urlparse(url)
    body_bytes = json.dumps(body).encode("utf-8")

    # 计算签名头（host/path/query 取自最终 URL）
    headers = volc_sign.sign_headers(
        access_key=access_key,
        secret_key=secret_key,
        method="POST",
        host=parsed.netloc,
        path=parsed.path,
        raw_query=parsed.query,
        body=body_bytes,
    )

    req = urllib.request.Request(url, data=body_bytes, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            ctype = resp.headers.get("Content-Type", "")
            return resp.status, parse_response(raw, ctype)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        ctype = exc.headers.get("Content-Type", "") if exc.headers else ""
        return exc.code, parse_response(raw, ctype)


# ==================== 校验 ====================


def get_result_status(resp: dict):
    """取 Result.Status（轮询用）；缺失返回 None。"""
    result = resp.get("Result") if isinstance(resp, dict) else None
    if isinstance(result, dict):
        return result.get("Status")
    return None


def has_metadata_error(resp: dict) -> bool:
    """判断 ResponseMetadata.Error 是否为非空错误对象。"""
    meta = resp.get("ResponseMetadata") if isinstance(resp, dict) else None
    if isinstance(meta, dict):
        err = meta.get("Error")
        return isinstance(err, dict) and bool(err.get("Code"))
    return False


def run_checks(checks: list[str], schemas: dict, *, status: int, resp: dict,
               result_schema_name: str | None) -> tuple[str, str]:
    """执行校验项，返回 (verdict, error)。任一不通过即 fail，error 记首个失败原因。"""
    for check in checks:
        if check == "http_2xx":
            if not (200 <= status < 300):
                return "fail", f"HTTP 期望 2xx，实际 {status}"

        elif check == "envelope":
            err = validate_schema(schemas["envelope"], resp)
            if err:
                return "fail", f"响应不符合信封 schema：{err}"

        elif check == "no_error":
            if has_metadata_error(resp):
                meta_err = resp["ResponseMetadata"]["Error"]
                return "fail", f"ResponseMetadata.Error 非空：{meta_err}"

        elif check == "result_schema":
            if not result_schema_name:
                return "fail", "step 声明了 result_schema 校验但未指定 result_schema 文件"
            validator = schemas.get(result_schema_name)
            if validator is None:
                return "fail", f"未找到 result_schema：{result_schema_name}"
            err = validate_schema(validator, resp)
            if err:
                return "fail", f"Result 不符合 schema：{err}"

        elif check == "error_status_4xx":
            if not (400 <= status < 500):
                return "fail", f"期望 4xx 错误，实际 {status}"

        elif check == "error_schema":
            err = validate_schema(schemas["error"], resp)
            if err:
                return "fail", f"错误响应不符合 schema：{err}"

        else:
            return "fail", f"未知 check：{check}"

    return "pass", ""


# ==================== 单个 step 执行 ====================


def run_step(step: dict, *, schemas: dict, config: dict, variables: dict,
             base_url: str, access_key: str, secret_key: str,
             dry_run: bool) -> tuple[CaseResult, bool]:
    """执行单个 step，返回 (CaseResult, 是否成功)。

    成功时会按 step.capture 把 Result 字段写入 variables，供后续 step 引用。
    """
    sid = step["id"]
    name = step.get("name", sid)
    action = step["action"]
    checks = step.get("checks", [])
    result_schema_name = step.get("result_schema")

    # 占位符替换：body 中的 ${group_id}/${asset_id}/${project_name} 等
    raw_body = step.get("body", {})
    body = substitute(raw_body, {**config, **variables})

    base_details = {"action": action, "request_body": truncate(body)}

    if dry_run:
        return CaseResult(
            id=sid, name=name, status="pass", duration_ms=0,
            details={**base_details, "dry_run": True, "url": build_url(base_url, action) if base_url else "",
                     "checks": checks, "result_schema": result_schema_name},
        ), True

    poll = step.get("poll")
    interval = config["poll_interval"]
    timeout_total = config["poll_timeout"]

    start = time.monotonic()
    polls = 0
    try:
        if poll:
            # 轮询：重复请求直到 until_field == until_value，或命中 fail_values，或超时
            deadline = time.monotonic() + timeout_total
            until_field = poll["until_field"]
            until_value = poll["until_value"]
            fail_values = set(poll.get("fail_values", []))
            status, resp = 0, {}
            while True:
                status, resp = send_signed_request(
                    base_url, action, body,
                    access_key=access_key, secret_key=secret_key, timeout=60,
                )
                polls += 1
                cur = get_result_status(resp) if until_field == "Status" else (
                    resp.get("Result", {}).get(until_field) if isinstance(resp.get("Result"), dict) else None
                )
                if status != 200 or has_metadata_error(resp):
                    break
                if cur == until_value or cur in fail_values:
                    break
                if time.monotonic() >= deadline:
                    break
                time.sleep(interval)
        else:
            status, resp = send_signed_request(
                base_url, action, body,
                access_key=access_key, secret_key=secret_key, timeout=120,
            )
            polls = 1
    except Exception as exc:  # noqa: BLE001
        elapsed = int((time.monotonic() - start) * 1000)
        return CaseResult(
            id=sid, name=name, status="error", error=f"请求异常：{exc!r}",
            duration_ms=elapsed, details={**base_details, "checks": checks},
        ), False

    elapsed = int((time.monotonic() - start) * 1000)

    verdict, error = run_checks(checks, schemas, status=status, resp=resp,
                                result_schema_name=result_schema_name)

    # 成功时按 capture 提取变量供后续 step 引用
    captured = {}
    if verdict == "pass":
        for var_name, result_field in (step.get("capture") or {}).items():
            result = resp.get("Result") if isinstance(resp, dict) else None
            if isinstance(result, dict) and result_field in result:
                variables[var_name] = result[result_field]
                captured[var_name] = result[result_field]

    poll_status = get_result_status(resp)
    return CaseResult(
        id=sid, name=name, status=verdict, error=error or None,
        expected=poll["until_value"] if poll else None,
        actual=poll_status if poll else None,
        duration_ms=elapsed,
        details={
            **base_details,
            "http_status": status,
            "polls": polls,
            "captured": captured,
            "checks": checks,
            "result_schema": result_schema_name,
            "response": truncate(resp),
        },
    ), verdict == "pass"


def main() -> int:
    parser = argparse.ArgumentParser(description="运行 Seedance 素材资产 API 测试用例")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="跳过真实请求，仅自测请求体构造、占位符替换与 schema 加载（无需 AK/SK）",
    )
    parser.add_argument(
        "--out", default=str(HERE / "reports"),
        help="报告输出目录，默认 ./reports",
    )
    args = parser.parse_args()

    schemas = load_schemas()
    config, steps = load_config_and_steps()
    if not steps:
        print("error: cases.yaml 中没有 steps", file=sys.stderr)
        return 1

    base_url = os.environ.get("API_BASE_URL", "")
    access_key = os.environ.get("ACCESS_KEY", "")
    secret_key = os.environ.get("SECRET_KEY", "")

    # project_name 是账号相关配置（需与 AK/SK 有权限的项目一致），
    # 优先级：环境变量 PROJECT_NAME > cases.yaml > default。
    env_project = os.environ.get("PROJECT_NAME", "")
    if env_project:
        config["project_name"] = env_project

    if not args.dry_run:
        missing = [n for n, v in (("API_BASE_URL", base_url), ("ACCESS_KEY", access_key),
                                  ("SECRET_KEY", secret_key)) if not v]
        if missing:
            print(f"error: 未设置 {' / '.join(missing)}；如需本地自测可加 --dry-run",
                  file=sys.stderr)
            return 1

    # 串行执行：素材资产是有依赖的生命周期链，后续 step 依赖前序输出。
    # 任一前置 step 失败时，依赖它的后续 step 仍执行但占位符可能未解析——
    # 为避免污染，前置生命周期 step 失败则跳过其依赖项（负向用例与真人会话独立，不跳过）。
    variables: dict = {}
    results: list[CaseResult] = []
    # 依赖前序产物的 step（需要 group_id / asset_id）。
    # create_liveness_session 与 invalid_get_asset_error 独立，不在此列。
    depends_on_chain = {
        "create_asset", "wait_asset_active", "list_assets", "list_asset_groups",
        "update_asset", "update_asset_group", "delete_asset", "delete_asset_group",
    }
    chain_broken = False

    for step in steps:
        if not args.dry_run and chain_broken and step["id"] in depends_on_chain:
            results.append(CaseResult(
                id=step["id"], name=step.get("name", step["id"]), status="error",
                error="前置生命周期 step 失败，跳过该依赖 step",
                details={"action": step.get("action"), "skipped": True},
            ))
            continue

        result, ok = run_step(step, schemas=schemas, config=config, variables=variables,
                              base_url=base_url, access_key=access_key, secret_key=secret_key,
                              dry_run=args.dry_run)
        results.append(result)
        # 生命周期链上的 step 失败 → 标记断链
        if not ok and step["id"] in (depends_on_chain | {"create_group"}):
            chain_broken = True

    report = Report(model="seedance-assets", cases=results)
    paths = report.write(args.out)
    s = report.summary()
    verdict = "PASS" if report.passed else "FAIL"
    print(f"seedance-assets: {verdict}  total={s['total']} pass={s['passed']} "
          f"fail={s['failed']} error={s['errored']} ({s['duration_ms']}ms)")
    print("报告已写入：" + "、".join(str(p) for p in paths.values()))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
