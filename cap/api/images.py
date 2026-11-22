"""镜像 / 能力 API。"""
import logging
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from cap.agents import list_agents
from cap.capabilities import list_capabilities, get_capability_detail
from cap.gc_singleton import get_gc, get_image_config
from cap.profile import Profile

router = APIRouter(prefix="/v1")
_LOG = logging.getLogger("cap.images")


class BuildRequest(BaseModel):
    profile_path: str          # 相对 cap 根目录的 profile yaml 路径
    image_tag: str | None = None


class BuildResponse(BaseModel):
    image_tag: str
    dockerfile: str
    runtime_env: dict
    post_start: list[str]
    gc: dict | None = None     # build 后触发的 GC 结果（若数量闸门触发）


class ImageInfo(BaseModel):
    tag: str
    size_mb: float
    last_used: str | None          # ISO，来自 usage 记录；无则为 null
    in_use: bool                   # 是否被运行中/created 容器占用
    ttl_expired: bool              # 是否已超过 ttl_hours（基于最近使用）
    age_hours: float | None        # 距最近使用的小时数


class GcTriggerResponse(BaseModel):
    removed: list[str]
    kept_count: int
    count_before: int
    count_after: int
    free_mem_mb: int
    message: str


@router.get("/capabilities")
def get_capabilities(
    source: str | None = Query(default=None, description="按来源过滤: local | remote | both"),
    q: str | None = Query(default=None, description="关键词，匹配 name 或 doc"),
) -> list[dict]:
    """查询已有能力，支持按来源过滤与关键词搜索。"""
    return list_capabilities(source=source, keyword=q)


@router.get("/capabilities/{name}")
def get_capability(name: str) -> dict:
    """单个能力的完整元数据（版本、来源、依赖、是否有本地资源等）。"""
    try:
        return get_capability_detail(name)
    except KeyError as e:
        raise HTTPException(404, str(e))


@router.get("/agents")
def get_agents() -> list[dict]:
    return list_agents()


@router.post("/images", response_model=BuildResponse)
def build_image(req: BuildRequest, root_path: str = ""):
    """按 profile 生成 Dockerfile + 执行 docker build。

    build 前走 GC 容量闸门（内存 + 数量），不通过则 503。
    """
    host_root = Path(__file__).resolve().parents[2]
    profile_path = host_root / req.profile_path
    if not profile_path.is_file():
        raise HTTPException(404, f"profile 不存在: {profile_path}")
    profile = Profile.from_yaml(profile_path)
    try:
        from cap.build.dockerfile import render_dockerfile
        artifact = render_dockerfile(profile, host_root, image_tag=req.image_tag)
    except (FileNotFoundError, ValueError, KeyError) as e:
        raise HTTPException(400, str(e))

    # ── 容量闸门 ──
    gc = get_gc()
    ok, reason, preempt_gc = gc.can_build()
    if not ok:
        raise HTTPException(503, reason)

    # 真实 docker build；失败不阻塞返回（用户可拿 Dockerfile 手动跑）
    built = False
    try:
        from cap.build.image_store import ImageStore
        ImageStore(host_root / "build").build_from_profile(profile, host_root, artifact.image_tag)
        built = True
    except Exception as e:
        _LOG.warning("docker build 失败（返回 Dockerfile 供手动构建）: %s", e)

    # build 成功后刷新 usage + 触发一次 GC（数量/TTL）
    gc_result = None
    if built:
        gc.record_usage(artifact.image_tag)
        gc_result = gc.run_gc(by_lru=False)

    return BuildResponse(
        image_tag=artifact.image_tag,
        dockerfile=artifact.dockerfile_text,
        runtime_env=artifact.runtime_env,
        post_start=artifact.post_start,
        gc=_gc_to_dict(preempt_gc) or _gc_to_dict(gc_result),
    )


@router.get("/images", response_model=list[ImageInfo])
def list_images() -> list[ImageInfo]:
    """列出 cap/* 镜像，带最近使用 / in_use / 是否 TTL 到期 信息。"""
    gc = get_gc()
    cfg = get_image_config()
    ttl_sec = cfg.ttl_hours * 3600 if cfg.ttl_hours > 0 else None
    in_use = gc._in_use_tags()
    usage = gc._load_usage()
    now = time.time()

    out: list[ImageInfo] = []
    for img in gc.client.images.list():
        tags = [t for t in (img.tags or []) if t.startswith(cfg.tag_prefix)]
        if not tags:
            continue
        primary = tags[0]
        last_used_iso = usage.get(primary)
        last_used_ts = gc.last_used_epoch(primary, img)
        in_use_flag = img.id in in_use
        age_hours = round(now - last_used_ts, 1) if last_used_ts else None
        ttl_expired = (
            ttl_sec is not None and last_used_ts > 0
            and (now - last_used_ts) > ttl_sec and not in_use_flag
        )
        out.append(ImageInfo(
            tag=primary,
            size_mb=round(img.size / 1024 / 1024, 1),
            last_used=last_used_iso,
            in_use=in_use_flag,
            ttl_expired=ttl_expired,
            age_hours=age_hours,
        ))
    return out


@router.delete("/images/gc", response_model=GcTriggerResponse)
def trigger_gc(by_lru: bool = Query(default=False, description="True=强制 LRU 到 max_images 以下")) -> GcTriggerResponse:
    """手动触发一次 GC（by_lru=True 走数量 LRU，否则仅 TTL 扫描）。"""
    gc = get_gc()
    r = gc.run_gc(by_lru=by_lru)
    return GcTriggerResponse(
        removed=r.removed,
        kept_count=len(r.kept),
        count_before=r.count_before,
        count_after=r.count_after,
        free_mem_mb=r.free_mem_mb,
        message=r.message,
    )


def _gc_to_dict(r) -> dict | None:
    if r is None:
        return None
    return {
        "removed": r.removed,
        "count_before": r.count_before,
        "count_after": r.count_after,
        "free_mem_mb": r.free_mem_mb,
        "message": r.message,
    }
