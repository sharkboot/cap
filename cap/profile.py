"""Profile 解析与校验。

Profile = 一份"组合方案"：agent 选哪个 + 勾选哪些能力 + 每个能力的 params
+ 运行时 api key 引用。

YAML 示例（profiles/example.yaml）：
    agent: pi
    capabilities:
      - name: open-code-review
        params:
          target_branch: main
      - name: llm-gateway
        params:
          models: [sonnet-4-5]
    api_keys:
      openai: ${OPENAI_KEY}
      anthropic: ${ANTHROPIC_KEY}
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class CapabilityRef:
    name: str
    params: dict = field(default_factory=dict)


@dataclass
class Profile:
    agent: str                       # 对应 cap/agents/ 里某个 agent 运行时的 NAME
    capabilities: list[CapabilityRef]
    api_keys: dict[str, str] = field(default_factory=dict)
    model: dict[str, Any] = field(default_factory=dict)     # provider / id / thinking 等
    resources: dict[str, Any] = field(default_factory=dict)  # cpu / mem / 超时
    raw: dict[str, Any] = field(default_factory=dict)       # 保留原始 yaml

    def capability_names(self) -> list[str]:
        return [c.name for c in self.capabilities]

    @classmethod
    def from_yaml(cls, path: Path) -> "Profile":
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls.parse(data)

    @classmethod
    def parse(cls, data: dict) -> "Profile":
        if not data.get("agent"):
            raise ValueError("profile 缺少 agent 字段")
        if not data.get("capabilities"):
            raise ValueError("profile 缺少 capabilities 字段（至少一个）")

        caps = []
        for item in data["capabilities"]:
            if isinstance(item, str):
                caps.append(CapabilityRef(name=item))
            elif isinstance(item, dict) and "name" in item:
                caps.append(CapabilityRef(name=item["name"], params=item.get("params", {})))
            else:
                raise ValueError(f"无法解析 capability 条目: {item!r}")

        return cls(
            agent=data["agent"],
            capabilities=caps,
            api_keys=data.get("api_keys", {}) or {},
            model=data.get("model", {}) or {},
            resources=data.get("resources", {}) or {},
            raw=data,
        )

    def validate_capabilities(self, registry: dict[str, type]) -> None:
        """按注册表校验能力名 + DEPENDS_ON。"""
        known = set(registry)
        for ref in self.capabilities:
            if ref.name not in known:
                raise ValueError(f"能力 {ref.name!r} 未在注册表中")
            cls = registry[ref.name]
            for dep in cls.DEPENDS_ON:
                if dep not in known or dep not in {r.name for r in self.capabilities}:
                    raise ValueError(f"能力 {ref.name!r} 依赖 {dep!r} 但未在 profile 中选中")
