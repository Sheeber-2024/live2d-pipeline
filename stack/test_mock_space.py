#!/usr/bin/env python3
"""模拟 see-through 的 Gradio Space，用于离线验证客户端协议实现。

支持模拟：正常流程、配额耗尽(null)、401 认证失败、SSE 进度事件、文件下载。
"""
import json, os, sys, threading, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8799
MODE = os.environ.get("MOCK_MODE", "ok")           # ok | quota | auth | sse_progress
EXPECT_TOKEN = os.environ.get("MOCK_TOKEN", "")    # 非空则要求 Bearer
FIXTURE = os.environ.get("MOCK_FIXTURE", "")       # 返回的 PSD 路径

STORE = {}
_lock = threading.Lock()


class H(BaseHTTPRequestHandler):
    server_version = "MockGradio/1.0"
    def log_message(self, *a): pass

    def _send(self, code, body: bytes, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth_ok(self) -> bool:
        if not EXPECT_TOKEN:
            return True
        got = self.headers.get("Authorization", "")
        return got == f"Bearer {EXPECT_TOKEN}"

    def do_POST(self):
        p = urlparse(self.path).path
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""

        if p == "/gradio_api/upload":
            if not self._auth_ok():
                return self._send(401, json.dumps({"error": "Authentication failed"}).encode())
            fid = uuid.uuid4().hex
            STORE[fid] = {"uploaded": len(body)}
            return self._send(200, json.dumps([f"/tmp/gradio/{fid}/input.png"]).encode())

        if p == "/gradio_api/call/inference":
            if not self._auth_ok():
                return self._send(401, json.dumps(
                    {"error": {"message": "Authentication failed, please make sure that a valid ModelScope token is supplied."}}).encode())
            if MODE == "auth":
                return self._send(401, json.dumps({"error": {"message": "Authentication failed"}}).encode())
            if MODE == "quota":
                # Gradio 真实行为：HTTP 200 + event_id，错误在 SSE 里出现
                return self._send(200, json.dumps({"event_id": "quota-ev"}).encode())
            return self._send(200, json.dumps({"event_id": "ev-" + uuid.uuid4().hex}).encode())

        return self._send(404, b'{"error":"not found"}')

    def do_GET(self):
        u = urlparse(self.path)
        p = u.path

        if p == "/gradio_api/info":
            if not self._auth_ok():
                return self._send(401, json.dumps({"error": {"message": "Authentication failed"}}).encode())
            return self._send(200, json.dumps({"named_endpoints": {
                "/inference": {"parameters": [
                    {"parameter_name": "image", "python_type": {"type": "filepath"}},
                    {"parameter_name": "resolution", "python_type": {"type": "float"}},
                    {"parameter_name": "seed", "python_type": {"type": "float"}},
                    {"parameter_name": "tblr_split", "python_type": {"type": "bool"}}]}}}).encode())

        if p.startswith("/gradio_api/call/inference/"):
            if p.endswith("quota-ev"):
                body = ("event: error\n"
                        'data: "You have exceeded your GPU quota"\n\n')
                return self._send(200, body.encode(), "text/event-stream")
            def gen():
                out = []
                if MODE == "sse_progress":
                    out.append('event: progress\ndata: {"title":"layerdiff","progress":0.5}\n\n')
                out.append('event: complete\ndata: ' +
                           json.dumps([{"path": "/tmp/gradio/mockout/seethrough_output.psd",
                                        "meta": {"_type": "gradio.FileData"}}]) + '\n\n')
                return "".join(out)
            return self._send(200, gen().encode(), "text/event-stream")

        if p.startswith("/gradio_api/file="):
            target = p.split("=", 1)[1]
            if FIXTURE and os.path.isfile(FIXTURE):
                with open(FIXTURE, "rb") as f:
                    return self._send(200, f.read(), "application/octet-stream")
            return self._send(404, b"missing fixture")

        return self._send(404, b'{"error":"not found"}')


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    print(f"mock space on {PORT} mode={MODE} token={'yes' if EXPECT_TOKEN else 'no'}", flush=True)
    srv.serve_forever()
