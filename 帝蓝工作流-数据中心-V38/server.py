#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
帝蓝工作流-数据中心-V45
=====================
局域网内统一的项目仓库服务端。制作端 / 审核端通过局域网地址访问，
把原来靠本地压缩包手动传来传去的流程，改成统一上传 / 下载 / 管理。

- 纯 Python 标准库实现（http.server + sqlite3），无第三方依赖。
- 项目结构与制作端 / 审核端一致：父项目 → 美术 / 分镜 / 视频 → 子项目。
- 原始工程包 zip 始终保留在 storage/packages 下。
- 元数据索引存放在 SQLite（database.sqlite）。
"""
import io
import json
import os
import re
import shutil
import socket
import sqlite3
import threading
import time
import uuid
import webbrowser
import zipfile
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs, unquote

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
STORAGE_DIR = ROOT / "storage"
PACKAGES_DIR = STORAGE_DIR / "packages"
PREVIEWS_DIR = STORAGE_DIR / "previews"
EXTRACTED_DIR = STORAGE_DIR / "extracted"
PENDING_DIR = STORAGE_DIR / "pending"
DB_PATH = ROOT / "database.sqlite"
CONFIG_PATH = ROOT / "config.json"

for d in (STATIC_DIR, STORAGE_DIR, PACKAGES_DIR, PREVIEWS_DIR, EXTRACTED_DIR, PENDING_DIR):
    d.mkdir(parents=True, exist_ok=True)

MODULE_LABELS = {"material": "美术", "image": "分镜", "video": "视频"}
MODULE_ORDER = ["material", "image", "video"]
CLIENT_LABELS = {"creator": "制作端", "reviewer": "审核端", "data_center": "数据中心合并"}

DEFAULT_CONFIG = {
    "server_name": "帝蓝工作流-数据中心",
    "host": "0.0.0.0",
    "port": 8777,
    "max_upload_mb": 4096,
}

_DB_LOCK = threading.Lock()

# --------------------------------------------------------------------------- #
# 传输任务登记（内存）：记录上传/下载任务，供"下载管理"面板查询进度
# --------------------------------------------------------------------------- #
_TRANSFERS = []          # 每项: {id,kind,name,size,done,total,status,started_at,updated_at,error}
_TRANSFERS_LOCK = threading.Lock()
_TRANSFERS_MAX = 200

def transfer_start(kind, name, total=0):
    tid = uuid.uuid4().hex[:12]
    rec = {"id": tid, "kind": kind, "name": name or "-", "size": int(total or 0),
           "done": 0, "total": int(total or 0), "status": "running",
           "started_at": now_str(), "updated_at": now_str(), "error": ""}
    with _TRANSFERS_LOCK:
        _TRANSFERS.append(rec)
        while len(_TRANSFERS) > _TRANSFERS_MAX:
            _TRANSFERS.pop(0)
    return tid

def transfer_update(tid, done=None, total=None):
    with _TRANSFERS_LOCK:
        for r in _TRANSFERS:
            if r["id"] == tid:
                if done is not None: r["done"] = int(done)
                if total is not None: r["total"] = int(total)
                r["updated_at"] = now_str()
                break

def transfer_finish(tid, status="done", error=""):
    with _TRANSFERS_LOCK:
        for r in _TRANSFERS:
            if r["id"] == tid:
                r["status"] = status
                if r["total"] and status == "done": r["done"] = r["total"]
                r["error"] = error or ""
                r["updated_at"] = now_str()
                break

def transfers_snapshot():
    with _TRANSFERS_LOCK:
        return [dict(r) for r in reversed(_TRANSFERS)]


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def new_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def safe_name(name, fallback="item"):
    name = str(name or "").strip()
    name = re.sub(r"[\\/:*?\"<>|]+", "_", name)
    name = name.strip(". ")
    return name or fallback


def module_label(module_type):
    return MODULE_LABELS.get(module_type, module_type or "")


def load_config():
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    merged = dict(DEFAULT_CONFIG)
    merged.update({k: v for k, v in (cfg or {}).items() if k in DEFAULT_CONFIG})
    return merged


def save_config(incoming):
    cfg = load_config()
    for k, v in (incoming or {}).items():
        if k in DEFAULT_CONFIG:
            cfg[k] = v
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg


# --------------------------------------------------------------------------- #
# 数据库
# --------------------------------------------------------------------------- #
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    with _DB_LOCK, db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS parents (
                parent_id   TEXT PRIMARY KEY,
                parent_name TEXT NOT NULL,
                created_at  TEXT,
                updated_at  TEXT
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS children (
                child_id        TEXT PRIMARY KEY,
                package_id      TEXT,
                parent_id       TEXT NOT NULL,
                parent_name     TEXT,
                module_type     TEXT NOT NULL,
                module_label    TEXT,
                child_name      TEXT NOT NULL,
                base_child_name TEXT,
                generator_name  TEXT,
                generator_names TEXT,
                client_type     TEXT,
                source_label    TEXT,
                review_status   TEXT,
                uploaded_at     TEXT,
                file_size       INTEGER,
                package_path    TEXT,
                preview_path    TEXT
            )""")
        conn.commit()


def parent_to_dict(row, conn):
    children = conn.execute(
        "SELECT * FROM children WHERE parent_id=? ORDER BY uploaded_at", (row["parent_id"],)
    ).fetchall()
    counts = {"material": 0, "image": 0, "video": 0}
    child_list = []
    for c in children:
        mt = c["module_type"]
        if mt in counts:
            counts[mt] += 1
        child_list.append(child_to_dict(c))
    return {
        "parent_id": row["parent_id"],
        "parent_name": row["parent_name"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "counts": counts,
        "children": child_list,
    }


def child_to_dict(c):
    try:
        gnames = json.loads(c["generator_names"]) if c["generator_names"] else []
    except Exception:
        gnames = []
    return {
        "child_id": c["child_id"],
        "package_id": c["package_id"],
        "parent_id": c["parent_id"],
        "parent_name": c["parent_name"],
        "module_type": c["module_type"],
        "module_label": c["module_label"] or module_label(c["module_type"]),
        "child_name": c["child_name"],
        "base_child_name": c["base_child_name"],
        "generator_name": c["generator_name"] or "",
        "generator_names": gnames,
        "client_type": c["client_type"] or "",
        "source_label": c["source_label"] or CLIENT_LABELS.get(c["client_type"], c["client_type"] or ""),
        "review_status": c["review_status"] or "",
        "uploaded_at": c["uploaded_at"],
        "file_size": c["file_size"] or 0,
        "has_preview": bool(c["preview_path"]),
    }


# --------------------------------------------------------------------------- #
# 工程包解析（用于读取自带元数据 + 提取预览图）
# --------------------------------------------------------------------------- #
def read_zip_json(zf, name):
    try:
        with zf.open(name, "r") as fh:
            return json.loads(fh.read().decode("utf-8"))
    except Exception:
        return None


def sniff_package_meta(zip_bytes):
    """从工程包中尽量读取归属元数据，作为上传元数据缺失时的兜底。"""
    meta = {}
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            names = zf.namelist()
            manifest = read_zip_json(zf, "manifest.json") or {}
            scope = manifest.get("project_scope") if isinstance(manifest.get("project_scope"), dict) else {}
            meta["module_type"] = manifest.get("module_type") or scope.get("module_type") or ""
            meta["parent_id"] = manifest.get("parent_id") or scope.get("parent_id") or ""
            meta["parent_name"] = manifest.get("parent_name") or scope.get("parent_name") or ""
            meta["child_id"] = manifest.get("child_id") or scope.get("child_id") or ""
            meta["child_name"] = manifest.get("child_name") or scope.get("child_name") or manifest.get("project_name") or ""
            meta["generator_name"] = manifest.get("generator_name") or manifest.get("responsible_nickname") or ""
            if not meta["module_type"]:
                pj = manifest.get("project_json")
                if not pj or pj not in names:
                    cands = [n for n in names if n.endswith("/project.json") or n == "project.json"]
                    pj = cands[0] if cands else ""
                if pj:
                    pdata = read_zip_json(zf, pj) or {}
                    meta["module_type"] = pdata.get("module_type") or pdata.get("project_module_type") or ""
                    if not meta["module_type"] and isinstance(pdata, dict):
                        # 兼容 V29 前的老工程包：无模块标识时按数据指纹推断，避免老视频包一律落到美术列。
                        if isinstance(pdata.get("material_workspaces"), dict) or pdata.get("material_module_version"):
                            meta["module_type"] = "material"
                        elif any(isinstance(tb, dict) and (tb.get("generated_videos") or tb.get("last_video_status"))
                                 for sc in (pdata.get("scenes") or []) if isinstance(sc, dict)
                                 for sh in (sc.get("shots") or []) if isinstance(sh, dict)
                                 for tb in (sh.get("tabs") or [])):
                            meta["module_type"] = "video"
    except Exception as e:
        print("[datacenter] 工程包元数据解析失败:", e)
    return meta


def _zip_member_for_public_path(zf, names, public_path):
    """把 project.json 里的 /output/... 或 /input/... 公共路径映射到 zip 包内的真实成员名。"""
    if not public_path:
        return None
    p = str(public_path).lstrip("/")
    # 直接命中
    if p in names:
        return p
    # 末尾匹配（包内 arcname 可能带或不带前导路径差异）
    tail = p.split("/")[-1]
    cands = [n for n in names if n.endswith("/" + p) or n.endswith(p)]
    if cands:
        cands.sort(key=len)
        return cands[0]
    # 退而求其次：用文件名匹配
    cands = [n for n in names if n.split("/")[-1] == tail and not n.endswith("/")]
    if cands:
        cands.sort(key=len)
        return cands[0]
    return None


def _first_candidate_media(project_data):
    """按 SC→子栏 顺序，取第一张待选分镜素材/素材图/分镜视频。
    返回 (kind, file_path, thumb_path)；kind 为 image/video。"""
    scenes = project_data.get("scenes") if isinstance(project_data, dict) else None
    if isinstance(scenes, list):
        # 按场景序号、子栏顺序
        def snum(sc):
            try:
                n = int(sc.get("scene_number") or 0)
                if n > 0:
                    return n
            except Exception:
                pass
            import re as _re
            mm = _re.search(r"(\d+)", str(sc.get("scene_code") or ""))
            return int(mm.group(1)) if mm else 999999
        for sc in sorted(scenes, key=snum):
            for sh in sorted(sc.get("shots") or [], key=lambda x: int(x.get("sort_order") or 999999)):
                for c in (sh.get("storyboard_candidates") or []):
                    if c.get("review_deleted"):
                        continue
                    fp = c.get("file_path") or c.get("path") or ""
                    if not fp:
                        continue
                    is_video = str(fp).lower().split("?")[0].endswith((".mp4", ".mov", ".webm", ".mkv", ".m4v"))
                    return ("video" if is_video else "image", fp, c.get("thumb_path") or "")
    # 美术模块：没有 storyboard_candidates，则找素材/生成图
    if isinstance(scenes, list):
        for sc in scenes:
            for sh in (sc.get("shots") or []):
                for tab in (sh.get("tabs") or []):
                    for img in (tab.get("generated_images") or []):
                        fp = img.get("file_path") or ""
                        if fp:
                            is_video = str(fp).lower().endswith((".mp4", ".mov", ".webm"))
                            return ("video" if is_video else "image", fp, img.get("thumb_path") or "")
    return (None, "", "")


def extract_preview(zip_bytes, child_id):
    """优先抽取工程内第一张待选分镜素材/素材图/分镜视频首帧作为预览缩略图。
    取不到时退回包内任意一张图片。不依赖任何第三方库。"""
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            names = zf.namelist()
            # 读 project.json
            manifest = read_zip_json(zf, "manifest.json") or {}
            pj = manifest.get("project_json")
            if not pj or pj not in names:
                cands = [n for n in names if n.endswith("/project.json") or n == "project.json"]
                pj = cands[0] if cands else None
            pdata = read_zip_json(zf, pj) if pj else None

            pick = None
            if isinstance(pdata, dict):
                kind, fp, thumb = _first_candidate_media(pdata)
                if kind == "video":
                    # 视频用首帧缩略图（thumb_path）；没有则放弃，走兜底图
                    pick = _zip_member_for_public_path(zf, names, thumb) if thumb else None
                elif kind == "image":
                    pick = _zip_member_for_public_path(zf, names, fp)
                    if not pick and thumb:
                        pick = _zip_member_for_public_path(zf, names, thumb)

            # 兜底：包内任意图片（优先 output）
            if not pick:
                imgs = [n for n in names
                        if n.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
                        and not n.endswith("/")]
                if not imgs:
                    return ""
                imgs.sort(key=lambda n: (("output" not in n.lower()), len(n)))
                pick = imgs[0]

            ext = Path(pick).suffix.lower() or ".png"
            out = PREVIEWS_DIR / f"{child_id}{ext}"
            with zf.open(pick, "r") as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst)
            return str(out.relative_to(ROOT))
    except Exception as e:
        print("[datacenter] 预览图提取失败:", e)
        return ""


# --------------------------------------------------------------------------- #
# 建档 / 上传
# --------------------------------------------------------------------------- #
def ensure_parent(conn, parent_id, parent_name):
    """优先按 parent_id 匹配，匹配不到按 parent_name 匹配，仍无则创建。"""
    row = None
    if parent_id:
        row = conn.execute("SELECT * FROM parents WHERE parent_id=?", (parent_id,)).fetchone()
    if row is None and parent_name:
        row = conn.execute("SELECT * FROM parents WHERE parent_name=?", (parent_name,)).fetchone()
    if row is not None:
        conn.execute("UPDATE parents SET updated_at=? WHERE parent_id=?", (now_str(), row["parent_id"]))
        return row["parent_id"], row["parent_name"]
    pid = parent_id or new_id("parent")
    pname = parent_name or pid
    conn.execute(
        "INSERT INTO parents(parent_id,parent_name,created_at,updated_at) VALUES(?,?,?,?)",
        (pid, pname, now_str(), now_str()),
    )
    return pid, pname


def unique_child_name(conn, parent_id, module_type, base_name):
    """生成新版本时的不重名子项目名：xxx（版本2）、（版本3）…"""
    existing = {r["child_name"] for r in conn.execute(
        "SELECT child_name FROM children WHERE parent_id=? AND module_type=?",
        (parent_id, module_type)).fetchall()}
    if base_name not in existing:
        return base_name
    n = 2
    while f"{base_name}（版本{n}）" in existing:
        n += 1
    return f"{base_name}（版本{n}）"


def store_package(zip_bytes, child_id):
    path = PACKAGES_DIR / f"{child_id}.zip"
    with open(path, "wb") as fh:
        fh.write(zip_bytes)
    return str(path.relative_to(ROOT))


def merge_generator_names(existing_json, new_name):
    try:
        names = json.loads(existing_json) if existing_json else []
    except Exception:
        names = []
    if new_name and new_name not in names:
        names.append(new_name)
    return names


def _scene_num(sc):
    try:
        n = int(sc.get("scene_number") or 0)
        if n > 0:
            return n
    except Exception:
        pass
    import re as _re
    m = _re.search(r"(\d+)", str(sc.get("scene_code") or ""))
    return int(m.group(1)) if m else 999999


def merge_packages(child_ids, target_name="", generator_name=""):
    """把数据中心里多个子项目工程包按顺序合并成一个新工程包，并入库为新的子项目。
    规则与制作端一致：以第一个为基底；后续包的 SC 编号不存在则整段加入，
    已存在则把其子栏(shot)接到该 SC 后面；SC/子栏顺序按选择顺序，编号统一重排。
    各源包文件按原 arcname 收进新包（不同子项目 pid 不冲突），project.json 引用保持有效。"""
    if not isinstance(child_ids, list) or len(child_ids) < 2:
        raise ValueError("请至少选择两个子项目进行合并")
    rows = []
    with db() as conn:
        for cid in child_ids:
            r = conn.execute("SELECT * FROM children WHERE child_id=?", (cid,)).fetchone()
            if not r:
                raise ValueError(f"子项目不存在：{cid}")
            rows.append(r)
    # 校验同父项目、同模块
    if len({r["parent_id"] for r in rows}) > 1:
        raise ValueError("只能合并同一个父项目下的子项目")
    if len({r["module_type"] for r in rows}) > 1:
        raise ValueError("只能合并同一个模块（美术/分镜/视频）下的子项目")
    module_type = rows[0]["module_type"]
    parent_id = rows[0]["parent_id"]
    parent_name = rows[0]["parent_name"]

    out_buf = io.BytesIO()
    merged_project = None
    merged_manifest = None
    project_json_arc = None
    seen_arcs = set()
    META_FILES = {"manifest.json", "export_info.json", "usage_summary.json"}
    scene_by_num = {}
    asset_ids = set()
    group_ids = set()

    with zipfile.ZipFile(out_buf, "w", compression=zipfile.ZIP_DEFLATED) as out:
        for idx, r in enumerate(rows):
            pkg_path = ROOT / (r["package_path"] or "")
            if not pkg_path.exists():
                raise ValueError(f"子项目工程包文件缺失：{r['child_name']}")
            with zipfile.ZipFile(pkg_path, "r") as zf:
                manifest = read_zip_json(zf, "manifest.json") or {}
                pj_arc = manifest.get("project_json")
                names = zf.namelist()
                if not pj_arc or pj_arc not in names:
                    cands = [n for n in names if n.endswith("/project.json") or n == "project.json"]
                    pj_arc = cands[0] if cands else None
                pdata = read_zip_json(zf, pj_arc) if pj_arc else None
                if not isinstance(pdata, dict):
                    raise ValueError(f"无法读取工程包 project.json：{r['child_name']}")

                if idx == 0:
                    merged_project = json.loads(json.dumps(pdata, ensure_ascii=False))
                    merged_manifest = json.loads(json.dumps(manifest, ensure_ascii=False))
                    project_json_arc = pj_arc
                    merged_project["scenes"] = merged_project.get("scenes") or []
                    merged_project["assets"] = merged_project.get("assets") or []
                    merged_project["asset_groups"] = merged_project.get("asset_groups") or []
                    for sc in merged_project["scenes"]:
                        scene_by_num[_scene_num(sc)] = sc
                    for a in merged_project["assets"]:
                        if isinstance(a, dict):
                            asset_ids.add(a.get("asset_id"))
                    for g in merged_project["asset_groups"]:
                        if isinstance(g, dict):
                            group_ids.add(g.get("group_id"))
                else:
                    # 合并 SC / 子栏
                    for sc in (pdata.get("scenes") or []):
                        num = _scene_num(sc)
                        if num in scene_by_num:
                            tgt = scene_by_num[num]
                            tgt.setdefault("shots", [])
                            base = len(tgt["shots"])
                            for j, sh in enumerate(sc.get("shots") or [], 1):
                                sh["sort_order"] = base + j
                                tgt["shots"].append(sh)
                        else:
                            merged_project["scenes"].append(sc)
                            scene_by_num[num] = sc
                    # 合并素材
                    for a in (pdata.get("assets") or []):
                        if isinstance(a, dict) and a.get("asset_id") not in asset_ids:
                            merged_project["assets"].append(a); asset_ids.add(a.get("asset_id"))
                    for g in (pdata.get("asset_groups") or []):
                        if isinstance(g, dict) and g.get("group_id") not in group_ids:
                            merged_project["asset_groups"].append(g); group_ids.add(g.get("group_id"))

                # 收文件（跳过元信息与各自的 project.json；保持原 arcname，pid 不同不冲突）
                for info in zf.infolist():
                    arc = info.filename
                    if arc.endswith("/"):
                        continue
                    if arc in META_FILES or arc == pj_arc:
                        continue
                    if arc in seen_arcs:
                        continue
                    seen_arcs.add(arc)
                    out.writestr(arc, zf.read(info))

        # SC 按编号排序，重排 SC 与子栏编号
        scenes = sorted(merged_project.get("scenes") or [], key=_scene_num)
        for si, sc in enumerate(scenes, 1):
            sc["scene_number"] = _scene_num(sc)
            sc["sort_order"] = si
            shots = sorted(sc.get("shots") or [], key=lambda x: int(x.get("sort_order") or 999999))
            for hi, sh in enumerate(shots, 1):
                sh["sort_order"] = hi
                sh["shot_code"] = f"{hi:02d}"
            sc["shots"] = shots
        merged_project["scenes"] = scenes

        new_name = (target_name or rows[0]["base_child_name"] or rows[0]["child_name"] or "合并子项目").strip()
        merged_project["project_name"] = new_name
        merged_project["child_name"] = new_name
        if isinstance(merged_project.get("project_scope"), dict):
            merged_project["project_scope"]["child_name"] = new_name
        merged_project["merge_info"] = {
            "merged_at": now_str(),
            "source_child_ids": child_ids,
            "source_names": [r["child_name"] for r in rows],
            "merged_in": "data_center",
        }

        # 更新 manifest 的名字与计数引用
        if isinstance(merged_manifest, dict):
            merged_manifest["child_name"] = new_name
            merged_manifest["project_name"] = new_name
            if isinstance(merged_manifest.get("project_scope"), dict):
                merged_manifest["project_scope"]["child_name"] = new_name

        out.writestr("manifest.json", json.dumps(merged_manifest or {}, ensure_ascii=False, indent=2))
        out.writestr(project_json_arc or "project.json",
                     json.dumps(merged_project, ensure_ascii=False, indent=2))

    zip_bytes = out_buf.getvalue()
    meta = {
        "parent_id": parent_id,
        "parent_name": parent_name,
        "module_type": module_type,
        "base_child_name": new_name,
        "child_name": new_name,
        "generator_name": generator_name or rows[0]["generator_name"] or "",
        "client_type": rows[0]["client_type"] or "creator",
    }
    # 合并结果直接入库为新版本（不与现有冲突时按原名）
    return do_upload(zip_bytes, meta, on_conflict="new_version")


def do_upload(zip_bytes, meta, on_conflict="ask"):
    """
    返回:
      正常入库 -> {"ok":True,"child":{...},"parent_id":...}
      需要决策 -> {"ok":False,"conflict":True,"token":...,"existing":{...},"incoming":{...}}
    """
    fallback = sniff_package_meta(zip_bytes)
    module_type = (meta.get("module_type") or fallback.get("module_type") or "").strip()
    if module_type not in MODULE_LABELS:
        module_type = module_type if module_type in MODULE_LABELS else "material"
    parent_id = (meta.get("parent_id") or fallback.get("parent_id") or "").strip()
    parent_name = (meta.get("parent_name") or fallback.get("parent_name") or "").strip()
    base_child_name = (meta.get("base_child_name") or meta.get("child_name")
                       or fallback.get("child_name") or "未命名子项目").strip()
    generator_name = (meta.get("generator_name") or fallback.get("generator_name") or "").strip()
    client_type = (meta.get("client_type") or "creator").strip()
    review_status = meta.get("review_status") or ""
    if isinstance(review_status, (dict, list)):
        review_status = json.dumps(review_status, ensure_ascii=False)

    # 审核端上传的项目，在基础名后加「(审核)」后缀，与制作端原项目区分
    if client_type == "reviewer" and not base_child_name.endswith("(审核)") and "(审核)" not in base_child_name:
        base_child_name = f"{base_child_name}(审核)"

    # 子项目展示名带上生成者后缀，便于区分不同人上传的同名项目
    display_name = base_child_name
    if generator_name and not display_name.endswith("_" + generator_name):
        display_name = f"{base_child_name}_{generator_name}"

    with _DB_LOCK, db() as conn:
        pid, pname = ensure_parent(conn, parent_id, parent_name)
        existing = conn.execute(
            "SELECT * FROM children WHERE parent_id=? AND module_type=? AND child_name=?",
            (pid, module_type, display_name)).fetchone()

        if existing is not None and on_conflict == "ask":
            token = new_id("pending")
            (PENDING_DIR / f"{token}.zip").write_bytes(zip_bytes)
            (PENDING_DIR / f"{token}.json").write_text(json.dumps({
                "parent_id": pid, "parent_name": pname, "module_type": module_type,
                "base_child_name": base_child_name, "display_name": display_name,
                "generator_name": generator_name, "client_type": client_type,
                "review_status": review_status,
            }, ensure_ascii=False), encoding="utf-8")
            return {"ok": False, "conflict": True, "token": token,
                    "existing": child_to_dict(existing),
                    "incoming": {"child_name": display_name, "module_label": module_label(module_type),
                                 "generator_name": generator_name}}

        return _finalize_upload(conn, zip_bytes, {
            "parent_id": pid, "parent_name": pname, "module_type": module_type,
            "base_child_name": base_child_name, "display_name": display_name,
            "generator_name": generator_name, "client_type": client_type,
            "review_status": review_status,
        }, existing_row=existing if on_conflict == "overwrite" else None,
            force_new=(on_conflict == "new_version"))


def _finalize_upload(conn, zip_bytes, info, existing_row=None, force_new=False):
    pid = info["parent_id"]
    module_type = info["module_type"]
    display_name = info["display_name"]
    generator_name = info["generator_name"]

    if force_new:
        display_name = unique_child_name(conn, pid, module_type, display_name)
        existing_row = None

    if existing_row is not None:
        child_id = existing_row["child_id"]
        gnames = merge_generator_names(existing_row["generator_names"], generator_name)
        # 覆盖：保留一个底层备份
        old_path = ROOT / (existing_row["package_path"] or "")
        if old_path.exists():
            bak = old_path.with_suffix(".bak.zip")
            try:
                shutil.copy2(old_path, bak)
            except Exception:
                pass
    else:
        child_id = new_id("child")
        gnames = [generator_name] if generator_name else []

    package_path = store_package(zip_bytes, child_id)
    preview_path = extract_preview(zip_bytes, child_id)
    file_size = len(zip_bytes)
    source_label = CLIENT_LABELS.get(info["client_type"], info["client_type"])

    if existing_row is not None:
        conn.execute("""
            UPDATE children SET package_id=?,parent_name=?,module_label=?,child_name=?,
              base_child_name=?,generator_name=?,generator_names=?,client_type=?,source_label=?,
              review_status=?,uploaded_at=?,file_size=?,package_path=?,preview_path=?
            WHERE child_id=?""", (
            new_id("pkg"), info["parent_name"], module_label(module_type), display_name,
            info["base_child_name"], generator_name, json.dumps(gnames, ensure_ascii=False),
            info["client_type"], source_label, info["review_status"], now_str(), file_size,
            package_path, preview_path, child_id))
    else:
        conn.execute("""
            INSERT INTO children(child_id,package_id,parent_id,parent_name,module_type,module_label,
              child_name,base_child_name,generator_name,generator_names,client_type,source_label,
              review_status,uploaded_at,file_size,package_path,preview_path)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            child_id, new_id("pkg"), pid, info["parent_name"], module_type, module_label(module_type),
            display_name, info["base_child_name"], generator_name,
            json.dumps(gnames, ensure_ascii=False), info["client_type"], source_label,
            info["review_status"], now_str(), file_size, package_path, preview_path))
    conn.execute("UPDATE parents SET updated_at=? WHERE parent_id=?", (now_str(), pid))
    conn.commit()
    row = conn.execute("SELECT * FROM children WHERE child_id=?", (child_id,)).fetchone()
    return {"ok": True, "child": child_to_dict(row), "parent_id": pid}


def resolve_conflict(token, action):
    zip_file = PENDING_DIR / f"{token}.zip"
    meta_file = PENDING_DIR / f"{token}.json"
    if not zip_file.exists() or not meta_file.exists():
        return {"ok": False, "error": "待处理上传已过期或不存在"}
    zip_bytes = zip_file.read_bytes()
    info = json.loads(meta_file.read_text(encoding="utf-8"))
    with _DB_LOCK, db() as conn:
        existing = conn.execute(
            "SELECT * FROM children WHERE parent_id=? AND module_type=? AND child_name=?",
            (info["parent_id"], info["module_type"], info["display_name"])).fetchone()
        if action == "overwrite":
            res = _finalize_upload(conn, zip_bytes, info, existing_row=existing, force_new=False)
        else:  # new_version
            res = _finalize_upload(conn, zip_bytes, info, existing_row=None, force_new=True)
    try:
        zip_file.unlink(); meta_file.unlink()
    except Exception:
        pass
    return res


# --------------------------------------------------------------------------- #
# 下载
# --------------------------------------------------------------------------- #
def rewrite_package_name(zip_bytes, display_name):
    """下载时把数据库里的展示名（含「(审核)」/生成者后缀）写回包内 manifest 与 project.json，
    使下载归位后的子项目名与数据中心一致。"""
    if not display_name:
        return zip_bytes
    try:
        zin = zipfile.ZipFile(io.BytesIO(zip_bytes), "r")
        names = zin.namelist()
        manifest = read_zip_json(zin, "manifest.json") or {}
        pj_arc = manifest.get("project_json")
        if not pj_arc or pj_arc not in names:
            cands = [n for n in names if n.endswith("/project.json") or n == "project.json"]
            pj_arc = cands[0] if cands else None
        # 改写 manifest
        if manifest:
            manifest["child_name"] = display_name
            manifest["project_name"] = display_name
            manifest["child_id"] = display_name
            if isinstance(manifest.get("project_scope"), dict):
                manifest["project_scope"]["child_name"] = display_name
                manifest["project_scope"]["child_id"] = display_name
        pdata = read_zip_json(zin, pj_arc) if pj_arc else None
        if isinstance(pdata, dict):
            pdata["project_name"] = display_name
            pdata["child_name"] = display_name
            pdata["child_id"] = display_name
            if isinstance(pdata.get("project_scope"), dict):
                pdata["project_scope"]["child_name"] = display_name
                pdata["project_scope"]["child_id"] = display_name
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zout:
            for info in zin.infolist():
                if info.filename == "manifest.json" and manifest:
                    zout.writestr(info, json.dumps(manifest, ensure_ascii=False, indent=2))
                elif pj_arc and info.filename == pj_arc and isinstance(pdata, dict):
                    zout.writestr(info, json.dumps(pdata, ensure_ascii=False, indent=2))
                else:
                    zout.writestr(info, zin.read(info.filename))
        zin.close()
        return out.getvalue()
    except Exception as e:
        print("[datacenter] 改写包名失败:", e)
        return zip_bytes


def child_zip_path(child_id):
    with db() as conn:
        row = conn.execute("SELECT * FROM children WHERE child_id=?", (child_id,)).fetchone()
    if not row:
        return None, None
    p = ROOT / (row["package_path"] or "")
    if not p.exists():
        return None, None
    fname = safe_name(row["child_name"] or child_id) + ".zip"
    return p, fname


def build_parent_bundle(parent_id):
    """把整个父项目下所有子项目打成一个 bundle zip。
    bundle 内含 parent_bundle.json + children/<child_id>.zip（各子项目原始工程包）。"""
    with db() as conn:
        prow = conn.execute("SELECT * FROM parents WHERE parent_id=?", (parent_id,)).fetchone()
        if not prow:
            return None, None
        crows = conn.execute("SELECT * FROM children WHERE parent_id=? ORDER BY module_type,uploaded_at",
                             (parent_id,)).fetchall()
    bundle_meta = {
        "bundle_type": "dilan_parent_bundle",
        "schema_version": "1.0",
        "parent_id": prow["parent_id"],
        "parent_name": prow["parent_name"],
        "exported_at": now_str(),
        "children": [],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for c in crows:
            p = ROOT / (c["package_path"] or "")
            if not p.exists():
                continue
            arc = f"children/{c['child_id']}.zip"
            zf.writestr(arc, rewrite_package_name(p.read_bytes(), c["child_name"]))
            cd = child_to_dict(c)
            cd["package_arcname"] = arc
            bundle_meta["children"].append(cd)
        zf.writestr("parent_bundle.json", json.dumps(bundle_meta, ensure_ascii=False, indent=2))
    fname = safe_name(prow["parent_name"] or parent_id) + "_父项目打包.zip"
    return buf.getvalue(), fname


def delete_parent(parent_id):
    with _DB_LOCK, db() as conn:
        prow = conn.execute("SELECT * FROM parents WHERE parent_id=?", (parent_id,)).fetchone()
        if not prow:
            return {"ok": False, "error": "父项目不存在"}
        children = conn.execute("SELECT * FROM children WHERE parent_id=?", (parent_id,)).fetchall()
        n = 0
        for row in children:
            for rel in (row["package_path"], row["preview_path"]):
                if rel:
                    fp = ROOT / rel
                    if fp.exists():
                        try: fp.unlink()
                        except Exception: pass
            n += 1
        conn.execute("DELETE FROM children WHERE parent_id=?", (parent_id,))
        conn.execute("DELETE FROM parents WHERE parent_id=?", (parent_id,))
        conn.commit()
    return {"ok": True, "deleted_children": n}


def delete_child(child_id):
    with _DB_LOCK, db() as conn:
        row = conn.execute("SELECT * FROM children WHERE child_id=?", (child_id,)).fetchone()
        if not row:
            return {"ok": False, "error": "子项目不存在"}
        for rel in (row["package_path"], row["preview_path"]):
            if rel:
                fp = ROOT / rel
                if fp.exists():
                    try:
                        fp.unlink()
                    except Exception:
                        pass
        conn.execute("DELETE FROM children WHERE child_id=?", (child_id,))
        conn.commit()
    return {"ok": True}


# --------------------------------------------------------------------------- #
# multipart 解析（仅取一个文件字段 + 文本字段）
# --------------------------------------------------------------------------- #
def parse_multipart(body, content_type):
    fields, files = {}, {}
    m = re.search(r"boundary=([^;]+)", content_type or "")
    if not m:
        return fields, files
    boundary = m.group(1).strip().strip('"').encode()
    delim = b"--" + boundary
    for part in body.split(delim):
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        if b"\r\n\r\n" not in part:
            continue
        head, data = part.split(b"\r\n\r\n", 1)
        head_text = head.decode("utf-8", "replace")
        nm = re.search(r'name="([^"]*)"', head_text)
        if not nm:
            continue
        name = nm.group(1)
        fm = re.search(r'filename="([^"]*)"', head_text)
        if fm:
            files[name] = {"filename": fm.group(1), "data": data}
        else:
            fields[name] = data.decode("utf-8", "replace")
    return fields, files


# --------------------------------------------------------------------------- #
# HTTP 处理器
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    server_version = "DilanDataCenter/1.0"

    def log_message(self, fmt, *args):
        print("[datacenter]", self.address_string(), "-", fmt % args)

    # ----- 响应辅助 ----- #
    def cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def send_json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_bytes(self, data, content_type, filename=None, code=200):
        self.send_response(code)
        self.cors()
        self.send_header("Content-Type", content_type)
        if filename:
            from urllib.parse import quote
            self.send_header("Content-Disposition",
                             "attachment; filename*=UTF-8''" + quote(filename))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_file(self, path, content_type=None):
        path = Path(path)
        if not path.exists() or not path.is_file():
            self.send_json(404, {"error": "not found"})
            return
        if content_type is None:
            ext = path.suffix.lower()
            content_type = {
                ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                ".js": "application/javascript; charset=utf-8", ".json": "application/json; charset=utf-8",
                ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
                ".zip": "application/zip",
            }.get(ext, "application/octet-stream")
        data = path.read_bytes()
        self.send_response(200)
        self.cors()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def read_body(self):
        length = int(self.headers.get("Content-Length") or "0")
        return self.rfile.read(length) if length else b""

    # ----- 路由 ----- #
    def do_OPTIONS(self):
        self.send_response(204)
        self.cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        qs = parse_qs(parsed.query)
        try:
            if path in ("/", "/index.html"):
                self.send_file(STATIC_DIR / "index.html")
            elif path in ("/style.css", "/script.js"):
                self.send_file(STATIC_DIR / path.lstrip("/"))
            elif path == "/api/projects":
                self.api_projects()
            elif path == "/api/transfers":
                self.send_json(200, {"transfers": transfers_snapshot()})
            elif path == "/api/config":
                self.send_json(200, load_config())
            elif path.startswith("/api/download/child/"):
                self.api_download_child(path.rsplit("/", 1)[-1])
            elif path.startswith("/api/download/parent/"):
                self.api_download_parent(path.rsplit("/", 1)[-1])
            elif path.startswith("/api/preview/"):
                self.api_preview(path.rsplit("/", 1)[-1])
            else:
                self.send_json(404, {"error": "not found"})
        except Exception as e:
            print("GET ERROR", e)
            self.send_json(500, {"error": str(e)})

    def do_POST(self):
        parsed = urlsplit(self.path)
        path = parsed.path
        try:
            if path == "/api/upload":
                self.api_upload()
            elif path == "/api/upload/resolve-conflict":
                self.api_resolve_conflict()
            elif path == "/api/config":
                body = json.loads(self.read_body() or b"{}")
                self.send_json(200, save_config(body))
            elif path == "/api/merge":
                payload = json.loads(self.read_body() or b"{}")
                ids = payload.get("child_ids") or payload.get("children") or []
                if isinstance(ids, str):
                    ids = [ids]
                try:
                    result = merge_packages(ids, target_name=payload.get("target_name") or payload.get("name") or "",
                                            generator_name=payload.get("generator_name") or "")
                    self.send_json(200, result)
                except Exception as e:
                    self.send_json(400, {"error": str(e)})
            else:
                self.send_json(404, {"error": "not found"})
        except Exception as e:
            print("POST ERROR", e)
            self.send_json(500, {"error": str(e)})

    def do_DELETE(self):
        parsed = urlsplit(self.path)
        path = parsed.path
        try:
            if path.startswith("/api/child/"):
                self.send_json(200, delete_child(path.rsplit("/", 1)[-1]))
            elif path.startswith("/api/parent/"):
                from urllib.parse import unquote
                self.send_json(200, delete_parent(unquote(path.rsplit("/", 1)[-1])))
            else:
                self.send_json(404, {"error": "not found"})
        except Exception as e:
            self.send_json(500, {"error": str(e)})

    # ----- API 实现 ----- #
    def api_projects(self):
        with db() as conn:
            prows = conn.execute("SELECT * FROM parents ORDER BY updated_at DESC").fetchall()
            parents = [parent_to_dict(p, conn) for p in prows]
        self.send_json(200, {"parents": parents, "module_order": MODULE_ORDER,
                             "module_labels": MODULE_LABELS})

    def api_upload(self):
        ctype = self.headers.get("Content-Type") or ""
        body = self.read_body()
        if "multipart/form-data" in ctype:
            fields, files = parse_multipart(body, ctype)
            meta = json.loads(fields.get("meta") or "{}")
            on_conflict = fields.get("on_conflict") or meta.get("on_conflict") or "ask"
            f = files.get("file") or {}
            zip_bytes = f.get("data") or b""
        else:
            payload = json.loads(body or b"{}")
            meta = payload.get("meta") or payload
            on_conflict = payload.get("on_conflict") or "ask"
            data_url = payload.get("dataUrl") or payload.get("data_url") or ""
            import base64
            if "," in data_url:
                data_url = data_url.split(",", 1)[1]
            zip_bytes = base64.b64decode(data_url) if data_url else b""
        if not zip_bytes:
            self.send_json(400, {"error": "缺少工程包文件"})
            return
        _child_label = (meta.get("child_name") or meta.get("project_name") or "工程包")
        _tid = transfer_start("upload", _child_label, total=len(zip_bytes))
        transfer_update(_tid, done=len(zip_bytes))
        try:
            result = do_upload(zip_bytes, meta, on_conflict=on_conflict)
        except Exception as e:
            transfer_finish(_tid, "failed", str(e)); raise
        if result.get("conflict"):
            transfer_finish(_tid, "done")  # 等待用户选择，视为已接收
        elif result.get("ok"):
            transfer_finish(_tid, "done")
        else:
            transfer_finish(_tid, "failed", result.get("error") or "")
        self.send_json(200, result)

    def api_resolve_conflict(self):
        payload = json.loads(self.read_body() or b"{}")
        token = payload.get("token") or ""
        action = payload.get("action") or "new_version"
        if action not in ("overwrite", "new_version"):
            self.send_json(400, {"error": "action 必须是 overwrite 或 new_version"})
            return
        self.send_json(200, resolve_conflict(token, action))

    def api_download_child(self, child_id):
        p, fname = child_zip_path(child_id)
        if not p:
            self.send_json(404, {"error": "子项目工程包不存在"})
            return
        with db() as conn:
            row = conn.execute("SELECT child_name FROM children WHERE child_id=?", (child_id,)).fetchone()
        data = rewrite_package_name(p.read_bytes(), row["child_name"] if row else "")
        _tid = transfer_start("download", (row["child_name"] if row else fname) or fname, total=len(data))
        transfer_update(_tid, done=len(data)); transfer_finish(_tid, "done")
        self.send_bytes(data, "application/zip", fname)

    def api_download_parent(self, parent_id):
        data, fname = build_parent_bundle(parent_id)
        if data is None:
            self.send_json(404, {"error": "父项目不存在"})
            return
        _tid = transfer_start("download", fname, total=len(data))
        transfer_update(_tid, done=len(data)); transfer_finish(_tid, "done")
        self.send_bytes(data, "application/zip", fname)

    def api_preview(self, child_id):
        with db() as conn:
            row = conn.execute("SELECT preview_path FROM children WHERE child_id=?", (child_id,)).fetchone()
        if not row or not row["preview_path"]:
            self.send_json(404, {"error": "无预览图"})
            return
        self.send_file(ROOT / row["preview_path"])


# --------------------------------------------------------------------------- #
# 启动
# --------------------------------------------------------------------------- #
def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def main():
    init_db()
    cfg = load_config()
    host = os.environ.get("DILAN_DC_HOST", cfg.get("host", "0.0.0.0"))
    port = int(os.environ.get("DILAN_DC_PORT", cfg.get("port", 8777)))
    ip = lan_ip()
    print("=" * 52)
    print("帝蓝工作流-数据中心-V45")
    print(f"本机访问:   http://127.0.0.1:{port}")
    print(f"局域网访问: http://{ip}:{port}")
    print("制作端 / 审核端的“数据中心地址”请填写上面的局域网地址。")
    print("=" * 52)
    server = ThreadingHTTPServer((host, port), Handler)
    if os.environ.get("DILAN_NO_BROWSER") != "1":
        try:
            webbrowser.open(f"http://127.0.0.1:{port}")
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[datacenter] 已停止。")


if __name__ == "__main__":
    main()
