# LLM-Gateway skill（占位）

当用户说"用 llm-gateway 调 <model>"时，走容器内 8088 端点：

```
curl -s http://127.0.0.1:${LLM_GATEWAY_PORT}/v1/chat/completions \
  -d '{"model": "<model>", "messages": [...]}'
```

MVP 阶段此 skill 只做占位，真实路由在 gateway.py 实现。
