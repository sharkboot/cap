"""cap 控制面 FastAPI 入口。

启动：
    python -m cap.main
或
    cap  （若通过 pyproject 装了 CLI）
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI

from cap.agents import list_agents
from cap.capabilities import list_capabilities
from cap.api import images, sessions

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")

app = FastAPI(
    title="cap - 能力集成框架",
    version="0.1.0",
    description="按 profile 生成 Dockerfile、构建镜像、起容器、代理 agent 对话与文件收发",
)


@app.on_event("startup")
def _startup() -> None:
    # 打印已注册的能力与 agent，方便排查
    caps = [c["name"] for c in list_capabilities()]
    ags = [a["name"] for a in list_agents()]
    logging.getLogger("cap").info("registered capabilities=%s agents=%s", caps, ags)
    # 启动后台镜像 GC 线程（内存闸门 + 数量上限 + TTL 扫描）
    from cap.gc_singleton import init_gc
    init_gc()


@app.on_event("shutdown")
def _shutdown() -> None:
    from cap.gc_singleton import get_gc
    try:
        get_gc().stop_background()
    except Exception:
        pass


app.include_router(images.router, tags=["capabilities"])
app.include_router(sessions.router, tags=["sessions"])


@app.get("/v1/health")
def control_health() -> dict:
    return {"status": "ok", "version": "0.1.0"}


def run() -> None:
    import uvicorn
    uvicorn.run("cap.main:app", host="0.0.0.0", port=8080, reload=False)


if __name__ == "__main__":
    run()
