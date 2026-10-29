#!/usr/bin/env python3
"""验证"真实模型 HTTP 接入路径"可跑通。

无需真实 API key：启动本地 OpenAI 兼容 mock server，让 ModelClient 走 live 分支，
确认请求/响应契约正确。证明 live 路径不是纸面代码。

运行：python examples/verify_live_path.py
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

RECEIVED = {}


class MockOpenAI(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        RECEIVED["payload"] = json.loads(self.rfile.read(n).decode())
        RECEIVED["auth"] = self.headers.get("Authorization")
        action = {"action": "search_materials", "args": {"query": "支持字幕"},
                  "reason": "先检索素材"}
        resp = {"choices": [{"message": {"content": json.dumps(action)}}]}
        data = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def main():
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
              "all_proxy", "ALL_PROXY"):
        os.environ.pop(k, None)

    srv = HTTPServer(("127.0.0.1", 0), MockOpenAI)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    os.environ["MODEL_BASE_URL"] = f"http://127.0.0.1:{port}/v1"
    os.environ["MODEL_API_KEY"] = "test-key"
    os.environ["MODEL_NAME"] = "mock-model"

    from src.agent.llm import ModelClient
    client = ModelClient()
    print("mode:", client.mode)
    assert client.offline is False, "应识别为 live 模式"

    action = client.decide("system", [{"role": "user", "content": "hi"}])
    print("parsed action:", action)
    print("server received auth:", RECEIVED.get("auth"))
    print("server received model:", RECEIVED["payload"]["model"])

    assert action["action"] == "search_materials"
    assert RECEIVED["auth"] == "Bearer test-key"
    assert RECEIVED["payload"]["model"] == "mock-model"
    srv.shutdown()
    print("\n[OK] live 模型接入路径契约验证通过（payload/鉴权/解析均正确）")


if __name__ == "__main__":
    main()
