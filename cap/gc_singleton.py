"""全局单例：ImageGC + 配置。

main.py 启动时调 init_gc()；sessions / images API 通过 get_gc() 拿到同一实例。
惰性初始化，避免 import 时就连 docker。
"""
from __future__ import annotations

import logging
from pathlib import Path

from cap.build.gc import ImageGC
from cap.config import CapConfig

_CAP_ROOT = Path(__file__).resolve().parents[1]

_gc: ImageGC | None = None
_cfg: CapConfig | None = None
_log = logging.getLogger("cap.gc.singleton")


def init_gc() -> ImageGC:
    """启动时调用：加载配置、创建 GC、起后台线程。"""
    global _gc, _cfg
    if _gc is not None:
        return _gc
    _cfg = CapConfig.load(_CAP_ROOT)
    # usage 记录放 work/ 下（与容器 workdir 同级，便于运维查看）
    usage_dir = _CAP_ROOT / "work"
    _gc = ImageGC(_cfg.images, usage_dir)
    _gc.start_background()
    _log.info("GC 单例就绪 max_images=%d ttl_hours=%d min_free_mem_mb=%d",
              _cfg.images.max_images, _cfg.images.ttl_hours, _cfg.images.min_free_mem_mb)
    return _gc


def get_gc() -> ImageGC:
    global _gc
    if _gc is None:
        _gc = init_gc()
    return _gc


def get_image_config():
    global _cfg
    if _cfg is None:
        _cfg = CapConfig.load(_CAP_ROOT)
    return _cfg.images
