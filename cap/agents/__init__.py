"""Agent 运行时注册表。

Agent 是"桥梁"：负责把已配置好的能力暴露成 agent 可调用（通过 skill）。
框架不为每个 agent 写能力安装逻辑——能力安装由各 Capability 类负责。
Agent 只负责：
1. 声明 agent CLI 的安装方式（build_agent_layer）
2. 声明 agent 对话薄壳的启动方式（bridge_cmd）
3. 声明 agent 对话端点 / 文件端点 / 健康检查端点
"""
from .base import AgentRuntime
from .pi import PiRuntime
from .open_code_review import OpenCodeReviewRuntime

_ALL_RUNTIMES: list[type[AgentRuntime]] = [PiRuntime, OpenCodeReviewRuntime]
_REGISTRY: dict[str, type[AgentRuntime]] = {}
for _cls in _ALL_RUNTIMES:
    _REGISTRY[_cls.NAME] = _cls


def get_agent(name: str) -> AgentRuntime:
    if name not in _REGISTRY:
        raise KeyError(f"未注册的 agent: {name!r}；可用: {', '.join(sorted(_REGISTRY))}")
    return _REGISTRY[name]()


def list_agents() -> list[dict]:
    return [
        {"name": n, "doc": (cls.__doc__ or "").strip().split("\n")[0]}
        for n, cls in _REGISTRY.items()
    ]
