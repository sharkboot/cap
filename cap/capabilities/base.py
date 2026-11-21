"""Capability 基类 + build 输出契约。

设计原则：
- 每个能力类是纯数据 + build() 纯函数：只读 CapabilityContext，输出 CapabilityBuildOutput。
- 能力之间不直接引用彼此；协作通过 profile.params 或依赖字段表达。
- 能力类**不**持有运行时状态；docker run 时需要的 env 通过 runtime_env() 单独返回。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cap.profile import Profile


@dataclass
class CapabilityContext:
    """传给 Capability.build() 的上下文。

    - host_root: 宿主机 cap 项目根目录（E:/LLM/cap），能力可从这里
      把 resources/<name>/ 下的文件 COPY 进镜像。
    - params: profile.capabilities[i].params，用户在该 profile 里
      为该能力配的运行时参数。
    """
    profile: "Profile"
    host_root: Path
    params: dict
    logger: logging.Logger = field(default_factory=logging.getLogger)


@dataclass
class CapabilityBuildOutput:
    """一个能力 build() 的产物。

    框架的 Dockerfile 生成器会：
    1. 把所有能力的 dockerfile_lines 按顺序追加到镜像
    2. 合并所有 env → 写进镜像的 ENV 层
    3. 合并所有 volumes → VOLUME 指令 + docker run 时挂载
    4. 收集 post_start → 容器启动后、agent 对话前，框架依次执行
    5. health_probe 供健康检查轮询
    """
    dockerfile_lines: list[str]
    env: dict[str, str] = field(default_factory=dict)
    volumes: list[str] = field(default_factory=list)
    health_probe: str | None = None
    post_start: list[str] = field(default_factory=list)


class Capability:
    """能力基类。子类必须设置 NAME，重写 build()。

    示例子类（open-code-review）：
        class OpenCodeReviewCapability(Capability):
            NAME = "open-code-review"
            VERSION = "1.0.0"

            def build(self, ctx: CapabilityContext) -> CapabilityBuildOutput:
                return CapabilityBuildOutput(
                    dockerfile_lines=[
                        "RUN npm i -g @alibaba-group/open-code-review",
                        "COPY resources/open-code-review/skills/ /root/.skills/open-code-review/",
                        "COPY resources/open-code-review/config.yaml /root/.config/ocr/",
                    ],
                    env={},
                    volumes=[],
                    health_probe=None,
                    post_start=[],
                )
    """

    NAME: str = "abstract"
    VERSION: str = "0.0.0"
    #: 可选：声明依赖的其它能力 NAME（框架做校验，不强制）
    DEPENDS_ON: tuple[str, ...] = ()

    def build(self, ctx: CapabilityContext) -> CapabilityBuildOutput:
        raise NotImplementedError

    def validate(self, profile: "Profile") -> None:
        """profile 级校验：该能力需要的资源/其它能力是否齐备。默认 no-op。
        校验失败应直接 raise。
        """

    def runtime_env(self, ctx: CapabilityContext) -> dict[str, str]:
        """运行时（docker run 时）注入的 env。

        用于敏感信息（API key）或需要宿主机决定的参数。
        返回值会被框架在 docker run 时通过 --env 注入，**不落镜像**。
        """
        return {}

    def pre_build_check(self, ctx: CapabilityContext) -> None:
        """build 前的本地检查（如 resources/<name>/ 目录是否存在）。默认 no-op。"""
