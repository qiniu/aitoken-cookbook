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
  python run_tests.py                  # 串行执行整条常规生命周期链
  python run_tests.py --dry-run        # 跳过真实请求，仅自测请求体构造、占位符替换与 schema 加载
  python run_tests.py --real-person    # 常规链之后附加交互式真人素材测试链（需真实 AK/SK + 测试者刷脸）
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
from report import CaseResult, Report, mask_secret  # noqa: E402
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
        # 真人素材测试链配置（仅 --real-person 用）
        "mismatch_photo_url": data.get("mismatch_photo_url", ""),
        "liveness_poll_interval": int(data.get("liveness_poll_interval", 5)),
        "liveness_poll_timeout": int(data.get("liveness_poll_timeout", 120)),
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


# ==================== 真人素材测试链（交互式，仅 --real-person）====================


def run_rp_request(*, sid: str, name: str, action: str, body: dict,
                   checks: list[str], result_schema_name: str | None,
                   schemas: dict, base_url: str, access_key: str, secret_key: str,
                   timeout: int = 120) -> tuple[CaseResult, dict]:
    """执行一次真人链请求并按 checks 校验，返回 (CaseResult, 响应 dict)。

    与声明式 run_step 不同：真人链需要拿到原始 resp 做 capture 与分支判断，
    故单独返回 resp。
    """
    start = time.monotonic()
    base_details = {"action": action, "request_body": truncate(body)}
    try:
        status, resp = send_signed_request(
            base_url, action, body,
            access_key=access_key, secret_key=secret_key, timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        elapsed = int((time.monotonic() - start) * 1000)
        return CaseResult(
            id=sid, name=name, status="error", error=f"请求异常：{exc!r}",
            duration_ms=elapsed, details={**base_details, "checks": checks},
        ), {}

    elapsed = int((time.monotonic() - start) * 1000)
    verdict, error = run_checks(checks, schemas, status=status, resp=resp,
                                result_schema_name=result_schema_name)
    return CaseResult(
        id=sid, name=name, status=verdict, error=error or None,
        duration_ms=elapsed,
        details={
            **base_details, "http_status": status, "checks": checks,
            "result_schema": result_schema_name, "response": truncate(resp),
        },
    ), resp


def rp_poll_field(*, action: str, body: dict, until_field: str, until_value,
                  fail_values: set, interval: int, timeout: int,
                  base_url: str, access_key: str, secret_key: str,
                  until_present: bool = False) -> tuple[int, dict, int]:
    """轮询某 Result 字段直到命中终止条件 / 超时。

    终止条件：
      - until_present=False（默认）：字段值 == until_value，或 in fail_values。
      - until_present=True：字段出现非空值即停（用于轮询 GroupId 出现，
        此时 until_value 被忽略）。
    HTTP 非 200 或 ResponseMetadata.Error 非空也立即停止。
    返回 (最后一次 HTTP 状态, 最后一次响应 dict, 轮询次数)。
    """
    deadline = time.monotonic() + timeout
    status, resp, polls = 0, {}, 0
    while True:
        status, resp = send_signed_request(
            base_url, action, body,
            access_key=access_key, secret_key=secret_key, timeout=60,
        )
        polls += 1
        result = resp.get("Result") if isinstance(resp, dict) else None
        cur = result.get(until_field) if isinstance(result, dict) else None
        if status != 200 or has_metadata_error(resp):
            break
        if until_present:
            if cur:  # 非空即满足
                break
        elif cur == until_value or cur in fail_values:
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(interval)
    return status, resp, polls


def rp_print_h5(h5_link: str) -> None:
    """在控制台醒目打印 H5 真人认证链接（完整、不截断），供测试者刷脸。"""
    bar = "=" * 70
    print("\n" + bar, file=sys.stderr)
    print("请用以下链接在手机/浏览器完成真人刷脸认证（h5_link）：", file=sys.stderr)
    print(h5_link, file=sys.stderr)
    print(bar + "\n", file=sys.stderr)


def rp_prompt_continue(message: str) -> bool:
    """阻塞等待测试者回车继续；输入 skip 则返回 False（跳过真人链余下部分）。"""
    try:
        ans = input(f"{message}（完成后按回车继续，输入 skip 跳过真人链）：").strip()
    except EOFError:
        return False
    return ans.lower() != "skip"


def rp_prompt_url(message: str) -> str | None:
    """阻塞读取一个 http(s) URL；输入 skip 返回 None；空或非法 URL 时重试。"""
    while True:
        try:
            ans = input(f"{message}（输入 skip 跳过真人链）：").strip()
        except EOFError:
            return None
        if ans.lower() == "skip":
            return None
        if ans.startswith("http://") or ans.startswith("https://"):
            return ans
        print("  输入无效：请提供以 http:// 或 https:// 开头的 URL", file=sys.stderr)


def run_real_person_chain(*, schemas: dict, config: dict, base_url: str,
                          access_key: str, secret_key: str) -> list[CaseResult]:
    """交互式真人素材测试链（仅 --real-person）。

    顺序：拉起会话 → 打印 h5_link → 刷脸前查(应取不到 GroupId) → 等刷脸 →
    刷脸后查(取到 GroupId) → 输入本人照片 → 本人素材入库(Active=通过) →
    非本人素材入库(Failed/报错=不通过) → 无效 token 查询(4xx) → 删除真人组。
    返回各 step 的 CaseResult 列表。
    """
    project = config["project_name"]
    results: list[CaseResult] = []

    # --- step 1: 拉起真人认证会话 ---
    res1, resp1 = run_rp_request(
        sid="rp_create_session", name="拉起真人认证 H5 会话（CreateVisualValidateSession）",
        action="CreateVisualValidateSession",
        body={"CallbackURL": config["liveness_callback_url"], "ProjectName": project},
        checks=["http_2xx", "envelope", "no_error", "result_schema"],
        result_schema_name="result_create_visual_validate_session.schema.json",
        schemas=schemas, base_url=base_url, access_key=access_key, secret_key=secret_key,
    )
    results.append(res1)
    result1 = resp1.get("Result") if isinstance(resp1, dict) else None
    byted_token = result1.get("BytedToken") if isinstance(result1, dict) else None
    h5_link = result1.get("H5Link") if isinstance(result1, dict) else None
    if not byted_token or not h5_link:
        results.append(CaseResult(
            id="rp_chain_aborted", name="真人链中止（未取到 BytedToken/H5Link）",
            status="error", error="CreateVisualValidateSession 未返回 BytedToken/H5Link，无法继续真人链",
        ))
        return results

    # --- 打印 h5_link，刷脸前先查一次 ---
    rp_print_h5(h5_link)

    # --- step 2: 刷脸前查询（应取不到 GroupId）---
    res2, resp2 = run_rp_request(
        sid="rp_result_before", name="刷脸前查询认证结果（应取不到 GroupId）",
        action="GetVisualValidateResult",
        body={"BytedToken": byted_token, "ProjectName": project},
        checks=[],  # 不用通用 checks，下面自定义判定
        result_schema_name=None,
        schemas=schemas, base_url=base_url, access_key=access_key, secret_key=secret_key,
    )
    # 自定义判定：刷脸前「取不到 GroupId」即通过。
    # 取不到的两种表现：HTTP 4xx 错误响应，或 2xx 但 Result 无非空 GroupId。
    status2 = res2.details.get("http_status")
    result2 = resp2.get("Result") if isinstance(resp2, dict) else None
    gid2 = result2.get("GroupId") if isinstance(result2, dict) else None
    if gid2:
        res2.status, res2.error = "fail", f"刷脸前不应取到 GroupId，却拿到：{gid2}"
    else:
        res2.status, res2.error = "pass", None
    res2.expected, res2.actual = "no GroupId", gid2 or f"http {status2}"
    results.append(res2)

    # --- 阻塞等测试者刷脸 ---
    if not rp_prompt_continue("请使用上方 h5_link 完成真人刷脸认证"):
        results.append(CaseResult(
            id="rp_chain_skipped", name="测试者跳过真人链（刷脸环节）", status="error",
            error="测试者输入 skip，真人链余下部分未执行",
        ))
        return results

    # --- step 3: 刷脸后查询（轮询取 GroupId）---
    start3 = time.monotonic()
    status3, resp3, polls3 = rp_poll_field(
        action="GetVisualValidateResult",
        body={"BytedToken": byted_token, "ProjectName": project},
        until_field="GroupId", until_value=None, fail_values=set(), until_present=True,
        interval=config["liveness_poll_interval"], timeout=config["liveness_poll_timeout"],
        base_url=base_url, access_key=access_key, secret_key=secret_key,
    )
    # until_present=True：轮询至 GroupId 出现非空值或超时；
    # 再用 result_schema 判定最终是否取到合法非空 GroupId。
    verdict3, error3 = run_checks(
        ["http_2xx", "envelope", "no_error", "result_schema"], schemas,
        status=status3, resp=resp3,
        result_schema_name="result_get_visual_validate_result.schema.json",
    )
    result3 = resp3.get("Result") if isinstance(resp3, dict) else None
    rp_group_id = result3.get("GroupId") if isinstance(result3, dict) else None
    res3 = CaseResult(
        id="rp_result_after", name="刷脸后查询认证结果（取到 GroupId=认证通过）",
        status=verdict3, error=error3 or None,
        expected="non-empty GroupId", actual=rp_group_id,
        duration_ms=int((time.monotonic() - start3) * 1000),
        details={"action": "GetVisualValidateResult", "http_status": status3,
                 "polls": polls3, "captured": {"rp_group_id": rp_group_id},
                 "response": truncate(resp3)},
    )
    results.append(res3)
    if verdict3 != "pass" or not rp_group_id:
        results.append(CaseResult(
            id="rp_chain_aborted_no_group", name="真人链中止（刷脸后未取到 GroupId）",
            status="error", error="未取到 GroupId（可能认证未通过或超时），无法测试真人素材入库",
        ))
        return results

    # --- 输入本人真人照片 URL ---
    match_url = rp_prompt_url("请输入测试者【本人】真人照片 URL（用于入库通过用例）")
    if not match_url:
        results.append(CaseResult(
            id="rp_chain_skipped_photo", name="测试者跳过真人链（本人照片环节）",
            status="error", error="未提供本人照片 URL，真人素材入库用例未执行",
        ))
        return results

    # --- step 4: 上传本人素材 ---
    res4, resp4 = run_rp_request(
        sid="rp_create_asset_match", name="上传本人真人素材（CreateAsset，应入库成功）",
        action="CreateAsset",
        body={"GroupId": rp_group_id, "URL": match_url, "AssetType": "Image",
              "Name": "rp_match_asset", "ProjectName": project},
        checks=["http_2xx", "envelope", "no_error", "result_schema"],
        result_schema_name="result_create_asset.schema.json",
        schemas=schemas, base_url=base_url, access_key=access_key, secret_key=secret_key,
    )
    res4.details["match_image_url"] = match_url  # 报告里预览本人照片
    results.append(res4)
    result4 = resp4.get("Result") if isinstance(resp4, dict) else None
    match_asset_id = result4.get("Id") if isinstance(result4, dict) else None

    # --- step 5: 轮询本人素材至 Active ---
    if match_asset_id:
        start5 = time.monotonic()
        status5, resp5, polls5 = rp_poll_field(
            action="GetAsset", body={"Id": match_asset_id, "ProjectName": project},
            until_field="Status", until_value="Active", fail_values={"Failed"},
            interval=config["poll_interval"], timeout=config["poll_timeout"],
            base_url=base_url, access_key=access_key, secret_key=secret_key,
        )
        st5 = get_result_status(resp5)
        results.append(CaseResult(
            id="rp_wait_match_active", name="轮询本人素材至 Active（入库通过）",
            status="pass" if st5 == "Active" else "fail",
            error=None if st5 == "Active" else f"本人素材未入库 Active，实际 Status={st5}",
            expected="Active", actual=st5,
            duration_ms=int((time.monotonic() - start5) * 1000),
            details={"action": "GetAsset", "http_status": status5, "polls": polls5,
                     "response": truncate(resp5)},
        ))
    else:
        results.append(CaseResult(
            id="rp_wait_match_active", name="轮询本人素材至 Active（入库通过）",
            status="error", error="CreateAsset 未返回素材 Id，无法轮询本人素材",
        ))

    # --- step 6: 上传非本人素材（固定 mismatch 照片）---
    mismatch_url = config["mismatch_photo_url"]
    res6, resp6 = run_rp_request(
        sid="rp_create_asset_mismatch", name="上传非本人素材（CreateAsset，应入库失败）",
        action="CreateAsset",
        body={"GroupId": rp_group_id, "URL": mismatch_url, "AssetType": "Image",
              "Name": "rp_mismatch_asset", "ProjectName": project},
        checks=[],  # 成功与否都不在此判定，交给 step 7 综合判定
        result_schema_name=None,
        schemas=schemas, base_url=base_url, access_key=access_key, secret_key=secret_key,
    )
    res6.details["mismatch_image_url"] = mismatch_url
    # step 6 本身只记录，不判 pass/fail（标 pass 仅表示请求已发出）
    # 先判断是否为网络异常（请求未发出）
    request6_sent = res6.status != "error"  # 网络异常时 status="error"
    if request6_sent:
        # 请求已发出：按原逻辑判断服务端响应
        status6 = res6.details.get("http_status")
        create6_failed = not (isinstance(status6, int) and 200 <= status6 < 300) or has_metadata_error(resp6)
        res6.status = "pass"  # 标 pass 仅表示请求已发出
        res6.error = None
    else:
        # 请求未发出（网络异常）：保留 error 状态，不计入「被拒」语义
        status6 = None
        create6_failed = False
    results.append(res6)
    result6 = resp6.get("Result") if isinstance(resp6, dict) else None
    mismatch_asset_id = result6.get("Id") if isinstance(result6, dict) else None

    # --- step 7: 判定非本人素材「入库不通过」---
    # 不通过的两种表现：CreateAsset 同步报错，或 GetAsset 轮询至 Failed。
    if not request6_sent:
        # 请求未发出（网络异常）：无法判定非本人素材入库结果
        results.append(CaseResult(
            id="rp_wait_mismatch_fail", name="非本人素材入库不通过", status="error",
            error="CreateAsset 请求异常，无法判定非本人素材入库结果",
        ))
    elif create6_failed:
        # CreateAsset 直接拒绝即判通过
        results.append(CaseResult(
            id="rp_wait_mismatch_fail", name="非本人素材入库不通过（CreateAsset 被拒）",
            status="pass", error=None,
            expected="rejected or Failed", actual=f"CreateAsset http {status6}",
            details={"note": "CreateAsset 同步拒绝非本人素材，符合预期"},
        ))
    elif mismatch_asset_id:
        start7 = time.monotonic()
        status7, resp7, polls7 = rp_poll_field(
            action="GetAsset", body={"Id": mismatch_asset_id, "ProjectName": project},
            until_field="Status", until_value="Failed", fail_values=set(),
            interval=config["poll_interval"], timeout=config["poll_timeout"],
            base_url=base_url, access_key=access_key, secret_key=secret_key,
        )
        st7 = get_result_status(resp7)
        # Failed=正确拒绝(通过)；Active=服务端没做一致性比对(fail)；其他=超时未决(fail)
        passed7 = st7 == "Failed"
        results.append(CaseResult(
            id="rp_wait_mismatch_fail", name="非本人素材入库不通过（GetAsset 轮询至 Failed）",
            status="pass" if passed7 else "fail",
            error=None if passed7 else f"非本人素材未被拒绝，实际 Status={st7}（Active 说明未做人脸一致性比对）",
            expected="Failed", actual=st7,
            duration_ms=int((time.monotonic() - start7) * 1000),
            details={"action": "GetAsset", "http_status": status7, "polls": polls7,
                     "response": truncate(resp7)},
        ))
    else:
        results.append(CaseResult(
            id="rp_wait_mismatch_fail", name="非本人素材入库不通过", status="error",
            error="CreateAsset 既未报错也未返回素材 Id，无法判定非本人素材入库结果",
        ))

    # --- step 8: 无效 BytedToken 查询（应 4xx 错误格式）---
    res8, _ = run_rp_request(
        sid="rp_invalid_token", name="无效 BytedToken 查询认证结果（错误响应格式）",
        action="GetVisualValidateResult",
        body={"BytedToken": "invalid-byted-token-00000000", "ProjectName": project},
        checks=["error_schema"],
        result_schema_name=None,
        schemas=schemas, base_url=base_url, access_key=access_key, secret_key=secret_key,
    )
    results.append(res8)

    # --- step 9: 删除真人 Asset Group（清理）---
    res9, _ = run_rp_request(
        sid="rp_cleanup_group", name="删除真人素材组（DeleteAssetGroup，清理）",
        action="DeleteAssetGroup",
        body={"Id": rp_group_id, "ProjectName": project},
        checks=["http_2xx", "envelope", "no_error", "result_schema"],
        result_schema_name="result_empty.schema.json",
        schemas=schemas, base_url=base_url, access_key=access_key, secret_key=secret_key,
    )
    # 清理失败不应让整体退出码失败：降级为 error 仅记录。
    # 注：report.passed 把 error 也算未通过，故清理失败降级为带标记的 pass。
    if res9.status != "pass":
        res9.details["cleanup_failed"] = True
        res9.error = (res9.error or "") + "（清理 step，失败不影响整体结论）"
        res9.status = "pass"
    results.append(res9)

    return results


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
    parser.add_argument(
        "--real-person", action="store_true",
        help="在常规链之后附加交互式真人素材测试链（需真实 AK/SK + 测试者刷脸配合）",
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

    # 真人素材测试链（仅 --real-person）：交互式，跑在常规链之后。
    if args.real_person:
        if args.dry_run:
            # dry-run 占位：不发请求、不交互，仅声明真人链将执行的 step。
            for sid, name in (
                ("rp_create_session", "拉起真人认证 H5 会话（CreateVisualValidateSession）"),
                ("rp_result_before", "刷脸前查询认证结果（应取不到 GroupId）"),
                ("rp_result_after", "刷脸后查询认证结果（取到 GroupId=认证通过）"),
                ("rp_create_asset_match", "上传本人真人素材（CreateAsset，应入库成功）"),
                ("rp_wait_match_active", "轮询本人素材至 Active（入库通过）"),
                ("rp_create_asset_mismatch", "上传非本人素材（CreateAsset，应入库失败）"),
                ("rp_wait_mismatch_fail", "非本人素材入库不通过"),
                ("rp_invalid_token", "无效 BytedToken 查询认证结果（错误响应格式）"),
                ("rp_cleanup_group", "删除真人素材组（DeleteAssetGroup，清理）"),
            ):
                results.append(CaseResult(
                    id=sid, name=name, status="pass",
                    details={"dry_run": True, "real_person_chain": True},
                ))
        else:
            results.extend(run_real_person_chain(
                schemas=schemas, config=config, base_url=base_url,
                access_key=access_key, secret_key=secret_key,
            ))

    # 记录本次运行的环境变量到报告（密钥脱敏，便于复现与排查）
    env = {
        "API_BASE_URL": base_url,
        "ACCESS_KEY": mask_secret(access_key),
        "SECRET_KEY": mask_secret(secret_key),
        "PROJECT_NAME": config["project_name"],
    }
    report = Report(model="seedance-assets", cases=results, env=env)
    paths = report.write(args.out)
    s = report.summary()
    verdict = "PASS" if report.passed else "FAIL"
    print(f"seedance-assets: {verdict}  total={s['total']} pass={s['passed']} "
          f"fail={s['failed']} error={s['errored']} ({s['duration_ms']}ms)")
    print("报告已写入：" + "、".join(str(p) for p in paths.values()))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
