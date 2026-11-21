"""冒烟：按 profile 渲染 Dockerfile + 打印运行时 env / post_start / 健康端点。

不执行 docker build（避免无 Docker 环境失败）。
用法：py scripts/smoke_dockerfile.py [profile_path]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cap.profile import Profile
from cap.build.dockerfile import render_dockerfile


def main() -> None:
    profile_arg = sys.argv[1] if len(sys.argv) > 1 else "profiles/example-multi.yaml"
    profile_path = ROOT / profile_arg
    profile = Profile.from_yaml(profile_path)
    artifact = render_dockerfile(profile, ROOT)

    print("=" * 60)
    print(f"image_tag    : {artifact.image_tag}")
    print(f"workdir root : {ROOT}")
    print("=" * 60)
    print(artifact.dockerfile_text)
    print("=" * 60)
    print("runtime_env  :", json.dumps(artifact.runtime_env, ensure_ascii=False))
    print("runtime_vols :", artifact.runtime_volumes)
    print("post_start   :", json.dumps(artifact.post_start, ensure_ascii=False))
    print("health_probes:", artifact.health_probes)


if __name__ == "__main__":
    main()
