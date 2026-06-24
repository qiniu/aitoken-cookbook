#!/usr/bin/env python3
"""火山方舟 / BytePlus Signature V4 签名（HMAC-SHA256），纯标准库实现。

素材资产（Assets）API 用 AK/SK 签名鉴权，而非 Bearer token。本模块按火山
Signature V4 算法逐字段计算签名头，供 run_tests.py 在每次请求前注入：
  X-Date / X-Content-Sha256 / Authorization（外加固定的 Content-Type）。

固定 region=cn-beijing、service=ark。派生签名密钥的种子直接用 SK（BytePlus
风格，非 AWS 的 "AWS4"+sk 前缀）。签名所覆盖的头固定为：
  content-type;host;x-content-sha256;x-date

注意：签名依赖真实 UTC 时间戳（X-Date），与时间强相关；signed_headers 的顺序
和 canonical 计算必须与服务端完全一致，否则会鉴权失败（403）。
"""

from __future__ import annotations

import hashlib
import hmac
import urllib.parse
from datetime import datetime, timezone

# 固定区域与服务（与素材资产 API 一致）
REGION = "cn-beijing"
SERVICE = "ark"

# 参与签名的头（固定顺序，分号分隔）
SIGNED_HEADERS = "content-type;host;x-content-sha256;x-date"


def _hmac_sha256(key: bytes, msg: str) -> bytes:
    """HMAC-SHA256：key 作密钥，msg（字符串）作消息，返回原始字节摘要。"""
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _sha256_hex(data: bytes) -> str:
    """对字节求 SHA256 并返回十六进制小写字符串。"""
    return hashlib.sha256(data).hexdigest()


def _rfc3986_escape(s: str) -> str:
    """RFC3986 转义：空格→%20、~ 不转义，与服务端 canonical 规则对齐。"""
    # quote 默认不转义 ~ 和字母数字与 _.-，与火山 rfc3986Escape 行为一致
    return urllib.parse.quote(s, safe="-_.~")


def _canonical_query(raw_query: str) -> str:
    """把 query string 规范化：按 key 字典序（同 key 再按 value）排序并 RFC3986 编码。"""
    if not raw_query:
        return ""
    # parse_qsl 保留重复 key；keep_blank_values 保证空值参数也参与
    pairs = urllib.parse.parse_qsl(raw_query, keep_blank_values=True)
    # 先按 value 再按 key 排序，确保同 key 多值时顺序稳定
    pairs.sort(key=lambda kv: (kv[0], kv[1]))
    return "&".join(f"{_rfc3986_escape(k)}={_rfc3986_escape(v)}" for k, v in pairs)


def sign_headers(
    *,
    access_key: str,
    secret_key: str,
    method: str,
    host: str,
    path: str,
    raw_query: str,
    body: bytes,
    now: datetime | None = None,
) -> dict[str, str]:
    """计算并返回需注入请求的签名相关头。

    参数：
      access_key / secret_key  火山 AK/SK
      method                   HTTP 方法（如 POST）
      host                     请求主机名（不含 scheme），如 ark.cn-beijing.volces.com
      path                     请求路径（如 /api/v3/）
      raw_query                原始 query string（如 Action=CreateAsset&Version=2024-01-01）
      body                     请求体原始字节（可为空）
      now                      签名时间（UTC）；默认取当前 UTC 时间。测试可注入固定时间。

    返回 dict，含 Content-Type / X-Date / X-Content-Sha256 / Authorization。
    """
    if now is None:
        now = datetime.now(timezone.utc)

    x_date = now.strftime("%Y%m%dT%H%M%SZ")
    short_date = now.strftime("%Y%m%d")
    body_hash = _sha256_hex(body)

    # 规范请求头（固定顺序，与 SIGNED_HEADERS 对应）
    canonical_headers = (
        "content-type:application/json\n"
        f"host:{host}\n"
        f"x-content-sha256:{body_hash}\n"
        f"x-date:{x_date}\n"
    )

    canonical_request = (
        f"{method}\n"
        f"{path}\n"
        f"{_canonical_query(raw_query)}\n"
        f"{canonical_headers}\n"
        f"{SIGNED_HEADERS}\n"
        f"{body_hash}"
    )

    credential_scope = f"{short_date}/{REGION}/{SERVICE}/request"
    string_to_sign = (
        "HMAC-SHA256\n"
        f"{x_date}\n"
        f"{credential_scope}\n"
        f"{_sha256_hex(canonical_request.encode('utf-8'))}"
    )

    # 派生签名密钥：SK 直接作种子（BytePlus 风格）
    k_date = _hmac_sha256(secret_key.encode("utf-8"), short_date)
    k_region = _hmac_sha256(k_date, REGION)
    k_service = _hmac_sha256(k_region, SERVICE)
    k_signing = _hmac_sha256(k_service, "request")
    signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    authorization = (
        f"HMAC-SHA256 Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={SIGNED_HEADERS}, "
        f"Signature={signature}"
    )

    return {
        "Content-Type": "application/json",
        "X-Date": x_date,
        "X-Content-Sha256": body_hash,
        "Authorization": authorization,
    }
