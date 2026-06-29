#!/usr/bin/env python3
"""test-cases 公共报告模块。

设计：固定骨架 + 自由 details。
公共 runner 只依赖每个 case 的固定元字段（id/name/status/expected/actual/
error/duration_ms）即可生成三种报告；模型特有的数据（请求参数、媒体 URL、
原始响应片段等）放进自由的 details 字段，不影响通用逻辑。

一次产出三份报告：
- report.json  机器可读，唯一事实源
- report.md    人类速览（GitHub / 编辑器里直接看表格）
- report.html  富展示（details 中的图片 / 视频 URL 自动内嵌预览）
"""

from __future__ import annotations

import html as _html
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# 允许的 case 状态：通过 / 断言失败 / 执行报错
Status = str  # "pass" | "fail" | "error"

# details 中按 key 后缀识别媒体类型，用于 HTML 预览
_IMAGE_KEY_HINTS = ("image_url", "image", "img_url")
_VIDEO_KEY_HINTS = ("video_url", "video")


@dataclass
class CaseResult:
    """单个测试 case 的结果。

    固定元字段供公共 runner 统一处理；details 放模型特有数据。
    """

    id: str
    name: str
    status: Status
    expected: Any = None
    actual: Any = None
    error: str | None = None
    duration_ms: int = 0
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """转为字典，固定字段顺序，便于人读与 diff。"""
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "expected": self.expected,
            "actual": self.actual,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "details": self.details,
        }


# 状态对应的展示符号
_STATUS_ICON = {"pass": "✓", "fail": "✗", "error": "!"}


def mask_secret(value: str, *, head: int = 4, tail: int = 4) -> str:
    """对密钥类字符串脱敏：保留首尾若干字符，中间用 *** 替代。

    既能确认运行时用的是哪一把 key，又不泄露完整值（报告常被转发）。
    过短（长度 <= head+tail）的串只露首字符，其余打码，避免被反推。
    """
    if not value:
        return ""
    n = len(value)
    if n <= head + tail:
        return value[0] + "*" * (n - 1) if n > 1 else "*"
    return f"{value[:head]}***{value[-tail:]}"


@dataclass
class Report:
    """一次模型测试运行的完整报告。"""

    model: str
    cases: list[CaseResult] = field(default_factory=list)
    # 运行时环境变量（展示用，敏感值应由调用方脱敏后传入）
    env: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, int]:
        """统计总数与各状态数量、总耗时。"""
        passed = sum(1 for c in self.cases if c.status == "pass")
        failed = sum(1 for c in self.cases if c.status == "fail")
        errored = sum(1 for c in self.cases if c.status == "error")
        return {
            "total": len(self.cases),
            "passed": passed,
            "failed": failed,
            "errored": errored,
            "duration_ms": sum(c.duration_ms for c in self.cases),
        }

    @property
    def passed(self) -> bool:
        """是否全部通过（无 fail 且无 error）。"""
        s = self.summary()
        return s["failed"] == 0 and s["errored"] == 0

    def to_dict(self) -> dict[str, Any]:
        """完整报告字典，顶层固定为 model / env / summary / cases。"""
        return {
            "model": self.model,
            "env": self.env,
            "summary": self.summary(),
            "cases": [c.to_dict() for c in self.cases],
        }

    def to_json(self, *, indent: int = 2) -> str:
        """机器可读 JSON（缩进良好，人读也不费劲）。"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def to_markdown(self) -> str:
        """人类速览的 Markdown 报告。"""
        s = self.summary()
        verdict = "✅ PASS" if self.passed else "❌ FAIL"
        lines = [
            f"# 测试报告：{self.model}",
            "",
            f"**结果**：{verdict}　|　"
            f"总数 {s['total']}　通过 {s['passed']}　失败 {s['failed']}　"
            f"错误 {s['errored']}　耗时 {s['duration_ms']}ms",
            "",
        ]
        # 运行变量区块：记录本次测试用的环境变量
        if self.env:
            lines.append("## 运行变量")
            lines.append("")
            lines.append("| variable | value |")
            lines.append("|----------|-------|")
            for k, v in self.env.items():
                lines.append(f"| {_md_cell(k)} | {_md_cell(v)} |")
            lines.append("")
        lines += [
            "| status | id | name | expected | actual | error | duration |",
            "|--------|----|------|----------|--------|-------|----------|",
        ]
        for c in self.cases:
            icon = _STATUS_ICON.get(c.status, c.status)
            lines.append(
                f"| {icon} | {c.id} | {c.name} | "
                f"{_md_cell(c.expected)} | {_md_cell(c.actual)} | "
                f"{_md_cell(c.error)} | {c.duration_ms}ms |"
            )
        lines.append("")
        return "\n".join(lines)

    def to_html(self) -> str:
        """富展示 HTML 报告：自包含、含媒体预览。"""
        s = self.summary()
        verdict_class = "ok" if self.passed else "bad"
        verdict_text = "PASS" if self.passed else "FAIL"
        rows = "\n".join(_html_row(c) for c in self.cases)
        return _HTML_TEMPLATE.format(
            model=_html.escape(self.model),
            verdict_class=verdict_class,
            verdict_text=verdict_text,
            total=s["total"],
            passed=s["passed"],
            failed=s["failed"],
            errored=s["errored"],
            duration=s["duration_ms"],
            env=self._env_html(),
            rows=rows,
        )

    def _env_html(self) -> str:
        """渲染运行变量区块；无变量时返回空串（不显示该区块）。"""
        if not self.env:
            return ""
        rows = "\n".join(
            f"<tr><td class='envk'>{_html.escape(str(k))}</td>"
            f"<td><code>{_html.escape('' if v is None else str(v))}</code></td></tr>"
            for k, v in self.env.items()
        )
        return (
            "<h2>运行变量</h2>\n"
            "<table class='env'>\n"
            "<thead><tr><th>variable</th><th>value</th></tr></thead>\n"
            f"<tbody>\n{rows}\n</tbody>\n</table>"
        )

    def write(self, out_dir: str | Path) -> dict[str, Path]:
        """把三种报告写入目录，返回 {格式: 路径}。"""
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        paths = {
            "json": out / "report.json",
            "md": out / "report.md",
            "html": out / "report.html",
        }
        paths["json"].write_text(self.to_json(), encoding="utf-8")
        paths["md"].write_text(self.to_markdown(), encoding="utf-8")
        paths["html"].write_text(self.to_html(), encoding="utf-8")
        return paths


def _md_cell(value: Any) -> str:
    """把任意值渲染为安全的 Markdown 单元格内容。"""
    if value is None:
        return ""
    text = str(value)
    # 转义竖线并把换行压成空格，避免破坏表格
    return text.replace("|", "\\|").replace("\n", " ")


def _looks_like(key: str, hints: tuple[str, ...]) -> bool:
    """判断 details 的 key 是否命中某类媒体提示词。"""
    low = key.lower()
    return any(h in low for h in hints)


def _render_detail_value(key: str, value: Any) -> str:
    """渲染单条 detail：媒体 URL 转为可预览标签，其余转义为文本。"""
    if isinstance(value, str):
        if _looks_like(key, _IMAGE_KEY_HINTS):
            url = _html.escape(value)
            return f'<img src="{url}" alt="{_html.escape(key)}" loading="lazy">'
        if _looks_like(key, _VIDEO_KEY_HINTS):
            url = _html.escape(value)
            return f'<video src="{url}" controls preload="metadata"></video>'
    return _html.escape(json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value)


def _html_row(case: CaseResult) -> str:
    """生成 HTML 表格中的一行（含 details 折叠区）。"""
    icon = _STATUS_ICON.get(case.status, case.status)
    details_html = ""
    if case.details:
        items = "".join(
            f"<div class='kv'><span class='k'>{_html.escape(str(k))}</span>"
            f"<span class='v'>{_render_detail_value(str(k), v)}</span></div>"
            for k, v in case.details.items()
        )
        details_html = f"<details><summary>details</summary>{items}</details>"
    return (
        f"<tr class='{_html.escape(case.status)}'>"
        f"<td class='status'>{icon}</td>"
        f"<td>{_html.escape(case.id)}</td>"
        f"<td>{_html.escape(case.name)}</td>"
        f"<td>{_html.escape('' if case.expected is None else str(case.expected))}</td>"
        f"<td>{_html.escape('' if case.actual is None else str(case.actual))}</td>"
        f"<td class='err'>{_html.escape(case.error or '')}</td>"
        f"<td>{case.duration_ms}ms</td>"
        f"<td>{details_html}</td>"
        f"</tr>"
    )


# 自包含 HTML 模板：内联样式，无外部依赖
_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>测试报告 - {model}</title>
<style>
  body {{ font-family: -apple-system, "Segoe UI", sans-serif; margin: 2rem; color: #1f2328; }}
  h1 {{ font-size: 1.4rem; }}
  .verdict {{ display: inline-block; padding: .2rem .7rem; border-radius: 6px;
              font-weight: 700; color: #fff; }}
  .verdict.ok {{ background: #1a7f37; }}
  .verdict.bad {{ background: #cf222e; }}
  .meta {{ color: #57606a; margin: .5rem 0 1rem; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ border: 1px solid #d0d7de; padding: .5rem .6rem; text-align: left;
            vertical-align: top; font-size: .9rem; }}
  th {{ background: #f6f8fa; }}
  tr.pass .status {{ color: #1a7f37; font-weight: 700; }}
  tr.fail .status, tr.error .status {{ color: #cf222e; font-weight: 700; }}
  tr.fail {{ background: #fff5f5; }}
  tr.error {{ background: #fff8f0; }}
  .err {{ color: #cf222e; }}
  img, video {{ max-width: 240px; max-height: 240px; border-radius: 6px;
                display: block; margin: .3rem 0; }}
  .kv {{ margin: .25rem 0; }}
  .kv .k {{ color: #57606a; margin-right: .5rem; font-weight: 600; }}
  details summary {{ cursor: pointer; color: #0969da; }}
  h2 {{ font-size: 1.1rem; margin: 1.5rem 0 .5rem; }}
  table.env {{ width: auto; margin-bottom: 1rem; }}
  table.env td.envk {{ color: #57606a; font-weight: 600; }}
  table.env code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
</style>
</head>
<body>
<h1>测试报告：{model}</h1>
<div class="meta">
  <span class="verdict {verdict_class}">{verdict_text}</span>
  &nbsp; 总数 {total} · 通过 {passed} · 失败 {failed} · 错误 {errored} · 耗时 {duration}ms
</div>
{env}
<h2>用例结果</h2>
<table>
<thead>
<tr><th>status</th><th>id</th><th>name</th><th>expected</th><th>actual</th>
<th>error</th><th>duration</th><th>details</th></tr>
</thead>
<tbody>
{rows}
</tbody>
</table>
</body>
</html>
"""


# 便捷别名：asdict 用于需要直接序列化 dataclass 的场景
__all__ = ["CaseResult", "Report", "asdict", "mask_secret"]
