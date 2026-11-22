"""镜像 GC：容量闸门 + LRU/TTL 回收 + usage 追踪。

策略（按用户决策）：
- 内存闸门：build 前用 psutil 读宿主机空闲内存，低于 min_free_mem_mb 拒绝。
- 数量闸门：cap/* 镜像数 >= max_images 时，build 前触发一次 LRU 回收。
- TTL 回收：镜像"最近使用"（usage 记录或构建时间兜底）超过 ttl_hours 且
  未被运行中容器占用 → 删除。TTL 从最近使用起算。
- 引用保护：任何被 running/created 容器占用的镜像一律不删。

usage 记录：Docker 镜像本身不记"最近被谁用"，这里用 work/image_usage.json
记录 {tag: last_used_iso_ts}。容器启动时写、GC 时读。文件缺失/损坏时用
镜像 created 时间兜底（偏保守：构建久远的视为久未用）。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import docker
import psutil

from cap.config import ImageConfig

USAGE_FILE = "image_usage.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(s: str) -> float:
    """ISO 字符串 → epoch 秒；解析失败返回 0。"""
    try:
        return datetime.fromisoformat(s).timestamp()
    except (ValueError, TypeError):
        return 0.0


@dataclass
class GcResult:
    removed: list[str]
    kept: list[str]
    reason_by_tag: dict[str, str]
    free_mem_mb: int
    count_before: int
    count_after: int
    message: str


class ImageGC:
    def __init__(self, cfg: ImageConfig, usage_dir: Path, client: docker.DockerClient | None = None):
        self.cfg = cfg
        self.usage_dir = Path(usage_dir)
        self.usage_dir.mkdir(parents=True, exist_ok=True)
        self.usage_file = self.usage_dir / USAGE_FILE
        self._client = client  # 惰性：None 时每次调用 docker.from_env()，避免 daemon 未起时硬挂
        self.log = logging.getLogger("cap.gc")
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def client(self) -> docker.DockerClient:
        """惰性 docker client。daemon 未起时第一次访问才抛错，不影响 app 启动。"""
        if self._client is None:
            self._client = docker.from_env()
        return self._client

    # ── usage 记录 ──────────────────────────────────────────
    def record_usage(self, tag: str) -> None:
        """容器启动某镜像时调用，刷新其"最近使用"时间戳。"""
        with self._lock:
            data = self._load_usage()
            data[tag] = _now_iso()
            self._save_usage(data)

    def _load_usage(self) -> dict[str, str]:
        if not self.usage_file.is_file():
            return {}
        try:
            return json.loads(self.usage_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            self.log.warning("usage 文件损坏，重置为空")
            return {}

    def _save_usage(self, data: dict[str, str]) -> None:
        self.usage_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def last_used_epoch(self, tag: str, image: "docker.models.images.Image") -> float:
        """最近使用 epoch；无记录则用镜像 created 时间兜底。"""
        data = self._load_usage()
        if tag in data:
            ts = _parse_iso(data[tag])
            if ts > 0:
                return ts
        # 兜底：镜像 created
        try:
            return _parse_iso(image.attrs.get("Created", ""))
        except Exception:
            return 0.0

    # ── 容量闸门（build 前）────────────────────────────────
    def free_mem_mb(self) -> int:
        """宿主机当前空闲内存（MB）。psutil 读不到时返回 0（保守放行）。"""
        try:
            return int(psutil.virtual_memory().available / (1024 * 1024))
        except Exception as e:
            self.log.warning("psutil 读取内存失败: %s", e)
            return 0

    def count_images(self) -> int:
        return len([i for i in self.client.images.list()
                   if any(t.startswith(self.cfg.tag_prefix) for t in (i.tags or []))])

    def can_build(self) -> tuple[bool, str, GcResult | None]:
        """build 前闸门。返回 (是否可 build, 原因, 触发的 GC 结果)。

        - 内存不足 → 拒绝
        - 数量达上限 → 先自动 LRU GC，GC 后仍满才拒绝
        """
        free = self.free_mem_mb()
        if free > 0 and free < self.cfg.min_free_mem_mb:
            reason = (f"宿主机空闲内存 {free}MB < 要求 {self.cfg.min_free_mem_mb}MB，"
                      f"拒绝 build 以防 OOM")
            return False, reason, None

        gc_result: GcResult | None = None
        if self.count_images() >= self.cfg.max_images:
            gc_result = self.run_gc(by_lru=True)
            if self.count_images() >= self.cfg.max_images:
                reason = (f"cap/* 镜像数已达上限 {self.cfg.max_images}，"
                          f"GC 后仍无法腾出名额（可能全被运行中容器占用）")
                return False, reason, gc_result
        return True, "ok", gc_result

    # ── GC 执行 ────────────────────────────────────────────
    def _in_use_tags(self) -> set[str]:
        """被 running/created 容器占用的镜像 tag。"""
        in_use: set[str] = set()
        for c in self.client.containers.list():
            img_id = c.attrs.get("Image", "")
            if not img_id:
                continue
            in_use.add(img_id)
        return in_use

    def run_gc(self, by_lru: bool = False) -> GcResult:
        """执行一次 GC。

        by_lru=True：数量闸门触发的强制 LRU（删最久未用的，直到 < max_images）
        by_lru=False：仅 TTL 扫描（删超 ttl_hours 未用的）
        """
        with self._lock:
            free = self.free_mem_mb()
            count_before = self.count_images()
            in_use = self._in_use_tags()
            ttl_sec = self.cfg.ttl_hours * 3600 if self.cfg.ttl_hours > 0 else None
            now = time.time()

            # 收集 cap/* 镜像：tag, id, size, last_used, in_use
            candidates = []
            for img in self.client.images.list():
                tags = [t for t in (img.tags or []) if t.startswith(self.cfg.tag_prefix)]
                if not tags:
                    continue
                primary = tags[0]
                last_used = min(self.last_used_epoch(t, img) for t in tags)
                candidates.append({
                    "tags": tags,
                    "id": img.id,
                    "short_id": img.short_id,
                    "size": img.size,
                    "last_used": last_used,
                    "in_use": img.id in in_use,
                })

            removed: list[str] = []
            reason_by_tag: dict[str, str] = {}
            kept: list[str] = []

            # 1) TTL：最近使用超 ttl 且未被占用
            if ttl_sec is not None:
                for c in candidates:
                    if c["in_use"]:
                        kept.extend(c["tags"]); reason_by_tag[c["tags"][0]] = "in_use"; continue
                    age = now - c["last_used"] if c["last_used"] else float("inf")
                    if c["last_used"] == 0:
                        # 无 usage 记录且读不到 created：保守保留
                        kept.extend(c["tags"]); reason_by_tag[c["tags"][0]] = "no_usage_kept"; continue
                    if age > ttl_sec:
                        self._remove(c, "ttl_expired")
                        removed.extend(c["tags"])
                        reason_by_tag[c["tags"][0]] = "ttl_expired"
                    else:
                        kept.extend(c["tags"]); reason_by_tag[c["tags"][0]] = "ttl_ok"

            # 2) LRU：仍 >= max_images 时删最久未用的未占用镜像
            if by_lru:
                remaining = [c for c in self._remaining() ]
                # 按 last_used 升序（最旧优先删），跳过 in_use
                remaining = [c for c in remaining if not c["in_use"]]
                remaining.sort(key=lambda c: c["last_used"])
                # 删到 count < max_images
                current = self.count_images()
                for c in remaining:
                    if current < self.cfg.max_images:
                        break
                    if self._remove(c, "lru_overflow"):
                        current -= 1
                        removed.extend(c["tags"])
                        reason_by_tag[c["tags"][0]] = "lru_overflow"

            count_after = self.count_images()
            # 清理 usage 记录里已删的 tag
            self._prune_usage(removed)

            msg = f"GC: before={count_before} after={count_after} removed={removed}"
            self.log.info("%s free_mem=%dMB", msg, free)
            return GcResult(
                removed=removed, kept=kept, reason_by_tag=reason_by_tag,
                free_mem_mb=free, count_before=count_before, count_after=count_after,
                message=msg,
            )

    def _remaining(self) -> list[dict]:
        in_use = self._in_use_tags()
        out = []
        for img in self.client.images.list():
            tags = [t for t in (img.tags or []) if t.startswith(self.cfg.tag_prefix)]
            if not tags:
                continue
            out.append({
                "tags": tags, "id": img.id, "short_id": img.short_id,
                "size": img.size, "last_used": min(self.last_used_epoch(t, img) for t in tags),
                "in_use": img.id in in_use,
            })
        return out

    def _remove(self, c: dict, why: str) -> bool:
        """删镜像的所有 tag（同一 image id）。被占用则跳过。"""
        if c.get("in_use"):
            self.log.info("skip in-use image %s (%s)", c["tags"], why)
            return False
        for t in c["tags"]:
            try:
                self.client.images.remove(t, force=False)
                self.log.info("removed image tag %s (%s)", t, why)
            except docker.errors.ImageNotFound:
                pass
            except docker.errors.APIError as e:
                self.log.warning("remove %s failed: %s", t, e)
        return True

    def _prune_usage(self, removed_tags: list[str]) -> None:
        if not removed_tags:
            return
        data = self._load_usage()
        for t in removed_tags:
            data.pop(t, None)
        self._save_usage(data)

    # ── 后台 GC 线程 ───────────────────────────────────────
    def start_background(self) -> None:
        if self.cfg.gc_interval_sec <= 0:
            self.log.info("gc_interval_sec=0，不启动后台 GC")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()

        def _loop() -> None:
            while not self._stop.wait(self.cfg.gc_interval_sec):
                try:
                    r = self.run_gc(by_lru=False)
                    self.log.info("background gc: %s", r.message)
                except Exception as e:
                    self.log.error("background gc 异常: %s", e)

        self._thread = threading.Thread(target=_loop, daemon=True, name="cap-image-gc")
        self._thread.start()
        self.log.info("后台 GC 线程已启动（每 %ds）", self.cfg.gc_interval_sec)

    def stop_background(self) -> None:
        self._stop.set()
