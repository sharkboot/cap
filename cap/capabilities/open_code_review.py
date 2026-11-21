"""open-code-review 能力。

示例：一个需要 npm 全局安装 CLI、拷贝 skill 文件和配置的能力。
"""
from .base import Capability, CapabilityContext, CapabilityBuildOutput


class OpenCodeReviewCapability(Capability):
    """代码审查能力：全局安装 @alibaba-group/open-code-review，注入审查 skill。"""
    NAME = "open-code-review"
    VERSION = "1.0.0"

    def pre_build_check(self, ctx: CapabilityContext) -> None:
        # 本地资源目录（skills/ + config）若缺失，构建时 COPY 会失败
        res_dir = ctx.host_root / "resources" / self.NAME
        if not res_dir.is_dir():
            raise FileNotFoundError(
                f"能力 {self.NAME} 的资源目录缺失: {res_dir}\n"
                f"请创建该目录并放入 skills/ 与 config.yaml"
            )

    def build(self, ctx: CapabilityContext) -> CapabilityBuildOutput:
        res = "resources/open-code-review"
        return CapabilityBuildOutput(
            dockerfile_lines=[
                "RUN npm i -g @alibaba-group/open-code-review",
                f"COPY {res}/skills/ /root/.skills/open-code-review/",
                f"COPY {res}/config.yaml /root/.config/ocr/config.yaml",
            ],
            env={},
            volumes=[],
            health_probe=None,
            post_start=[],
        )

    def runtime_env(self, ctx: CapabilityContext) -> dict[str, str]:
        # 默认审查分支，可由 profile.params 覆盖
        return {"OCR_DEFAULT_BRANCH": str(ctx.params.get("target_branch", "main"))}
