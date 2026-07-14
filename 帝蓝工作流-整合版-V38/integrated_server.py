#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
帝蓝工作流整合版 V44
- 统一入口： http://127.0.0.1:8787/material、/image 或 /video
- 素材、图片、视频工具作为三个独立子服务运行，核心代码互不合并。
"""
import atexit
import base64
import io
import json
import http.client
import http.server
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
MATERIAL_DIR = ROOT / "tools" / "material"
IMAGE_DIR = ROOT / "tools" / "image"
VIDEO_DIR = ROOT / "tools" / "video"
MATERIAL_PORT = int(os.environ.get("DILAN_MATERIAL_PORT", "8790"))
IMAGE_PORT = int(os.environ.get("DILAN_IMAGE_PORT", "8788"))
VIDEO_PORT = int(os.environ.get("DILAN_VIDEO_PORT", "8789"))
GATEWAY_PORT = int(os.environ.get("DILAN_GATEWAY_PORT", "8787"))

CHILDREN = []

HOP_BY_HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade"
}


def is_port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.25)
        return s.connect_ex(("127.0.0.1", port)) == 0


def wait_port(port: int, name: str, timeout: float = 8.0):
    start = time.time()
    while time.time() - start < timeout:
        if is_port_open(port):
            return True
        time.sleep(0.15)
    raise RuntimeError(f"{name} 子服务启动超时，端口 {port} 未监听。")


def start_child(name: str, tool_dir: Path, port: int):
    if is_port_open(port):
        print(f"[{name}] 端口 {port} 已被占用，将直接尝试代理到该端口。")
        return None
    env = os.environ.copy()
    env["DILAN_CHILD_PORT"] = str(port)
    env["PYTHONUNBUFFERED"] = "1"
    script = tool_dir / "image_server.py"
    if not script.exists():
        raise FileNotFoundError(f"找不到 {name} 子服务文件：{script}")
    print(f"[{name}] 启动子服务：http://127.0.0.1:{port}")
    proc = subprocess.Popen([sys.executable, str(script)], cwd=str(tool_dir), env=env)
    CHILDREN.append(proc)
    wait_port(port, name)
    return proc


def cleanup_children():
    for proc in CHILDREN:
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
    for proc in CHILDREN:
        if proc and proc.poll() is None:
            try:
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


def pick_tool_from_referer(headers) -> str:
    ref = headers.get("Referer") or headers.get("referer") or ""
    path = urlsplit(ref).path.lower()
    if path.startswith("/material"):
        return "material"
    if path.startswith("/video"):
        return "video"
    if path.startswith("/image"):
        return "image"
    return "material"


def upstream_for_tool(tool: str):
    if tool == "material":
        return MATERIAL_PORT
    if tool == "video":
        return VIDEO_PORT
    return IMAGE_PORT


def decode_import_data_url(data_url: str) -> bytes:
    text = str(data_url or "").strip()
    if text.startswith("data:") and "," in text:
        text = text.split(",", 1)[1]
    return base64.b64decode(text)


def read_zip_json(zf: zipfile.ZipFile, name: str):
    try:
        with zf.open(name, "r") as fh:
            return json.loads(fh.read().decode("utf-8"))
    except Exception:
        return None


def sniff_import_package_module(body: bytes) -> str:
    """Read the package metadata and decide which child module should receive it."""
    try:
        payload = json.loads((body or b"{}").decode("utf-8"))
        raw = decode_import_data_url(payload.get("dataUrl") or payload.get("data_url") or "")
        if not raw.startswith(b"PK"):
            return ""
        with zipfile.ZipFile(io.BytesIO(raw), "r") as zf:
            names = set(zf.namelist())
            manifest = read_zip_json(zf, "manifest.json") or {}
            module_type = str(manifest.get("module_type") or manifest.get("tool_module") or "").strip()
            if not module_type:
                project_json = manifest.get("project_json")
                if not project_json or project_json not in names:
                    candidates = [n for n in names if n.endswith("/project.json") or n == "project.json"]
                    project_json = candidates[0] if candidates else ""
                if project_json:
                    project_data = read_zip_json(zf, project_json) or {}
                    module_type = str(project_data.get("module_type") or project_data.get("project_module_type") or "").strip()
            return module_type if module_type in {"material", "image", "video"} else ""
    except Exception as e:
        print(f"[gateway] 工程包模块识别失败，将按当前页面模块导入：{e}")
        return ""


def set_user_env_var_windows(name: str, value: str):
    """在 Windows「用户变量」区写入环境变量（setx，写入注册表持久化）。
    注意：setx 只对之后新建的进程生效；已运行的网关与子服务必须重启后才会读到新值。"""
    completed = subprocess.run(["setx", name, value], capture_output=True, text=True)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip() or f"setx 返回码 {completed.returncode}"
        raise RuntimeError(detail)


class GatewayHandler(http.server.BaseHTTPRequestHandler):
    server_version = "DilanIntegratedV44/1.0"

    def log_message(self, fmt, *args):
        print("[gateway]", self.address_string(), "-", fmt % args)

    def redirect(self, target: str):
        self.send_response(302)
        self.send_header("Location", target)
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def send_json(self, code: int, obj: dict):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def handle_env_status(self):
        """报告网关进程当前是否已读到 TOS 用户环境变量（反映重启后的“生效”状态）。"""
        self.send_json(200, {
            "ak_set": bool(os.environ.get("VOLC_TOS_ACCESS_KEY_ID") or os.environ.get("TOS_ACCESS_KEY_ID")),
            "sk_set": bool(os.environ.get("VOLC_TOS_SECRET_ACCESS_KEY") or os.environ.get("TOS_SECRET_ACCESS_KEY")),
            "platform": os.name,
        })

    def handle_set_tos_credentials(self):
        """把前端填写的 AK/SK 写入 Windows 用户环境变量（setx）。不回显、不落盘到任何文件。"""
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            body = {}
        ak = str(body.get("ak") or body.get("access_key_id") or "").strip()
        sk = str(body.get("sk") or body.get("secret_access_key") or "").strip()
        if not ak and not sk:
            self.send_json(400, {"error": "未填写 Access Key ID / Secret Access Key。"})
            return
        if os.name != "nt":
            self.send_json(400, {"error": "自动写入环境变量当前仅支持 Windows。请在系统中手动设置 VOLC_TOS_ACCESS_KEY_ID / VOLC_TOS_SECRET_ACCESS_KEY。"})
            return
        written = []
        try:
            if ak:
                set_user_env_var_windows("VOLC_TOS_ACCESS_KEY_ID", ak)
                written.append("VOLC_TOS_ACCESS_KEY_ID")
            if sk:
                set_user_env_var_windows("VOLC_TOS_SECRET_ACCESS_KEY", sk)
                written.append("VOLC_TOS_SECRET_ACCESS_KEY")
        except Exception as e:
            self.send_json(500, {"error": f"写入用户环境变量失败：{e}"})
            return
        print(f"[gateway] 已写入用户环境变量：{'、'.join(written)}（需重启程序后生效）")
        self.send_json(200, {
            "ok": True,
            "written": written,
            "restart_required": True,
            "message": "AK/SK 已写入 Windows 用户环境变量。请完全关闭本程序窗口后重新双击 start.bat，新设置才会生效。",
        })

    def do_GET(self):
        self.handle_any()

    def do_POST(self):
        self.handle_any()

    def do_HEAD(self):
        self.handle_any(head_only=True)

    def handle_any(self, head_only: bool = False):
        parsed = urlsplit(self.path)
        raw_path = parsed.path or "/"
        query = ("?" + parsed.query) if parsed.query else ""

        if raw_path in ("/", ""):
            self.redirect("/material")
            return

        # 系统级接口由网关直接处理（不按 Referer 代理到子模块）：写 TOS 用户环境变量 / 查询状态。
        if raw_path == "/api/system/set_tos_credentials":
            if self.command.upper() == "POST":
                self.handle_set_tos_credentials()
            else:
                self.send_error(405, "method not allowed")
            return
        if raw_path == "/api/system/env_status":
            self.handle_env_status()
            return

        tool = None
        upstream_path = raw_path

        if raw_path == "/material" or raw_path.startswith("/material/"):
            tool = "material"
            upstream_path = raw_path[len("/material"):] or "/"
        elif raw_path == "/video" or raw_path.startswith("/video/"):
            tool = "video"
            upstream_path = raw_path[len("/video"):] or "/"
        elif raw_path == "/image" or raw_path.startswith("/image/"):
            tool = "image"
            upstream_path = raw_path[len("/image"):] or "/"
        elif raw_path.startswith(("/api/", "/input/", "/output/", "/temp/")):
            tool = pick_tool_from_referer(self.headers)
            upstream_path = raw_path
        elif raw_path in ("/favicon.ico",):
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        else:
            self.send_error(404, "not found")
            return

        # Normalise /material, /video and /image to each child tool's root page.
        if upstream_path in ("", "/"):
            upstream_path = "/"

        if self.command.upper() == "POST" and upstream_path == "/api/project/import":
            self.proxy_project_import(upstream_path, query, fallback_tool=tool, head_only=head_only)
            return

        # 大文件流式导入：按块转发上传体，网关不把整包读进内存（工程包/素材包再大也不溢出）。
        if self.command.upper() == "POST" and upstream_path in ("/api/project/import/stream", "/api/assets/package/import/stream"):
            self.proxy_stream(f"http://127.0.0.1:{upstream_for_tool(tool)}{upstream_path}{query}")
            return

        port = upstream_for_tool(tool)
        url = f"http://127.0.0.1:{port}{upstream_path}{query}"
        self.proxy_request(url, head_only=head_only)

    def proxy_project_import(self, upstream_path: str, query: str, fallback_tool: str, head_only: bool = False):
        length = int(self.headers.get("Content-Length") or "0")
        body = self.rfile.read(length) if length else None
        target_tool = sniff_import_package_module(body or b"") or fallback_tool
        if target_tool != fallback_tool:
            print(f"[gateway] 工程包自动转发：{fallback_tool} -> {target_tool}")
        port = upstream_for_tool(target_tool)
        url = f"http://127.0.0.1:{port}{upstream_path}{query}"
        self.proxy_request_with_body(url, body, head_only=head_only)

    def proxy_request_with_body(self, url: str, body, head_only: bool = False):
        headers = {}
        for k, v in self.headers.items():
            lk = k.lower()
            if lk in HOP_BY_HOP_HEADERS or lk in ("host", "content-length"):
                continue
            headers[k] = v
        headers["Host"] = urlsplit(url).netloc

        req = urllib.request.Request(url, data=body, headers=headers, method=self.command)
        try:
            with urllib.request.urlopen(req, timeout=3600) as resp:
                data = b"" if head_only else resp.read()
                self.send_response(resp.status, resp.reason)
                self.copy_response_headers(resp.headers, data)
                self.end_headers()
                if not head_only:
                    self.wfile.write(data)
        except urllib.error.HTTPError as e:
            data = b"" if head_only else e.read()
            self.send_response(e.code, e.reason)
            self.copy_response_headers(e.headers, data)
            self.end_headers()
            if not head_only:
                self.wfile.write(data)
        except Exception as e:
            msg = f"Gateway proxy error: {e}".encode("utf-8", "replace")
            self.send_response(502)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            if not head_only:
                self.wfile.write(msg)

    def proxy_request(self, url: str, head_only: bool = False):
        length = int(self.headers.get("Content-Length") or "0")
        body = self.rfile.read(length) if length else None
        self.proxy_request_with_body(url, body, head_only=head_only)

    def proxy_stream(self, url: str):
        """把上传请求体按块流式转发到子服务，全程只占用一个块的内存（用于大文件导入）。
        响应体（导入结果 JSON）很小，可整块读回。"""
        length = int(self.headers.get("Content-Length") or "0")
        parts = urlsplit(url)
        conn = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=3600)
        try:
            path = parts.path + (("?" + parts.query) if parts.query else "")
            conn.putrequest(self.command, path, skip_host=True, skip_accept_encoding=True)
            for k, v in self.headers.items():
                lk = k.lower()
                if lk in HOP_BY_HOP_HEADERS or lk == "host":
                    continue
                conn.putheader(k, v)
            conn.putheader("Host", parts.netloc)
            conn.endheaders()
            remaining = length
            chunk_size = 1024 * 1024
            while remaining > 0:
                data = self.rfile.read(min(chunk_size, remaining))
                if not data:
                    break
                conn.send(data)
                remaining -= len(data)
            resp = conn.getresponse()
            data = resp.read()
            self.send_response(resp.status, resp.reason)
            self.copy_response_headers(resp.headers, data)
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            msg = f"Gateway stream error: {e}".encode("utf-8", "replace")
            try:
                self.send_response(502)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(msg)))
                self.end_headers()
                self.wfile.write(msg)
            except Exception:
                pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def copy_response_headers(self, src_headers, data: bytes):
        sent_len = False
        for k, v in src_headers.items():
            lk = k.lower()
            if lk in HOP_BY_HOP_HEADERS:
                continue
            if lk == "content-length":
                self.send_header("Content-Length", str(len(data)))
                sent_len = True
                continue
            # 子服务的 Set-Cookie 暂时不使用，避免图片/视频之间互相污染。
            if lk == "set-cookie":
                continue
            self.send_header(k, v)
        if not sent_len:
            self.send_header("Content-Length", str(len(data)))


def handle_exit_signal(signum, frame):
    print("\n[gateway] 收到退出信号，正在关闭素材/图片/视频子服务...")
    cleanup_children()
    raise SystemExit(0)


def main():
    print("============================================")
    print("帝蓝工作流整合版 V44")
    print("统一入口: http://127.0.0.1:%s/material" % GATEWAY_PORT)
    print("素材子服务: http://127.0.0.1:%s" % MATERIAL_PORT)
    print("图片子服务: http://127.0.0.1:%s" % IMAGE_PORT)
    print("视频子服务: http://127.0.0.1:%s" % VIDEO_PORT)
    print("============================================")
    atexit.register(cleanup_children)
    for sig in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGINT", None)):
        if sig is not None:
            try:
                signal.signal(sig, handle_exit_signal)
            except Exception:
                pass
    start_child("美术", MATERIAL_DIR, MATERIAL_PORT)
    start_child("分镜", IMAGE_DIR, IMAGE_PORT)
    start_child("视频", VIDEO_DIR, VIDEO_PORT)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", GATEWAY_PORT), GatewayHandler)
    url = f"http://127.0.0.1:{GATEWAY_PORT}/material"
    print(f"[gateway] 启动统一入口：{url}")
    if os.environ.get("DILAN_NO_BROWSER") != "1":
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    finally:
        cleanup_children()


if __name__ == "__main__":
    main()
