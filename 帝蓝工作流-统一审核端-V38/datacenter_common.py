# -*- coding: utf-8 -*-
"""
数据中心客户端公共模块（制作端三模块共用）
-------------------------------------------
- 数据中心地址保存在共享目录 shared_project_index/data_center.json，三模块共用一份，可配置。
- 仅用标准库 urllib 发起请求；上传走 multipart，下载取原始字节。
- 这里不做任何工程归位逻辑，归位仍由各模块自身的 import_project_package / 网关转发完成。
"""
import base64
import io
import json
import urllib.request
import urllib.error
import uuid
import zipfile
from pathlib import Path

DC_CONFIG_NAME = "data_center.json"
DEFAULT_DC_URL = "http://192.168.1.99:8777"


def dc_config_path(shared_dir):
    return Path(shared_dir) / DC_CONFIG_NAME


def load_dc_config(shared_dir):
    p = dc_config_path(shared_dir)
    if not p.exists():
        return {"url": DEFAULT_DC_URL}
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(cfg, dict):
            cfg = {}
    except Exception:
        cfg = {}
    cfg.setdefault("url", DEFAULT_DC_URL)
    return cfg


def save_dc_config(shared_dir, url):
    p = dc_config_path(shared_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    url = (str(url or "").strip().rstrip("/")) or DEFAULT_DC_URL
    p.write_text(json.dumps({"url": url}, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"url": url}


def dc_base(shared_dir):
    url = (load_dc_config(shared_dir).get("url") or DEFAULT_DC_URL).strip().rstrip("/")
    if not url:
        raise ValueError("尚未配置数据中心地址")
    return url


# --------------------------------------------------------------------------- #
# HTTP 辅助
# --------------------------------------------------------------------------- #
def _open(req, timeout=600):
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            data = json.loads(body)
            msg = data.get("error") or body
        except Exception:
            msg = body
        raise ValueError(f"数据中心返回错误（HTTP {e.code}）：{msg}")
    except urllib.error.URLError as e:
        raise ValueError(f"无法连接数据中心：{e.reason}。请检查“数据中心地址”是否正确、服务端是否已启动、是否在同一局域网。")


def get_json(url, timeout=60):
    req = urllib.request.Request(url, method="GET")
    with _open(req, timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_bytes(url, timeout=1800):
    req = urllib.request.Request(url, method="GET")
    with _open(req, timeout) as resp:
        return resp.read()


def post_json(url, payload, timeout=120):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    with _open(req, timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def post_multipart(url, fields, file_field, filename, file_bytes, timeout=1800):
    boundary = "----dilan" + uuid.uuid4().hex
    out = io.BytesIO()

    def w(s):
        out.write(s.encode("utf-8") if isinstance(s, str) else s)

    for k, v in (fields or {}).items():
        w(f"--{boundary}\r\n")
        w(f'Content-Disposition: form-data; name="{k}"\r\n\r\n')
        w(str(v))
        w("\r\n")
    w(f"--{boundary}\r\n")
    w(f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n')
    w("Content-Type: application/zip\r\n\r\n")
    w(file_bytes)
    w("\r\n")
    w(f"--{boundary}--\r\n")
    body = out.getvalue()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Content-Length": str(len(body)),
    })
    with _open(req, timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# --------------------------------------------------------------------------- #
# 高层操作
# --------------------------------------------------------------------------- #
def list_projects(shared_dir):
    return get_json(dc_base(shared_dir) + "/api/projects")


def upload_package(shared_dir, zip_bytes, filename, meta, on_conflict="ask"):
    # 先做一次快速可达性探测（5 秒），连不上立即明确报错，避免大包上传长时间无响应
    try:
        get_json(dc_base(shared_dir) + "/api/config", timeout=5)
    except Exception:
        raise ValueError("无法连接数据中心：请检查“数据中心地址”是否正确、数据中心服务端是否已启动、本机与数据中心是否在同一局域网、防火墙是否放行端口。")
    fields = {"meta": json.dumps(meta, ensure_ascii=False), "on_conflict": on_conflict}
    return post_multipart(dc_base(shared_dir) + "/api/upload", fields, "file", filename, zip_bytes)


def resolve_conflict(shared_dir, token, action):
    return post_json(dc_base(shared_dir) + "/api/upload/resolve-conflict",
                     {"token": token, "action": action})


def download_child_bytes(shared_dir, child_id):
    return get_bytes(dc_base(shared_dir) + "/api/download/child/" + child_id)


def download_parent_children(shared_dir, parent_id):
    """下载整个父项目打包，返回 [{name, module_label, dataUrl, filename}]，供前端逐个走现有导入归位。"""
    raw = get_bytes(dc_base(shared_dir) + "/api/download/parent/" + parent_id)
    items = []
    with zipfile.ZipFile(io.BytesIO(raw), "r") as zf:
        meta = {}
        try:
            meta = json.loads(zf.read("parent_bundle.json").decode("utf-8"))
        except Exception:
            meta = {}
        arc_to_info = {c.get("package_arcname"): c for c in (meta.get("children") or [])}
        for name in zf.namelist():
            if name.startswith("children/") and name.lower().endswith(".zip"):
                data = zf.read(name)
                info = arc_to_info.get(name, {})
                items.append({
                    "name": info.get("child_name") or Path(name).stem,
                    "module_label": info.get("module_label") or "",
                    "filename": (info.get("child_name") or Path(name).stem) + ".zip",
                    "dataUrl": "data:application/zip;base64," + base64.b64encode(data).decode("ascii"),
                })
    return {"parent_name": meta.get("parent_name") or "", "children": items}


def bytes_to_data_url(zip_bytes):
    return "data:application/zip;base64," + base64.b64encode(zip_bytes).decode("ascii")


def merge_packages(shared_dir, child_ids, target_name="", generator_name=""):
    return post_json(dc_base(shared_dir) + "/api/merge",
                     {"child_ids": child_ids, "target_name": target_name, "generator_name": generator_name})

def preview_bytes(shared_dir, child_id):
    """取数据中心某子项目预览图字节（连同 content-type 猜测）。"""
    return get_bytes(dc_base(shared_dir) + "/api/preview/" + child_id)

def delete_child(shared_dir, child_id):
    """删除数据中心里的某个子项目。"""
    import urllib.request
    req = urllib.request.Request(dc_base(shared_dir) + "/api/child/" + child_id, method="DELETE")
    return _open_json(req)


def _open_json(req, timeout=60):
    with _open(req, timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))
