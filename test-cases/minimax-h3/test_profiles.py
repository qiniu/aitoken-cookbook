#!/usr/bin/env python3
"""MiniMax H3 profile 测试。"""

from __future__ import annotations

import unittest

from profiles import apply_profile_overrides, case_skip_reason, load_profiles


class ProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profiles = load_profiles()

    def test_documented_capability_matrix(self):
        h3 = self.profiles["minimax-h3"]
        h3_max = self.profiles["minimax-h3-max"]
        self.assertEqual(h3.default_model, "MiniMax-H3")
        self.assertEqual(h3.resolutions, frozenset({"768P", "2K"}))
        self.assertEqual((h3.min_duration, h3.max_duration), (4, 15))
        self.assertFalse(h3.prompt_expansion_modes)
        self.assertEqual(h3_max.default_model, "MiniMax-H3-Max")
        self.assertEqual(h3_max.resolutions, frozenset({"480P", "768P"}))
        self.assertEqual((h3_max.min_duration, h3_max.max_duration), (5, 15))
        self.assertEqual(
            h3_max.prompt_expansion_modes,
            frozenset({"disabled", "balanced", "quality"}),
        )

    def test_profile_specific_case_is_skipped(self):
        reason = case_skip_reason(
            {"profiles": ["minimax-h3-max"]}, self.profiles["minimax-h3"]
        )
        self.assertIn("minimax-h3-max", reason)

    def test_profile_override_does_not_mutate_case(self):
        case = {
            "resolution": "768P",
            "profile_overrides": {"minimax-h3": {"resolution": "2K"}},
        }
        merged = apply_profile_overrides(case, self.profiles["minimax-h3"])
        self.assertEqual(merged["resolution"], "2K")
        self.assertEqual(case["resolution"], "768P")


if __name__ == "__main__":
    unittest.main()
