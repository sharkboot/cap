"""Docker 镜像生命周期：build / list / remove。

用 docker-py SDK，不需要 shell 出去。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import docker

from .dockerfile import DockerfileArtifact, render_dockerfile


@dataclass
class BuiltImage:
    tag: str
    dockerfile: str
    build_dir: Path
    artifact: DockerfileArtifact


class ImageStore:
    def __init__(self, build_root: Path):
        self.build_root = Path(build_root)
        self.build_root.mkdir(parents=True, exist_ok=True)
        self.client = docker.from_env()
        self.log = logging.getLogger("cap.image")

    def build_from_profile(self, profile, host_root: Path, image_tag: str | None = None) -> BuiltImage:
        """按 profile 生成 Dockerfile + 执行 docker build。"""
        artifact = render_dockerfile(profile, host_root, image_tag=image_tag)
        tag = artifact.image_tag
        build_dir = self.build_root / tag.replace("/", "_")
        build_dir.mkdir(parents=True, exist_ok=True)

        # Dockerfile 构建上下文：cap 项目根目录（resources/ + agent_bridge/ 都要在）
        dockerfile_path = artifact.write(build_dir)
        # 在构建上下文里放一份 Dockerfile（指向 cap 根目录的 context）
        (build_dir / "Dockerfile").write_text(artifact.dockerfile_text, encoding="utf-8")

        self.log.info("docker build %s -> %s (context=%s)", build_dir, tag, host_root)
        image, logs = self.client.images.build(
            path=str(host_root),        # 构建上下文 = cap 项目根
            dockerfile=str(dockerfile_path.relative_to(host_root)),
            tag=tag,
            rm=True,
        )
        self.log.info("built image: %s", image.tags)
        return BuiltImage(tag=tag, dockerfile=artifact.dockerfile_text,
                          build_dir=build_dir, artifact=artifact)

    def list(self, prefix: str = "cap/") -> list[dict]:
        out = []
        for img in self.client.images.list():
            for t in (img.tags or [f"<none>:id-{img.short_id}"]):
                if t.startswith(prefix):
                    out.append({"tag": t, "size_mb": round(img.short_id and img.size / 1024 / 1024, 1)})
        return out

    def remove(self, tag: str) -> None:
        self.client.images.remove(tag, force=False)
