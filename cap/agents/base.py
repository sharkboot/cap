"""Agent 运行时契约。

Agent 只是桥梁，不负责能力安装。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class AgentContext:
    """传给 AgentRuntime.build_agent_layer() 的上下文。"""
    profile: Any    # cap.profile.Profile
    logger: Any = None


class AgentRuntime:
    NAME: str = "abstract"
    VERSION: str = "0.0.0"

    # ── 必须实现 ────────────────────────────────────────────
    def build_agent_layer(self, ctx: AgentContext) -> list[str]:
        """返回 Dockerfile 片段：安装 agent CLI、配置 agent 基础 env。

        能力安装**不在此处**——由各 Capability.build() 负责。
        """
        raise NotImplementedError

    def bridge_cmd(self, profile: Any) -> list[str]:
        """容器内启动 agent 对话薄壳的 CMD（exec form）。

        薄壳会按 profile 启动 agent CLI、spawn 各能力的 post_start、暴露 HTTP 端点。
        """
        raise NotImplementedError

    def chat_endpoint(self, host_port: str) -> str:
        """返回对话端点 URL（框架代理目标）。"""
        raise NotImplementedError

    def file_endpoint(self, host_port: str) -> str:
        raise NotImplementedError

    def health_endpoint(self, host_port: str) -> str:
        raise NotImplementedError

    # ── 可选 ────────────────────────────────────────────────
    def default_model(self) -> dict:
        """该 agent 的默认模型配置。"""
        return {}
