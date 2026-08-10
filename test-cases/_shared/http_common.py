#!/usr/bin/env python3
"""test-cases 公共 HTTP 常量。

各模型的 run_tests.py 用 urllib 直接发请求，此处集中它们共用的请求头取值，
避免每个套件各写一份。
"""

from __future__ import annotations

# 网关 WAF 会按 User-Agent 拦截 urllib 的默认标识（Python-urllib/3.x），
# 返回 403 error code: 1010；任何自定义 UA 均可正常放行，故不必伪装成浏览器。
USER_AGENT = "aitoken-cookbook-tests/1.0"
