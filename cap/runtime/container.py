"""容器生命周期：创建 / 运行 / 健康检查 / 销毁。"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

import docker
from docker.models.containers import Container

from cap.build.dockerfile import DockerfileArtifact
from cap.profile import Profile


@dataclass
class RunningContainer:
    container: Container
    workdir: Path
    host_port: str
    artifact: DockerfileArtifact


class ContainerManager:
    def __init__(self, work_root: Path, log_prefix: str = "cap",
                 usage_recorder: callable | None = None):
        """usage_recorder: 容器启动某镜像时回调 (image_tag) → 刷新其"最近使用"。
        不传则不记录（GC 会退化为按镜像 created 时间兜底）。"""
        self.work_root = Path(work_root)
        self.work_root.mkdir(parents=True, exist_ok=True)
        self.client = docker.from_env()
        self.log = logging.getLogger(f"{log_prefix}.container")
        self._prefix = log_prefix
        self._usage_recorder = usage_recorder

    def start(
        self,
        artifact: DockerfileArtifact,
        profile: Profile,
        session_name: str | None = None,
    ) -> RunningContainer:
        """起容器：挂 /work、注入运行时 env、映射薄壳端口。

        健康检查：先起容器，轮询 agent 薄壳 /v1/health；
        对 artifact.post_start 里声明的 sidecar 健康端点也一并轮询。
        """
        name = f"{self._prefix}-{session_name or time.strftime('%H%M%S')}"
        workdir = self.work_root / name
        workdir.mkdir(parents=True, exist_ok=True)

        # 挂 /work 宿主机目录 + 各能力声明的 volume
        binds = {str(workdir): {"bind": "/work", "mode": "rw"}}
        for v in artifact.runtime_volumes:
            host = workdir / v.lstrip("/")
            host.mkdir(parents=True, exist_ok=True)
            binds[str(host)] = {"bind": v, "mode": "rw"}

        env = dict(artifact.runtime_env)
        env.update({k: str(v) for k, v in (profile.resources.get("env") or {}).items()})

        self.log.info("start container %s binds=%s env_keys=%s", name, list(binds), list(env))
        container = self.client.containers.run(
            image=artifact.image_tag,
            name=name,
            detach=True,
            volumes=binds,
            environment=env,
            ports={"8000/tcp": None},
            working_dir="/work",
            # 容器隔离 + 安全边界
            read_only=False,
            mem_limit=profile.resources.get("mem", "2g"),
            nano_cpus=int(float(profile.resources.get("cpu", 1.0)) * 1e9),
            network_mode="bridge",
            auto_remove=False,
        )
        running = RunningContainer(
            container=container,
            workdir=workdir,
            host_port=container.ports["8000/tcp"][0]["HostPort"],
            artifact=artifact,
        )
        # 记录镜像"最近使用"（供 GC 的 LRU/TTL 基准）
        if self._usage_recorder is not None:
            try:
                self._usage_recorder(artifact.image_tag)
            except Exception as e:
                self.log.warning("usage 记录失败（不影响运行）: %s", e)
        self._wait_healthy(running)
        return running

    def _wait_healthy(self, running: RunningContainer, timeout: int = 60) -> None:
        """轮询 agent 薄壳 /v1/health 与所有能力健康端点。"""
        import httpx
        base = f"http://localhost:{running.host_port}"
        endpoints = [f"{base}/v1/health"] + list(running.artifact.health_probes)
        deadline = time.time() + timeout
        last_err = None
        while time.time() < deadline:
            ok = True
            for ep in endpoints:
                try:
                    r = httpx.get(ep, timeout=5)
                    if r.status_code != 200:
                        ok = False
                        last_err = f"{ep} -> {r.status_code}"
                except Exception as e:
                    ok = False
                    last_err = f"{ep} -> {e}"
            if ok:
                return
            time.sleep(1)
        raise TimeoutError(f"容器健康检查超时: {last_err}")

    def stop(self, running: RunningContainer, remove: bool = True) -> None:
        try:
            running.container.stop(timeout=10)
        finally:
            if remove:
                running.container.remove(force=True)
            self.log.info("stopped container %s", running.container.name)
