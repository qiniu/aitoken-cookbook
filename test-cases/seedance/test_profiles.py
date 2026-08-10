#!/usr/bin/env python3
"""Seedance 模型能力档案测试。"""

from __future__ import annotations

import unittest

from profiles import apply_profile_overrides, load_profiles, unmet_requirement


class ProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profiles = load_profiles()

    def test_official_capability_matrix(self):
        standard = self.profiles["seedance-2.0"]
        fast = self.profiles["seedance-2.0-fast"]
        mini = self.profiles["seedance-2.0-mini"]
        v25 = self.profiles["seedance-2.5"]

        self.assertEqual(set(self.profiles), {
            "seedance-2.5",
            "seedance-2.0",
            "seedance-2.0-fast",
            "seedance-2.0-mini",
        })
        self.assertIn("4k", standard.resolutions)
        self.assertNotIn("4k", fast.resolutions)
        self.assertNotIn("4k", mini.resolutions)
        self.assertEqual(v25.resolutions, frozenset({"480p", "720p"}))
        self.assertEqual(v25.output_formats, frozenset({"mp4", "mov"}))
        self.assertEqual(v25.max_duration, 30)
        self.assertTrue(v25.audio_only_reference)
        self.assertEqual(v25.max_reference_images, 30)
        self.assertEqual(v25.max_reference_videos, 10)
        self.assertEqual(v25.max_reference_audios, 10)
        self.assertEqual(v25.max_total_reference_assets, 50)

    def test_requirement_explains_unsupported_resolution(self):
        reason = unmet_requirement(
            {"resolution": "4k"}, self.profiles["seedance-2.0-mini"]
        )

        self.assertEqual(
            reason,
            "profile seedance-2.0-mini 不支持 resolution=4k（支持：480p, 720p）",
        )

    def test_supported_requirements_return_no_reason(self):
        reason = unmet_requirement(
            {
                "output_format": "mov",
                "min_max_duration": 30,
                "audio_only_reference": True,
                "min_max_reference_videos": 6,
            },
            self.profiles["seedance-2.5"],
        )

        self.assertIsNone(reason)

    def test_unknown_requirement_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "未知 profile requirement"):
            unmet_requirement(
                {"resoultion": "4k"}, self.profiles["seedance-2.0"]
            )

    def test_profile_override_wins_over_case_value_without_mutating_input(self):
        case = {
            "id": "first-frame",
            "scenario": "image_to_video",
            "duration": 5,
            "ratio": "16:9",
        }

        merged = apply_profile_overrides(case, self.profiles["seedance-2.5"])

        self.assertEqual(merged["duration"], -1)
        self.assertEqual(merged["ratio"], "adaptive")
        self.assertEqual(case["duration"], 5)
        self.assertEqual(case["ratio"], "16:9")


if __name__ == "__main__":
    unittest.main()
