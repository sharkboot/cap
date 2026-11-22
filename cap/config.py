"""cap.toml 配置加载。

用标准库 tomllib（Python 3.11+）。加载失败时返回带默认值的 ImageConfig，
让控制面在缺 cap.toml 时仍可运行（走内置默认）。
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

_CAP_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class ImageConfig:
    """[images] 配置段。"""
    max_images: int = 20
    ttl_hours: int = 72              # 0 = 不启用 TTL
    min_free_mem_mb: int = 2048
    gc_interval_sec: int = 3600       # 0 = 不自动扫
    est_build_mem_mb: int = 1024
    est_image_disk_mb: int = 512
    tag_prefix: str = "cap/"

    @classmethod
    def from_cap_toml(cls, cap_root: Path | None = None) -> "ImageConfig":
        root = cap_root or _CAP_ROOT
        toml = root / "cap.toml"
        if not toml.is_file():
            return cls()
        with open(toml, "rb") as f:
            data = tomllib.load(f)
        img = data.get("images", {})
        return cls(
            max_images=int(img.get("max_images", 20)),
            ttl_hours=int(img.get("ttl_hours", 72)),
            min_free_mem_mb=int(img.get("min_free_mem_mb", 2048)),
            gc_interval_sec=int(img.get("gc_interval_sec", 3600)),
            est_build_mem_mb=int(img.get("est_build_mem_mb", 1024)),
            est_image_disk_mb=int(img.get("est_image_disk_mb", 512)),
            tag_prefix=str(img.get("tag_prefix", "cap/")),
        )


@dataclass
class CapConfig:
    """整个 cap.toml 的加载结果（当前只用 [images]，其它段后续按需加）。"""
    images: ImageConfig = field(default_factory=ImageConfig)

    @classmethod
    def load(cls, cap_root: Path | None = None) -> "CapConfig":
        return cls(images=ImageConfig.from_cap_toml(cap_root))
