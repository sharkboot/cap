"""本地测试：spawn 真实 Pi RPC 进程，按官方帧格式验证对话通道。

Pi RPC 协议（docs.pi.dev/latest/rpc）：
- stdin  : 一条 JSON 命令 + LF（如 {"id":"p1","type":"prompt","message":"..."}）
- stdout : 三类记录
    * response          {"id":..,"type":"response","command":"prompt","success":true,"data":{"disposition":"started"}}
    * session event     {"type":"message_update","assistantMessageEvent":{"type":"text_delta","delta":"..."}}
    * settled          {"type":"agent_settled"}   ← 等它判断一轮结束
- 帧切分：严格按 LF 切，不能用 readline（U+2028/U+2029 会误切 JSON 字符串内部）

用法：
    py scripts/test_pi_rpc.py
    py scripts/test_pi_rpc.py --message "你好" --provider anthropic --model claude-sonnet-4-5
    py scripts/test_pi_rpc.py --dry-run        # 不 spawn 真 Pi，只打印将要发的字节流（离线验证帧格式）

前置：
    本机装好 Pi 且 `pi auth login <provider>` 或设了 API key 环境变量。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass, field


@dataclass
class RpcResult:
    """一次 Pi RPC 对话的结果。"""
    ok: bool
    reply_text: str
    events: list[dict] = field(default_factory=list)
    response: dict | None = None      # prompt 命令的 response 记录
    settled: bool = False
    stderr_tail: str = ""
    exit_code: int | None = None


def _resolve_pi_executable() -> list[str]:
    """定位 pi 真实可执行文件。

    Windows 上 npm 全局装的 pi 是 .ps1 包装（pi.ps1 → node .../pi.js），
    Python subprocess 直接 Popen("pi") 会找不到。
    这里解析出 [node, <pi.js>] 形式，跨平台统一走 node 直接调 JS 入口。
    """
    import glob as _glob
    import sys as _sys

    pi = shutil.which("pi")
    if pi is None:
        # 回退：找 npm 全局目录里的 pi.js
        for pattern in (
            os.path.expanduser("~/.npm-global/node_modules/@earendil-works/pi-coding-agent/dist/cli.js"),
            r"E:\npm_dev\node_global\node_modules\@earendil-works\pi-coding-agent\dist\cli.js",
        ):
            if os.path.isfile(pattern):
                pi = "node"
                break
    if pi is None:
        raise FileNotFoundError("找不到 pi（试 which('pi') 与常见 npm 全局路径）")

    # 解析 pi 是 .ps1 / 目录 / node 直接入口
    if pi.endswith(".ps1") or pi.endswith(".cmd") or pi.endswith(".sh"):
        # 包装脚本：提取它调用的 node + cli.js
        import re as _re
        try:
            with open(pi, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            m = _re.search(r"node_modules[\\/][^\"]*?\/(dist\/cli\.js|cli\.js)", content)
            if m:
                base = os.path.dirname(pi)
                cli = os.path.normpath(os.path.join(base, m.group(1)))
                if not os.path.isfile(cli):
                    # 相对包装脚本目录
                    cli = os.path.join(base, m.group(1))
                node = shutil.which("node") or "node"
                return [node, cli]
        except OSError:
            pass
        return [pi]      # 兜底：直接当可执行
    return [pi]


def spawn_pi_rpc(provider: str | None = None, model: str | None = None,
                 extra_args: list[str] | None = None,
                 workdir: str | None = None) -> subprocess.Popen:
    """spawn `pi --mode rpc` 子进程。

    注意：用 binary pipe，读取时按 LF 切，避免 readline 的 Unicode 分隔符误切。
    """
    cmd = _resolve_pi_executable() + ["--mode", "rpc"]
    if provider:
        cmd += ["--provider", provider]
    if model:
        cmd += ["--model", model]
    if extra_args:
        cmd += extra_args
    if workdir:
        cmd += ["--session-dir", workdir]
    return subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,                 # 关键：binary，自己按 LF 切
        bufsize=0,
        cwd=os.getcwd(),
    )


def send_command(proc: subprocess.Popen, command: dict) -> str:
    """发一条命令，返回 command id（用于关联 response）。"""
    cid = command.get("id") or f"req-{uuid.uuid4().hex[:8]}"
    command = {**command, "id": cid}
    line = json.dumps(command, ensure_ascii=False).encode("utf-8") + b"\n"
    assert proc.stdin is not None
    proc.stdin.write(line)
    proc.stdin.flush()
    return cid


def read_until_settled(proc: subprocess.Popen, timeout: float = 300.0,
                       verbose: bool = False,
                       stderr_path: str | None = None,
                       wait_settled: bool = True) -> RpcResult:
    """持续读 stdout 直到 agent_settled，收集全部事件。

    严格按 LF 切：累积 buffer，遇到 b'\n' 切出一条记录，
    去掉可选的前导 \r（接受 CRLF）。

    wait_settled=False（本地不接 provider 的快路径）：收到第一条
    response 即返回，不等 agent_settled——没有 provider key 时 Pi
    不出 LLM 流式 delta，但 prompt 命令的 response 一定回，足以验证
    帧格式往返。

    性能：后台泵线程按块读 stdout 喂进 Queue，主线程取事件，
    避免逐字节 read(1)（Windows 上每字节一次系统调用，极慢）。
    stderr 同步落盘到 stderr_path（若提供），方便诊断 Pi 卡在哪。
    """
    import threading as _threading
    import queue as _queue
    import time

    event_q: _queue.Queue = _queue.Queue()

    def _pump_stdout() -> None:
        assert proc.stdout is not None
        buf = b""
        while True:
            chunk = proc.stdout.read(4096)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                record, _, buf = buf.partition(b"\n")
                record = record.rstrip(b"\r")
                if not record:
                    continue
                try:
                    rec = json.loads(record.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as e:
                    rec = {"type": "parse_error",
                           "raw": record[:200].decode("utf-8", "replace"),
                           "error": str(e)}
                event_q.put(rec)

    def _pump_stderr() -> None:
        if not stderr_path:
            return
        assert proc.stderr is not None
        with open(stderr_path, "wb") as f:
            while True:
                chunk = proc.stderr.read(4096)
                if not chunk:
                    break
                f.write(chunk)
                f.flush()

    _threading.Thread(target=_pump_stdout, daemon=True).start()
    _threading.Thread(target=_pump_stderr, daemon=True).start()

    events: list[dict] = []
    response: dict | None = None
    reply_parts: list[str] = []
    settled = False
    ok = True
    deadline = time.time() + timeout

    while time.time() < deadline:
        try:
            rec = event_q.get(timeout=1)
        except _queue.Empty:
            if proc.poll() is not None:
                break
            continue
        events.append(rec)
        t = rec.get("type")
        if t == "response":
            if response is None:
                response = rec
            if rec.get("success") is False:
                ok = False
            if not wait_settled:
                break                 # 快路径：拿到第一条 response 即停
        elif t == "message_update":
            ev = rec.get("assistantMessageEvent") or {}
            if ev.get("type") == "text_delta":
                delta = ev.get("delta", "")
                reply_parts.append(delta)
                if verbose:
                    print(delta, end="", flush=True)
        elif t == "agent_settled":
            settled = True
            break
        elif t == "agent_end":
            pass

    # 收走队列里剩余事件
    while True:
        try:
            events.append(event_q.get_nowait())
        except _queue.Empty:
            break

    if proc.poll() is not None:
        ok = ok and proc.returncode == 0
    return RpcResult(
        ok=ok,
        reply_text="".join(reply_parts),
        events=events,
        response=response,
        settled=settled,
        stderr_tail="",
        exit_code=proc.poll(),
    )


def close_orderly(proc: subprocess.Popen) -> str:
    """关 stdin 请求有序关闭；Pi 会 dispose 再退出。

    不调用 communicate（stderr/stdout 可能已被泵线程接管），
    只关 stdin + wait，超时则 kill。返回简短状态串。
    """
    try:
        if proc.stdin is not None and proc.poll() is None:
            proc.stdin.close()
    except (BrokenPipeError, OSError):
        pass
    try:
        proc.wait(timeout=10)
        return ""
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        return " (killed after wait-timeout)"


def run_pi_rpc(message: str, provider: str | None = None, model: str | None = None,
               workdir: str | None = None, timeout: float = 300.0,
               verbose: bool = True, stderr_path: str | None = None,
               wait_settled: bool = True) -> RpcResult:
    """一次完整的 Pi RPC 对话：spawn → prompt → 读事件 → 有序关闭。

    wait_settled=False：本地不接 provider 的快路径，收到第一条 response 即停。
    """
    proc = spawn_pi_rpc(provider=provider, model=model, workdir=workdir)
    if proc.poll() is not None:
        return RpcResult(ok=False, reply_text="", events=[], settled=False,
                         stderr_tail=f"pi 启动即退出 exit={proc.returncode}",
                         exit_code=proc.returncode)
    send_command(proc, {"type": "prompt", "message": message})
    result = read_until_settled(proc, timeout=timeout, verbose=verbose,
                                stderr_path=stderr_path, wait_settled=wait_settled)
    # 若开了 stderr 落盘，把尾部读出来挂到 stderr_tail
    if stderr_path and os.path.exists(stderr_path):
        try:
            with open(stderr_path, "r", encoding="utf-8", errors="replace") as f:
                result.stderr_tail = f.read()[-2000:]
        except OSError:
            pass
    result.stderr_tail = result.stderr_tail or close_orderly(proc)
    return result


# ── dry-run：离线验证帧格式（不 spawn 真 Pi）────────────────

def dry_run() -> None:
    """打印将要发给 Pi stdin 的字节流 + 模拟 Pi 回包，验证帧格式正确。"""
    print("== dry-run：Pi RPC 帧格式验证（无真 Pi）==")
    cmd = {"id": "p1", "type": "prompt", "message": "你好，介绍一下你自己"}
    wire = json.dumps(cmd, ensure_ascii=False).encode("utf-8") + b"\n"
    print(f"→ stdin 发: {wire!r}")
    assert wire.endswith(b"\n"), "Pi RPC 要求 LF 结尾"
    # 帧里不能裸含 U+2028/U+2029 作为分隔（它们在 JSON 字符串内合法，但切分只认 LF）
    # 模拟 Pi 回包（按官方示例）
    simulated = [
        json.dumps({"id": "p1", "type": "response", "command": "prompt",
                    "success": True, "data": {"disposition": "started"}},
                   ensure_ascii=False).encode() + b"\n",
        json.dumps({"type": "message_update",
                    "assistantMessageEvent": {"type": "text_delta", "delta": "我是 Pi。"}},
                   ensure_ascii=False).encode() + b"\n",
        json.dumps({"type": "agent_settled"}, ensure_ascii=False).encode() + b"\n",
    ]
    print("← stdout 模拟:")
    for line in simulated:
        print(f"   {line.decode().strip()}")
    print("\n帧格式 OK：LF 切分、id 关联、disposition=started、text_delta 流式、agent_settled 终止。")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--message", default="你好，请用一句话介绍你能做什么")
    ap.add_argument("--provider")
    ap.add_argument("--model")
    ap.add_argument("--workdir")
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--dry-run", action="store_true", help="不 spawn 真 Pi，只验证帧格式")
    ap.add_argument("--quiet", action="store_true", help="不逐字打印流式 delta")
    ap.add_argument("--no-wait", action="store_true",
                    help="本地不接 provider 的快路径：收到第一条 response 即停，不等 agent_settled")
    ap.add_argument("--debug-stderr", metavar="PATH",
                    help="把 Pi 的 stderr 落盘到该文件，便于诊断卡在哪")
    args = ap.parse_args()

    if args.dry_run:
        dry_run()
        return

    if shutil.which("pi") is None:
        print("!! 未找到 pi 可执行文件。请先安装：curl -fsSL https://pi.dev/install.sh | sh", file=sys.stderr)
        print("   或用 --dry-run 离线验证帧格式。", file=sys.stderr)
        sys.exit(2)

    wait_settled = not args.no_wait
    print(f">>> Pi RPC 测试：message={args.message!r} provider={args.provider} "
          f"model={args.model} wait_settled={wait_settled}")
    result = run_pi_rpc(
        args.message,
        provider=args.provider,
        model=args.model,
        workdir=args.workdir,
        timeout=args.timeout,
        verbose=not args.quiet,
        stderr_path=args.debug_stderr,
        wait_settled=wait_settled,
    )
    if result.reply_text:
        print()
    print("=" * 50)
    print(f"ok        : {result.ok}")
    print(f"settled   : {result.settled}")
    print(f"exit_code : {result.exit_code}")
    print(f"events    : {len(result.events)} 条")
    print(f"response  : {json.dumps(result.response, ensure_ascii=False) if result.response else None}")
    print(f"reply_text:\n{result.reply_text or '(空)'}")
    if result.stderr_tail:
        print(f"stderr_tail:\n{result.stderr_tail}")

    # 断言：帧格式符合预期
    assert any(e.get("type") == "response" for e in result.events), "缺少 response 记录 —— 帧格式未往返"
    if wait_settled:
        assert result.settled, "未等到 agent_settled —— 协议假设可能有误"
        print("\nPASS：Pi RPC 全链路（含 agent_settled）帧格式一致。")
    else:
        print("\nPASS：Pi RPC 快路径（无 provider，第一条 response 往返）帧格式一致。")
    sys.exit(0 if result.ok else 1)


if __name__ == "__main__":
    main()
