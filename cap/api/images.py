"""镜像 / 能力 API。"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from cap.agents import list_agents
from cap.capabilities import list_capabilities, get_capability_detail
from cap.profile import Profile

router = APIRouter(prefix="/v1")


class BuildRequest(BaseModel):
    profile_path: str          # 相对 cap 根目录的 profile yaml 路径
    image_tag: str | None = None


class BuildResponse(BaseModel):
    image_tag: str
    dockerfile: str
    runtime_env: dict
    post_start: list[str]


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
    """按 profile 生成 Dockerfile + 执行 docker build。"""
    from pathlib import Path
    from cap.build.dockerfile import render_dockerfile

    host_root = Path(__file__).resolve().parents[2]
    profile_path = host_root / req.profile_path
    if not profile_path.is_file():
        raise HTTPException(404, f"profile 不存在: {profile_path}")
    profile = Profile.from_yaml(profile_path)
    try:
        artifact = render_dockerfile(profile, host_root, image_tag=req.image_tag)
    except (FileNotFoundError, ValueError, KeyError) as e:
        raise HTTPException(400, str(e))

    # 可选：真实 docker build
    if artifact.image_tag:
        try:
            from cap.build.image_store import ImageStore
            ImageStore(host_root / "build").build_from_profile(profile, host_root, artifact.image_tag)
        except Exception as e:
            # build 失败不阻塞返回（用户可先拿 Dockerfile 手动跑）
            return BuildResponse(
                image_tag=artifact.image_tag,
                dockerfile=artifact.dockerfile_text,
                runtime_env=artifact.runtime_env,
                post_start=artifact.post_start,
            )
    return BuildResponse(
        image_tag=artifact.image_tag,
        dockerfile=artifact.dockerfile_text,
        runtime_env=artifact.runtime_env,
        post_start=artifact.post_start,
    )
