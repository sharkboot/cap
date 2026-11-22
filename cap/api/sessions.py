"""会话 / 对话 / 文件 API。"""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from cap.runtime.container import ContainerManager, RunningContainer
from cap.runtime.proxy import ContainerProxy
from cap.build.dockerfile import DockerfileArtifact
from cap.profile import Profile

router = APIRouter(prefix="/v1")

# 会话表：session_id → (running, proxy, profile, artifact)
_SESSIONS: dict[str, tuple[RunningContainer, ContainerProxy, Profile, DockerfileArtifact]] = {}


class StartRequest(BaseModel):
    profile_path: str
    image_tag: str | None = None
    name: str | None = None


class StartResponse(BaseModel):
    session_id: str
    image_tag: str
    workdir: str
    host_port: str


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    events: list[dict] | None = None


@router.post("/sessions", response_model=StartResponse)
def start_session(req: StartRequest):
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

    try:
        from cap.gc_singleton import get_gc
        gc = get_gc()
        manager = ContainerManager(host_root / "work", usage_recorder=gc.record_usage)
        running = manager.start(artifact, profile, session_name=req.name)
    except Exception as e:
        raise HTTPException(500, f"容器启动失败: {e}")

    proxy = ContainerProxy(running)
    sid = uuid.uuid4().hex[:12]
    _SESSIONS[sid] = (running, proxy, profile, artifact)
    return StartResponse(
        session_id=sid,
        image_tag=artifact.image_tag,
        workdir=str(running.workdir),
        host_port=running.host_port,
    )


@router.post("/sessions/{sid}/chat", response_model=ChatResponse)
def chat(sid: str, req: ChatRequest):
    if sid not in _SESSIONS:
        raise HTTPException(404, f"会话 {sid} 不存在")
    _, proxy, _, _ = _SESSIONS[sid]
    try:
        result = proxy.chat(req.message, req.session_id)
    except Exception as e:
        raise HTTPException(502, f"agent 薄壳调用失败: {e}")
    return ChatResponse(
        session_id=result.get("session_id", sid),
        reply=result.get("reply", ""),
        events=result.get("events"),
    )


@router.get("/sessions/{sid}/events")
def stream_events(sid: str, session_id: str | None = None):
    if sid not in _SESSIONS:
        raise HTTPException(404, f"会话 {sid} 不存在")
    _, proxy, _, _ = _SESSIONS[sid]
    target = session_id or sid

    def gen():
        for line in proxy.stream_events(target):
            yield f"data: {line}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/sessions/{sid}/files")
def list_files(sid: str):
    if sid not in _SESSIONS:
        raise HTTPException(404, f"会话 {sid} 不存在")
    _, proxy, _, _ = _SESSIONS[sid]
    return proxy.list_files()


@router.get("/sessions/{sid}/files/{path:path}")
def download_file(sid: str, path: str):
    if sid not in _SESSIONS:
        raise HTTPException(404, f"会话 {sid} 不存在")
    _, proxy, _, _ = _SESSIONS[sid]
    data = proxy.download(path)
    return StreamingResponse(iter([data]), media_type="application/octet-stream")


@router.post("/sessions/{sid}/files/{path:path}")
async def upload_file(sid: str, path: str, file: UploadFile = File(...)):
    if sid not in _SESSIONS:
        raise HTTPException(404, f"会话 {sid} 不存在")
    _, proxy, _, _ = _SESSIONS[sid]
    content = await file.read()
    return proxy.upload(path, content)


@router.delete("/sessions/{sid}")
def stop_session(sid: str, remove: bool = True):
    if sid not in _SESSIONS:
        raise HTTPException(404, f"会话 {sid} 不存在")
    running, _, _, _ = _SESSIONS.pop(sid)
    ContainerManager(Path(__file__).resolve().parents[2] / "work").stop(running, remove=remove)
    return {"session_id": sid, "stopped": True, "removed": remove}
