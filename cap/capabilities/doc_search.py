"""doc-search 能力：最简能力，只注入 skill 指令，不带 sidecar。

演示"纯 skill"能力的最小 build()。
"""
from .base import Capability, CapabilityContext, CapabilityBuildOutput


class DocSearchCapability(Capability):
    """文档搜索 skill：拷贝 skill 指令目录，agent 按 prompt 调用内置 grep/find。"""
    NAME = "doc-search"
    VERSION = "1.0.0"

    def build(self, ctx: CapabilityContext) -> CapabilityBuildOutput:
        res = "resources/doc-search"
        return CapabilityBuildOutput(
            dockerfile_lines=[
                f"COPY {res}/skills/ /root/.skills/doc-search/",
            ],
            env={},
            volumes=[],
            health_probe=None,
            post_start=[],
        )
