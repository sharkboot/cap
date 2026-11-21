"""框架 → 容器内 agent 薄壳的 HTTP 代理。

薄壳在容器 8000 端口暴露：
  POST /v1/chat          发一条用户消息（JSON body）
  GET  /v1/chat/events   SSE 订阅事件流
  GET  /v1/files         列 /work 下产物
  GET  /v1/files/{path}  下载
  POST /v1/files         上传
  GET  /v1/health        健康检查
框架侧只做透传 + 会话 id 映射。
"""
from __future__ import annotations

import httpx
from cap.runtime.container import RunningContainer


class ContainerProxy:
    def __init__(self, running: RunningContainer, client: httpx.Client | None = None):
        self.running = running
        self.client = client or httpx.Client(timeout=120)

    @property
    def base(self) -> str:
        return f"http://localhost:{self.running.host_port}"

    # ── 对话 ────────────────────────────────────────────────
    def chat(self, message: str, session_id: str | None = None) -> dict:
        r = self.client.post(
            f"{self.base}/v1/chat",
            json={"message": message, "session_id": session_id},
        )
        r.raise_for_status()
        return r.json()

    def stream_events(self, session_id: str):
        """SSE 事件流（生成器）。"""
        with self.client.stream("GET", f"{self.base}/v1/chat/events?session_id={session_id}") as r:
            for line in r.iter_lines():
                if line:
                    yield line

    # ── 文件 ────────────────────────────────────────────────
    def list_files(self) -> list[dict]:
        r = self.client.get(f"{self.base}/v1/files")
        r.raise_for_status()
        return r.json()

    def download(self, path: str) -> bytes:
        r = self.client.get(f"{self.base}/v1/files/{path}")
        r.raise_for_status()
        return r.content

    def upload(self, path: str, content: bytes) -> dict:
        r = self.client.post(
            f"{self.base}/v1/files/{path}",
            content=content,
        )
        r.raise_for_status()
        return r.json()

    # ── 健康 ────────────────────────────────────────────────
    def health(self) -> dict:
        r = self.client.get(f"{self.base}/v1/health")
        r.raise_for_status()
        return r.json()
