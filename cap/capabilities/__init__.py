"""能力注册表。

新增能力：
1. 在本目录下加 <name>.py，继承 Capability 实现 build()
2. 在下方 _REGISTRY 的类列表里加一行
3. 把该能力需要的资源文件（skills/、config/、bin/ 等）放到
   E:/LLM/cap/resources/<name>/ 目录
4. profile.yaml 里加 - name: <name>
"""
from .base import Capability, CapabilityContext, CapabilityBuildOutput
from .open_code_review import OpenCodeReviewCapability
from .llm_gateway import LlmGatewayCapability
from .doc_search import DocSearchCapability

_ALL_CAPABILITIES: list[type[Capability]] = [
    OpenCodeReviewCapability,
    LlmGatewayCapability,
    DocSearchCapability,
]

_REGISTRY: dict[str, type[Capability]] = {}
for _cls in _ALL_CAPABILITIES:
    if _cls.NAME == "abstract":
        raise RuntimeError(f"{_cls.__name__} 未设置 NAME")
    _REGISTRY[_cls.NAME] = _cls


def get_capability(name: str) -> Capability:
    """按 NAME 取能力实例；未注册时抛 KeyError，可用能力列表在 error message 里。"""
    if name not in _REGISTRY:
        available = ", ".join(sorted(_REGISTRY))
        raise KeyError(f"未注册的能力: {name!r}；可用: {available}")
    return _REGISTRY[name]()


def list_capabilities() -> list[dict]:
    """列出所有已注册能力（用于 /v1/capabilities 端点）。"""
    out = []
    for name, cls in _REGISTRY.items():
        inst = cls()
        out.append({
            "name": name,
            "version": inst.VERSION,
            "doc": (inst.__doc__ or "").strip().split("\n")[0],
        })
    return out
