"""OpenCodeReview 运行时。

示例：按你给的命令 `npm i -g @alibaba-group/open-code-review`。
"""
from .base import AgentRuntime, AgentContext


class OpenCodeReviewRuntime(AgentRuntime):
    """OpenCodeReview agent：npm 全局安装 @alibaba-group/open-code-review。"""
    NAME = "open-code-review"
    VERSION = "latest"

    def build_agent_layer(self, ctx: AgentContext) -> list[str]:
        return [
            "FROM node:22-slim",
            "RUN npm i -g @alibaba-group/open-code-review",
            "RUN pip install --no-cache-dir fastapi uvicorn",
            "COPY agent_bridge/ /opt/agent_bridge/",
            'WORKDIR /work',
            'VOLUME ["/work"]',
        ]

    def bridge_cmd(self, profile) -> list[str]:
        return [
            "python", "-m", "agent_bridge.server",
            "--agent", "open-code-review",
            "--agent-cmd", "open-code-review --rpc",
            "--port", "8000",
        ]

    def chat_endpoint(self, host_port: str) -> str:
        return f"http://localhost:{host_port}/v1/chat"

    def file_endpoint(self, host_port: str) -> str:
        return f"http://localhost:{host_port}/v1/files"

    def health_endpoint(self, host_port: str) -> str:
        return f"http://localhost:{host_port}/v1/health"
