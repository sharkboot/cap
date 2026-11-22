"""能力注册表。

新增能力：
1. 在本目录下加 <name>.py，继承 Capability 实现 build()
2. 在下方 _REGISTRY 的类列表里加一行
3. 把该能力需要的资源文件（skills/、config/、bin/ 等）放到
   E:/LLM/cap/resources/<name>/ 目录
4. profile.yaml 里加 - name: <name>
"""
from pathlib import Path

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


def _host_root() -> Path:
    """cap 项目根目录（含 resources/）。从本文件位置推导，跨部署稳定。"""
    return Path(__file__).resolve().parents[2]


def _has_local_resources(name: str) -> bool:
    """该能力是否有本地 resources/<name>/ 可烘焙进镜像。"""
    return (_host_root() / "resources" / name).is_dir()


def _capability_meta(name: str, cls: type[Capability]) -> dict:
    """单个能力的元数据。"""
    inst = cls()
    return {
        "name": name,
        "version": inst.VERSION,
        "source": inst.SOURCE,                       # local / remote / both
        "has_local_resources": _has_local_resources(name),  # 实际可烘焙的判定
        "depends_on": list(inst.DEPENDS_ON),
        "doc": (inst.__doc__ or "").strip().split("\n")[0],
        "class": cls.__name__,
    }


def list_capabilities(source: str | None = None, keyword: str | None = None) -> list[dict]:
    """列出能力，可按来源与关键词过滤（用于 /v1/capabilities 端点）。

    source : local | remote | both | None（None = 不过滤）
    keyword: 在 name + doc 里做大小写不敏感的子串匹配；None = 不过滤
    """
    kw = keyword.strip().lower() if keyword else None
    out = []
    for name, cls in _REGISTRY.items():
        meta = _capability_meta(name, cls)
        # 来源过滤
        if source:
            want = source
            got = meta["source"]
            if want == "local" and not (got in ("local", "both") and meta["has_local_resources"]):
                continue
            if want == "remote" and got not in ("remote", "both"):
                continue
            if want == "both" and got != "both":
                continue
        # 关键词过滤
        if kw:
            haystack = (meta["name"] + " " + meta["doc"]).lower()
            if kw not in haystack:
                continue
        out.append(meta)
    return out


def get_capability_detail(name: str) -> dict:
    """单个能力完整元数据；未注册时抛 KeyError。"""
    if name not in _REGISTRY:
        available = ", ".join(sorted(_REGISTRY))
        raise KeyError(f"未注册的能力: {name!r}；可用: {available}")
    return _capability_meta(name, _REGISTRY[name])
