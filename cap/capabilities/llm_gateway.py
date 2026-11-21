"""llm-gateway 能力：容器内起一个本地 LLM 网关 sidecar。

演示"复杂能力"的完整契约：Dockerfile 片段 + 运行时 env + 卷 + 健康检查 + post_start sidecar。
"""
from .base import Capability, CapabilityContext, CapabilityBuildOutput


class LlmGatewayCapability(Capability):
    """LLM 网关能力：安装 sidecar 脚本、声明 /data/llm-gateway 卷、
    容器启动后 spawn 网关进程、健康检查轮询。

    API key 走运行时 env（不落镜像）。
    """
    NAME = "llm-gateway"
    VERSION = "1.0.0"
    DEPENDS_ON: tuple[str, ...] = ()

    PORT = 8088

    def pre_build_check(self, ctx: CapabilityContext) -> None:
        gateway = ctx.host_root / "resources" / self.NAME / "gateway.py"
        if not gateway.is_file():
            raise FileNotFoundError(
                f"能力 {self.NAME} 缺少 gateway.py: {gateway}\n"
                f"请提供该脚本（或改为 RUN pip install ... 安装预置包）"
            )

    def build(self, ctx: CapabilityContext) -> CapabilityBuildOutput:
        res = "resources/llm-gateway"
        return CapabilityBuildOutput(
            dockerfile_lines=[
                "RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*",
                f"COPY {res}/gateway.py /opt/llm-gateway/gateway.py",
                f"COPY {res}/skills/ /root/.skills/llm-gateway/",
                f"RUN mkdir -p /data/llm-gateway",
            ],
            env={"LLM_GATEWAY_PORT": str(self.PORT)},
            volumes=["/data/llm-gateway"],
            health_probe=f"http://127.0.0.1:{self.PORT}/health",
            # 容器启动后、agent 对话前，先拉 up 网关 sidecar
            post_start=[
                "nohup python /opt/llm-gateway/gateway.py --port ${LLM_GATEWAY_PORT} "
                "--data-dir /data/llm-gateway > /data/llm-gateway/gateway.log 2>&1 &"
            ],
        )

    def runtime_env(self, ctx: CapabilityContext) -> dict[str, str]:
        # 敏感 key 从 profile.api_keys 取（框架在 docker run 时注入）
        keys = ctx.profile.api_keys or {}
        out: dict[str, str] = {}
        if keys.get("openai"):
            out["OPENAI_API_KEY"] = keys["openai"]
        if keys.get("anthropic"):
            out["ANTHROPIC_API_KEY"] = keys["anthropic"]
        return out
