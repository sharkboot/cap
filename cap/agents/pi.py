"""Pi (earendil-works) 运行时。

Pi 支持 --mode rpc（JSONL stdin/stdout 长连接）+ 原生 skill/MCP。
"""
from .base import AgentRuntime, AgentContext


class PiRuntime(AgentRuntime):
    """Pi agent：用 npm 全局安装 pi-coding-agent，薄壳启动 `pi --mode rpc`。"""
    NAME = "pi"
    VERSION = "latest"

    def build_agent_layer(self, ctx: AgentContext) -> list[str]:
        return [
            "FROM node:22-slim",
            "RUN npm i -g --ignore-scripts @earendil-works/pi-coding-agent",
            "RUN pip install --no-cache-dir fastapi uvicorn",
            "COPY agent_bridge/ /opt/agent_bridge/",
            'WORKDIR /work',
            'VOLUME ["/work"]',
        ]

    def bridge_cmd(self, profile) -> list[str]:
        # 薄壳 spawn `pi --mode rpc --no-session`（MVP 单会话、不落盘）
        return [
            "python", "-m", "agent_bridge.server",
            "--agent", "pi",
            "--agent-cmd", "pi --mode rpc --no-session",
            "--port", "8000",
        ]

    def chat_endpoint(self, host_port: str) -> str:
        return f"http://localhost:{host_port}/v1/chat"

    def file_endpoint(self, host_port: str) -> str:
        return f"http://localhost:{host_port}/v1/files"

    def health_endpoint(self, host_port: str) -> str:
        return f"http://localhost:{host_port}/v1/health"

    def default_model(self) -> dict:
        return {"provider": "anthropic", "id": "claude-sonnet-4-5"}
