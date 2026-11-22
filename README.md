# cap - 能力集成框架

按"勾选能力项 + agent 桥梁"的方式生成 Dockerfile，构建镜像，起容器，代理 agent 对话与文件收发。

## License

本项目以 [MIT](LICENSE) 协议开源。

## 核心思想

- **能力项（Capability）** 是扩展点。每个能力类重写 `build()`，输出：
  - Dockerfile 片段（`RUN npm i -g ...` / `COPY ...` / `ENV ...`）
  - 运行时环境变量（`docker run` 时注入，不落镜像）
  - 需要挂载的 volume
  - 健康检查端点
  - 容器启动后执行的 `post_start` 命令（如 sidecar）
- **Agent**（Pi / OpenCodeReview / ...）只是桥梁，按 skill 调用已配置好的能力。
- 控制面（FastAPI）管 构建 → 运行 → 对话 → 文件 四个环节。

## 目录结构

```
cap/
├─ cap/                       # 控制面 Python 包
│  ├─ main.py                 # FastAPI 入口
│  ├─ profile.py              # profile.yaml 解析
│  ├─ capabilities/           # 能力基类 + 各能力子类 + 注册表
│  │  ├─ base.py              # Capability, CapabilityContext, CapabilityBuildOutput
│  │  ├─ open_code_review.py
│  │  ├─ llm_gateway.py
│  │  ├─ doc_search.py
│  │  └─ __init__.py         # 注册表
│  ├─ agents/                 # agent 运行时基类 + Pi / OCCR 子类
│  ├─ build/                  # Dockerfile 生成 + 镜像生命周期
│  ├─ runtime/                # 容器管理 + HTTP 代理
│  └─ api/                    # FastAPI 路由（images / sessions）
├─ agent_bridge/              # 容器内薄壳（spawn agent CLI + 暴露 HTTP 端点）
├─ resources/                 # 各能力需要 COPY 进镜像的资源文件
├─ profiles/                  # 用户维护的组合方案
├─ cap.toml                   # 控制面配置
└─ pyproject.toml
```

## 快速上手

```bash
# 1. 安装依赖
pip install -e .

# 2. 起控制面
python -m cap.main
# → http://localhost:8080/v1/health

# 2b. 查询已有能力（支持来源过滤 / 关键词搜索 / 单能力详情）
curl "http://localhost:8080/v1/capabilities"          # 全部
curl "http://localhost:8080/v1/capabilities?source=local"
curl "http://localhost:8080/v1/capabilities?q=llm"
curl "http://localhost:8080/v1/capabilities/doc-search"

# 3. 构建镜像（按 profile）
curl -X POST http://localhost:8080/v1/images \
  -H 'content-type: application/json' \
  -d '{"profile_path": "profiles/example-multi.yaml"}'

# 4. 起会话
curl -X POST http://localhost:8080/v1/sessions \
  -H 'content-type: application/json' \
  -d '{"profile_path": "profiles/example-multi.yaml"}'
# → 返回 session_id

# 5. 对话
curl -X POST http://localhost:8080/v1/sessions/<sid>/chat \
  -H 'content-type: application/json' \
  -d '{"message": "审查一下 main 分支最近的 diff"}'

# 6. 下载产物
curl http://localhost:8080/v1/sessions/<sid>/files
curl -OJ http://localhost:8080/v1/sessions/<sid>/files/review-main.md
```

## 新增一个能力

1. `cap/capabilities/` 下加 `<name>.py`：
   ```python
   from .base import Capability, CapabilityContext, CapabilityBuildOutput

   class MyCapability(Capability):
       NAME = "my-cap"
       VERSION = "1.0.0"

       def build(self, ctx):
           return CapabilityBuildOutput(
               dockerfile_lines=[
                   "RUN npm i -g @scope/my-cap-cli",
                   "COPY resources/my-cap/skills/ /root/.skills/my-cap/",
               ],
               env={},
               volumes=[],
               health_probe=None,
               post_start=[],
           )
   ```
2. `cap/capabilities/__init__.py` 的 `_ALL_CAPABILITIES` 列表里加一行
3. 把资源文件放到 `resources/my-cap/`
4. `profiles/*.yaml` 里加 `- name: my-cap`
5. 重新 build 镜像

框架其它代码**零改动**。

## 新增一个 agent 运行时

1. `cap/agents/` 下加 `<name>.py`，继承 `AgentRuntime`
2. 实现 `build_agent_layer()`（安装 agent CLI 的 Dockerfile 片段）
3. 实现 `bridge_cmd()`（容器内薄壳启动 agent 的 exec form）
4. `cap/agents/__init__.py` 注册

## 能力 vs Agent 的分工

| 维度 | 能力（Capability） | Agent（AgentRuntime） |
|------|-------------------|---------------------|
| 扩展频率 | 高（每个新工具/服务一个） | 低（Pi / OCCR / Aider 等少数） |
| 职责 | 装依赖、拷文件、声明 env/卷/sidecar | 装 agent CLI、声明对话协议 |
| 扩展点 | `build()` 返回 Dockerfile 片段 + 运行时元数据 | `build_agent_layer()` 返回基础层 |
| 示例 | `open-code-review`、`llm-gateway`、`doc-search` | `pi`、`open-code-review`（同名 agent） |

## MVP 边界

- 单会话 / 单容器 / 单 agent
- 文件交换走 `/work` 卷（挂载在 `cap/work/<session>/`）
- agent 对话走 Pi `--mode rpc`（JSONL stdin/stdout）
- **不做**：镜像推送、GC、多实例并发、Web UI、审计日志、provider 多实例
- **不做**：能力级热更新（改 profile 需重新 build）

## 安全

- 敏感 env（API key）只在 `docker run` 时注入，**不烧进镜像**
- 容器以 `bridge` 网络 + 资源限额 + 独立 /work 卷隔离
- 薄壳文件端点限制在 `/work` 内（resolve + startswith 检查）
