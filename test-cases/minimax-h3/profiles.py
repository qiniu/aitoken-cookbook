#!/usr/bin/env python3
"""MiniMax H3 模型能力档案加载与用例匹配。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_PROFILES_PATH = HERE / "profiles.yaml"


@dataclass(frozen=True)
class MiniMaxH3Profile:
    """一个 MiniMax H3 模型系列的可测试能力。"""

    name: str
    default_model: str
    resolutions: frozenset[str]
    min_duration: int
    max_duration: int
    prompt_expansion_modes: frozenset[str]


def load_profiles(path: Path | None = None) -> dict[str, MiniMaxH3Profile]:
    """从 YAML 加载并校验全部能力档案。"""
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
        "default_model",
        "resolutions",
        "min_duration",
        "max_duration",
        "prompt_expansion_modes",
    }
    profiles: dict[str, MiniMaxH3Profile] = {}
    for name, raw in raw_profiles.items():
        if not isinstance(raw, dict):
            raise ValueError(f"profile {name} 必须是对象")
        missing = sorted(required - raw.keys())
        if missing:
            raise ValueError(f"profile {name} 缺少字段：{', '.join(missing)}")
        min_duration = int(raw["min_duration"])
        max_duration = int(raw["max_duration"])
        if min_duration > max_duration:
            raise ValueError(f"profile {name} 的 min_duration 不能大于 max_duration")
        profiles[name] = MiniMaxH3Profile(
            name=str(name),
            default_model=str(raw["default_model"]),
            resolutions=frozenset(str(value) for value in raw["resolutions"]),
            min_duration=min_duration,
            max_duration=max_duration,
            prompt_expansion_modes=frozenset(
                str(value) for value in raw["prompt_expansion_modes"]
            ),
        )
    return profiles


def case_skip_reason(case: dict, profile: MiniMaxH3Profile) -> str | None:
    """返回用例不适用于当前 profile 的原因。"""
    allowed_profiles = case.get("profiles")
    if allowed_profiles and profile.name not in allowed_profiles:
        return f"用例仅适用于：{', '.join(str(v) for v in allowed_profiles)}"

    requirements = case.get("requires", {})
    supported_keys = {"resolution", "min_duration", "prompt_expansion_mode"}
    unknown = sorted(set(requirements) - supported_keys)
    if unknown:
        raise ValueError(f"未知 profile requirement：{', '.join(unknown)}")

    if "resolution" in requirements:
        resolution = str(requirements["resolution"])
        if resolution not in profile.resolutions:
            supported = ", ".join(sorted(profile.resolutions))
            return f"profile {profile.name} 不支持 resolution={resolution}（支持：{supported}）"
    if "min_duration" in requirements:
        duration = int(requirements["min_duration"])
        if profile.max_duration < duration:
            return f"profile {profile.name} 最长仅支持 {profile.max_duration} 秒"
    if "prompt_expansion_mode" in requirements:
        mode = str(requirements["prompt_expansion_mode"])
        if mode not in profile.prompt_expansion_modes:
            return f"profile {profile.name} 不支持 prompt_expansion_mode={mode}"
    return None


def apply_profile_overrides(case: dict, profile: MiniMaxH3Profile) -> dict:
    """复制用例并应用该 profile 的覆盖参数。"""
    merged = dict(case)
    overrides_by_profile = case.get("profile_overrides", {})
    if overrides_by_profile:
        if not isinstance(overrides_by_profile, dict):
            raise ValueError("case.profile_overrides 必须是对象")
        overrides = overrides_by_profile.get(profile.name, {})
        if not isinstance(overrides, dict):
            raise ValueError(f"case.profile_overrides.{profile.name} 必须是对象")
        merged.update(overrides)
    return merged


__all__ = [
    "MiniMaxH3Profile",
    "apply_profile_overrides",
    "case_skip_reason",
    "load_profiles",
]
