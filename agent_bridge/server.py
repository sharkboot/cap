"""容器内 agent 对话薄壳。

职责（单一）：
1. 启动时按 profile 执行各能力的 post_start（如 llm-gateway sidecar）
2. spawn agent CLI（Pi / OpenCodeReview...）的 RPC 模式
3. 暴露 HTTP 端点：
   POST /v1/chat           发一条用户消息 → 返回 agent 回复
   GET  /v1/chat/events    SSE 订阅 agent 事件流
   GET  /v1/files         列 /work 下产物
   GET  /v1/files/{path}  下载
   POST /v1/files/{path}  上传
   GET  /v1/health        健康检查（含各能力端点）

薄壳**不实现** agent 内部协议——只把 agent CLI 的 stdin/stdout 桥到 HTTP。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path
from queue import Queue, Empty

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import StreamingResponse

app = FastAPI(title="agent-bridge", version="0.1.0")

# 运行时全局：一个 agent 子进程 + 事件队列（MVP 单会话；多会话后续扩展）
_state = {
    "proc": None,
    "agent_name": "pi",
    "agent_cmd": None,
    "events": Queue(),
    "last_events": [],   # 最近 N 条事件，用于 /v1/chat 的同步返回
    "post_start_cmds": [],
    "health_endpoints": [],
    "workdir": Path("/work"),
}


def _spawn_agent() -> None:
    """spawn agent 子进程（Pi --mode rpc）。

    按官方协议：
    - binary pipe（不用 text=True），自己按 LF 切分，避免 readline 把
      JSON 字符串内的 U+2028/U+2029 误判为记录边界。
    - 子进程 cwd = /work（Pi 的工作目录，控制 session 分组）。
    """
    cmd = _state["agent_cmd"]
    if cmd is None:
        raise RuntimeError("agent_cmd 未设置")
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(_state["workdir"]),
        text=False,                 # binary：自己按 LF 切
        bufsize=0,
    )
    _state["proc"] = proc

    def _pump() -> None:
        assert proc.stdout is not None
        buf = b""
        for byte_iter in iter(lambda: proc.stdout.read(1), b""):
            buf += byte_iter
            if byte_iter == b"\n":
                record = buf.strip(b"\r")      # 接受可选 CRLF 前导
                buf = b""
                if not record:
                    continue
                try:
                    event = json.loads(record.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    event = {"type": "raw", "text": record.decode("utf-8", "replace")}
                _state["last_events"].append(event)
                if len(_state["last_events"]) > 200:
                    _state["last_events"].pop(0)
                _state["events"].put(event)

    threading.Thread(target=_pump, daemon=True).start()


def _send_pi_command(command: dict) -> str:
    """发一条 Pi RPC 命令，返回 command id（用于关联 response）。"""
    import uuid as _uuid
    cid = command.get("id") or f"req-{_uuid.uuid4().hex[:8]}"
    command = {**command, "id": cid}
    line = json.dumps(command, ensure_ascii=False).encode("utf-8") + b"\n"
    assert _state["proc"] is not None and _state["proc"].stdin is not None
    _state["proc"].stdin.write(line)
    _state["proc"].stdin.flush()
    return cid


def _run_post_start() -> None:
    """执行各能力 build() 声明的 post_start（如 sidecar 启动）。"""
    for cmd in _state["post_start_cmds"]:
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
            if r.returncode != 0:
                print(f"[bridge] post_start 失败: {cmd} -> {r.stderr}", flush=True)
        except Exception as e:
            print(f"[bridge] post_start 异常: {cmd} -> {e}", flush=True)


@app.on_event("startup")
def _startup() -> None:
    _run_post_start()
    _spawn_agent()


@app.on_event("shutdown")
def _shutdown() -> None:
    p = _state.get("proc")
    if p and p.poll() is None:
        p.terminate()
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()


# ── 端点 ────────────────────────────────────────────────────

@app.post("/v1/chat")
async def chat(req: Request):
    body = await req.json()
    message = body.get("message", "")
    # Pi RPC 是单会话有状态进程；session_id 仅作客户端追踪用
    session_id = body.get("session_id") or uuid.uuid4().hex[:12]

    # 按真实 Pi 协议发 prompt 命令（stdin JSONL + LF）
    req_id = _send_pi_command({"type": "prompt", "message": message})

    # 同步等回复：
    #  - 收 id==req_id 的 response，确认 disposition=started
    #  - 累积 message_update.assistantMessageEvent.text_delta
    #  - 等 agent_settled 判断一轮结束（不是 agent_end）
    deadline = time.time() + int(os.environ.get("CHAT_TIMEOUT", "600"))
    reply_parts: list[str] = []
    settled = False
    response: dict | None = None
    while time.time() < deadline:
        try:
            ev = _state["events"].get(timeout=1)
        except Empty:
            if reply_parts and settled:
                break
            continue
        t = ev.get("type")
        if t == "response" and ev.get("id") == req_id:
            response = ev
            if ev.get("success") is False:
                break
        elif t == "message_update":
            delta_ev = ev.get("assistantMessageEvent") or {}
            if delta_ev.get("type") == "text_delta":
                reply_parts.append(delta_ev.get("delta", ""))
        elif t == "agent_settled":
            settled = True
            break

    reply = "".join(reply_parts)
    return {
        "session_id": session_id,
        "reply": reply,
        "settled": settled,
        "prompt_response": response,
        "events": _state["last_events"][-20:],
    }


@app.get("/v1/chat/events")
async def stream_events(session_id: str | None = None):
    async def gen():
        while True:
            try:
                ev = _state["events"].get(timeout=15)
            except Empty:
                yield ": keepalive\n\n"
                continue
            payload = json.dumps(ev, ensure_ascii=False)
            yield f"data: {payload}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/v1/files")
async def list_files():
    root = _state["workdir"]
    out = []
    for p in root.rglob("*"):
        if p.is_file():
            rel = p.relative_to(root).as_posix()
            out.append({"path": rel, "size": p.stat().st_size, "mtime": p.stat().st_mtime})
    return out


@app.get("/v1/files/{path:path}")
async def download_file(path: str):
    fp = (_state["workdir"] / path).resolve()
    if not str(fp).startswith(str(_state["workdir"].resolve())):
        raise HTTPException(403, "越界路径")
    if not fp.is_file():
        raise HTTPException(404, f"文件不存在: {path}")
    return fp.read_bytes()


@app.post("/v1/files/{path:path}")
async def upload_file(path: str, file: UploadFile = File(...)):
    content = await file.read()
    fp = (_state["workdir"] / path)
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_bytes(content)
    return {"path": path, "size": len(content)}


@app.get("/v1/health")
async def health():
    """agent 薄壳自身健康 + 各能力健康端点（如有）都通过才算 ok。"""
    out: dict = {"agent": "ok", "cap": {}}
    for ep in _state["health_endpoints"]:
        try:
            r = httpx.get(ep, timeout=5)
            out["cap"][ep] = "ok" if r.status_code == 200 else f"HTTP {r.status_code}"
        except Exception as e:
            out["cap"][ep] = f"err: {e}"
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", required=True)
    ap.add_argument("--agent-cmd", required=True)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--post-start", default="[]", help="JSON array of shell commands")
    ap.add_argument("--health-endpoints", default="[]", help="JSON array of URLs")
    args = ap.parse_args()

    _state["agent_name"] = args.agent
    _state["agent_cmd"] = args.agent_cmd.split()
    _state["post_start_cmds"] = json.loads(args.post_start)
    _state["health_endpoints"] = json.loads(args.health_endpoints)

    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
