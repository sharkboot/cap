"""cap - 能力集成框架。

架构要点：
- 能力项 (Capability) 是扩展点：每个能力类重写 build()，输出 Dockerfile 片段 + 环境变量 + 卷 + 健康检查。
- Agent 只是桥梁：薄壳启动 agent CLI (Pi / OpenCodeReview ...)，按 skill 调用已配置好的能力。
- 控制面：FastAPI 服务，管理 构建 / 运行 / 对话 / 文件 四个环节。
"""

__version__ = "0.1.0"
