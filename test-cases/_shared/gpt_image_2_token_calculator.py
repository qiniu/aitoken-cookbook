#!/usr/bin/env python3
"""Replicate the OpenAI docs gpt-image-2 output token calculator."""

from __future__ import annotations

import argparse
import sys


TOKENS_PER_LONG_EDGE = {
    "low": 16,
    "medium": 48,
    "high": 96,
}

MIN_PIXEL_BUDGET = 655_360
MAX_PIXEL_BUDGET = 8_294_400
MAX_EDGE_LENGTH = 3_840
MAX_ASPECT_RATIO = 3
DIMENSION_MULTIPLE = 16


def parse_positive_int(value: str) -> int:
    try:
        number = int(value, 10)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a whole number") from exc

    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")

    return number


def validate_size(width: int, height: int) -> list[str]:
    errors: list[str] = []

    if width % DIMENSION_MULTIPLE != 0 or height % DIMENSION_MULTIPLE != 0:
        errors.append("Width and height must both be divisible by 16.")

    pixel_budget = width * height
    if pixel_budget > MAX_PIXEL_BUDGET:
        errors.append(
            f"Pixel budget must be no greater than {MAX_PIXEL_BUDGET:,} pixels, inclusive."
        )
    if pixel_budget < MIN_PIXEL_BUDGET:
        errors.append(
            f"Pixel budget must be at least {MIN_PIXEL_BUDGET:,} pixels, inclusive."
        )

    longest_edge = max(width, height)
    shortest_edge = min(width, height)
    if longest_edge > MAX_EDGE_LENGTH:
        errors.append(
            f"Maximum edge length must be less than or equal to {MAX_EDGE_LENGTH:,}px."
        )
    if longest_edge / shortest_edge > MAX_ASPECT_RATIO:
        errors.append("Aspect ratio must be no greater than 3:1.")

    return errors


def calculate_output_tokens(width: int, height: int, quality: str) -> int:
    long_edge_tokens = TOKENS_PER_LONG_EDGE[quality]
    longest_edge = max(width, height)
    shortest_edge = min(width, height)
    short_edge_tokens = round(long_edge_tokens * shortest_edge / longest_edge)

    if width >= height:
        width_tokens = long_edge_tokens
        height_tokens = short_edge_tokens
    else:
        width_tokens = short_edge_tokens
        height_tokens = long_edge_tokens

    token_area = width_tokens * height_tokens
    return -(-(token_area * (2_000_000 + width * height)) // 4_000_000)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Estimate gpt-image-2 image output tokens from quality and size."
    )
    parser.add_argument(
        "quality",
        nargs="?",
        choices=sorted(TOKENS_PER_LONG_EDGE),
        default="low",
        help="image quality setting: low, medium, or high",
    )
    parser.add_argument(
        "width",
        nargs="?",
        type=parse_positive_int,
        default=1024,
        help="image width in pixels",
    )
    parser.add_argument(
        "height",
        nargs="?",
        type=parse_positive_int,
        default=1024,
        help="image height in pixels",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print a JSON object instead of just the token count",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    errors = validate_size(args.width, args.height)
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 2

    tokens = calculate_output_tokens(args.width, args.height, args.quality)
    if args.json:
        print(
            "{"
            f'"model":"gpt-image-2",'
            f'"quality":"{args.quality}",'
            f'"width":{args.width},'
            f'"height":{args.height},'
            f'"output_tokens":{tokens}'
            "}"
        )
    else:
        print(tokens)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
