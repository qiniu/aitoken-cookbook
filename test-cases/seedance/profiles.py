#!/usr/bin/env python3
"""Seedance 模型能力档案加载与用例匹配。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_PROFILES_PATH = HERE / "profiles.yaml"


@dataclass(frozen=True)
class SeedanceProfile:
    """一个 Seedance 模型系列的可测试能力。"""

    name: str
    resolutions: frozenset[str]
    output_formats: frozenset[str]
    max_duration: int
    audio_only_reference: bool
    max_reference_images: int
    max_reference_videos: int
    max_reference_audios: int
    max_total_reference_assets: int
    scenario_overrides: dict[str, dict[str, object]]


def load_profiles(path: Path | None = None) -> dict[str, SeedanceProfile]:
    """从 YAML 加载并校验全部 Seedance 能力档案。"""
    try:
        import yaml
    except ImportError:
        raise RuntimeError("缺少依赖 pyyaml，请执行 pip install pyyaml") from None

    source = path or DEFAULT_PROFILES_PATH
    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    raw_profiles = data.get("profiles") if isinstance(data, dict) else None
    if not isinstance(raw_profiles, dict) or not raw_profiles:
        raise ValueError(f"{source} 缺少非空 profiles 对象")

    required = {
        "resolutions",
        "output_formats",
        "max_duration",
        "audio_only_reference",
        "max_reference_images",
        "max_reference_videos",
        "max_reference_audios",
        "max_total_reference_assets",
        "scenario_overrides",
    }
    profiles: dict[str, SeedanceProfile] = {}
    for name, raw in raw_profiles.items():
        if not isinstance(raw, dict):
            raise ValueError(f"profile {name} 必须是对象")
        missing = sorted(required - raw.keys())
        if missing:
            raise ValueError(f"profile {name} 缺少字段：{', '.join(missing)}")
        scenario_overrides = raw["scenario_overrides"]
        if not isinstance(scenario_overrides, dict):
            raise ValueError(f"profile {name}.scenario_overrides 必须是对象")
        profiles[name] = SeedanceProfile(
            name=name,
            resolutions=frozenset(str(value) for value in raw["resolutions"]),
            output_formats=frozenset(str(value) for value in raw["output_formats"]),
            max_duration=int(raw["max_duration"]),
            audio_only_reference=bool(raw["audio_only_reference"]),
            max_reference_images=int(raw["max_reference_images"]),
            max_reference_videos=int(raw["max_reference_videos"]),
            max_reference_audios=int(raw["max_reference_audios"]),
            max_total_reference_assets=int(raw["max_total_reference_assets"]),
            scenario_overrides={
                str(scenario): dict(overrides)
                for scenario, overrides in scenario_overrides.items()
            },
        )
    return profiles


def unmet_requirement(requirements: dict, profile: SeedanceProfile) -> str | None:
    """返回首个不满足的能力说明；全部满足时返回 None。"""
    supported_keys = {
        "resolution",
        "output_format",
        "min_max_duration",
        "audio_only_reference",
        "min_max_reference_videos",
    }
    unknown = sorted(set(requirements) - supported_keys)
    if unknown:
        raise ValueError(f"未知 profile requirement：{', '.join(unknown)}")

    if "resolution" in requirements:
        expected = str(requirements["resolution"])
        if expected not in profile.resolutions:
            supported = ", ".join(sorted(profile.resolutions))
            return (
                f"profile {profile.name} 不支持 resolution={expected}"
                f"（支持：{supported}）"
            )
    if "output_format" in requirements:
        expected = str(requirements["output_format"])
        if expected not in profile.output_formats:
            supported = ", ".join(sorted(profile.output_formats))
            return (
                f"profile {profile.name} 不支持 output_format={expected}"
                f"（支持：{supported}）"
            )
    if "min_max_duration" in requirements:
        expected = int(requirements["min_max_duration"])
        if profile.max_duration < expected:
            return (
                f"profile {profile.name} 最长仅支持 {profile.max_duration} 秒，"
                f"用例要求至少 {expected} 秒"
            )
    if "audio_only_reference" in requirements:
        expected = bool(requirements["audio_only_reference"])
        if profile.audio_only_reference != expected:
            return f"profile {profile.name} 不支持纯音频参考"
    if "min_max_reference_videos" in requirements:
        expected = int(requirements["min_max_reference_videos"])
        if profile.max_reference_videos < expected:
            return (
                f"profile {profile.name} 最多支持 {profile.max_reference_videos} 段参考视频，"
                f"用例要求至少 {expected} 段"
            )
    return None


def apply_profile_overrides(case: dict, profile: SeedanceProfile) -> dict:
    """复制 case 并应用模型任务限制和 case 级 profile 覆盖。"""
    merged = dict(case)
    scenario = str(case.get("scenario", "text_to_video"))
    merged.update(profile.scenario_overrides.get(scenario, {}))

    per_case = case.get("profile_overrides", {})
    if per_case:
        if not isinstance(per_case, dict):
            raise ValueError("case.profile_overrides 必须是对象")
        overrides = per_case.get(profile.name, {})
        if not isinstance(overrides, dict):
            raise ValueError(f"case.profile_overrides.{profile.name} 必须是对象")
        merged.update(overrides)
    return merged


__all__ = [
    "SeedanceProfile",
    "apply_profile_overrides",
    "load_profiles",
    "unmet_requirement",
]
