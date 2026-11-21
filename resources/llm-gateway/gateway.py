"""llm-gateway sidecar（占位实现）。

真实实现：读 /data/llm-gateway 下缓存，暴露 OpenAI 兼容 /v1/chat/completions，
按 profile.params.models 路由到各 provider。这里只做最小骨架。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "models": os.environ.get("LLM_GATEWAY_MODELS", "")}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.send_response(404)
            return
        length = int(self.headers.get("content-length", 0))
        body = self.rfile.read(length)
        # MVP：回显 + 提示接入 provider
        resp = {"echo": json.loads(body or b"{}"), "note": "llm-gateway MVP 占位：请实现 provider 路由"}
        out = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, fmt, *args):
        print(f"[llm-gateway] {fmt % args}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8088)
    ap.add_argument("--data-dir", default="/data/llm-gateway")
    args = ap.parse_args()
    os.makedirs(args.data_dir, exist_ok=True)
    print(f"[llm-gateway] listening on :{args.port}", flush=True)
    HTTPServer(("0.0.0.0", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
