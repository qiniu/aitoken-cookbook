import base64
import importlib.util
import struct
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "gpt_image_2_run_tests", HERE / "run_tests.py"
)
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


def make_rgba_png(alpha: int) -> bytes:
    """构造一个 1x1 RGBA PNG，alpha 由测试显式指定。"""
    signature = b"\x89PNG\r\n\x1a\n"

    def chunk(kind: bytes, data: bytes) -> bytes:
        payload = kind + data
        return struct.pack(">I", len(data)) + payload + struct.pack(">I", zlib.crc32(payload))

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    pixels = zlib.compress(bytes([0, 255, 0, 0, alpha]))
    return signature + chunk(b"IHDR", ihdr) + chunk(b"IDAT", pixels) + chunk(b"IEND", b"")


class GenerationRequestTests(unittest.TestCase):
    def test_existing_connectivity_case_covers_transparent_background(self):
        _, cases = RUNNER.load_cases()
        case = next(item for item in cases if item["id"] == "gen_connectivity_basic")

        self.assertEqual(len(cases), 16)
        self.assertEqual(case["background"], "transparent")
        self.assertEqual(case["output_format"], "png")
        self.assertIn("background_echo", case["checks"])
        self.assertIn("image_has_transparent_pixel", case["checks"])

    def test_build_gen_body_includes_transparent_background_and_png_output(self):
        body = RUNNER.build_gen_body(
            "gpt-image-2",
            "draw an icon",
            "low",
            "1024x1024",
            {"background": "transparent", "output_format": "png"},
        )

        self.assertEqual(
            body,
            {
                "model": "gpt-image-2",
                "prompt": "draw an icon",
                "quality": "low",
                "size": "1024x1024",
                "n": 1,
                "background": "transparent",
                "output_format": "png",
            },
        )

    def test_png_has_transparent_pixel_checks_decoded_alpha(self):
        self.assertTrue(RUNNER.png_has_transparent_pixel(make_rgba_png(0)))
        self.assertFalse(RUNNER.png_has_transparent_pixel(make_rgba_png(255)))

    def test_transparent_background_checks_accept_real_transparency(self):
        response = {
            "background": "transparent",
            "data": [{"b64_json": base64.b64encode(make_rgba_png(0)).decode()}],
        }

        verdict = RUNNER.run_checks(
            ["background_echo", "image_has_transparent_pixel"],
            200,
            response,
            {"background": "transparent", "size": "1x1"},
            0,
            1,
        )

        self.assertEqual(verdict[0], "pass")

    def test_transparent_background_checks_reject_fully_opaque_png(self):
        response = {
            "background": "transparent",
            "data": [{"b64_json": base64.b64encode(make_rgba_png(255)).decode()}],
        }

        verdict = RUNNER.run_checks(
            ["background_echo", "image_has_transparent_pixel"],
            200,
            response,
            {"background": "transparent", "size": "1x1"},
            0,
            1,
        )

        self.assertEqual(verdict[0], "fail")
        self.assertIn("透明像素", verdict[1])

    def test_run_case_uses_case_level_transparent_background_options(self):
        class Calculator:
            @staticmethod
            def validate_size(_width, _height):
                return []

            @staticmethod
            def calculate_output_tokens(_width, _height, _quality):
                return 196

        case = {
            "id": "transparent",
            "name": "transparent",
            "endpoint": "generations",
            "quality": "low",
            "size": "1024x1024",
            "prompt": "draw an isolated icon on a transparent background",
            "background": "transparent",
            "output_format": "png",
            "checks": [],
        }

        with patch.object(RUNNER, "send_request", return_value=(200, {})):
            result = RUNNER.run_case(
                case,
                calc=Calculator(),
                config={"prompt": "default", "edit_prompt": "edit", "edit_image": ""},
                model="gpt-image-2",
                base_url="https://example.test/v1",
                api_key="test-key",
                dry_run=False,
            )

        self.assertEqual(
            result.details["request"]["body"],
            {
                "model": "gpt-image-2",
                "prompt": "draw an isolated icon on a transparent background",
                "quality": "low",
                "size": "1024x1024",
                "n": 1,
                "background": "transparent",
                "output_format": "png",
            },
        )


if __name__ == "__main__":
    unittest.main()
