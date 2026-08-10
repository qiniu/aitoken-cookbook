#!/usr/bin/env python3
"""公共测试报告行为测试。"""

from __future__ import annotations

import unittest

from report import CaseResult, Report


class ReportSkippedTests(unittest.TestCase):
    """防止 skipped 被误计为失败或从报告中消失。"""

    def test_skipped_is_counted_but_does_not_fail_report(self):
        report = Report(
            model="ep-custom",
            cases=[
                CaseResult(id="ok", name="通过", status="pass"),
                CaseResult(
                    id="skip",
                    name="跳过",
                    status="skipped",
                    details={"skip_reason": "profile 不支持 4k"},
                ),
            ],
        )

        self.assertEqual(report.summary()["skipped"], 1)
        self.assertTrue(report.passed)

    def test_skipped_is_visible_in_markdown_and_html(self):
        report = Report(
            model="ep-custom",
            cases=[
                CaseResult(
                    id="skip",
                    name="跳过",
                    status="skipped",
                    details={"skip_reason": "profile 不支持 mov"},
                )
            ],
        )

        self.assertIn("跳过 1", report.to_markdown())
        self.assertIn("○", report.to_markdown())
        self.assertIn("class='skipped'", report.to_html())
        self.assertIn("跳过 1", report.to_html())


if __name__ == "__main__":
    unittest.main()
