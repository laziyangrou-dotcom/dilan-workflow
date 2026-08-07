#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Dilan Video Workflow Tool V9 - parent/child projects + SC tabs edition.
Run: python image_server.py
Open: http://localhost:8787
"""
import base64
import io
import json
import mimetypes
import os
import re
import shutil
import time
import threading
import uuid
import urllib.request
import urllib.error
import http.server
import math
import hashlib
import importlib.util
import zipfile
import subprocess
import traceback
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, parse_qs, unquote

PORT = int(os.environ.get("DILAN_CHILD_PORT", "8787"))
OPENAI_BASE = "https://api.openai.com/v1"  # kept only for old-project compatibility
CHAT_MODEL = "gpt-4.1-mini"
ARK_BASE = "https://ark.cn-beijing.volces.com/api/v3"
DEFAULT_VIDEO_MODEL = "doubao-seedance-2-0-260128"
FAST_VIDEO_MODEL = "doubao-seedance-2-0-fast-260128"
V25_VIDEO_MODEL = "doubao-seedance-2-5-260628"
VIDEO_MODEL_LABELS = {
    "seedance2.0": DEFAULT_VIDEO_MODEL,
    "seedance2.0fast": FAST_VIDEO_MODEL,
    "seedance2.5": V25_VIDEO_MODEL,
    DEFAULT_VIDEO_MODEL: DEFAULT_VIDEO_MODEL,
    FAST_VIDEO_MODEL: FAST_VIDEO_MODEL,
    V25_VIDEO_MODEL: V25_VIDEO_MODEL,
}
VIDEO_MODEL_DISPLAY = {DEFAULT_VIDEO_MODEL: "seedance2.0", FAST_VIDEO_MODEL: "seedance2.0fast", V25_VIDEO_MODEL: "seedance2.5"}
PROJECT_MODULE_TYPE = "video"
PROJECT_MODULE_LABEL = "视频模块"
PROJECT_PACKAGE_TYPE = "dilan_project_package"
LEGACY_PROJECT_PACKAGE_TYPE = "image_storyboard_child_project_full"
PROJECT_MODULE_LABELS = {"material": "美术模块", "video": "视频模块"}
ROOT = Path(__file__).resolve().parent
SHARED_PROJECT_INDEX_DIR = ROOT.parent.parent / "shared_project_index"
SHARED_PARENTS_JSON = SHARED_PROJECT_INDEX_DIR / "parents.json"
import sys as _sys
if str(ROOT.parent) not in _sys.path:
    _sys.path.insert(0, str(ROOT.parent))
PROJECT_MODULE_KEYS = ["material", "video"]
PROJECT_MODULE_UI_LABELS = {"material": "美术", "video": "视频"}
PROJECTS_DIR = ROOT / "projects"
INPUT_DIR = ROOT / "input"
OUTPUT_DIR = ROOT / "output"
OUTPUT_IMAGE_DIR = OUTPUT_DIR / "image"  # legacy image folder, kept for compatibility
OUTPUT_VIDEO_DIR = OUTPUT_DIR / "video"
CONFIG_PATH = ROOT / "config.json"
USERS_PATH = ROOT / "users.json"
USAGE_PATH = ROOT / "usage.json"
EXPORTS_DIR = ROOT / "exports"
IMPORTS_DIR = ROOT / "imports"
PROJECT_SEP = "__CHILD__"

CATEGORIES = ["人物", "场景", "道具", "音频", "视频"]
GROUPED_CATEGORIES = set()  # V24：取消素材组，五类均平铺
USD_PER_1K_TOKENS = 0.005
DEFAULT_CONFIG = {
    "global_defaults": {
        "video_model": DEFAULT_VIDEO_MODEL,
        "video_ratio": "16:9",
        "video_resolution": "720p",
        "video_duration": 8,
        "video_seed": "",
        "video_generate_audio": True,
        "video_watermark": False,
        # legacy image keys are kept so old project files can still normalize cleanly
        "image_model": "gpt-image-2",
        "image_ratio": "16:9",
        "image_resolution": "1K",
        "image_quality": "medium",
        "default_scene_count": 1,
        "default_shot_count_per_scene": 1,
        "expand_all_scenes": True,
        "show_asset_panel": True,
        "output_rule": "output/video/{project}/{scene_code}/{shot_id}/",
        "input_rule": "input/{project}/{category}/",
        "usd_per_1k_tokens": USD_PER_1K_TOKENS,
        "seedance_fps": 24,
        "seedance_regular_no_video_cny_per_million": 46,
        "seedance_regular_with_video_cny_per_million": 28,
        "seedance_fast_no_video_cny_per_million": 37,
        "seedance_fast_with_video_cny_per_million": 22,
        "seedance_unknown_input_video_seconds": 4,
        "tos_enabled": False,
        "tos_bucket": "",
        "tos_region": "cn-beijing",
        "tos_endpoint": "https://tos-cn-beijing.volces.com",
        "tos_prefix": "dilan-workflow/video-module/",
        "tos_presign_expire_seconds": 86400,
        "tos_presign_refresh_margin_seconds": 600,
        "tos_upload_generated_videos": True,
    }
}

for d in (PROJECTS_DIR, INPUT_DIR, OUTPUT_DIR, OUTPUT_IMAGE_DIR, OUTPUT_VIDEO_DIR, EXPORTS_DIR, IMPORTS_DIR):
    d.mkdir(parents=True, exist_ok=True)

SIZE_MAP = {
    "1K": {"1:1": "1024x1024", "4:3": "1024x768", "16:9": "1536x864", "9:16": "864x1536", "21:9": "1792x768"},
    "2K": {"1:1": "2048x2048", "4:3": "2048x1536", "16:9": "2560x1440", "9:16": "1440x2560", "21:9": "2560x1080"},
    "4K": {"1:1": "4096x4096", "4:3": "4096x3072", "16:9": "3840x2160", "9:16": "2160x3840", "21:9": "4096x1755"},
}

INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
AT_PATTERN = re.compile(r"@([^\s@]+)")


def normalize_video_model(model):
    raw = str(model or "").strip()
    return VIDEO_MODEL_LABELS.get(raw, DEFAULT_VIDEO_MODEL)


def display_video_model(model):
    return VIDEO_MODEL_DISPLAY.get(normalize_video_model(model), "seedance2.0")


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def stamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def new_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def safe_name(name: str, fallback: str = "project") -> str:
    s = INVALID_CHARS.sub("_", str(name or "").strip())
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s[:80] or fallback


def safe_file_name(name: str, fallback: str = "file") -> str:
    s = INVALID_CHARS.sub("_", str(name or "").strip())
    s = s.replace("/", "_").replace("\\", "_").strip(" .")
    return s[:120] or fallback


def safe_replace_file(src: Path, dst: Path):
    """跨平台“改名/覆盖”：等价于改名，但目标已存在时原子覆盖而非报错。

    Windows 下 Path.rename / os.rename 在目标已存在时会抛 WinError 183
    （“当文件已存在时，无法创建该文件”），导致素材改名失败并可能残留孤儿文件，
    下次再改成同名时又触发 183。改用 os.replace：目标已存在则原子覆盖。
    调用方已用 asset_name_exists 保证素材名全局唯一，dst 若存在必是上次失败
    残留的孤儿文件，覆盖是安全的。
    """
    if src == dst:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.replace(str(src), str(dst))


def parse_positive_int(value, default=None):
    """Parse a positive integer from user input. Returns default when empty/invalid."""
    text = str(value or "").strip()
    if not text or not re.fullmatch(r"\d+", text):
        return default
    try:
        n = int(text)
    except Exception:
        return default
    return n if n >= 1 else default


def scene_code_from_number(num):
    num = parse_positive_int(num, 1) or 1
    return f"SC-{num:04d}"


def scene_number_from_scene(scene, fallback=None):
    n = parse_positive_int(scene.get("scene_number"), None)
    if n is not None:
        return n
    code = str(scene.get("scene_code") or "")
    m = re.search(r"(\d+)", code)
    if m:
        try:
            return max(1, int(m.group(1)))
        except Exception:
            pass
    return parse_positive_int(scene.get("sort_order"), fallback) or (fallback or 1)


def max_scene_number(scenes):
    nums = [scene_number_from_scene(s, None) for s in (scenes or [])]
    nums = [n for n in nums if n is not None]
    return max(nums) if nums else 0


def validate_scene_range(start, end):
    start_blank = str(start or "").strip() == ""
    end_blank = str(end or "").strip() == ""
    if start_blank and end_blank:
        return None, None
    if start_blank or end_blank:
        raise ValueError("分镜范围需要同时填写开始和结束，或两个都不填")
    a = parse_positive_int(start, None)
    b = parse_positive_int(end, None)
    if a is None or b is None:
        raise ValueError("分镜范围只能填写数字，且必须大于 0")
    if a > b:
        raise ValueError("分镜范围必须左小于等于右，例如 12 到 30，或 12 到 12")
    return a, b


def read_json_file(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json_file(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_config():
    if not CONFIG_PATH.exists():
        write_json_file(CONFIG_PATH, DEFAULT_CONFIG)
    cfg = read_json_file(CONFIG_PATH, DEFAULT_CONFIG) or DEFAULT_CONFIG.copy()
    merged = json.loads(json.dumps(DEFAULT_CONFIG, ensure_ascii=False))
    merged.setdefault("global_defaults", {})
    merged["global_defaults"].update(cfg.get("global_defaults") or {})
    return merged


def save_config(cfg):
    merged = load_config()
    incoming = cfg.get("global_defaults", cfg)
    allowed = set(DEFAULT_CONFIG["global_defaults"].keys())
    for k, v in incoming.items():
        if k in allowed:
            merged["global_defaults"][k] = v
    merged["global_defaults"]["default_scene_count"] = 1
    merged["global_defaults"]["default_shot_count_per_scene"] = 1
    merged["global_defaults"]["output_rule"] = "output/video/{project}/{scene_code}/{shot_id}/"
    merged["global_defaults"]["input_rule"] = "input/{project}/{category}/"
    write_json_file(CONFIG_PATH, merged)
    return merged


def split_project_ref(project_ref: str):
    raw = str(project_ref or "")
    if PROJECT_SEP in raw:
        parent, child = raw.split(PROJECT_SEP, 1)
        return safe_name(parent, "父级项目"), safe_name(child, "子项目")
    return None, safe_name(raw, "project")


def make_project_ref(parent_id: str, child_id: str) -> str:
    return f"{safe_name(parent_id, '父级项目')}{PROJECT_SEP}{safe_name(child_id, '子项目')}"


def parent_path(parent_id: str) -> Path:
    pid = safe_name(parent_id, "父级项目")
    p = (PROJECTS_DIR / pid).resolve()
    if PROJECTS_DIR.resolve() not in p.parents and p != PROJECTS_DIR.resolve():
        raise ValueError("invalid parent path")
    return p


def parent_json_path(parent_id: str) -> Path:
    return parent_path(parent_id) / "parent.json"


def project_path(project_id: str) -> Path:
    parent_id, child_id = split_project_ref(project_id)
    if parent_id:
        p = (parent_path(parent_id) / "children" / child_id).resolve()
    else:
        p = (PROJECTS_DIR / child_id).resolve()
    if PROJECTS_DIR.resolve() not in p.parents and p != PROJECTS_DIR.resolve():
        raise ValueError("invalid project path")
    return p


def project_json_path(project_id: str) -> Path:
    return project_path(project_id) / "project.json"


def ensure_project_dirs(project_id: str):
    storage = safe_name(project_id, "project")
    for cat in CATEGORIES:
        (INPUT_DIR / storage / cat).mkdir(parents=True, exist_ok=True)
    (OUTPUT_IMAGE_DIR / storage).mkdir(parents=True, exist_ok=True)
    (OUTPUT_VIDEO_DIR / storage).mkdir(parents=True, exist_ok=True)
    project_path(project_id).mkdir(parents=True, exist_ok=True)


def tool_root_for_module(module_type: str) -> Path:
    return ROOT.parent / str(module_type or PROJECT_MODULE_TYPE)


def module_projects_dir(module_type: str) -> Path:
    return tool_root_for_module(module_type) / "projects"


def module_parent_path(module_type: str, parent_id: str) -> Path:
    return module_projects_dir(module_type) / safe_name(parent_id, "父级项目")


def module_parent_json_path(module_type: str, parent_id: str) -> Path:
    return module_parent_path(module_type, parent_id) / "parent.json"


def module_child_dirs(module_type: str, parent_id: str):
    base = module_parent_path(module_type, parent_id) / "children"
    if not base.exists():
        return []
    return [p for p in base.iterdir() if p.is_dir() and (p / "project.json").exists()]


def module_child_count(module_type: str, parent_id: str) -> int:
    return len(module_child_dirs(module_type, parent_id))


def read_json_file_safe(path: Path, default=None):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def load_shared_parent_index():
    SHARED_PROJECT_INDEX_DIR.mkdir(parents=True, exist_ok=True)
    data = read_json_file_safe(SHARED_PARENTS_JSON, None)
    if not isinstance(data, dict):
        data = {"version": 1, "parents": []}
    if not isinstance(data.get("parents"), list):
        data["parents"] = []
    return data


def save_shared_parent_index(data):
    SHARED_PROJECT_INDEX_DIR.mkdir(parents=True, exist_ok=True)
    write_json_file(SHARED_PARENTS_JSON, data)


def merge_parent_to_shared(parent_id, parent_name=None, created_at=None, updated_at=None):
    pid = safe_name(parent_id, "父级项目")
    if not pid:
        return
    now = now_str()
    data = load_shared_parent_index()
    found = None
    for p in data.get("parents", []):
        if safe_name(p.get("parent_id") or "", "父级项目") == pid:
            found = p
            break
    if not found:
        found = {"parent_id": pid, "parent_name": parent_name or pid, "created_at": created_at or now, "updated_at": updated_at or now}
        data["parents"].append(found)
    else:
        if parent_name:
            found["parent_name"] = parent_name
        found["updated_at"] = updated_at or found.get("updated_at") or now
        found["created_at"] = found.get("created_at") or created_at or now
    save_shared_parent_index(data)


def remove_parent_from_shared(parent_id):
    pid = safe_name(parent_id, "父级项目")
    data = load_shared_parent_index()
    data["parents"] = [p for p in data.get("parents", []) if safe_name(p.get("parent_id") or "", "父级项目") != pid]
    save_shared_parent_index(data)


def ensure_parent_folder_for_module(module_type, parent_id, parent_name=None):
    pid = safe_name(parent_id, "父级项目")
    fp = module_parent_json_path(module_type, pid)
    fp.parent.mkdir(parents=True, exist_ok=True)
    existing = read_json_file_safe(fp, None)
    if not isinstance(existing, dict):
        existing = {"parent_id": pid, "parent_name": parent_name or pid, "created_at": now_str(), "updated_at": now_str(), "version": 2}
    else:
        existing["parent_id"] = pid
        if parent_name:
            existing["parent_name"] = parent_name
        existing["parent_name"] = existing.get("parent_name") or pid
        existing["updated_at"] = now_str()
        existing["version"] = existing.get("version") or 2
    write_json_file(fp, existing)
    return existing


def ensure_parent_across_modules(parent_id, parent_name=None):
    pid = safe_name(parent_id, "父级项目")
    pname = safe_name(parent_name or pid, "父级项目")
    merge_parent_to_shared(pid, pname)
    for m in PROJECT_MODULE_KEYS:
        ensure_parent_folder_for_module(m, pid, pname)


def shared_parent_exists(parent_id):
    pid = safe_name(parent_id, "父级项目")
    if not pid:
        return False
    data = load_shared_parent_index()
    if any(safe_name(p.get("parent_id") or "", "父级项目") == pid for p in data.get("parents", [])):
        return True
    for m in PROJECT_MODULE_KEYS:
        if module_parent_json_path(m, pid).exists():
            return True
    return False




def find_existing_parent_by_identity(parent_id=None, parent_name=None):
    """Return an existing local parent matched by package parent_id first, then parent_name.
    This lets imported工程包 automatically return to its original parent project when possible,
    even across machines where the internal id may differ but the displayed parent name is the same.
    """
    pid = safe_name(parent_id or "", "")
    pname = safe_name(parent_name or "", "")
    if not pid and not pname:
        return None
    for p in collect_shared_parent_items():
        existing_id = safe_name(p.get("parent_id") or "", "")
        existing_name = safe_name(p.get("parent_name") or existing_id, "")
        if pid and existing_id == pid:
            return {"parent_id": existing_id, "parent_name": existing_name or existing_id, "matched_by": "parent_id"}
    if pname:
        for p in collect_shared_parent_items():
            existing_id = safe_name(p.get("parent_id") or "", "")
            existing_name = safe_name(p.get("parent_name") or existing_id, "")
            if existing_name == pname:
                return {"parent_id": existing_id, "parent_name": existing_name or existing_id, "matched_by": "parent_name"}
    return None


def resolve_import_parent_identity(manifest, project_data, parent_data, old_parent=None):
    manifest = manifest if isinstance(manifest, dict) else {}
    project_data = project_data if isinstance(project_data, dict) else {}
    parent_data = parent_data if isinstance(parent_data, dict) else {}
    scope = manifest.get("project_scope") if isinstance(manifest.get("project_scope"), dict) else {}
    parent_id = (
        project_data.get("parent_id")
        or manifest.get("parent_id")
        or scope.get("parent_id")
        or parent_data.get("parent_id")
        or old_parent
        or "导入项目"
    )
    parent_name = (
        project_data.get("parent_name")
        or manifest.get("parent_name")
        or scope.get("parent_name")
        or parent_data.get("parent_name")
        or parent_id
        or "导入项目"
    )
    source_parent_id = safe_name(parent_id, "导入项目")
    source_parent_name = safe_name(parent_name, source_parent_id)
    matched = find_existing_parent_by_identity(source_parent_id, source_parent_name)
    if matched:
        return {
            "source_parent_id": source_parent_id,
            "source_parent_name": source_parent_name,
            "target_parent_id": matched["parent_id"],
            "target_parent_name": matched.get("parent_name") or matched["parent_id"],
            "matched_by": matched.get("matched_by") or "existing",
            "created_new_parent": False,
        }
    return {
        "source_parent_id": source_parent_id,
        "source_parent_name": source_parent_name,
        "target_parent_id": source_parent_id,
        "target_parent_name": source_parent_name,
        "matched_by": "created_from_package",
        "created_new_parent": True,
    }

def collect_shared_parent_items():
    parents = {}
    data = load_shared_parent_index()
    for p in data.get("parents", []):
        pid = safe_name(p.get("parent_id") or "", "父级项目")
        if not pid:
            continue
        parents[pid] = {
            "parent_id": pid,
            "parent_name": p.get("parent_name") or pid,
            "created_at": p.get("created_at") or "",
            "updated_at": p.get("updated_at") or "",
        }
    for m in PROJECT_MODULE_KEYS:
        base = module_projects_dir(m)
        if not base.exists():
            continue
        for d in base.iterdir():
            fp = d / "parent.json"
            if not d.is_dir() or not fp.exists():
                continue
            pdata = read_json_file_safe(fp, {}) or {}
            pid = safe_name(pdata.get("parent_id") or d.name, "父级项目")
            if not pid:
                continue
            item = parents.setdefault(pid, {"parent_id": pid, "parent_name": pdata.get("parent_name") or pid, "created_at": pdata.get("created_at") or "", "updated_at": pdata.get("updated_at") or ""})
            if pdata.get("parent_name") and (not item.get("parent_name") or item.get("parent_name") == pid):
                item["parent_name"] = pdata.get("parent_name")
            item["updated_at"] = max(str(item.get("updated_at") or ""), str(pdata.get("updated_at") or ""))
            item["created_at"] = item.get("created_at") or pdata.get("created_at") or ""
    for pid, item in parents.items():
        counts = {m: module_child_count(m, pid) for m in PROJECT_MODULE_KEYS}
        item["module_counts"] = counts
        item["module_labels"] = PROJECT_MODULE_UI_LABELS
        item["child_count"] = counts.get(PROJECT_MODULE_TYPE, 0)
        item["total_child_count"] = sum(counts.values())
        if not item.get("updated_at"):
            item["updated_at"] = ""
    # Persist merged parents so fresh modules see the same parent list later.
    merged = {"version": 1, "parents": [{k: v for k, v in item.items() if k in ("parent_id", "parent_name", "created_at", "updated_at")} for item in parents.values()]}
    save_shared_parent_index(merged)
    return sorted(parents.values(), key=lambda x: x.get("updated_at") or "", reverse=True)


def delete_parent_across_modules(parent_id):
    pid = safe_name(parent_id, "父级项目")
    for m in PROJECT_MODULE_KEYS:
        base = module_parent_path(m, pid)
        # Remove input/output files for every child project in that module.
        for child in module_child_dirs(m, pid):
            ref = make_project_ref(pid, child.name)
            mroot = tool_root_for_module(m)
            for q in (mroot / "input" / safe_name(ref), mroot / "output" / "image" / safe_name(ref), mroot / "output" / "video" / safe_name(ref)):
                if q.exists():
                    shutil.rmtree(q)
        if base.exists():
            shutil.rmtree(base)
    remove_parent_from_shared(pid)


def rename_parent_across_modules(parent_id, new_name):
    pid = safe_name(parent_id, "父级项目")
    pname = safe_name(new_name, "父级项目")
    merge_parent_to_shared(pid, pname, updated_at=now_str())
    for m in PROJECT_MODULE_KEYS:
        fp = module_parent_json_path(m, pid)
        if fp.exists():
            data = read_json_file_safe(fp, {}) or {}
            data["parent_id"] = pid
            data["parent_name"] = pname
            data["updated_at"] = now_str()
            write_json_file(fp, data)


def create_parent_data(name):
    pid = safe_name(name, "父级项目")
    return {"parent_id": pid, "parent_name": pid, "created_at": now_str(), "updated_at": now_str(), "version": 2}


def load_parent(parent_id):
    fp = parent_json_path(parent_id)
    if not fp.exists():
        raise FileNotFoundError("parent project not found")
    data = read_json_file(fp, {}) or {}
    data["parent_id"] = safe_name(data.get("parent_id") or parent_id, "父级项目")
    data["parent_name"] = safe_name(data.get("parent_name") or data["parent_id"], "父级项目")
    return data


def save_parent(parent_id, data):
    data["parent_id"] = safe_name(data.get("parent_id") or parent_id, "父级项目")
    data["parent_name"] = safe_name(data.get("parent_name") or data["parent_id"], "父级项目")
    data["updated_at"] = now_str()
    parent_path(parent_id).mkdir(parents=True, exist_ok=True)
    write_json_file(parent_json_path(parent_id), data)
    return data


def list_child_project_dirs(parent_id):
    base = parent_path(parent_id) / "children"
    if not base.exists():
        return []
    return [p for p in base.iterdir() if p.is_dir() and (p / "project.json").exists()]


def parent_meta(parent_id):
    try:
        data = load_parent(parent_id)
    except Exception:
        data = {"parent_id": parent_id, "parent_name": parent_id, "created_at": "", "updated_at": ""}
    child_count = len(list_child_project_dirs(parent_id))
    return {
        "parent_id": safe_name(parent_id, "父级项目"),
        "parent_name": data.get("parent_name") or parent_id,
        "created_at": data.get("created_at", ""),
        "updated_at": data.get("updated_at", ""),
        "child_count": child_count,
    }


def project_default_settings(defaults):
    return {
        "video_model": defaults.get("video_model", DEFAULT_VIDEO_MODEL),
        "video_ratio": defaults.get("video_ratio", "16:9"),
        "video_resolution": defaults.get("video_resolution", "720p"),
        "video_duration": int(defaults.get("video_duration", 8) or 8),
        "video_seed": str(defaults.get("video_seed", "") or ""),
        "video_generate_audio": bool(defaults.get("video_generate_audio", True)),
        "video_watermark": bool(defaults.get("video_watermark", False)),
        # legacy image settings retained for old project compatibility
        "image_model": defaults.get("image_model", "gpt-image-2"),
        "image_ratio": defaults.get("image_ratio", "16:9"),
        "image_resolution": defaults.get("image_resolution", "1K"),
        "image_quality": defaults.get("image_quality", "medium"),
    }


def make_tab(index=1, settings=None):
    settings = settings or project_default_settings(load_config()["global_defaults"])
    return {
        "tab_id": new_id("tab"),
        "tab_name": f"标签{index}",
        "sort_order": index,
        "settings": dict(settings),
        "referenced_assets": [],
        "messages": [],
        "generated_images": [],  # compatibility name: video records are stored here with media_type='video'
        "generated_videos": [],
        "current_base_image_id": None,
        "context_summary": "",
        "draft_prompt": "",
    }


def make_shot(index=1, settings=None):
    tab = make_tab(1, settings)
    return {
        "shot_id": new_id("shot"),
        "shot_code": f"{index:02d}",
        "sort_order": index,
        "status": "unconfirmed",
        "duration_seconds": "",
        "tabs": [tab],
        "active_tab_id": tab["tab_id"],
        "storyboard_candidates": [],
    }


def make_scene(index=1, scene_number=None):
    num = parse_positive_int(scene_number, index) or index or 1
    return {
        "scene_id": new_id("scene"),
        "scene_number": num,
        "scene_code": scene_code_from_number(num),
        "sort_order": index,
        "expanded": True,
        "time_start": {"h": "", "m": "", "s": ""},
        "time_end": {"h": "", "m": "", "s": ""},
        "shots": [],
        "review": {
            "current_round": 1,
            "max_round_available": 1,
            "rounds": {"1": make_review_round("一审")},
        },
    }


def create_project_data(name, parent_id=None, scene_start=None, scene_end=None):
    cfg = load_config()["global_defaults"]
    settings = project_default_settings(cfg)
    child_id = safe_name(name, "子项目")
    parent_id = safe_name(parent_id, "父级项目") if parent_id else None
    project_id = make_project_ref(parent_id, child_id) if parent_id else child_id
    start, end = validate_scene_range(scene_start, scene_end)
    scenes = []
    if start is not None:
        for order, num in enumerate(range(start, end + 1), 1):
            scene = make_scene(order, num)
            scene["expanded"] = bool(cfg.get("expand_all_scenes", True))
            scene["shots"].append(make_shot(1, settings))
            scenes.append(scene)
    data = {
        "project_id": project_id,
        "project_name": child_id,
        "child_id": child_id,
        "parent_id": parent_id,
        "version": 3,
        "created_at": now_str(),
        "updated_at": now_str(),
        "project_settings": {
            **settings,
            "expand_all_scenes": bool(cfg.get("expand_all_scenes", True)),
            "show_asset_panel": bool(cfg.get("show_asset_panel", True)),
        },
        "assets": [],
        "asset_groups": [],
        "scenes": scenes,
    }
    return normalize_project(data, rename_dirs=False)


_PROJECT_MUTATION_LOCKS = {}
_PROJECT_MUTATION_LOCKS_GUARD = threading.Lock()


def project_mutation_lock(pid):
    """Per-project lock so concurrent video-generate / running-status / save
    operations serialize their read-modify-write of the project JSON. Without
    it, parallel Seedance tasks could lose each other's appended videos
    (last-write-wins). The slow Seedance poll stays OUTSIDE this lock so
    generations still run in parallel; only the brief load->save is serialized."""
    key = str(pid or "")
    with _PROJECT_MUTATION_LOCKS_GUARD:
        lk = _PROJECT_MUTATION_LOCKS.get(key)
        if lk is None:
            lk = threading.RLock()
            _PROJECT_MUTATION_LOCKS[key] = lk
    return lk


def load_project(pid):
    fp = project_json_path(pid)
    if not fp.exists():
        raise FileNotFoundError("project not found")
    data = read_json_file(fp, {}) or {}
    return normalize_project(data, rename_dirs=False)


def save_project(pid, data):
    data["updated_at"] = now_str()
    if PROJECT_SEP in str(pid or ""):
        parent_id, child_id = split_project_ref(pid)
        data["parent_id"] = parent_id
        data["child_id"] = child_id
        data["project_id"] = make_project_ref(parent_id, child_id)
    else:
        data["project_id"] = safe_name(pid, "project")
    normalize_project(data, rename_dirs=True)
    write_json_file(project_json_path(data["project_id"]), data)
    if data.get("parent_id"):
        try:
            p = load_parent(data["parent_id"])
        except Exception:
            p = create_parent_data(data["parent_id"])
        p["updated_at"] = data["updated_at"]
        save_parent(data["parent_id"], p)
    return data


def _iter_project_tabs(data):
    # 遍历工程内所有标签页（scene -> shot -> tab），供生成结果并集保护使用。
    for scene in (data.get("scenes") or []):
        for shot in (scene.get("shots") or []):
            for tab in (shot.get("tabs") or []):
                if isinstance(tab, dict):
                    yield tab


def merge_generated_media_preserve(client_data, disk_data):
    # 把磁盘上已存在、但客户端整份快照里缺失的生成结果（视频/图片）与其生成消息并回
    # 客户端数据，避免“前端整份覆盖保存”把后台并发刚写回的结果吞掉。生成结果只会通过
    # 专门的删除接口移除，绝不会经由整份保存接口删除，因此这里只做“按 id 补齐”
    # （只增不减），语义安全，不会让已删除的结果复活。
    if not isinstance(client_data, dict) or not isinstance(disk_data, dict):
        return
    disk_tabs = {}
    for tab in _iter_project_tabs(disk_data):
        tid = tab.get("tab_id")
        if tid:
            disk_tabs[tid] = tab
    if not disk_tabs:
        return
    for tab in _iter_project_tabs(client_data):
        dtab = disk_tabs.get(tab.get("tab_id"))
        if not dtab:
            continue
        # generated_images 是视频/图片本体，generated_videos 是它的镜像，按 image_id 补齐。
        imgs = tab.get("generated_images")
        if not isinstance(imgs, list):
            imgs = []
        have = {it.get("image_id") for it in imgs if isinstance(it, dict)}
        for it in (dtab.get("generated_images") or []):
            if isinstance(it, dict) and it.get("image_id") not in have:
                imgs.append(it); have.add(it.get("image_id"))
        tab["generated_images"] = imgs
        tab["generated_videos"] = imgs
        # 生成/对话消息按 message_id 只补不删，补齐后按时间排序，避免结果气泡丢失。
        msgs = tab.get("messages")
        if not isinstance(msgs, list):
            msgs = []
        mhave = {m.get("message_id") for m in msgs if isinstance(m, dict)}
        added = False
        for m in (dtab.get("messages") or []):
            if isinstance(m, dict) and m.get("message_id") and m.get("message_id") not in mhave:
                msgs.append(m); mhave.add(m.get("message_id")); added = True
        if added:
            msgs.sort(key=lambda mm: str(mm.get("time") or "") if isinstance(mm, dict) else "")
        tab["messages"] = msgs
        # 运行状态兜底（与前端 adoptProject 一致）：整份覆盖保存不得把磁盘上已完成(success/error)
        # 的视频状态退回“生成中”。真正的新生成会由生成接口直接把 running 写盘，不依赖这条整份保存，
        # 故当磁盘已是终态、而客户端快照仍是 running/缺省时，以磁盘终态为准，避免刷新后假“生成中”。
        _dst = (dtab.get("last_video_status") or {}).get("state")
        _cst = (tab.get("last_video_status") or {}).get("state")
        if _dst in ("success", "error") and _cst in ("running", "submitting", "polling", None):
            tab["last_video_status"] = dtab.get("last_video_status")


def operation_log_path(pid):
    # Snapshot / operation-log feature removed: keep helper for compatibility only.
    return project_path(pid) / "operation_log.jsonl"


def snapshots_dir(pid):
    # Snapshot feature removed: keep helper for compatibility only.
    return project_path(pid) / "snapshots"


def project_stats(data):
    scene_count = len(data.get("scenes", []) or [])
    shot_count = 0
    tab_count = 0
    image_count = 0
    candidate_count = 0
    for scene in data.get("scenes", []) or []:
        for shot in scene.get("shots", []) or []:
            shot_count += 1
            candidate_count += len(shot.get("storyboard_candidates", []) or [])
            for tab in shot.get("tabs", []) or []:
                tab_count += 1
                image_count += len(tab.get("generated_images", []) or [])
    return {"scene_count": scene_count, "shot_count": shot_count, "tab_count": tab_count, "generated_image_count": image_count, "storyboard_candidate_count": candidate_count}


def append_operation_log(pid, action, user_name="", **details):
    # Operation-log feature removed. No file is written.
    return None


def create_project_snapshot(pid, reason="manual", data=None, user_name=""):
    # Snapshot feature removed. No snapshot file is written.
    return None


def read_operation_logs(pid, limit=500):
    return []


def list_project_snapshots(pid):
    return []


def normalize_time_obj(v):
    if not isinstance(v, dict):
        return {"h": "", "m": "", "s": ""}
    return {"h": str(v.get("h", "")), "m": str(v.get("m", "")), "s": str(v.get("s", ""))}


def make_review_round(label="一审", status="pending", locked=False, shots=None):
    return {
        "label": label,
        "status": status if status in ("pending", "approved", "rejected") else "pending",
        "locked": bool(locked),
        "submitted_at": None,
        "submitted_by": None,
        "shots": shots,
    }


def normalize_scene_review(scene):
    review = scene.get("review") if isinstance(scene.get("review"), dict) else {}
    current = parse_positive_int(review.get("current_round"), 1) or 1
    current = min(max(current, 1), 3)
    max_available = parse_positive_int(review.get("max_round_available"), 1) or 1
    max_available = min(max(max_available, current, 1), 3)
    rounds = review.get("rounds") if isinstance(review.get("rounds"), dict) else {}
    labels = {"1": "一审", "2": "二审", "3": "三审"}
    for n in range(1, max_available + 1):
        key = str(n)
        raw = rounds.get(key) if isinstance(rounds.get(key), dict) else {}
        status = raw.get("status", "pending")
        if status not in ("pending", "approved", "rejected"):
            status = "pending"
        rounds[key] = {
            **make_review_round(labels[key], status, raw.get("locked", False), raw.get("shots")),
            **raw,
            "label": labels[key],
            "status": status,
            "locked": bool(raw.get("locked", False)),
        }
        rounds[key].setdefault("notes", {"text": "", "images": []})
    review = {
        "current_round": current,
        "max_round_available": max_available,
        "rounds": rounds,
    }
    scene["review"] = review
    return review


def project_temp_public_prefix(pid: str) -> str:
    try:
        rel = project_path(pid).relative_to(PROJECTS_DIR).as_posix()
    except Exception:
        rel = safe_name(pid, "project")
    return f"/temp/{rel}"


def normalize_tab(tab, index, default_settings):
    tab.setdefault("tab_id", new_id("tab"))
    tab["sort_order"] = int(tab.get("sort_order") or index)
    tab["tab_name"] = safe_file_name(tab.get("tab_name") or f"标签{index}", f"标签{index}")
    tab.setdefault("settings", dict(default_settings))
    if not isinstance(tab.get("settings"), dict):
        tab["settings"] = dict(default_settings)
    tab["settings"] = {**dict(default_settings), **tab.get("settings", {})}
    tab.setdefault("referenced_assets", [])
    tab.setdefault("messages", [])
    for m in tab.get("messages", []):
        m.setdefault("message_id", new_id("msg"))
    tab.setdefault("generated_images", [])
    tab.setdefault("generated_videos", [])
    # Keep generated_images as the compatibility source-of-truth. Video records carry media_type='video'.
    if tab.get("generated_videos") and not tab.get("generated_images"):
        tab["generated_images"] = tab.get("generated_videos", [])
    for gi in tab.get("generated_images", []):
        gi.setdefault("image_id", gi.get("video_id") or new_id("vid"))
        gi.setdefault("video_id", gi.get("image_id"))
        gi.setdefault("media_type", "video" if str(gi.get("file_path", "")).startswith("/output/video/") else gi.get("media_type", "image"))
    tab["generated_videos"] = tab.get("generated_images", [])
    tab.setdefault("current_base_image_id", None)
    tab.setdefault("context_summary", "")
    tab.setdefault("draft_prompt", "")
    return tab


def ensure_shot_tabs(shot, default_settings):
    tabs = shot.get("tabs")
    if not isinstance(tabs, list) or not tabs:
        migrated = make_tab(1, shot.get("settings") or default_settings)
        for k in ("referenced_assets", "messages", "generated_images", "generated_videos", "current_base_image_id", "context_summary", "draft_prompt"):
            if k in shot:
                migrated[k] = shot.get(k)
        tabs = [migrated]
    tabs = sorted(tabs, key=lambda t: int(t.get("sort_order") or 999999))
    norm_tabs = []
    for i, tab in enumerate(tabs, 1):
        tab = normalize_tab(tab, i, default_settings)
        tab["sort_order"] = i
        norm_tabs.append(tab)
    shot["tabs"] = norm_tabs
    if not any(t.get("tab_id") == shot.get("active_tab_id") for t in norm_tabs):
        shot["active_tab_id"] = norm_tabs[0]["tab_id"]
    # Backward mirrors for older tools that might read shot.settings/messages directly.
    active = get_active_tab(shot)
    for k in ("settings", "referenced_assets", "messages", "generated_images", "generated_videos", "current_base_image_id", "context_summary", "draft_prompt"):
        shot[k] = active.get(k)
    return shot


def get_active_tab(shot, tab_id=None):
    tabs = shot.get("tabs") or []
    desired = tab_id or shot.get("active_tab_id")
    for t in tabs:
        if t.get("tab_id") == desired:
            return t
    if tabs:
        return tabs[0]
    tab = make_tab(1)
    shot["tabs"] = [tab]
    shot["active_tab_id"] = tab["tab_id"]
    return tab


def normalize_project(data, rename_dirs=True):
    parent_id = data.get("parent_id")
    parent_id = safe_name(parent_id, "父级项目") if parent_id else None
    child_id = safe_name(data.get("child_id") or data.get("project_name") or data.get("project_id") or "project", "子项目")
    if parent_id:
        data["parent_id"] = parent_id
        data["child_id"] = child_id
        data["project_id"] = make_project_ref(parent_id, child_id)
        data["project_name"] = safe_name(data.get("project_name") or child_id, "子项目")
    else:
        data["project_id"] = safe_name(data.get("project_id") or child_id, "project")
        data["project_name"] = safe_name(data.get("project_name") or data["project_id"], "project")
        data["child_id"] = data.get("child_id") or data["project_id"]
    data["version"] = max(int(data.get("version") or 1), 3)
    data.setdefault("assets", [])
    data.setdefault("asset_groups", [])
    ensure_asset_groups(data)
    data.setdefault("scenes", [])
    defaults = project_default_settings(load_config()["global_defaults"])
    data.setdefault("project_settings", defaults)
    data["project_settings"] = {**defaults, **(data.get("project_settings") or {})}
    ensure_project_dirs(data["project_id"])

    old_codes = {s.get("scene_id"): s.get("scene_code") for s in data.get("scenes", [])}
    raw_scenes = data.get("scenes", []) if isinstance(data.get("scenes"), list) else []
    seen_numbers = set()
    normalized_scenes = []
    fallback_next = 1
    for scene in raw_scenes:
        scene.setdefault("scene_id", new_id("scene"))
        num = scene_number_from_scene(scene, fallback_next)
        if num in seen_numbers:
            num = max(seen_numbers) + 1 if seen_numbers else num
            while num in seen_numbers:
                num += 1
        seen_numbers.add(num)
        scene["scene_number"] = num
        scene["scene_code"] = scene_code_from_number(num)
        normalized_scenes.append(scene)
        fallback_next = max(fallback_next + 1, num + 1)
    scenes = sorted(normalized_scenes, key=lambda s: scene_number_from_scene(s, 999999))
    for si, scene in enumerate(scenes, 1):
        scene["sort_order"] = si
        scene["scene_number"] = scene_number_from_scene(scene, si)
        scene["scene_code"] = scene_code_from_number(scene["scene_number"])
        scene.setdefault("expanded", True)
        scene["time_start"] = normalize_time_obj(scene.get("time_start"))
        scene["time_end"] = normalize_time_obj(scene.get("time_end"))
        normalize_scene_review(scene)
        shots = sorted(scene.get("shots", []), key=lambda x: int(x.get("sort_order") or 999999))
        for hi, shot in enumerate(shots, 1):
            shot.setdefault("shot_id", new_id("shot"))
            shot["sort_order"] = hi
            shot["shot_code"] = f"{hi:02d}"
            shot.setdefault("status", "unconfirmed")
            shot.setdefault("duration_seconds", "")
            shot.setdefault("storyboard_candidates", [])
            ensure_shot_tabs(shot, data.get("project_settings") or defaults)
        scene["shots"] = shots
    data["scenes"] = scenes

    if rename_dirs:
        rename_scene_dirs(data["project_id"], old_codes, data["scenes"])
    return data


def rename_scene_dirs(project_id, old_codes, scenes):
    # Rename both legacy image output and current video output folders.
    for base_root in (OUTPUT_IMAGE_DIR, OUTPUT_VIDEO_DIR):
        base = base_root / safe_name(project_id, "project")
        base.mkdir(parents=True, exist_ok=True)
        moves = []
        for scene in scenes:
            old = old_codes.get(scene.get("scene_id"))
            new = scene.get("scene_code")
            if old and old != new and (base / old).exists():
                tmp = base / f"__renaming__{scene.get('scene_id')}"
                if tmp.exists():
                    shutil.rmtree(tmp)
                moves.append((base / old, tmp, base / new))
        for src, tmp, dst in moves:
            if src.exists():
                src.rename(tmp)
        for src, tmp, dst in moves:
            if dst.exists():
                for item in tmp.iterdir():
                    target = dst / item.name
                    if target.exists():
                        continue
                    item.rename(target)
                shutil.rmtree(tmp)
            elif tmp.exists():
                tmp.rename(dst)


def project_meta(pid):
    try:
        data = load_project(pid)
    except Exception:
        data = {}
    image_count = 0
    for scene in data.get("scenes", []):
        for shot in scene.get("shots", []):
            for tab in shot.get("tabs", []):
                image_count += len(tab.get("generated_images", []))
    return {
        "project_id": data.get("project_id") or pid,
        "project_name": data.get("project_name") or split_project_ref(pid)[1],
        "parent_id": data.get("parent_id"),
        "child_id": data.get("child_id"),
        "updated_at": data.get("updated_at", ""),
        "created_at": data.get("created_at", ""),
        "image_count": image_count,
        "scene_count": len(data.get("scenes", [])),
    }


def find_scene(data, scene_id):
    for s in data.get("scenes", []):
        if s.get("scene_id") == scene_id:
            return s
    raise KeyError("scene not found")


def find_shot(data, shot_id):
    for scene in data.get("scenes", []):
        for shot in scene.get("shots", []):
            if shot.get("shot_id") == shot_id:
                return scene, shot
    raise KeyError("shot not found")


def find_tab(data, shot_id, tab_id=None):
    scene, shot = find_shot(data, shot_id)
    tab = get_active_tab(shot, tab_id)
    return scene, shot, tab


def get_asset_by_id(data, asset_id):
    for a in data.get("assets", []):
        if a.get("asset_id") == asset_id:
            return a
    return None


def get_asset_by_name(data, name):
    for a in data.get("assets", []):
        if str(a.get("name", "")).lower() == str(name).lower():
            return a
    return None


def normalize_group_name(name, fallback="素材组"):
    return safe_file_name(name, fallback)

def asset_name_exists(data, name, exclude_id=""):
    needle = str(name or "").strip().lower()
    if not needle:
        return False
    for a in data.get("assets", []) or []:
        if exclude_id and a.get("asset_id") == exclude_id:
            continue
        if str(a.get("name", "")).strip().lower() == needle:
            return True
    return False

def ensure_asset_groups(data):
    assets = data.get("assets") if isinstance(data.get("assets"), list) else []
    data["assets"] = assets
    raw_groups = data.get("asset_groups") if isinstance(data.get("asset_groups"), list) else []
    normalized = []
    valid = {}
    order_next = {cat: 1 for cat in CATEGORIES}
    seen_names = {cat: set() for cat in CATEGORIES}

    for g in raw_groups:
        if not isinstance(g, dict):
            continue
        cat = g.get("category") if g.get("category") in GROUPED_CATEGORIES else None
        if not cat:
            continue
        gid = g.get("group_id") or new_id("grp")
        while gid in valid:
            gid = new_id("grp")
        base_name = normalize_group_name(g.get("name") or ("未命名" + cat), "未命名" + cat)
        key = base_name.lower()
        if key in seen_names[cat]:
            i = 2
            candidate = f"{base_name}({i})"
            while candidate.lower() in seen_names[cat]:
                i += 1
                candidate = f"{base_name}({i})"
            base_name = candidate
            key = base_name.lower()
        seen_names[cat].add(key)
        order = parse_positive_int(g.get("sort_order"), order_next[cat]) or order_next[cat]
        order_next[cat] = max(order_next[cat], order + 1)
        ng = {
            "group_id": gid,
            "category": cat,
            "name": base_name,
            "sort_order": order,
            "expanded": bool(g.get("expanded", True)),
            "created_at": g.get("created_at") or now_str(),
        }
        normalized.append(ng)
        valid[gid] = ng

    def make_default_group(cat):
        base_name = "未分组" + cat
        key = base_name.lower()
        if key in seen_names[cat]:
            # Reuse existing default-like group when possible.
            for gg in normalized:
                if gg.get("category") == cat and gg.get("name", "").lower() == key:
                    return gg
        gid = new_id("grp")
        while gid in valid:
            gid = new_id("grp")
        g = {
            "group_id": gid,
            "category": cat,
            "name": base_name,
            "sort_order": order_next[cat],
            "expanded": True,
            "created_at": now_str(),
        }
        order_next[cat] += 1
        normalized.append(g)
        valid[gid] = g
        seen_names[cat].add(key)
        return g

    for a in assets:
        if not isinstance(a, dict):
            continue
        a.setdefault("asset_id", new_id("asset"))
        cat = a.get("category") if a.get("category") in CATEGORIES else "道具"
        a["category"] = cat
        a["name"] = safe_file_name(a.get("name") or "素材", "素材")
        if cat in GROUPED_CATEGORIES and not a.get("temporary"):
            gid = a.get("group_id")
            if not gid or gid not in valid or valid[gid].get("category") != cat:
                a["group_id"] = make_default_group(cat)["group_id"]
        else:
            a.pop("group_id", None)

    normalized.sort(key=lambda g: (CATEGORIES.index(g.get("category")) if g.get("category") in CATEGORIES else 999, int(g.get("sort_order") or 999999)))
    for cat in GROUPED_CATEGORIES:
        i = 1
        for g in [x for x in normalized if x.get("category") == cat]:
            g["sort_order"] = i
            i += 1
    data["asset_groups"] = normalized
    return normalized

def get_asset_group_by_id(data, group_id):
    ensure_asset_groups(data)
    for g in data.get("asset_groups", []) or []:
        if g.get("group_id") == group_id:
            return g
    return None

def remove_assets_by_ids(data, ids):
    ids = set(ids or [])
    keep = []
    for a in data.get("assets", []) or []:
        if a.get("asset_id") in ids:
            remove_public_file(a.get("file_path"))
        else:
            keep.append(a)
    data["assets"] = keep
    for scene in data.get("scenes", []) or []:
        for shot in scene.get("shots", []) or []:
            for tab in shot.get("tabs", []) or []:
                tab["referenced_assets"] = [x for x in tab.get("referenced_assets", []) if x not in ids]

def referenced_aliases(prompt: str, data: dict, assets=None):
    text = str(prompt or "")
    assets = assets if assets is not None else (data.get("assets", []) or [])
    assets = [a for a in assets if a.get("name")]
    names = sorted({str(a.get("name")) for a in assets}, key=len, reverse=True)
    aliases = []
    lower_text = text.lower()
    i = 0
    while i < len(text):
        if text[i] != "@":
            i += 1
            continue
        matched = None
        for name in names:
            start = i + 1
            end = start + len(name)
            if lower_text[start:end] == name.lower():
                matched = name
                break
        if matched:
            if matched not in aliases:
                aliases.append(matched)
            i += 1 + len(matched)
        else:
            m = re.match(r"@([^\s@]+)", text[i:])
            if m and m.group(1) not in aliases:
                aliases.append(m.group(1))
                i += len(m.group(0))
            else:
                i += 1
    return aliases


def public_to_local(path_or_url: str):
    p = str(path_or_url or "")
    if p.startswith("/input/"):
        rel = unquote(p[len("/input/"):])
        local = (INPUT_DIR / rel).resolve()
        if INPUT_DIR.resolve() in local.parents or local == INPUT_DIR.resolve():
            return local
    if p.startswith("/output/image/"):
        rel = unquote(p[len("/output/image/"):])
        local = (OUTPUT_IMAGE_DIR / rel).resolve()
        if OUTPUT_IMAGE_DIR.resolve() in local.parents or local == OUTPUT_IMAGE_DIR.resolve():
            return local
    if p.startswith("/output/video/"):
        rel = unquote(p[len("/output/video/"):])
        local = (OUTPUT_VIDEO_DIR / rel).resolve()
        if OUTPUT_VIDEO_DIR.resolve() in local.parents or local == OUTPUT_VIDEO_DIR.resolve():
            return local
    if p.startswith("/temp/"):
        rel = unquote(p[len("/temp/"):])
        local = (PROJECTS_DIR / rel).resolve()
        if PROJECTS_DIR.resolve() in local.parents or local == PROJECTS_DIR.resolve():
            return local
    local = Path(p)
    if local.exists():
        return local
    return None


def local_to_data_url(fp: Path):
    mime = mimetypes.guess_type(str(fp))[0] or "image/png"
    b64 = base64.b64encode(fp.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def parse_data_url(data_url):
    if not data_url or "," not in data_url:
        raise ValueError("missing dataUrl")
    head, b64 = data_url.split(",", 1)
    raw = base64.b64decode(b64)
    ext = ".png"
    if "image/jpeg" in head or "image/jpg" in head:
        ext = ".jpg"
    elif "image/webp" in head:
        ext = ".webp"
    elif "image/png" in head:
        ext = ".png"
    elif "image/svg" in head:
        ext = ".svg"
    elif "audio/mpeg" in head or "audio/mp3" in head:
        ext = ".mp3"
    elif "audio/wav" in head or "audio/x-wav" in head:
        ext = ".wav"
    elif "audio" in head:
        ext = ".m4a"
    elif "video/mp4" in head:
        ext = ".mp4"
    elif "video/webm" in head:
        ext = ".webm"
    elif "video/quicktime" in head:
        ext = ".mov"
    elif "video" in head:
        ext = ".mp4"
    return raw, ext


def ratio_size(ratio, resolution):
    return SIZE_MAP.get(resolution, SIZE_MAP["1K"]).get(ratio, SIZE_MAP["1K"]["16:9"])


def quality_for_api(q):
    q = (q or "medium").lower()
    if q in ("low", "medium", "high", "auto", "standard", "hd"):
        return q
    return "medium"


def extract_text_response(obj):
    if isinstance(obj, dict):
        if isinstance(obj.get("output_text"), str) and obj.get("output_text").strip():
            return obj.get("output_text").strip()
        if isinstance(obj.get("choices"), list) and obj["choices"]:
            choice = obj["choices"][0]
            msg = (choice.get("message") or {}) if isinstance(choice, dict) else {}
            content = msg.get("content")
            if isinstance(content, str):
                return content.strip()
            if isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, dict):
                        if isinstance(item.get("text"), str):
                            parts.append(item.get("text"))
                        elif item.get("type") == "text" and isinstance(item.get("text"), str):
                            parts.append(item.get("text"))
                if parts:
                    return "\n".join(parts).strip()
        if isinstance(obj.get("output"), list):
            parts = []
            for item in obj["output"]:
                if not isinstance(item, dict):
                    continue
                for c in item.get("content", []):
                    if isinstance(c, dict):
                        if isinstance(c.get("text"), str):
                            parts.append(c.get("text"))
                        elif c.get("type") in ("output_text", "text") and isinstance(c.get("text"), str):
                            parts.append(c.get("text"))
            if parts:
                return "\n".join(parts).strip()
    return ""


def extract_usage_tokens(obj):
    if isinstance(obj, dict):
        usage = obj.get("usage")
        if isinstance(usage, dict):
            for k in ("total_tokens", "input_tokens", "output_tokens"):
                try:
                    v = int(usage.get(k) or 0)
                    if v:
                        return v
                except Exception:
                    pass
        for val in obj.values():
            found = extract_usage_tokens(val)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = extract_usage_tokens(item)
            if found:
                return found
    return 0


def approx_text_tokens(text):
    # Mixed Chinese/English approximation. Real API usage wins when available.
    return max(1, math.ceil(len(str(text or "")) / 2))


def estimate_image_tokens(prompt, settings, image_paths=None):
    res = settings.get("image_resolution", "1K")
    q = settings.get("image_quality", "medium")
    base = {"1K": 1200, "2K": 3000, "4K": 6500}.get(res, 1200)
    mult = {"low": 0.7, "medium": 1.0, "high": 1.6}.get(q, 1.0)
    refs = len(image_paths or []) * 250
    return int(base * mult + approx_text_tokens(prompt) + refs)


def current_usd_per_1k():
    try:
        return float(load_config()["global_defaults"].get("usd_per_1k_tokens") or USD_PER_1K_TOKENS)
    except Exception:
        return USD_PER_1K_TOKENS


def load_usage():
    data = read_json_file(USAGE_PATH, {"users": {}, "events": []}) or {"users": {}, "events": []}
    data.setdefault("users", {})
    data.setdefault("events", [])
    return data


def save_usage(data):
    write_json_file(USAGE_PATH, data)


def normalize_user_name(name):
    return safe_name(name or "未命名用户", "未命名用户")


def get_user_usage(name):
    name = normalize_user_name(name)
    data = load_usage()
    item = data.get("users", {}).get(name) or {"user_name": name, "tokens": 0, "usd": 0.0, "cny": 0.0}
    return {
        "user_name": name,
        "tokens": int(item.get("tokens") or 0),
        "usd": round(float(item.get("usd") or 0), 6),
        "cny": round(float(item.get("cny") or 0), 4),
    }


def record_usage(user_name, kind, model, tokens, project_id=None, shot_id=None, tab_id=None, scene=None, shot=None, cny=None, cost_details=None):
    user_name = normalize_user_name(user_name)
    tokens = max(0, int(tokens or 0))
    usd = round(tokens / 1000 * current_usd_per_1k(), 6)
    cny_value = round(float(cny or 0), 4)
    data = load_usage()
    users = data.setdefault("users", {})
    item = users.setdefault(user_name, {"user_name": user_name, "tokens": 0, "usd": 0.0, "cny": 0.0})
    item["tokens"] = int(item.get("tokens") or 0) + tokens
    item["usd"] = round(float(item.get("usd") or 0) + usd, 6)
    item["cny"] = round(float(item.get("cny") or 0) + cny_value, 4)
    item["updated_at"] = now_str()
    event = {
        "time": now_str(), "user_name": user_name, "kind": kind, "model": model,
        "tokens": tokens, "usd": usd, "cny": cny_value,
        "project_id": project_id, "shot_id": shot_id, "tab_id": tab_id,
    }
    if cost_details:
        event["cost_details"] = cost_details
    # Store scene/shot metadata at the moment of cost creation. This preserves cost attribution
    # even if the SC or shot is later deleted before project export.
    if isinstance(scene, dict):
        event.update({
            "scene_id": scene.get("scene_id"),
            "scene_code": scene.get("scene_code"),
            "scene_number": scene_number_from_scene(scene, None),
        })
    if isinstance(shot, dict):
        event.update({"shot_code": shot.get("shot_code")})
    data.setdefault("events", []).append(event)
    if len(data["events"]) > 5000:
        data["events"] = data["events"][-5000:]
    save_usage(data)
    return get_user_usage(user_name)


def call_openai_chat(api_key, thread, user_text, referenced_assets=None):
    if not api_key:
        raise ValueError("missing OpenAI API Key")
    referenced_assets = referenced_assets or []
    assets_text = "、".join([a.get("name", "") for a in referenced_assets if a])
    system_prompt = (
        "你是一个对话式生图助手，负责先和用户确认画面需求，再准备生图。"
        "你现在只做理解、复述、补充建议和澄清，不生成图片，也不要输出生硬的关键词堆砌提示词。"
        "请用自然中文简洁回复。若用户描述已经足够清楚，就概括为一段可执行的画面理解，并告诉用户可以点击生成。"
    )
    messages = [{"role": "system", "content": system_prompt}]
    if assets_text:
        messages.append({"role": "system", "content": "当前已引用素材：" + assets_text})
    for m in thread.get("messages", [])[-12:]:
        role = m.get("role")
        if role in ("user", "assistant") and m.get("content"):
            messages.append({"role": role, "content": m.get("content")})
    messages.append({"role": "user", "content": user_text})
    payload = {"model": CHAT_MODEL, "messages": messages, "temperature": 0.6}
    req = urllib.request.Request(
        OPENAI_BASE + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            raw = resp.read().decode("utf-8")
            obj = json.loads(raw)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI HTTP {e.code}: {body[:1000]}")
    except Exception as e:
        raise RuntimeError(f"OpenAI request failed: {e}")
    text = extract_text_response(obj)
    if not text:
        raise RuntimeError("OpenAI response did not contain assistant text")
    tokens = extract_usage_tokens(obj) or approx_text_tokens(json.dumps(messages, ensure_ascii=False) + text)
    return text, tokens


def extract_b64_image(obj):
    if isinstance(obj, dict):
        for key in ("b64_json", "image_base64", "image", "data"):
            val = obj.get(key)
            if key == "data" and isinstance(val, list):
                for item in val:
                    found = extract_b64_image(item)
                    if found:
                        return found
            elif isinstance(val, str) and len(val) > 100:
                if val.startswith("data:image"):
                    return val.split(",", 1)[-1]
                return val
        for val in obj.values():
            found = extract_b64_image(val)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = extract_b64_image(item)
            if found:
                return found
    return None


def call_openai_image(api_key, mode, model, prompt, settings, image_paths=None):
    if not api_key:
        raise ValueError("missing OpenAI API Key")
    size = ratio_size(settings.get("image_ratio", "16:9"), settings.get("image_resolution", "1K"))
    payload = {
        "model": model or "gpt-image-2",
        "prompt": prompt,
        "size": size,
        "quality": quality_for_api(settings.get("image_quality", "medium")),
        "output_format": "png",
        "n": 1,
    }
    endpoint = "/images/generations"
    if image_paths:
        endpoint = "/images/edits"
        imgs = []
        for fp in image_paths[:16]:
            try:
                imgs.append({"image_url": local_to_data_url(fp)})
            except Exception:
                pass
        payload["images"] = imgs
    req = urllib.request.Request(
        OPENAI_BASE + endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            raw = resp.read().decode("utf-8")
            obj = json.loads(raw)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI HTTP {e.code}: {body[:1000]}")
    except Exception as e:
        raise RuntimeError(f"OpenAI request failed: {e}")
    b64 = extract_b64_image(obj)
    if not b64:
        raise RuntimeError("OpenAI response did not contain a base64 image. Raw keys: " + ", ".join(obj.keys()))
    tokens = extract_usage_tokens(obj) or estimate_image_tokens(prompt, settings, image_paths)
    return base64.b64decode(b64), tokens


def make_mock_svg(project, scene_code, shot_code, prompt, settings):
    text = (prompt or "未输入").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    lines = []
    while text:
        lines.append(text[:32])
        text = text[32:]
        if len(lines) >= 6:
            break
    svg_lines = "".join([f'<text x="60" y="{250+i*42}" fill="#d8fff1" font-size="28">{line}</text>' for i, line in enumerate(lines)])
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="576" viewBox="0 0 1024 576">
<rect width="1024" height="576" fill="#0a0c0f"/>
<rect x="28" y="28" width="968" height="520" rx="28" fill="#11161d" stroke="#00e5a0" stroke-width="3"/>
<text x="60" y="95" fill="#00e5a0" font-size="34" font-family="Arial, Microsoft YaHei">MOCK IMAGE / 未填写 API Key</text>
<text x="60" y="145" fill="#e8eaed" font-size="24" font-family="Arial, Microsoft YaHei">{project} · {scene_code} / {shot_code}</text>
<text x="60" y="185" fill="#8a9098" font-size="22" font-family="Arial, Microsoft YaHei">{settings.get('image_ratio')} · {settings.get('image_resolution')} · {settings.get('image_quality')}</text>
{svg_lines}
</svg>'''
    return svg.encode("utf-8"), ".svg"


def build_context_prompt(thread, user_text, referenced_assets, operation):
    prior_user = [m.get("content", "") for m in thread.get("messages", [])[-8:] if m.get("role") == "user"]
    assets_text = "、".join([a.get("name", "") for a in referenced_assets if a])
    base = [
        "你正在为一个影视/动画项目生成单张画面内容。请只生成画面本身，不要生成分镜表格、故事板模板、纸张边框、页眉、页脚、项目栏、SEQ/SC/SHOT/PAGE/TIME/DURATION 等文字栏，也不要在画面上添加任何非用户明确要求的文字。",
        "用户如果说“分镜”或“镜头”，含义是画面构图和镜头内容，不是分镜表格模板。"
    ]
    if thread.get("context_summary"):
        base.append("当前标签页上下文：" + thread.get("context_summary", ""))
    if prior_user:
        base.append("本标签页近期要求：" + " / ".join(prior_user[-5:]))
    if assets_text:
        base.append("已引用素材：" + assets_text + "。请把这些素材作为画面参考。")
    if operation == "edit":
        base.append("本次是基于所选图片继续修改。默认保留上一张图中的主体、角色关系、风格与构图，只修改用户明确要求改变的部分。")
    else:
        base.append("本次是重新生成一张新图，但仍保持当前标签页已经形成的创作方向。")
    base.append("用户最新要求：" + (user_text or ""))
    return "\n".join(base)


def update_context_summary(thread, user_text):
    old = thread.get("context_summary") or ""
    new = (old + "；" + user_text).strip("；") if old else user_text
    if len(new) > 900:
        new = new[-900:]
    thread["context_summary"] = new


def decide_operation(thread, user_text, explicit_mode, selected_base_id):
    if explicit_mode == "regenerate":
        return "generate", None
    if selected_base_id:
        return "edit", selected_base_id
    current = thread.get("current_base_image_id")
    if current:
        edit_words = ["改", "修改", "换", "保持", "不变", "更", "远一点", "近一点", "背景", "视角", "构图", "颜色", "去掉", "加上", "放大", "缩小"]
        if any(w in user_text for w in edit_words):
            return "edit", current
    return "generate", None


def infer_submit_mode(thread, user_text, selected_base_id=None):
    text = (user_text or "").strip()
    if selected_base_id:
        return "generate"
    if not text:
        return "generate"
    gen_words = ["生成", "出图", "画", "绘制", "做一张", "来一张", "做个", "做一个", "创建", "制作", "镜头", "海报", "场景"]
    edit_words = ["改", "修改", "换", "保持", "不变", "背景", "视角", "构图", "颜色", "去掉", "加上", "放大", "缩小", "远一点", "近一点"]
    chat_words = ["你理解", "先别生成", "不要生成", "不要直接生成", "别直接生成", "先不要生成", "先别画", "不要画", "别画", "先聊", "聊一下", "确认", "讨论", "先讨论", "给我方案", "怎么看", "为什么", "是否", "是不是", "可不可以", "怎么"]
    if any(w in text for w in chat_words):
        return "chat"
    if any(w in text for w in gen_words + edit_words):
        return "generate"
    if any(w in text for w in ["几个", "多少", "吗", "？", "?"]) and not thread.get("generated_images"):
        return "chat"
    return "generate"


def rebuild_context_summary(thread):
    parts = [m.get("content", "") for m in thread.get("messages", []) if m.get("role") == "user" and m.get("content")]
    new = "；".join(parts)
    if len(new) > 900:
        new = new[-900:]
    thread["context_summary"] = new


def rebuild_referenced_assets(data, thread):
    existing_assets = []
    keep_temp = []
    for aid in thread.get("referenced_assets", []):
        a = get_asset_by_id(data, aid)
        if a:
            existing_assets.append(a)
            if a.get("temporary"):
                keep_temp.append(aid)
    refs = []
    for m in thread.get("messages", []):
        if m.get("role") != "user":
            continue
        for name in referenced_aliases(m.get("content", ""), data, existing_assets):
            a = next((x for x in existing_assets if str(x.get("name", "")).lower() == str(name).lower()), None)
            if a and a.get("asset_id") not in refs:
                refs.append(a.get("asset_id"))
    for aid in keep_temp:
        if aid not in refs:
            refs.append(aid)
    thread["referenced_assets"] = refs


def rollback_from_message(data, shot, thread, message_id):
    if not message_id:
        return False
    msgs = thread.get("messages", [])
    idx = None
    for i, m in enumerate(msgs):
        if m.get("message_id") == message_id and m.get("role") == "user":
            idx = i
            break
    if idx is None:
        return False
    removed = msgs[idx:]
    removed_ids = {m.get("message_id") for m in removed}
    thread["messages"] = msgs[:idx]
    new_images = []
    removed_img_ids = set()
    for img in thread.get("generated_images", []):
        if img.get("source_message_id") in removed_ids:
            removed_img_ids.add(img.get("image_id"))
            # Keep files on disk. Only remove the database record.
        else:
            new_images.append(img)
    thread["generated_images"] = new_images
    shot["storyboard_candidates"] = [c for c in shot.get("storyboard_candidates", []) if c.get("image_id") not in removed_img_ids]
    shot["status"] = "confirmed" if shot.get("storyboard_candidates") else "unconfirmed"
    thread["current_base_image_id"] = thread["generated_images"][-1].get("image_id") if thread.get("generated_images") else None
    rebuild_context_summary(thread)
    rebuild_referenced_assets(data, thread)
    return True



def collect_at_mention_names(data):
    names = set()
    for a in data.get("assets", []) or []:
        name = str(a.get("name") or "").lstrip("@")
        if name:
            names.add(name)
    for scene in data.get("scenes", []) or []:
        for shot in scene.get("shots", []) or []:
            for tab in shot.get("tabs", []) or []:
                for rec in list(tab.get("generated_images", []) or []) + list(tab.get("generated_videos", []) or []):
                    if isinstance(rec, dict):
                        for key in ("display_name", "name"):
                            name = str(rec.get(key) or "").lstrip("@")
                            if name:
                                names.add(name)
    return names


def replace_at_mentions_in_text(text, old_name, new_name, protected_names=None):
    if not isinstance(text, str) or not old_name or not new_name or old_name == new_name:
        return text
    old_token = str(old_name).lstrip("@")
    new_token = str(new_name).lstrip("@")
    if not old_token or not new_token or old_token == new_token:
        return text
    names = protected_names or set()
    old_lower = old_token.lower()
    longer_names = sorted(
        [str(n).lstrip("@") for n in names if str(n).lstrip("@") and str(n).lstrip("@") != old_token and str(n).lstrip("@").lower().startswith(old_lower)],
        key=len,
        reverse=True,
    )
    pattern = re.compile(r"(^|[^A-Za-z0-9._%+\-])@" + re.escape(old_token))

    def repl(match):
        prefix = match.group(1)
        at_index = match.start() + len(prefix)
        name_start = at_index + 1
        name_end = name_start + len(old_token)
        tail = text[name_end:]
        if tail[:1] and re.match(r"[A-Za-z0-9_\-＿]", tail[:1]):
            return match.group(0)
        rest_lower = text[name_start:].lower()
        for name in longer_names:
            if rest_lower.startswith(name.lower()):
                return match.group(0)
        return prefix + "@" + new_token

    return pattern.sub(repl, text)


def replace_at_mentions_in_project(data, old_name, new_name):
    """Synchronize @name references in saved drafts, prompts and history after an asset/image rename."""
    if not old_name or not new_name or old_name == new_name:
        return 0
    changed = 0
    protected_names = collect_at_mention_names(data)

    def apply(obj, key):
        nonlocal changed
        if not isinstance(obj, dict):
            return
        before = obj.get(key)
        after = replace_at_mentions_in_text(before, old_name, new_name, protected_names)
        if after != before:
            obj[key] = after
            changed += 1

    for scene in data.get("scenes", []) or []:
        for shot in scene.get("shots", []) or []:
            for key in ("context_summary", "draft_prompt"):
                apply(shot, key)
            for tab in shot.get("tabs", []) or []:
                for key in ("context_summary", "draft_prompt"):
                    apply(tab, key)
                for msg in tab.get("messages", []) or []:
                    apply(msg, "content")
                    for ver in msg.get("content_versions", []) or []:
                        apply(ver, "content")
                seen_records = set()
                records = list(tab.get("generated_images", []) or []) + list(tab.get("generated_videos", []) or [])
                for rec in records:
                    if not isinstance(rec, dict):
                        continue
                    rid = id(rec)
                    if rid in seen_records:
                        continue
                    seen_records.add(rid)
                    for key in ("prompt", "user_message", "display_name", "name"):
                        apply(rec, key)
    return changed


def iter_generated_image_records(data):
    for scene in data.get("scenes", []) or []:
        for shot in scene.get("shots", []) or []:
            for tab in shot.get("tabs", []) or []:
                for img in tab.get("generated_images", []) or []:
                    yield scene, shot, tab, img


def display_name_for_image(img):
    return safe_file_name(img.get("display_name") or img.get("name") or Path(str(img.get("file_path") or "image")).stem, "图片")


def delete_generated_images_by_ids(data, ids, delete_files=True):
    ids = set(ids or [])
    if not ids:
        return 0
    deleted_paths = set()
    deleted = 0
    for scene in data.get("scenes", []) or []:
        for shot in scene.get("shots", []) or []:
            for tab in shot.get("tabs", []) or []:
                kept = []
                for img in tab.get("generated_images", []) or []:
                    if img.get("image_id") in ids:
                        deleted += 1
                        if img.get("file_path"):
                            deleted_paths.add(img.get("file_path"))
                            if delete_files:
                                remove_public_file(img.get("file_path"))
                    else:
                        kept.append(img)
                tab["generated_images"] = kept
                if tab.get("current_base_image_id") in ids:
                    tab["current_base_image_id"] = kept[-1].get("image_id") if kept else None
            before = len(shot.get("storyboard_candidates", []) or [])
            shot["storyboard_candidates"] = [c for c in shot.get("storyboard_candidates", []) or [] if c.get("image_id") not in ids and c.get("file_path") not in deleted_paths]
            if before != len(shot.get("storyboard_candidates", []) or []):
                shot["status"] = "confirmed" if shot.get("storyboard_candidates") else "unconfirmed"
    return deleted


def rename_generated_image_by_id(data, image_id, new_name):
    new_name = safe_file_name(new_name or "图片", "图片")
    target = None
    for scene, shot, tab, img in iter_generated_image_records(data):
        if img.get("image_id") == image_id:
            target = (scene, shot, tab, img)
            break
    if not target:
        return None, None, None
    scene, shot, tab, img = target
    old_url = img.get("file_path") or ""
    old_name = display_name_for_image(img)
    fp = public_to_local(old_url)
    final_name = new_name
    new_url = old_url
    if fp and fp.exists() and fp.is_file():
        sibling_names = {p.stem for p in fp.parent.iterdir() if p.is_file() and p != fp}
        final_name = unique_name_with_paren(sibling_names, new_name)
        new_fp = fp.with_name(final_name + fp.suffix)
        if new_fp != fp:
            fp.rename(new_fp)
        try:
            if OUTPUT_VIDEO_DIR.resolve() in new_fp.resolve().parents or new_fp.resolve() == OUTPUT_VIDEO_DIR.resolve():
                rel = new_fp.relative_to(OUTPUT_VIDEO_DIR).as_posix()
                new_url = "/output/video/" + rel
            else:
                rel = new_fp.relative_to(OUTPUT_IMAGE_DIR).as_posix()
                new_url = "/output/image/" + rel
        except Exception:
            new_url = old_url
    # Update every record with the same image id or path.
    for _scene, _shot, _tab, gi in iter_generated_image_records(data):
        if gi.get("image_id") == image_id or gi.get("file_path") == old_url:
            gi["file_path"] = new_url
            gi["display_name"] = final_name
            gi["name"] = final_name
    for _scene in data.get("scenes", []) or []:
        for _shot in _scene.get("shots", []) or []:
            for c in _shot.get("storyboard_candidates", []) or []:
                if c.get("image_id") == image_id or c.get("file_path") == old_url:
                    c["file_path"] = new_url
                    c["display_name"] = final_name
    replace_at_mentions_in_project(data, old_name, final_name)
    return final_name, old_name, new_url


def find_message_by_id(tab, message_id):
    for m in tab.get("messages", []) or []:
        if m.get("message_id") == message_id:
            return m
    return None


def apply_user_message_for_submit(data, shot_id, tab_id, user_text, redo_id=""):
    scene, shot, tab = find_tab(data, shot_id, tab_id)
    if redo_id:
        msg = find_message_by_id(tab, redo_id)
        if msg and msg.get("role") == "user":
            versions = msg.setdefault("content_versions", [])
            if not versions:
                versions.append({"content": msg.get("content", ""), "time": msg.get("time", ""), "label": "原始"})
            if user_text and user_text != msg.get("content", ""):
                versions.append({"content": user_text, "time": now_str(), "label": f"重做{len(versions)}"})
                msg["content"] = user_text
                msg["version_view_index"] = len(versions) - 1
            msg["redone_at"] = now_str()
            return scene, shot, tab, msg.get("message_id")
    user_msg = None
    if user_text:
        user_msg = {"message_id": new_id("msg"), "role": "user", "content": user_text, "time": now_str()}
        tab.setdefault("messages", []).append(user_msg)
    return scene, shot, tab, user_msg.get("message_id") if user_msg else None


def unique_name_with_paren(existing_names, base_name):
    base = safe_file_name(base_name or "素材", "素材")
    if base not in existing_names:
        return base
    m = re.match(r"^(.*)\((\d+)\)$", base)
    if m:
        root = m.group(1)
        start = int(m.group(2)) + 1
    else:
        root = base
        start = 2
    n = start
    candidate = f"{root}({n})"
    while candidate in existing_names:
        n += 1
        candidate = f"{root}({n})"
    return candidate


def next_image_filename(shot_dir: Path, ext=".png"):
    shot_dir.mkdir(parents=True, exist_ok=True)
    max_n = 0
    for fp in shot_dir.iterdir():
        m = re.match(r"^(\d+)_", fp.name)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f"{max_n + 1:03d}_{stamp()}{ext}"


def remove_public_file(path):
    fp = public_to_local(path)
    if fp and fp.exists() and fp.is_file():
        try:
            fp.unlink()
        except Exception:
            # Windows 下文件被占用删不掉时改名挪开：不让旧文件占住素材名，
            # 否则同名重传会被迫改名成「xx(1)」或与旧文件混淆。
            try:
                fp.rename(fp.with_name(f".deleted_{uuid.uuid4().hex[:8]}_{fp.name}"))
            except Exception:
                pass


def find_generated_image_in_shot(shot, image_id):
    for tab in shot.get("tabs", []):
        for img in tab.get("generated_images", []):
            if img.get("image_id") == image_id:
                return tab, img
    return None, None


def sha256_bytes(data: bytes):
    return hashlib.sha256(data).hexdigest()


def sha256_file(fp: Path):
    h = hashlib.sha256()
    with fp.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def root_relative_arcname(fp: Path):
    fp = fp.resolve()
    try:
        return fp.relative_to(ROOT).as_posix()
    except Exception:
        return safe_file_name(fp.name, "file")


def add_dir_files(file_map, base_dir: Path, role: str):
    if not base_dir.exists() or not base_dir.is_dir():
        return
    for fp in sorted(base_dir.rglob("*")):
        if not fp.is_file():
            continue
        arc = root_relative_arcname(fp)
        file_map.setdefault(arc, {"path": fp, "role": role})


def compact_usage_event(ev):
    return {
        "time": ev.get("time"),
        "user_name": ev.get("user_name"),
        "kind": ev.get("kind"),
        "model": ev.get("model"),
        "tokens": int(ev.get("tokens") or 0),
        "usd": round(float(ev.get("usd") or 0), 6),
        "project_id": ev.get("project_id"),
        "scene_id": ev.get("scene_id"),
        "scene_code": ev.get("scene_code"),
        "scene_number": ev.get("scene_number"),
        "shot_id": ev.get("shot_id"),
        "shot_code": ev.get("shot_code"),
        "tab_id": ev.get("tab_id"),
    }


def empty_cost_bucket(extra=None):
    bucket = {
        "tokens": 0,
        "usd": 0.0,
        "event_count": 0,
        "chat_tokens": 0,
        "chat_usd": 0.0,
        "image_tokens": 0,
        "image_usd": 0.0,
        "other_tokens": 0,
        "other_usd": 0.0,
        "events": [],
    }
    if extra:
        bucket.update(extra)
    return bucket


def add_usage_to_bucket(bucket, ev):
    tokens = int(ev.get("tokens") or 0)
    usd = round(float(ev.get("usd") or 0), 6)
    kind = str(ev.get("kind") or "other")
    bucket["tokens"] += tokens
    bucket["usd"] = round(float(bucket.get("usd") or 0) + usd, 6)
    bucket["event_count"] += 1
    if kind == "chat":
        bucket["chat_tokens"] += tokens
        bucket["chat_usd"] = round(float(bucket.get("chat_usd") or 0) + usd, 6)
    elif kind == "image":
        bucket["image_tokens"] += tokens
        bucket["image_usd"] = round(float(bucket.get("image_usd") or 0) + usd, 6)
    else:
        bucket["other_tokens"] += tokens
        bucket["other_usd"] = round(float(bucket.get("other_usd") or 0) + usd, 6)
    bucket.setdefault("events", []).append(compact_usage_event(ev))


def build_usage_summary(project_data, responsible_nickname):
    pid = project_data.get("project_id")
    usage_data = load_usage()
    events = [ev for ev in usage_data.get("events", []) if ev.get("project_id") == pid]

    active_by_scene_id = {}
    active_by_scene_code = {}
    shot_to_scene = {}
    for scene in project_data.get("scenes", []) or []:
        scene_no = scene_number_from_scene(scene, None)
        scene_code = scene.get("scene_code") or scene_code_from_number(scene_no or 1)
        bucket = empty_cost_bucket({
            "scene_id": scene.get("scene_id"),
            "scene_number": scene_no,
            "scene_code": scene_code,
            "visibility": "visible",
        })
        active_by_scene_id[scene.get("scene_id")] = bucket
        active_by_scene_code[scene_code] = bucket
        for shot in scene.get("shots", []) or []:
            shot_to_scene[shot.get("shot_id")] = bucket

    hidden_groups = {}
    total = empty_cost_bucket()
    for ev in events:
        add_usage_to_bucket(total, ev)
        target = None
        sid = ev.get("shot_id")
        if sid and sid in shot_to_scene:
            target = shot_to_scene[sid]
        elif ev.get("scene_id") and ev.get("scene_id") in active_by_scene_id:
            target = active_by_scene_id[ev.get("scene_id")]
        elif ev.get("scene_code") and ev.get("scene_code") in active_by_scene_code:
            target = active_by_scene_code[ev.get("scene_code")]

        if target is not None:
            add_usage_to_bucket(target, ev)
        else:
            scene_code = ev.get("scene_code") or "UNKNOWN_DELETED_OR_UNASSIGNED"
            scene_number = parse_positive_int(ev.get("scene_number"), None)
            key = f"{scene_code}|{scene_number or ''}"
            if key not in hidden_groups:
                hidden_groups[key] = empty_cost_bucket({
                    "scene_id": ev.get("scene_id"),
                    "scene_number": scene_number,
                    "scene_code": scene_code,
                    "visibility": "hidden_for_default_ui",
                    "reason": "SC 已删除，或当前 project.json 中无法找到该费用事件对应的 SC/子栏。",
                })
            add_usage_to_bucket(hidden_groups[key], ev)

    by_sc = list(active_by_scene_id.values())
    by_sc.sort(key=lambda x: parse_positive_int(x.get("scene_number"), 999999) or 999999)
    hidden = list(hidden_groups.values())
    hidden.sort(key=lambda x: parse_positive_int(x.get("scene_number"), 999999) or 999999)
    return {
        "schema_version": "1.0",
        "project_id": pid,
        "project_name": project_data.get("project_name"),
        "responsible_nickname": responsible_nickname,
        "currency": "USD",
        "generated_at": now_str(),
        "total": {
            "tokens": total["tokens"],
            "usd": round(total["usd"], 6),
            "event_count": total["event_count"],
            "chat_tokens": total["chat_tokens"],
            "chat_usd": round(total["chat_usd"], 6),
            "image_tokens": total["image_tokens"],
            "image_usd": round(total["image_usd"], 6),
            "other_tokens": total["other_tokens"],
            "other_usd": round(total["other_usd"], 6),
        },
        "by_sc": by_sc,
        "hidden_deleted_or_unassigned_usage": hidden,
        "notes": [
            "by_sc 只包含当前 project.json 中仍存在的 SC，适合普通审核界面展示。",
            "hidden_deleted_or_unassigned_usage 保存已删除 SC 或无法映射费用事件，不默认外显，仅供后期查账。",
            "费用来自制作端 usage.json 记录；图片接口无官方 usage 时使用工具内估算规则。",
        ],
    }


def collect_project_export_files(project_data):
    pid = project_data.get("project_id")
    storage = safe_name(pid, "project")
    file_map = {}
    # Main project-related directories. Keep original relative paths so future import can restore easily.
    add_dir_files(file_map, project_path(pid) / "temp_refs", "temp_refs")
    add_dir_files(file_map, INPUT_DIR / storage, "input_assets")
    add_dir_files(file_map, OUTPUT_IMAGE_DIR / storage, "output_images")
    add_dir_files(file_map, OUTPUT_VIDEO_DIR / storage, "output_videos")

    # Also include any directly referenced image file, even if it is outside the normal project folders.
    refs = []
    for asset in project_data.get("assets", []) or []:
        refs.append((asset.get("file_path"), "referenced_asset"))
    for scene in project_data.get("scenes", []) or []:
        for shot in scene.get("shots", []) or []:
            for c in shot.get("storyboard_candidates", []) or []:
                refs.append((c.get("file_path"), "storyboard_candidate"))
            for tab in shot.get("tabs", []) or []:
                for img in tab.get("generated_images", []) or []:
                    refs.append((img.get("file_path"), "generated_image"))
    missing = []
    for public_path, role in refs:
        if not public_path:
            continue
        fp = public_to_local(public_path)
        if fp and fp.exists() and fp.is_file():
            file_map.setdefault(root_relative_arcname(fp), {"path": fp, "role": role})
        else:
            missing.append({"path": public_path, "role": role, "reason": "file_not_found"})
    return file_map, missing


def count_project_items(project_data):
    scenes = project_data.get("scenes", []) or []
    shot_count = 0
    tab_count = 0
    generated_image_count = 0
    storyboard_candidate_count = 0
    for scene in scenes:
        for shot in scene.get("shots", []) or []:
            shot_count += 1
            storyboard_candidate_count += len(shot.get("storyboard_candidates", []) or [])
            for tab in shot.get("tabs", []) or []:
                tab_count += 1
                generated_image_count += len(tab.get("generated_images", []) or [])
    return {
        "scene_count": len(scenes),
        "shot_count": shot_count,
        "tab_count": tab_count,
        "asset_count": len(project_data.get("assets", []) or []),
        "generated_image_count": generated_image_count,
        "storyboard_candidate_count": storyboard_candidate_count,
    }


def create_project_export_zip(project_id, responsible_nickname):
    data = load_project(project_id)
    append_operation_log(project_id, "export_project", user_name=responsible_nickname, project_id=project_id)
    create_project_snapshot(project_id, "before_export", data=data, user_name=responsible_nickname)
    pid = data.get("project_id")
    responsible_nickname = normalize_user_name(responsible_nickname or "未命名用户")
    parent_id, child_id = split_project_ref(pid)
    parent_data_for_export = None
    parent_name = parent_id
    if parent_id:
        try:
            parent_data_for_export = load_parent(parent_id)
        except Exception:
            parent_data_for_export = create_parent_data(parent_id)
        parent_name = parent_data_for_export.get("parent_name") or parent_id
    child_name = data.get("project_name") or child_id
    module_ui_label = PROJECT_MODULE_UI_LABELS.get(PROJECT_MODULE_TYPE, PROJECT_MODULE_LABEL)

    # Add export-only metadata to the project copy inside the package without mutating the local project file.
    project_copy = json.loads(json.dumps(data, ensure_ascii=False))
    project_copy["module_type"] = PROJECT_MODULE_TYPE
    project_copy["module_label"] = PROJECT_MODULE_LABEL
    project_copy["module_ui_label"] = module_ui_label
    project_copy["parent_name"] = parent_name
    project_copy["child_name"] = child_name
    project_copy["project_scope"] = {
        "parent_id": parent_id,
        "parent_name": parent_name,
        "module_type": PROJECT_MODULE_TYPE,
        "module_label": PROJECT_MODULE_LABEL,
        "module_ui_label": module_ui_label,
        "child_id": child_id,
        "child_name": child_name,
    }
    project_copy["export_owner"] = {
        "responsible_nickname": responsible_nickname,
        "exported_at": now_str(),
    }
    project_json_arc = root_relative_arcname(project_json_path(pid))
    project_json_bytes = json.dumps(project_copy, ensure_ascii=False, indent=2).encode("utf-8")

    parent_json_arc = None
    parent_json_bytes = None
    if parent_id:
        parent_json_arc = root_relative_arcname(parent_json_path(parent_id))
        parent_json_bytes = json.dumps(parent_data_for_export or create_parent_data(parent_id), ensure_ascii=False, indent=2).encode("utf-8")

    usage_summary = build_usage_summary(data, responsible_nickname)
    usage_summary_bytes = json.dumps(usage_summary, ensure_ascii=False, indent=2).encode("utf-8")
    usage_total = usage_summary.get("total") or {}
    hidden_usage = usage_summary.get("hidden_deleted_or_unassigned_usage") or []

    item_counts = count_project_items(data)
    file_map, missing_files = collect_project_export_files(data)
    export_info = {
        "schema_version": "1.0",
        "package_type": PROJECT_PACKAGE_TYPE,
        "module_type": PROJECT_MODULE_TYPE,
        "module_label": PROJECT_MODULE_LABEL,
        "exported_at": now_str(),
        "export_source": "production_client",
        "responsible_nickname": responsible_nickname,
        "source_project_id": pid,
        "parent_id": parent_id,
        "parent_name": parent_name,
        "child_id": child_id,
        "child_name": child_name,
        "project_name": data.get("project_name"),
        "module_ui_label": module_ui_label,
        "project_scope": {
            "parent_id": parent_id,
            "parent_name": parent_name,
            "module_type": PROJECT_MODULE_TYPE,
            "module_label": PROJECT_MODULE_LABEL,
            "module_ui_label": module_ui_label,
            "child_id": child_id,
            "child_name": child_name,
        },
        "note": "完整子项目工程包。",
    }
    export_info_bytes = json.dumps(export_info, ensure_ascii=False, indent=2).encode("utf-8")

    file_records = []
    def generated_record(arc, role, content):
        return {"path": arc, "type": role, "size": len(content), "sha256": sha256_bytes(content), "generated": True}

    file_records.append(generated_record(project_json_arc, "project_json", project_json_bytes))
    if parent_json_arc and parent_json_bytes is not None:
        file_records.append(generated_record(parent_json_arc, "parent_json", parent_json_bytes))
    file_records.append(generated_record("usage_summary.json", "usage_summary", usage_summary_bytes))
    file_records.append(generated_record("export_info.json", "export_info", export_info_bytes))

    physical_records = []
    for arc, meta in sorted(file_map.items()):
        fp = meta.get("path")
        if not fp or not fp.exists() or not fp.is_file():
            continue
        if arc in {project_json_arc, parent_json_arc}:
            continue
        rec = {"path": arc, "type": meta.get("role") or "file", "size": fp.stat().st_size, "sha256": sha256_file(fp), "generated": False}
        physical_records.append(rec)
    file_records.extend(physical_records)

    manifest = {
        "schema_version": "1.0",
        "package_type": PROJECT_PACKAGE_TYPE,
        "module_type": PROJECT_MODULE_TYPE,
        "module_label": PROJECT_MODULE_LABEL,
        "exported_at": now_str(),
        "tool_version": "v44_export_1",
        "generator_name": responsible_nickname,
        "responsible_nickname": responsible_nickname,
        "source_project_id": pid,
        "parent_id": parent_id,
        "parent_name": parent_name,
        "child_id": child_id,
        "child_name": child_name,
        "project_name": data.get("project_name"),
        "module_ui_label": module_ui_label,
        "project_scope": {
            "parent_id": parent_id,
            "parent_name": parent_name,
            "module_type": PROJECT_MODULE_TYPE,
            "module_label": PROJECT_MODULE_LABEL,
            "module_ui_label": module_ui_label,
            "child_id": child_id,
            "child_name": child_name,
        },
        "project_json": project_json_arc,
        "parent_json": parent_json_arc,
        "input_root": f"input/{safe_name(pid, 'project')}/",
        "output_image_root": f"output/image/{safe_name(pid, 'project')}/",
        "output_video_root": f"output/video/{safe_name(pid, 'project')}/",
        "temp_refs_root": root_relative_arcname(project_path(pid) / "temp_refs"),
        **item_counts,
        "usage_summary": {
            "currency": "USD",
            "total_tokens": int(usage_total.get("tokens") or 0),
            "total_usd": round(float(usage_total.get("usd") or 0), 6),
            "event_count": int(usage_total.get("event_count") or 0),
            "scene_count_with_usage": sum(1 for x in usage_summary.get("by_sc", []) if int(x.get("event_count") or 0) > 0),
            "hidden_deleted_or_unassigned_event_count": sum(int(x.get("event_count") or 0) for x in hidden_usage),
            "usage_summary_file": "usage_summary.json",
        },
        "missing_files": missing_files,
        "files": file_records,
    }
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")

    base_name = safe_file_name(f"{data.get('parent_id') or '项目'}__{data.get('project_name') or child_id}__工程导出_{stamp()}", "工程导出")
    zip_path = EXPORTS_DIR / f"{base_name}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", manifest_bytes)
        zf.writestr("export_info.json", export_info_bytes)
        zf.writestr("usage_summary.json", usage_summary_bytes)
        zf.writestr(project_json_arc, project_json_bytes)
        if parent_json_arc and parent_json_bytes is not None:
            zf.writestr(parent_json_arc, parent_json_bytes)
        for arc, meta in sorted(file_map.items()):
            fp = meta.get("path")
            if not fp or not fp.exists() or not fp.is_file():
                continue
            if arc in {project_json_arc, parent_json_arc}:
                continue
            zf.write(fp, arc)
    return zip_path, manifest


def decode_data_url_bytes(data_url: str):
    if not data_url or "," not in data_url:
        raise ValueError("missing dataUrl")
    return base64.b64decode(data_url.split(",", 1)[1])


def safe_zip_arcname(name: str):
    raw = str(name or "").replace("\\", "/")
    if not raw or raw.endswith("/"):
        return None
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise ValueError(f"压缩包内存在非法绝对路径：{raw}")
    norm = os.path.normpath(raw).replace("\\", "/")
    if norm in (".", ""):
        return None
    if norm == ".." or norm.startswith("../") or "/../" in norm:
        raise ValueError(f"压缩包内存在非法上级路径：{raw}")
    return norm


def read_zip_json(zf: zipfile.ZipFile, arc: str, default=None):
    if not arc:
        return default
    try:
        raw = zf.read(arc)
    except KeyError:
        return default
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return default





VISUAL_ASSET_PACKAGE_CATEGORIES = ["人物", "场景", "道具"]


def unique_asset_name_casefold(existing_names, base_name):
    base = safe_file_name(base_name or "素材", "素材")
    used = {str(x or "").strip().lower() for x in (existing_names or []) if str(x or "").strip()}
    if base.lower() not in used:
        return base
    m = re.match(r"^(.*)\((\d+)\)$", base)
    if m:
        root = m.group(1)
        start = int(m.group(2)) + 1
    else:
        root = base
        start = 2
    n = start
    candidate = f"{root}({n})"
    while candidate.lower() in used:
        n += 1
        candidate = f"{root}({n})"
    return candidate


def asset_package_name(base_name, index):
    base = safe_file_name(base_name or "素材", "素材")
    return base if int(index or 1) <= 1 else f"{base}({int(index)})"


def unique_zip_arc(used, arc):
    arc = str(arc or "file").replace("\\", "/")
    if arc not in used:
        used.add(arc)
        return arc
    p = Path(arc)
    stem = p.stem
    suffix = p.suffix
    parent = p.parent.as_posix()
    n = 2
    while True:
        cand_name = f"{stem}({n}){suffix}"
        cand = cand_name if parent in (".", "") else f"{parent}/{cand_name}"
        if cand not in used:
            used.add(cand)
            return cand
        n += 1


def asset_package_ext_from_path(fp, fallback=".png"):
    try:
        suffix = Path(str(fp or "")).suffix
        if suffix and re.fullmatch(r"\.[A-Za-z0-9]{1,8}", suffix):
            return suffix
    except Exception:
        pass
    return fallback


def collect_asset_package_from_project_assets(project_data):
    ensure_asset_groups(project_data)
    groups = {g.get("group_id"): g for g in project_data.get("asset_groups", []) or []}
    entries = []
    missing = []
    used_arcs = set()
    for a in project_data.get("assets", []) or []:
        if not isinstance(a, dict) or a.get("temporary"):
            continue
        cat = a.get("category")
        if cat not in VISUAL_ASSET_PACKAGE_CATEGORIES:
            continue
        name = safe_file_name(a.get("name") or "素材", "素材")
        group_name = None
        if cat in GROUPED_CATEGORIES:
            g = groups.get(a.get("group_id"))
            group_name = safe_file_name((g or {}).get("name") or ("未分组" + cat), "未分组" + cat)
        fp = public_to_local(a.get("file_path"))
        if not fp or not fp.exists() or not fp.is_file():
            missing.append({"category": cat, "name": name, "path": a.get("file_path"), "reason": "file_not_found"})
            continue
        ext = asset_package_ext_from_path(fp)
        if cat in GROUPED_CATEGORIES:
            arc = f"assets/{cat}/{safe_file_name(group_name, cat)}/{name}{ext}"
        else:
            arc = f"assets/{cat}/{name}{ext}"
        arc = unique_zip_arc(used_arcs, arc)
        entries.append({
            "category": cat,
            "group_name": group_name,
            "name": name,
            "file": arc,
            "source": "asset_library",
            "source_asset_id": a.get("asset_id"),
            "source_file_path": a.get("file_path"),
            "_local_path": str(fp),
        })
    return entries, missing


def material_workspace_label_to_category(key):
    return {"character": "人物", "scene": "场景", "object": "道具"}.get(key)


def collect_asset_package_from_material_workspaces(project_data):
    workspaces = project_data.get("material_workspaces") if isinstance(project_data.get("material_workspaces"), dict) else {}
    entries = []
    missing = []
    used_arcs = set()
    for key in ("character", "scene", "object"):
        cat = material_workspace_label_to_category(key)
        ws = workspaces.get(key) if isinstance(workspaces.get(key), dict) else None
        if not ws:
            continue
        scenes = ws.get("scenes") if isinstance(ws.get("scenes"), list) else []
        if key == "object":
            shots = []
            for scene in scenes:
                if isinstance(scene, dict):
                    shots.extend([x for x in (scene.get("shots") or []) if isinstance(x, dict)])
            for shot in shots:
                base_name = safe_file_name(shot.get("shot_code") or shot.get("name") or "道具", "道具")
                candidates = [x for x in (shot.get("storyboard_candidates") or []) if isinstance(x, dict)]
                for idx, c in enumerate(candidates, 1):
                    name = asset_package_name(base_name, idx)
                    fp = public_to_local(c.get("file_path"))
                    if not fp or not fp.exists() or not fp.is_file():
                        missing.append({"category": cat, "name": name, "path": c.get("file_path"), "reason": "file_not_found"})
                        continue
                    ext = asset_package_ext_from_path(fp)
                    arc = unique_zip_arc(used_arcs, f"assets/{cat}/{name}{ext}")
                    entries.append({"category": cat, "group_name": None, "name": name, "file": arc, "source": "material_workspace", "workspace": key, "source_shot_id": shot.get("shot_id"), "source_image_id": c.get("image_id"), "source_file_path": c.get("file_path"), "_local_path": str(fp)})
        else:
            for scene in scenes:
                if not isinstance(scene, dict):
                    continue
                group_name = safe_file_name(scene.get("scene_code") or scene.get("name") or ("未命名" + cat), "未命名" + cat)
                for shot in (scene.get("shots") or []):
                    if not isinstance(shot, dict):
                        continue
                    base_name = safe_file_name(shot.get("shot_code") or shot.get("name") or "素材", "素材")
                    candidates = [x for x in (shot.get("storyboard_candidates") or []) if isinstance(x, dict)]
                    for idx, c in enumerate(candidates, 1):
                        name = asset_package_name(base_name, idx)
                        fp = public_to_local(c.get("file_path"))
                        if not fp or not fp.exists() or not fp.is_file():
                            missing.append({"category": cat, "group_name": group_name, "name": name, "path": c.get("file_path"), "reason": "file_not_found"})
                            continue
                        ext = asset_package_ext_from_path(fp)
                        arc = unique_zip_arc(used_arcs, f"assets/{cat}/{group_name}/{name}{ext}")
                        entries.append({"category": cat, "group_name": group_name, "name": name, "file": arc, "source": "material_workspace", "workspace": key, "source_scene_id": scene.get("scene_id"), "source_shot_id": shot.get("shot_id"), "source_image_id": c.get("image_id"), "source_file_path": c.get("file_path"), "_local_path": str(fp)})
    return entries, missing


def create_asset_package_zip(project_id, responsible_nickname=""):
    data = load_project(project_id)
    pid = data.get("project_id") or project_id
    parent_id, child_id = split_project_ref(pid)
    source_tool = ROOT.name
    if source_tool == "material":
        entries, missing = collect_asset_package_from_material_workspaces(data)
        export_mode = "material_workspaces"
        if not entries:
            entries, missing2 = collect_asset_package_from_project_assets(data)
            missing.extend(missing2)
            export_mode = "asset_library_fallback"
    else:
        entries, missing = collect_asset_package_from_project_assets(data)
        export_mode = "asset_library"
    if not entries:
        raise ValueError("当前子项目没有可导出的图片素材。请先生成待选素材或在素材库中导入图片素材。")
    safe_entries = []
    file_records = []
    local_paths = {}
    for e in entries:
        local_path = Path(e.get("_local_path"))
        public_entry = {k: v for k, v in e.items() if k != "_local_path"}
        local_paths[public_entry.get("file")] = local_path
        file_records.append({"path": public_entry.get("file"), "type": "asset_file", "size": local_path.stat().st_size, "sha256": sha256_file(local_path), "category": public_entry.get("category"), "name": public_entry.get("name"), "group_name": public_entry.get("group_name")})
        safe_entries.append(public_entry)
    grouped_counts = {}
    for e in safe_entries:
        grouped_counts[e.get("category")] = grouped_counts.get(e.get("category"), 0) + 1
    manifest = {
        "schema_version": "1.0",
        "package_type": "dilan_asset_package",
        "exported_at": now_str(),
        "source_tool": source_tool,
        "export_mode": export_mode,
        "responsible_nickname": normalize_user_name(responsible_nickname or "未命名用户"),
        "source_project_id": pid,
        "parent_id": parent_id,
        "child_id": child_id,
        "project_name": data.get("project_name"),
        "categories": VISUAL_ASSET_PACKAGE_CATEGORIES,
        "asset_count": len(safe_entries),
        "category_counts": grouped_counts,
        "missing_files": missing,
        "assets": safe_entries,
        "files": file_records,
        "note": "独立素材包，只包含素材分类、素材组关系和图片本体，不包含工程分镜、提示词和生成历史。",
    }
    base_name = safe_file_name(f"{data.get('parent_id') or '项目'}__{data.get('project_name') or child_id}__素材包_{stamp()}", "素材包")
    zip_path = EXPORTS_DIR / f"{base_name}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
        zf.writestr("asset_package_info.json", json.dumps({
            "schema_version": "1.0",
            "exported_at": manifest["exported_at"],
            "source_tool": source_tool,
            "export_mode": export_mode,
            "source_project_id": pid,
            "asset_count": len(safe_entries),
        }, ensure_ascii=False, indent=2).encode("utf-8"))
        for e in safe_entries:
            src = local_paths.get(e.get("file"))
            if src:
                zf.write(src, e.get("file"))
    append_operation_log(project_id, "export_asset_package", user_name=responsible_nickname, asset_count=len(safe_entries), export_mode=export_mode)
    return zip_path, manifest


def find_or_create_import_group(project_data, category, group_name):
    ensure_asset_groups(project_data)
    group_name = normalize_group_name(group_name or ("未分组" + category), "未分组" + category)
    for g in project_data.get("asset_groups", []) or []:
        if g.get("category") == category and str(g.get("name", "")).strip().lower() == group_name.lower():
            return g, False
    orders = [int(g.get("sort_order") or 0) for g in project_data.get("asset_groups", []) if g.get("category") == category]
    g = {"group_id": new_id("grp"), "category": category, "name": group_name, "sort_order": (max(orders) + 1) if orders else 1, "expanded": True, "created_at": now_str()}
    project_data.setdefault("asset_groups", []).append(g)
    return g, True


LEGACY_ASSET_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
LEGACY_ASSET_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
LEGACY_ASSET_VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".avi"}


def synthesize_legacy_asset_entries(members):
    """老素材包兼容：按 zip 内目录结构合成与新版 manifest.assets 同构的清单。
    路径中出现的已知分类目录（人物/场景/道具/音频/视频）决定分类；没有分类目录时
    按扩展名归类（图片→道具，音频→音频，视频→视频）。能否导入仍由现有
    VISUAL_ASSET_PACKAGE_CATEGORIES 过滤（不支持的分类进入 skipped 列表提示用户）。"""
    known_categories = ("人物", "场景", "道具", "音频", "视频")
    entries = []
    for arc in sorted(members):
        low = str(arc or "")
        if not low or low.endswith("/"):
            continue
        name = low.rsplit("/", 1)[-1]
        if name.lower() in ("manifest.json", "export_info.json", "usage_summary.json", "project.json"):
            continue
        ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
        if ext in LEGACY_ASSET_IMAGE_EXTS:
            default_cat = "道具"
        elif ext in LEGACY_ASSET_AUDIO_EXTS:
            default_cat = "音频"
        elif ext in LEGACY_ASSET_VIDEO_EXTS:
            default_cat = "视频"
        else:
            continue
        cat = ""
        for seg in low.split("/")[:-1]:
            if seg in known_categories:
                cat = seg
                break
        stem = name.rsplit(".", 1)[0] if "." in name else name
        entries.append({"name": stem, "category": cat or default_cat, "file": arc})
    return entries


def import_asset_package_into_project(project_id, data_url, filename="", importer_name=""):
    # 兼容旧的 base64 JSON 上传：解码后落临时文件，走低内存磁盘解压路径。
    IMPORTS_DIR.mkdir(parents=True, exist_ok=True)
    _tmp = IMPORTS_DIR / f"_asset_import_{uuid.uuid4().hex}.zip"
    try:
        raw = decode_data_url_bytes(data_url)
        if len(raw) < 4 or not raw.startswith(b"PK"):
            raise ValueError("请选择本工具导出的 .zip 素材包")
        _tmp.write_bytes(raw)
        del raw
        return import_asset_package_from_zip_path(project_id, _tmp, filename, importer_name)
    finally:
        try:
            _tmp.unlink()
        except Exception:
            pass


def import_asset_package_from_zip_path(project_id, zip_path, filename="", importer_name=""):
    """从磁盘上的 zip 素材包直接解压导入，低内存。"""
    pid = safe_name(project_id or "", "")
    if not pid:
        raise ValueError("missing project")
    zip_path = Path(zip_path)
    if (not zip_path.exists()) or zip_path.stat().st_size < 4:
        raise ValueError("请选择本工具导出的 .zip 素材包")
    with open(zip_path, "rb") as _pk:
        if _pk.read(2) != b"PK":
            raise ValueError("请选择本工具导出的 .zip 素材包")
    data = load_project(pid)
    ensure_asset_groups(data)
    imported_at = now_str()
    imported = []
    renamed = []
    skipped = []
    created_groups = 0
    merged_groups = 0
    with zipfile.ZipFile(zip_path, "r") as zf:
        members = {}
        for info in zf.infolist():
            arc = safe_zip_arcname(info.filename)
            if arc is not None:
                members[arc] = info
        manifest = read_zip_json(zf, "manifest.json", {}) or {}
        package_type = str(manifest.get("package_type") or "").strip()
        if package_type and package_type != "dilan_asset_package":
            raise ValueError("请选择素材包，不要选择工程包")
        package_assets = manifest.get("assets") if isinstance(manifest.get("assets"), list) else []
        if not package_assets:
            # 兼容老版本素材包：没有 manifest 或没有 assets 清单时，先排除工程包，
            # 再按压缩包目录结构合成清单（目录名=分类，文件名=素材名）。
            if any(arc == "project.json" or arc.endswith("/project.json") for arc in members):
                raise ValueError("请选择素材包，不要选择工程包")
            package_assets = synthesize_legacy_asset_entries(members)
            if not package_assets:
                raise ValueError("素材包中没有可识别的素材文件：请确认压缩包内含 人物/场景/道具 目录或图片文件。")
        existing_names = [a.get("name") for a in data.get("assets", []) or [] if not a.get("temporary")]
        for item in package_assets:
            if not isinstance(item, dict):
                continue
            cat = item.get("category")
            if cat not in VISUAL_ASSET_PACKAGE_CATEGORIES:
                skipped.append({"name": item.get("name"), "category": cat, "reason": "unsupported_category"})
                continue
            arc = safe_zip_arcname(item.get("file") or "")
            if not arc or arc not in members:
                skipped.append({"name": item.get("name"), "category": cat, "reason": "file_missing_in_package"})
                continue
            base_name = safe_file_name(item.get("name") or Path(arc).stem or "素材", "素材")
            final_name = unique_asset_name_casefold(existing_names, base_name)
            if final_name != base_name:
                renamed.append({"from": base_name, "to": final_name})
            existing_names.append(final_name)
            ext = asset_package_ext_from_path(arc)
            out_dir = INPUT_DIR / safe_name(pid) / cat
            out_dir.mkdir(parents=True, exist_ok=True)
            out = out_dir / f"{final_name}{ext}"
            while out.exists():
                final_name = unique_asset_name_casefold(existing_names, final_name)
                existing_names.append(final_name)
                out = out_dir / f"{final_name}{ext}"
            with zf.open(members[arc], "r") as src, out.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            rel = out.relative_to(INPUT_DIR).as_posix()
            asset = {"asset_id": new_id("asset"), "name": out.stem, "category": cat, "file_path": "/input/" + rel, "created_at": imported_at, "imported_from_asset_package": {"filename": safe_file_name(filename or "素材包.zip", "素材包.zip"), "source_project_id": manifest.get("source_project_id"), "source_name": base_name, "imported_by": normalize_user_name(importer_name or "未命名用户"), "imported_at": imported_at}}
            if cat in GROUPED_CATEGORIES:
                group, created = find_or_create_import_group(data, cat, item.get("group_name") or ("未分组" + cat))
                asset["group_id"] = group.get("group_id")
                if created:
                    created_groups += 1
                else:
                    merged_groups += 1
            data.setdefault("assets", []).append(asset)
            imported.append(asset)
    ensure_asset_groups(data)
    save_project(pid, data)
    append_operation_log(pid, "import_asset_package", user_name=importer_name, imported_count=len(imported), renamed_count=len(renamed), created_groups=created_groups, skipped_count=len(skipped))
    return {"data": data, "imported_count": len(imported), "renamed": renamed, "renamed_count": len(renamed), "skipped": skipped, "skipped_count": len(skipped), "created_groups": created_groups, "merged_group_hits": merged_groups}

def import_child_name_from_filename(filename: str, fallback: str = "导入子项目"):
    raw = str(filename or "").replace("\\", "/").split("/")[-1].strip()
    if raw.lower().endswith(".zip"):
        raw = raw[:-4]
    raw = raw.strip()
    return safe_name(raw or fallback or "导入子项目", "导入子项目")

def replace_project_public_paths(value, old_pid, new_pid):
    old_storage = safe_name(old_pid, "project")
    new_storage = safe_name(new_pid, "project")
    old_parent, old_child = split_project_ref(old_pid)
    new_parent, new_child = split_project_ref(new_pid)
    if isinstance(value, str):
        out = value
        out = out.replace(f"/input/{old_storage}/", f"/input/{new_storage}/")
        out = out.replace(f"/output/image/{old_storage}/", f"/output/image/{new_storage}/")
        out = out.replace(f"/output/video/{old_storage}/", f"/output/video/{new_storage}/")
        if old_parent and old_child and new_parent and new_child:
            out = out.replace(f"/temp/{old_parent}/children/{old_child}/", f"/temp/{new_parent}/children/{new_child}/")
        out = out.replace(f"/temp/{old_pid}/", f"/temp/{new_pid}/")
        out = out.replace(f"/temp/{old_storage}/", f"/temp/{new_pid}/")
        return out
    if isinstance(value, list):
        return [replace_project_public_paths(x, old_pid, new_pid) for x in value]
    if isinstance(value, dict):
        return {k: replace_project_public_paths(v, old_pid, new_pid) for k, v in value.items()}
    return value



def _merge_copy_storage(old_pid, new_pid):
    """把某个源子项目的本地媒体文件复制到新子项目的存储目录（input/output）。"""
    old_s = safe_name(old_pid, "project"); new_s = safe_name(new_pid, "project")
    pairs = [(INPUT_DIR / old_s, INPUT_DIR / new_s),
             (OUTPUT_IMAGE_DIR / old_s, OUTPUT_IMAGE_DIR / new_s),
             (OUTPUT_VIDEO_DIR / old_s, OUTPUT_VIDEO_DIR / new_s)]
    for src, dst in pairs:
        if not src.exists():
            continue
        for f in src.rglob("*"):
            if f.is_file():
                rel = f.relative_to(src)
                target = dst / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    try:
                        shutil.copy2(f, target)
                    except Exception as e:
                        print("[merge] 复制文件失败:", f, e)


def merge_projects_local(source_pids, target_name="", importer_name=""):
    """把多个本地子项目按顺序合并成一个新的本地子项目。
    规则：以第一个项目为基底；后续项目的 SC 若编号不存在则整段加入，
    若编号已存在则把其子栏(shot)接到该 SC 子栏后面（先选的在前）。
    SC、子栏编号由 normalize_project 统一重排。被引用素材一并并入。"""
    if not isinstance(source_pids, list) or len(source_pids) < 2:
        raise ValueError("请至少选择两个子项目进行合并")
    datas = []
    parents = set()
    for pid in source_pids:
        d = load_project(pid)
        datas.append(d)
        parents.add(d.get("parent_id") or "")
    if len(parents) > 1:
        raise ValueError("只能合并同一个父项目下的子项目")
    parent_id = (datas[0].get("parent_id") or "") or None

    base_name = safe_name(target_name or datas[0].get("project_name") or "合并子项目", "合并子项目")
    if parent_id:
        new_pid, parent_id, child_id = choose_import_project_id(parent_id, base_name)
    else:
        # 无父项目的独立子项目
        cand = base_name; i = 1
        while project_path(cand).exists():
            i += 1; cand = f"{base_name}_{i}"
        new_pid, child_id = cand, cand
    ensure_project_dirs(new_pid)

    # 基底 = 第一个项目深拷贝，路径改写到新 pid 并复制文件
    merged = json.loads(json.dumps(datas[0], ensure_ascii=False))
    first_old_pid = datas[0].get("project_id")
    merged = replace_project_public_paths(merged, first_old_pid, new_pid)
    _merge_copy_storage(first_old_pid, new_pid)
    merged["scenes"] = merged.get("scenes") or []
    merged["assets"] = merged.get("assets") or []
    merged["asset_groups"] = merged.get("asset_groups") or []

    # SC 索引（按编号）
    def scene_key(sc): return int(scene_number_from_scene(sc, 1))
    scene_by_num = {scene_key(sc): sc for sc in merged["scenes"]}
    asset_ids = {a.get("asset_id") for a in merged["assets"] if isinstance(a, dict)}
    group_ids = {g.get("group_id") for g in merged["asset_groups"] if isinstance(g, dict)}

    for d in datas[1:]:
        old_pid = d.get("project_id")
        d2 = json.loads(json.dumps(d, ensure_ascii=False))
        d2 = replace_project_public_paths(d2, old_pid, new_pid)
        _merge_copy_storage(old_pid, new_pid)
        # 合并素材（按 asset_id 去重）
        for a in (d2.get("assets") or []):
            if isinstance(a, dict) and a.get("asset_id") not in asset_ids:
                merged["assets"].append(a); asset_ids.add(a.get("asset_id"))
        for g in (d2.get("asset_groups") or []):
            if isinstance(g, dict) and g.get("group_id") not in group_ids:
                merged["asset_groups"].append(g); group_ids.add(g.get("group_id"))
        # 合并 SC
        for sc in (d2.get("scenes") or []):
            num = scene_key(sc)
            if num in scene_by_num:
                tgt = scene_by_num[num]
                tgt.setdefault("shots", [])
                base = len(tgt["shots"])
                for j, sh in enumerate(sc.get("shots") or [], 1):
                    sh["shot_id"] = sh.get("shot_id") or new_id("shot")
                    sh["sort_order"] = base + j
                    tgt["shots"].append(sh)
            else:
                merged["scenes"].append(sc)
                scene_by_num[num] = sc

    merged["parent_id"] = parent_id
    merged["child_id"] = child_id
    merged["project_id"] = new_pid
    merged["project_name"] = base_name
    merged["created_at"] = now_str()
    merged["updated_at"] = now_str()
    merged["merge_info"] = {
        "merged_at": now_str(),
        "merged_by": normalize_user_name(importer_name or "未命名用户"),
        "source_project_ids": [d.get("project_id") for d in datas],
        "source_names": [d.get("project_name") for d in datas],
    }
    normalize_project(merged, rename_dirs=False)
    write_json_file(project_json_path(new_pid), merged)
    if parent_id:
        ensure_parent_across_modules(parent_id, parent_id)
    return {"data": merged, "project": project_meta(new_pid), "new_project_id": new_pid}


def choose_import_project_id(parent_id, child_id):
    parent_id = safe_name(parent_id or "导入项目", "导入项目")
    base_child = safe_name(child_id or "导入子项目", "导入子项目")
    i = 1
    while True:
        child = base_child if i == 1 else safe_name(f"{base_child}（导入{i-1}）", "导入子项目")
        candidate = make_project_ref(parent_id, child)
        conflict_paths = [project_json_path(candidate), INPUT_DIR / safe_name(candidate), OUTPUT_IMAGE_DIR / safe_name(candidate)]
        if "OUTPUT_VIDEO_DIR" in globals():
            conflict_paths.append(OUTPUT_VIDEO_DIR / safe_name(candidate))
        if not any(p.exists() for p in conflict_paths):
            return candidate, parent_id, child
        i += 1


def write_zip_member_to(zf: zipfile.ZipFile, info: zipfile.ZipInfo, dest: Path):
    dest = dest.resolve()
    allowed_roots = [PROJECTS_DIR.resolve(), INPUT_DIR.resolve(), OUTPUT_IMAGE_DIR.resolve(), OUTPUT_VIDEO_DIR.resolve(), IMPORTS_DIR.resolve()]
    if not any(dest == root or root in dest.parents for root in allowed_roots):
        raise ValueError(f"导入目标路径不安全：{dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zf.open(info, "r") as src, dest.open("wb") as out:
        shutil.copyfileobj(src, out)




def package_module_label(module_type):
    return PROJECT_MODULE_LABELS.get(str(module_type or "").strip(), str(module_type or "未知模块") or "未知模块")


def detect_project_package_module(manifest, project_data=None):
    project_data = project_data if isinstance(project_data, dict) else {}
    declared = str((manifest or {}).get("module_type") or (manifest or {}).get("tool_module") or project_data.get("module_type") or project_data.get("project_module_type") or "").strip()
    if declared:
        return declared
    return infer_project_package_module(project_data)


def infer_project_package_module(project_data):
    """兼容 V29 前的老工程包：没有模块标识时按工程数据指纹推断所属模块。
    美术：工作区结构（material_workspaces）是美术模块独有；
    视频：generated_videos / last_video_status / video_* 参数只出现在视频模块。
    老分镜包与未启用工作区的更老美术包结构相同，无法区分时返回空串，
    由 validate 按「用户当前发起导入的模块」兜底导入。"""
    data = project_data if isinstance(project_data, dict) else {}
    if isinstance(data.get("material_workspaces"), dict) or data.get("material_module_version") or data.get("active_material_workspace"):
        return "material"
    ps = data.get("project_settings") if isinstance(data.get("project_settings"), dict) else {}
    if any(k in ps for k in ("video_model", "video_duration", "video_ratio", "video_resolution")):
        return "video"
    for scene in (data.get("scenes") or []):
        if not isinstance(scene, dict):
            continue
        for shot in (scene.get("shots") or []):
            if not isinstance(shot, dict):
                continue
            for tab in (shot.get("tabs") or []):
                if not isinstance(tab, dict):
                    continue
                if tab.get("generated_videos") or tab.get("last_video_status"):
                    return "video"
                st = tab.get("settings") if isinstance(tab.get("settings"), dict) else {}
                if any(k in st for k in ("video_model", "video_duration", "video_generate_audio")):
                    return "video"
                for img in (tab.get("generated_images") or []):
                    if isinstance(img, dict) and (img.get("media_type") == "video" or img.get("video_id")):
                        return "video"
    return ""


def import_project_package_into_module(module_type, zip_path, filename="", importer_name=""):
    module_type = str(module_type or "").strip()
    if module_type == PROJECT_MODULE_TYPE:
        raise ValueError("工程包已经属于当前模块，无需转发导入")
    if module_type == "image":
        raise ValueError("分镜模块已从本工作流移除，无法导入分镜工程包")
    if module_type not in PROJECT_MODULE_KEYS:
        raise ValueError(f"未知工程包模块：{module_type or '空'}")
    target_script = ROOT.parent / module_type / "image_server.py"
    if not target_script.exists():
        raise ValueError(f"找不到{package_module_label(module_type)}的导入服务文件：{target_script}")
    module_name = f"_dilan_import_{module_type}_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, target_script)
    if spec is None or spec.loader is None:
        raise ValueError(f"无法加载{package_module_label(module_type)}导入服务")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.import_project_package_from_zip_path(zip_path, filename, importer_name, "")


def validate_project_package_module(manifest, project_data=None):
    project_data = project_data if isinstance(project_data, dict) else {}
    package_type = str((manifest or {}).get("package_type") or "").strip()
    if package_type == "dilan_asset_package":
        raise ValueError("请选择工程包，不要选择素材包")
    if package_type and package_type not in (PROJECT_PACKAGE_TYPE, LEGACY_PROJECT_PACKAGE_TYPE):
        raise ValueError("该 ZIP 不是帝蓝工作流工程包，无法导入")
    module_type = detect_project_package_module(manifest, project_data)
    if not module_type:
        # 兼容 V29 前的老工程包：无模块标识、内容指纹也无法判别（老分镜包与未启用
        # 工作区的老美术包结构一致）。按用户发起导入的当前模块导入，不再拒绝。
        return True
    if module_type != PROJECT_MODULE_TYPE:
        return False
    return True


def import_project_package(data_url, filename="", importer_name="", target_parent=""):
    # 兼容旧的 base64 JSON 上传：解码后落临时文件，走与大文件相同的低内存磁盘解压路径。
    IMPORTS_DIR.mkdir(parents=True, exist_ok=True)
    _tmp = IMPORTS_DIR / f"_import_{uuid.uuid4().hex}.zip"
    try:
        raw = decode_data_url_bytes(data_url)
        if len(raw) < 4 or not raw.startswith(b"PK"):
            raise ValueError("请选择由本工具导出的 .zip 工程包")
        _tmp.write_bytes(raw)
        del raw
        return import_project_package_from_zip_path(_tmp, filename, importer_name, target_parent)
    finally:
        try:
            _tmp.unlink()
        except Exception:
            pass


def import_project_package_from_zip_path(zip_path, filename="", importer_name="", target_parent=""):
    """从磁盘上的 zip 工程包直接解压导入：zipfile 只按需读取中央目录和单个成员，
    不会把整包读进内存，因此工程包再大也不会内存溢出。"""
    zip_path = Path(zip_path)
    if (not zip_path.exists()) or zip_path.stat().st_size < 4:
        raise ValueError("请选择由本工具导出的 .zip 工程包")
    with open(zip_path, "rb") as _pk:
        if _pk.read(2) != b"PK":
            raise ValueError("请选择由本工具导出的 .zip 工程包")
    imported_at = now_str()
    with zipfile.ZipFile(zip_path, "r") as zf:
        members = []
        member_names = set()
        for info in zf.infolist():
            arc = safe_zip_arcname(info.filename)
            if arc is None:
                continue
            members.append((arc, info))
            member_names.add(arc)
        manifest = read_zip_json(zf, "manifest.json", {}) or {}
        if str(manifest.get("package_type") or "").strip() == "dilan_asset_package":
            raise ValueError("请选择工程包，不要选择素材包")
        project_json_arc = manifest.get("project_json")
        if not project_json_arc or project_json_arc not in member_names:
            candidates = [arc for arc, _ in members if arc.endswith("/project.json") or arc == "project.json"]
            if not candidates:
                raise ValueError("工程包中没有找到 project.json")
            project_json_arc = candidates[0]
        project_data = read_zip_json(zf, project_json_arc, None)
        if not isinstance(project_data, dict):
            raise ValueError("project.json 无法读取或格式不正确")
        module_type = detect_project_package_module(manifest, project_data)
        if module_type and module_type != PROJECT_MODULE_TYPE:
            return import_project_package_into_module(module_type, zip_path, filename, importer_name)
        validate_project_package_module(manifest, project_data)
        # 老工程包补写模块标识：导入后再导出/识别不再缺失。
        project_data.setdefault("module_type", PROJECT_MODULE_TYPE)

        parent_json_arc = manifest.get("parent_json")
        parent_data = read_zip_json(zf, parent_json_arc, None) if parent_json_arc else None
        if not isinstance(parent_data, dict):
            parent_data = {}

        old_pid = project_data.get("project_id") or manifest.get("source_project_id") or manifest.get("project_id") or "导入项目"
        old_parent, old_child_from_pid = split_project_ref(old_pid)
        parent_identity = resolve_import_parent_identity(manifest, project_data, parent_data, old_parent)
        source_parent = parent_identity["source_parent_id"]
        source_parent_name = parent_identity["source_parent_name"]
        target_parent = parent_identity["target_parent_id"]
        target_parent_name = parent_identity["target_parent_name"]
        source_child_original = safe_name(
            project_data.get("child_id")
            or manifest.get("child_id")
            or ((manifest.get("project_scope") or {}).get("child_id") if isinstance(manifest.get("project_scope"), dict) else None)
            or project_data.get("child_name")
            or manifest.get("child_name")
            or project_data.get("project_name")
            or old_child_from_pid
            or "导入子项目",
            "导入子项目",
        )
        source_child = source_child_original
        new_pid, target_parent, target_child = choose_import_project_id(target_parent, source_child)

        old_storage = safe_name(old_pid, "project")
        new_storage = safe_name(new_pid, "project")
        old_parent = safe_name(old_parent or source_parent, "导入项目")
        old_child = safe_name(old_child_from_pid or source_child, "导入子项目")

        project_data = replace_project_public_paths(project_data, old_pid, new_pid)
        project_data["parent_id"] = target_parent
        project_data["child_id"] = target_child
        project_data["project_id"] = new_pid
        project_data["project_name"] = target_child
        project_data["updated_at"] = imported_at
        project_data.setdefault("created_at", imported_at)
        project_data["import_info"] = {
            "imported_at": imported_at,
            "imported_by": normalize_user_name(importer_name or "未命名用户"),
            "source_filename": safe_file_name(filename or "工程包.zip", "工程包.zip"),
            "source_project_id": old_pid,
            "source_parent_id": source_parent,
            "source_parent_name": source_parent_name,
            "target_parent_id": target_parent,
            "target_parent_name": target_parent_name,
            "parent_matched_by": parent_identity.get("matched_by"),
            "created_new_parent": parent_identity.get("created_new_parent"),
            "source_child_id": source_child_original,
            "imported_project_name_from_zip": source_child,
            "source_responsible_nickname": manifest.get("responsible_nickname") or (read_zip_json(zf, "export_info.json", {}) or {}).get("responsible_nickname"),
            "source_exported_at": manifest.get("exported_at"),
            "import_mode": "copy_no_overwrite",
            "source_module_type": PROJECT_MODULE_TYPE,
            "source_module_label": PROJECT_MODULE_LABEL,
        }

        usage_snapshot = read_zip_json(zf, "usage_summary.json", None)
        if isinstance(usage_snapshot, dict):
            project_data["imported_usage_summary"] = usage_snapshot

        # Create or update parent metadata from the package itself.
        if not isinstance(parent_data, dict) or not parent_data:
            parent_data = create_parent_data(target_parent)
        parent_data["parent_id"] = target_parent
        parent_data["parent_name"] = target_parent_name or target_parent
        parent_data["source_parent_id_from_package"] = source_parent
        parent_data["source_parent_name_from_package"] = source_parent_name
        parent_data["updated_at"] = imported_at
        parent_data["version"] = parent_data.get("version") or 2
        ensure_parent_across_modules(target_parent, parent_data.get("parent_name") or target_parent)
        write_json_file(parent_json_path(target_parent), parent_data)

        # Copy project-owned physical files, remapping package project id to the new local project id when needed.
        input_prefix = f"input/{old_storage}/"
        output_prefix = f"output/image/{old_storage}/"
        output_video_prefix = f"output/video/{old_storage}/"
        temp_prefix = f"projects/{old_parent}/children/{old_child}/temp_refs/"
        copied_files = 0
        for arc, info in members:
            if arc in {"manifest.json", "export_info.json", "usage_summary.json", project_json_arc, parent_json_arc}:
                continue
            if arc.startswith(input_prefix):
                rel = arc[len(input_prefix):]
                write_zip_member_to(zf, info, INPUT_DIR / new_storage / rel)
                copied_files += 1
            elif arc.startswith(output_prefix):
                rel = arc[len(output_prefix):]
                write_zip_member_to(zf, info, OUTPUT_IMAGE_DIR / new_storage / rel)
                copied_files += 1
            elif arc.startswith(output_video_prefix):
                rel = arc[len(output_video_prefix):]
                write_zip_member_to(zf, info, OUTPUT_VIDEO_DIR / new_storage / rel)
                copied_files += 1
            elif arc.startswith(temp_prefix):
                rel = arc[len(temp_prefix):]
                write_zip_member_to(zf, info, project_path(new_pid) / "temp_refs" / rel)
                copied_files += 1

        project_path(new_pid).mkdir(parents=True, exist_ok=True)
        meta_dir = project_path(new_pid) / "imported_package_meta"
        meta_dir.mkdir(parents=True, exist_ok=True)
        for meta_arc in ("manifest.json", "export_info.json", "usage_summary.json"):
            if meta_arc in member_names:
                (meta_dir / meta_arc).write_bytes(zf.read(meta_arc))
        (meta_dir / "import_summary.json").write_text(json.dumps({
            "imported_at": imported_at,
            "imported_by": normalize_user_name(importer_name or "未命名用户"),
            "source_filename": filename,
            "source_project_id": old_pid,
            "source_parent_id": source_parent,
            "source_parent_name": source_parent_name,
            "target_parent_id": target_parent,
            "target_parent_name": target_parent_name,
            "new_project_id": new_pid,
            "copied_files": copied_files,
        }, ensure_ascii=False, indent=2), encoding="utf-8")

        normalize_project(project_data, rename_dirs=False)
        write_json_file(project_json_path(new_pid), project_data)
        return {
            "data": project_data,
            "project": project_meta(new_pid),
            "manifest": manifest,
            "copied_files": copied_files,
            "imported_as_copy": new_pid != old_pid,
        }



# ---------------- Seedance 2.0 / Volcengine Ark video core ----------------

def ext_from_url_or_type(url: str, content_type: str = ""):
    lower = (url or "").split("?", 1)[0].lower()
    for ext in (".mp4", ".mov", ".webm", ".m4v"):
        if lower.endswith(ext):
            return ext
    ct = (content_type or "").lower()
    if "webm" in ct:
        return ".webm"
    if "quicktime" in ct or "mov" in ct:
        return ".mov"
    return ".mp4"


class ArkRequestError(RuntimeError):
    def __init__(self, message, *, status_code=None, body="", parsed=None, method="", path="", payload=None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body or ""
        self.parsed = parsed
        self.method = method or ""
        self.path = path or ""
        self.payload = payload


class SeedanceTaskError(RuntimeError):
    def __init__(self, message, *, task_id="", task_detail=None):
        super().__init__(message)
        self.task_id = task_id or ""
        self.task_detail = task_detail


def try_parse_json_text(text):
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def compact_text(text, limit=1800):
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... <已截断，原始长度 {len(text)} 字符>"


def sanitize_for_log(value, max_string=1600):
    """Return a debug-safe copy: keep structure, redact keys and huge base64 blobs."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            lk = str(k).lower()
            if lk in ("api_key", "apikey", "authorization", "access_key", "secret_key", "token"):
                out[k] = "<redacted>"
            else:
                out[k] = sanitize_for_log(v, max_string=max_string)
        return out
    if isinstance(value, list):
        return [sanitize_for_log(v, max_string=max_string) for v in value]
    if isinstance(value, str):
        if value.startswith("data:"):
            head = value.split(",", 1)[0]
            return f"{head},<base64 已隐藏，长度 {len(value)} 字符>"
        if len(value) > max_string:
            return value[:max_string] + f"... <已截断，原始长度 {len(value)} 字符>"
        return value
    return value


def pretty_debug_json(value):
    try:
        return json.dumps(sanitize_for_log(value), ensure_ascii=False, indent=2)
    except Exception:
        return compact_text(value)


def extract_ark_error_fields(obj):
    fields = {"code": "", "message": "", "param": "", "type": "", "request_id": ""}
    if isinstance(obj, dict):
        err = obj.get("error") if isinstance(obj.get("error"), dict) else obj
        if isinstance(err, dict):
            for key in fields:
                v = err.get(key) or err.get(key.replace("_", ""))
                if v is not None:
                    fields[key] = str(v)
        if not fields["request_id"]:
            for key in ("request_id", "requestId", "RequestId", "X-Tt-Logid"):
                if obj.get(key):
                    fields["request_id"] = str(obj.get(key))
                    break
    return fields


def ark_error_message_from_body(body, parsed=None):
    parsed = parsed if parsed is not None else try_parse_json_text(body)
    fields = extract_ark_error_fields(parsed) if parsed is not None else {}
    parts = []
    for label, key in (("code", "code"), ("param", "param"), ("type", "type"), ("message", "message"), ("request_id", "request_id")):
        val = fields.get(key) if isinstance(fields, dict) else ""
        if val:
            parts.append(f"{label}={val}")
    if parts:
        return "; ".join(parts)
    return compact_text(body, 1000)


def summarize_seedance_content(payload):
    items = []
    for i, c in enumerate((payload or {}).get("content") or []):
        if not isinstance(c, dict):
            items.append({"index": i, "value": sanitize_for_log(c)})
            continue
        typ = c.get("type") or ""
        row = {"index": i, "type": typ, "role": c.get("role") or ""}
        for key in ("text", "image_url", "audio_url", "video_url", "url"):
            if key in c:
                row[key] = sanitize_for_log(c.get(key), max_string=500)
        items.append(row)
    return items


def print_debug_block(title, obj_or_text=None):
    print("\n" + "=" * 88, flush=True)
    print(title, flush=True)
    print("=" * 88, flush=True)
    if obj_or_text is not None:
        if isinstance(obj_or_text, (dict, list)):
            print(pretty_debug_json(obj_or_text), flush=True)
        else:
            print(str(obj_or_text), flush=True)


def ark_json_request(method: str, path: str, api_key: str, payload=None, timeout=180):
    if not api_key:
        raise ValueError("missing Ark API Key")
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Authorization": "Bearer " + api_key}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(ARK_BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        parsed = try_parse_json_text(body)
        msg = f"Ark HTTP {e.code}: {ark_error_message_from_body(body, parsed)}"
        raise ArkRequestError(msg, status_code=e.code, body=body, parsed=parsed, method=method, path=path, payload=payload) from e
    except Exception as e:
        msg = f"Ark request failed: {type(e).__name__}: {e}"
        raise ArkRequestError(msg, method=method, path=path, payload=payload) from e


def deep_video_url(obj, seen=None):
    seen = seen or set()
    if not obj:
        return ""
    if isinstance(obj, str):
        if re.search(r"^https?://", obj) and re.search(r"(\.mp4|\.mov|\.webm|m3u8|video|tos|volc|byteimg|vod)", obj, re.I):
            return obj
        return ""
    oid = id(obj)
    if oid in seen:
        return ""
    seen.add(oid)
    if isinstance(obj, list):
        for item in obj:
            got = deep_video_url(item, seen)
            if got:
                return got
    if isinstance(obj, dict):
        for key in ("video_url", "videoUrl", "output_video_url", "outputVideoUrl", "result_video_url", "resultVideoUrl", "download_url", "downloadUrl", "url"):
            v = obj.get(key)
            if isinstance(v, str) and v.startswith("http"):
                return v
            if isinstance(v, dict):
                got = deep_video_url(v, seen)
                if got:
                    return got
        if isinstance(obj.get("video"), dict) and isinstance(obj["video"].get("url"), str):
            return obj["video"]["url"]
        if isinstance(obj.get("content"), list):
            for c in obj["content"]:
                if isinstance(c, dict) and (c.get("type") == "video_url" or c.get("video_url")):
                    vu = c.get("video_url")
                    if isinstance(vu, dict) and isinstance(vu.get("url"), str):
                        return vu["url"]
                    if isinstance(vu, str) and vu.startswith("http"):
                        return vu
                    if isinstance(c.get("url"), str) and c["url"].startswith("http"):
                        return c["url"]
        for v in obj.values():
            got = deep_video_url(v, seen)
            if got:
                return got
    return ""


def video_dimensions(resolution="720p", ratio="16:9"):
    base_h = {"480p": 480, "720p": 720, "1080p": 1080}.get(str(resolution or "720p"), 720)
    ratio = str(ratio or "16:9")
    parts = ratio.split(":")
    try:
        rw, rh = max(1, float(parts[0])), max(1, float(parts[1]))
    except Exception:
        rw, rh = 16.0, 9.0
    if rw >= rh:
        h = base_h
        w = int(round(base_h * rw / rh))
    else:
        w = base_h
        h = int(round(base_h * rh / rw))
    return max(1, w), max(1, h)


def estimate_seedance_cost(prompt: str, settings: dict, data: dict):
    cfg = load_config().get("global_defaults", {})
    model = normalize_video_model(settings.get("video_model") or cfg.get("video_model") or DEFAULT_VIDEO_MODEL)
    duration = parse_positive_int(settings.get("video_duration"), cfg.get("video_duration") or 8) or 8
    resolution = settings.get("video_resolution") or cfg.get("video_resolution") or "720p"
    ratio = settings.get("video_ratio") or cfg.get("video_ratio") or "16:9"
    fps = float(cfg.get("seedance_fps") or 24)
    unknown_input_seconds = float(cfg.get("seedance_unknown_input_video_seconds") or 4)
    aliases = referenced_aliases(prompt, data)
    assets_by_name = {str(a.get("name", "")).lower(): a for a in data.get("assets", []) or []}
    has_video_input = False
    for alias in aliases:
        a = assets_by_name.get(str(alias).lower())
        if a and a.get("category") == "视频":
            has_video_input = True
            break
    # 注意括号：or 0 必须作用于两个分支的取值结果，否则配置键缺失时 float(None) 直接崩溃。
    if model == V25_VIDEO_MODEL:
        price = float((cfg.get("seedance_v25_with_video_cny_per_million") if has_video_input else cfg.get("seedance_v25_no_video_cny_per_million")) or 0)
        if price <= 0:
            price = 42.0 if has_video_input else 70.0
    elif model == FAST_VIDEO_MODEL:
        price = float((cfg.get("seedance_fast_with_video_cny_per_million") if has_video_input else cfg.get("seedance_fast_no_video_cny_per_million")) or 0)
    else:
        price = float((cfg.get("seedance_regular_with_video_cny_per_million") if has_video_input else cfg.get("seedance_regular_no_video_cny_per_million")) or 0)
    if price <= 0:
        price = 22.0 if model == FAST_VIDEO_MODEL and has_video_input else 37.0 if model == FAST_VIDEO_MODEL else 42.0 if model == V25_VIDEO_MODEL and has_video_input else 70.0 if model == V25_VIDEO_MODEL else 28.0 if has_video_input else 46.0
    w, h = video_dimensions(resolution, ratio)
    billed_seconds = duration + (unknown_input_seconds if has_video_input else 0)
    tokens = int(round(billed_seconds * w * h * fps / 1024))
    cny = round(tokens * price / 1000000, 4)
    return {
        "model": display_video_model(model),
        "model_id": model,
        "resolution": resolution,
        "ratio": ratio,
        "duration_seconds": duration,
        "fps": fps,
        "width": w,
        "height": h,
        "has_video_input": has_video_input,
        "input_video_seconds_estimated": unknown_input_seconds if has_video_input else 0,
        "price_cny_per_million_tokens": price,
        "estimated_tokens": tokens,
        "estimated_cny": cny,
    }


def explain_seedance_error(err):
    """Build a short Chinese message for the front-end. Keep it human-readable."""
    fields = {}
    if isinstance(err, dict):
        fields = extract_ark_error_fields(err)
        text = json.dumps(err, ensure_ascii=False)
    else:
        text = str(err or "")
        parsed = try_parse_json_text(text)
        if parsed is not None:
            fields = extract_ark_error_fields(parsed)
    low = text.lower()
    code = (fields.get("code") or "").strip()
    msg = (fields.get("message") or "").strip()
    param = (fields.get("param") or "").strip()

    if param:
        if "audio_url" in param.lower():
            return f"音频参考参数不符合 Ark 要求：{param}。请检查音频是否被正确放入 audio_url，且上传后地址能被 Ark 访问。"
        if "video_url" in param.lower():
            return f"视频参考参数不符合 Ark 要求：{param}。请检查是否把音频/本地文件误传成 video_url，或参考视频是否不是公网 URL。"
        if "image_url" in param.lower():
            return f"图片参考参数不符合 Ark 要求：{param}。请检查图片 URL 或图片引用素材映射。"
        if "content" in param.lower():
            return f"请求体 content 字段不符合 Ark 要求：{param}。请检查提示词、@素材引用和音频/图片/视频字段类型。"
    if "reference_video" in low and "web url" in low:
        return "你引用了视频素材，但 Ark 接口要求参考视频必须是公网可访问的 Web URL。本地视频文件不能直接作为参考视频传给云端。"
    if "web url" in low and ("audio" in low or "audio_url" in low):
        return "音频参考地址不是 Ark 可访问的公网 URL。请检查工具是否把本地音频上传成了公网直链，或是否误用了本地路径 / localhost / data URL。"
    if "api key" in low or "unauthorized" in low or "invalidauthorization" in low or "authentication" in low:
        return "API Key 或鉴权信息无效。请检查右上角 Ark API Key 是否填写正确、是否还有权限调用当前模型。"
    if "setlimitexceeded" in low or "safe experience mode" in low or "limit" in low:
        return "账号或模型触发了调用限制。请到火山方舟模型开通/安全体验模式页面检查限额，或等待额度恢复。"
    if "insufficient" in low or "balance" in low or "quota" in low:
        return "账号余额、额度或资源包可能不足。请检查火山引擎费用中心与模型额度。"
    if "copyright" in low or "safety" in low or "policy" in low or "sensitive" in low:
        return "生成内容可能触发版权、安全或内容策略限制。请调整提示词，减少涉及真实人物、影视 IP、商标或敏感内容的描述。"
    if "timeout" in low or "timed out" in low or "readtimeout" in low:
        return "请求或轮询超时。可能是网络不稳定、任务排队过久或接口响应过慢。可以稍后重试。"
    if "prompt" in low and ("shorter than 1" in low or "不能为空" in low):
        return "提示词为空或被解析后为空。请检查 @素材解析后是否仍保留了有效文字描述。"
    if "duration" in low or "resolution" in low or "ratio" in low or "parameter" in low or "invalidparameter" in low or code.lower() == "invalidparameter":
        if msg:
            return f"请求参数不符合 Ark 接口要求：{msg}"
        return "请求参数不符合 Ark 接口要求。请检查模型、比例、分辨率、时长、参考素材类型是否匹配。"
    if msg:
        return f"Ark 返回错误：{msg}"
    return "视频生成失败。请展开技术详情查看 Ark 原始报错；后端终端已打印请求体、素材引用和完整异常信息。"


def build_seedance_error_report(exc, payload=None, context=None, task_detail=None):
    raw = str(exc)
    parsed = None
    http_status = None
    method = ""
    path = ""
    body = ""
    if isinstance(exc, ArkRequestError):
        parsed = exc.parsed
        http_status = exc.status_code
        method = exc.method
        path = exc.path
        body = exc.body
    else:
        parsed = try_parse_json_text(raw)

    fields = extract_ark_error_fields(parsed) if parsed is not None else {}
    human = explain_seedance_error(parsed if parsed is not None else raw)
    payload_summary = {
        "model": (payload or {}).get("model"),
        "ratio": (payload or {}).get("ratio"),
        "resolution": (payload or {}).get("resolution"),
        "duration": (payload or {}).get("duration"),
        "generate_audio": (payload or {}).get("generate_audio"),
        "watermark": (payload or {}).get("watermark"),
        "content": summarize_seedance_content(payload or {}),
    }
    if task_detail is None and hasattr(exc, "task_detail"):
        task_detail = getattr(exc, "task_detail")

    report = {
        "message_cn": human,
        "exception": raw,
        "http_status": http_status,
        "ark_method": method,
        "ark_path": path,
        "ark_error": fields,
        "context": context or {},
        "payload_summary": payload_summary,
        "payload": sanitize_for_log(payload or {}),
        "ark_raw_body": compact_text(body, 5000),
        "task_detail": sanitize_for_log(task_detail) if task_detail is not None else None,
    }
    lines = []
    lines.append("前端报错说明：" + human)
    lines.append("原始异常：" + raw)
    if http_status:
        lines.append(f"Ark HTTP 状态码：{http_status}")
    if method or path:
        lines.append(f"Ark 请求：{method} {path}")
    if fields:
        lines.append("Ark 错误字段：" + pretty_debug_json(fields))
    if context:
        lines.append("本次任务上下文：" + pretty_debug_json(context))
    lines.append("Seedance 请求体摘要：" + pretty_debug_json(payload_summary))
    lines.append("Seedance 完整请求体（已隐藏 API Key 与大体积 base64）：" + pretty_debug_json(payload or {}))
    if body:
        lines.append("Ark 原始响应体：" + compact_text(body, 5000))
    if task_detail is not None:
        lines.append("任务轮询详情：" + pretty_debug_json(task_detail))
    report["detail_text"] = "\n\n".join(lines)
    return report


def print_seedance_request_debug(context, payload):
    print_debug_block("Seedance 提交请求 - 上下文", context)
    print_debug_block("Seedance 提交请求 - 请求体摘要", {
        "model": (payload or {}).get("model"),
        "ratio": (payload or {}).get("ratio"),
        "resolution": (payload or {}).get("resolution"),
        "duration": (payload or {}).get("duration"),
        "generate_audio": (payload or {}).get("generate_audio"),
        "watermark": (payload or {}).get("watermark"),
        "content": summarize_seedance_content(payload or {}),
    })
    print_debug_block("Seedance 提交请求 - 完整请求体（已隐藏大体积 base64）", payload)


def print_seedance_error_debug(report):
    print_debug_block("Seedance 生成失败 - 结构化错误报告", report.get("detail_text") or report)
    print_debug_block("Seedance 生成失败 - Python traceback", traceback.format_exc())


def create_video_thumbnail(video_path: Path):
    try:
        if not video_path or not video_path.exists():
            return ""
        thumb = video_path.with_suffix(".jpg")
        if thumb.exists() and thumb.stat().st_size > 0:
            return thumb
        cmd = ["ffmpeg", "-y", "-ss", "00:00:00.600", "-i", str(video_path), "-frames:v", "1", "-q:v", "3", str(thumb)]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
        if thumb.exists() and thumb.stat().st_size > 0:
            return thumb
    except Exception:
        pass
    return ""


def public_thumb_url(thumb: Path):
    if not thumb:
        return ""
    try:
        thumb = thumb.resolve()
        if OUTPUT_VIDEO_DIR.resolve() in thumb.parents:
            return "/output/video/" + thumb.relative_to(OUTPUT_VIDEO_DIR).as_posix()
        if OUTPUT_IMAGE_DIR.resolve() in thumb.parents:
            return "/output/image/" + thumb.relative_to(OUTPUT_IMAGE_DIR).as_posix()
    except Exception:
        pass
    return ""


def download_video_to_project(url: str, out_dir: Path, resolution="", ratio=""):
    out_dir.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        content_type = resp.headers.get("Content-Type", "video/mp4")
        ext = ext_from_url_or_type(url, content_type)
        filename = next_image_filename(out_dir, ext)
        out = out_dir / filename
        with out.open("wb") as f:
            shutil.copyfileobj(resp, f)
    return out


# =========================================================================
# 火山 TOS（对象存储）支持：为 Seedance 2.0 参考视频提供公网可访问 URL。
#
# 设计原则（见开发文档）：
#   - 长期资产只存 asset["remote"] = {provider/bucket/key/sha256/size/...}；
#     预签名 URL 只是“临时门票”，每次调用前用 bucket+key 重新生成，绝不长期保存。
#   - 本地文件始终保留，TOS 对象丢失时可从本地重传。
#   - AK/SK 只从环境变量读取，绝不写入项目 JSON。
#   - tos SDK 懒加载：未启用 TOS（tos_enabled=false）或未安装 SDK 时，本模块
#     不会 import tos，现有图片/音频/普通视频流程完全不受影响。
# =========================================================================

def is_http_url(url: str) -> bool:
    u = str(url or "").strip().lower()
    return u.startswith("http://") or u.startswith("https://")


def is_data_url(url: str) -> bool:
    return str(url or "").strip().lower().startswith("data:")


def is_video_asset(asset: dict) -> bool:
    return str((asset or {}).get("category") or "") == "视频"


def is_audio_asset(asset: dict) -> bool:
    return str((asset or {}).get("category") or "") == "音频"


def get_tos_settings():
    """从 config.json 的 global_defaults + 环境变量读取 TOS 配置。AK/SK 只来自环境变量。"""
    cfg = load_config()
    g = cfg.get("global_defaults") or {}
    ak = os.environ.get("VOLC_TOS_ACCESS_KEY_ID") or os.environ.get("TOS_ACCESS_KEY_ID") or ""
    sk = os.environ.get("VOLC_TOS_SECRET_ACCESS_KEY") or os.environ.get("TOS_SECRET_ACCESS_KEY") or ""
    prefix = str(g.get("tos_prefix") or "dilan-workflow/video-module/").strip().strip("/")
    return {
        "enabled": bool(g.get("tos_enabled")),
        "bucket": str(g.get("tos_bucket") or "").strip(),
        "region": str(g.get("tos_region") or "cn-beijing").strip(),
        "endpoint": str(g.get("tos_endpoint") or "https://tos-cn-beijing.volces.com").strip(),
        "prefix": (prefix + "/") if prefix else "",
        "presign_expire_seconds": int(g.get("tos_presign_expire_seconds") or 86400),
        "refresh_margin_seconds": int(g.get("tos_presign_refresh_margin_seconds") or 600),
        "upload_generated_videos": bool(g.get("tos_upload_generated_videos", True)),
        "ak": ak,
        "sk": sk,
    }


_TOS_CLIENT_CACHE = {}
_TOS_CLIENT_GUARD = threading.Lock()


def get_tos_client():
    """返回缓存的 TosClientV2。未启用/缺配置/缺 SDK 时抛出可读中文错误。"""
    s = get_tos_settings()
    if not s["enabled"]:
        raise RuntimeError("TOS 未启用：请在视频模块 config.json 的 global_defaults 中设置 tos_enabled=true。")
    if not s["bucket"]:
        raise RuntimeError("TOS bucket 未配置，请在视频模块 config.json 中填写 tos_bucket。")
    if not s["ak"] or not s["sk"]:
        raise RuntimeError("TOS AK/SK 未配置，请设置环境变量 VOLC_TOS_ACCESS_KEY_ID 和 VOLC_TOS_SECRET_ACCESS_KEY。")
    cache_key = (s["ak"], s["sk"], s["endpoint"], s["region"])
    with _TOS_CLIENT_GUARD:
        client = _TOS_CLIENT_CACHE.get(cache_key)
        if client is not None:
            return client
        try:
            import tos
        except Exception as e:
            raise RuntimeError("缺少火山 TOS Python SDK，请先在视频模块安装依赖：pip install tos") from e
        try:
            client = tos.TosClientV2(s["ak"], s["sk"], s["endpoint"], s["region"])
        except Exception as e:
            raise RuntimeError(f"初始化 TOS 客户端失败：{e}") from e
        _TOS_CLIENT_CACHE[cache_key] = client
        return client


def build_tos_object_key(pid: str, asset: dict, fp: Path, file_sha256: str = "") -> str:
    """参考素材的对象 key：稳定、无中文、随 sha256 变化。"""
    s = get_tos_settings()
    suffix = (fp.suffix or "").lower() or ".mp4"
    asset_id = safe_name(asset.get("asset_id") or new_id("asset"))
    project_id = safe_name(pid or "unknown_project")
    digest = (file_sha256 or "")[:16] or "nohash"
    return f"{s['prefix']}projects/{project_id}/assets/{asset_id}_{digest}{suffix}"


def build_tos_generated_video_key(pid: str, video_id: str, fp: Path, file_sha256: str = "") -> str:
    """生成结果视频的对象 key。"""
    s = get_tos_settings()
    suffix = (fp.suffix or "").lower() or ".mp4"
    project_id = safe_name(pid or "unknown_project")
    vid = safe_name(video_id or new_id("vid"))
    digest = (file_sha256 or "")[:16] or "nohash"
    return f"{s['prefix']}projects/{project_id}/generated/{vid}_{digest}{suffix}"


def upload_file_to_tos(fp: Path, key: str, content_type: str = "") -> dict:
    """上传本地文件到 TOS，返回 remote 元数据。兼容不同 SDK 版本的上传方法。"""
    if not fp or not fp.exists() or not fp.is_file():
        raise RuntimeError(f"待上传文件不存在：{fp}")
    s = get_tos_settings()
    client = get_tos_client()
    bucket = s["bucket"]
    mime = content_type or mimetypes.guess_type(str(fp))[0] or "application/octet-stream"
    try:
        if hasattr(client, "put_object_from_file"):
            client.put_object_from_file(bucket, key, str(fp))
        else:
            with open(fp, "rb") as f:
                client.put_object(bucket, key, content=f.read())
    except Exception as e:
        raise RuntimeError(f"TOS 上传失败：{fp.name} -> {key}，原因：{e}") from e
    return {
        "provider": "tos",
        "bucket": bucket,
        "key": key,
        "region": s["region"],
        "endpoint": s["endpoint"],
        "content_type": mime,
        "size": fp.stat().st_size,
        "uploaded_at": now_str(),
        "status": "uploaded",
    }


def tos_object_exists(bucket: str, key: str) -> bool:
    """用 head_object 检查对象是否存在；SDK 无该方法时不强制检查（认为存在）。"""
    try:
        client = get_tos_client()
    except Exception:
        return False
    try:
        if hasattr(client, "head_object"):
            client.head_object(bucket, key)
            return True
        return True
    except Exception:
        return False


def generate_tos_presigned_get_url(bucket: str, key: str, expires: int = None) -> str:
    """生成 GET 预签名 URL。不同 tos SDK 版本的方法名/参数/返回值不同，这里做多重兜底。"""
    s = get_tos_settings()
    client = get_tos_client()
    expires = int(expires or s["presign_expire_seconds"])
    try:
        import tos
    except Exception as e:
        raise RuntimeError("缺少火山 TOS Python SDK，请先安装：pip install tos") from e
    # GET 方法的枚举/字面量在不同版本里可能不同，逐一兜底。
    method_get = "GET"
    http_enum = getattr(tos, "HttpMethodType", None)
    if http_enum is not None:
        method_get = (getattr(http_enum, "Http_Method_Get", None)
                      or getattr(http_enum, "HTTP_METHOD_GET", None)
                      or method_get)
    result = None
    last_exc = None
    if hasattr(client, "pre_signed_url"):
        for attempt in (
            lambda: client.pre_signed_url(method_get, bucket, key, expires),
            lambda: client.pre_signed_url(http_method=method_get, bucket=bucket, key=key, expires=expires),
            lambda: client.pre_signed_url(method=method_get, bucket=bucket, key=key, expires=expires),
        ):
            try:
                result = attempt()
                break
            except TypeError as te:
                last_exc = te
                continue
            except Exception as e:
                raise RuntimeError(f"TOS 预签名 URL 生成失败：{key}，原因：{e}") from e
    if result is None:
        raise RuntimeError(
            "当前 TOS SDK 未找到匹配的 pre_signed_url 调用方式，"
            f"请确认 tos SDK 版本（最后一次参数异常：{last_exc}）。"
        )
    if isinstance(result, str):
        return result
    for attr in ("signed_url", "presigned_url", "url"):
        v = getattr(result, attr, None)
        if isinstance(v, str) and v:
            return v
    if isinstance(result, dict):
        for k in ("signed_url", "presigned_url", "url"):
            if isinstance(result.get(k), str) and result[k]:
                return result[k]
    raise RuntimeError(f"TOS 预签名 URL 返回值无法解析为字符串：{type(result)}")


def ensure_asset_remote_url(pid: str, asset: dict, require_video: bool = False) -> str:
    """返回可直接给 Seedance 使用的公网 http(s) URL（绝不返回 data: 或本地路径）。

    需要时把本地文件上传到 TOS，并就地更新 asset["remote"]；
    调用方在之后必须 save_project(pid, data) 才能持久化新写入的 remote 元数据。
    """
    if not asset:
        raise RuntimeError("空素材，无法生成远程 URL。")
    name = asset.get("name") or asset.get("asset_id") or "未命名素材"
    # 1. 素材本身已是公网 URL（例如手动填写的 URL 素材）：直接用。
    for field in ("remote_url", "url", "file_path"):
        v = str(asset.get(field) or "").strip()
        if is_http_url(v):
            return v
    # 2. 已有 TOS remote 元数据：用 bucket+key 重新生成预签名 URL；对象不在则尝试本地补传。
    remote = asset.get("remote") if isinstance(asset.get("remote"), dict) else {}
    bucket = remote.get("bucket")
    key = remote.get("key")
    if remote.get("provider") == "tos" and bucket and key:
        if not tos_object_exists(bucket, key):
            fp = public_to_local(asset.get("file_path"))
            if fp and fp.exists() and fp.is_file():
                file_sha = sha256_file(fp)
                meta = upload_file_to_tos(fp, key, mimetypes.guess_type(str(fp))[0] or "")
                meta["sha256"] = file_sha
                remote.update(meta)
                asset["remote"] = remote
            else:
                raise RuntimeError(f"参考视频素材“{name}”已丢失：本地文件不存在，TOS 对象也不存在。请重新上传该素材。")
        return generate_tos_presigned_get_url(bucket, key)
    # 3. 尚未上传过：把本地文件上传到 TOS。
    fp = public_to_local(asset.get("file_path"))
    if not fp or not fp.exists() or not fp.is_file():
        raise RuntimeError(f"参考素材“{name}”没有可用的公网 URL，且本地文件不存在。请重新上传该素材，或为其填写公网 URL。")
    if require_video:
        if not get_tos_settings()["enabled"]:
            raise RuntimeError("当前引用了本地视频素材，但 Seedance 2.0 参考视频必须使用公网 URL。"
                               "请在视频模块 config.json 启用 TOS（tos_enabled=true）并配置 tos_bucket，或为该视频素材填写公网 URL。")
        mime = mimetypes.guess_type(str(fp))[0] or ""
        if mime and not mime.startswith("video/"):
            raise RuntimeError(f"素材“{name}”不是视频文件，不能作为 Seedance 参考视频。")
    file_sha = sha256_file(fp)
    key = build_tos_object_key(pid, asset, fp, file_sha)
    meta = upload_file_to_tos(fp, key, mimetypes.guess_type(str(fp))[0] or "")
    meta["sha256"] = file_sha
    asset["remote"] = meta
    return generate_tos_presigned_get_url(meta["bucket"], meta["key"])


def ensure_local_file_remote_url(fp: Path) -> str:
    """内容寻址的 TOS 缓存：本地文件按 sha256 生成固定 key，同一内容只上传一次，
    之后每次调用只重新生成预签名 URL。用于图片/音频等参考文件——把 Ark 请求体里的
    大体积 base64 全部替换成云端 URL（几十 MB 的 base64 请求体会被 Ark 网关直接
    断开连接：WinError 10054 远程主机强迫关闭了一个现有的连接）。"""
    fp = Path(fp)
    if not fp.exists() or not fp.is_file():
        raise RuntimeError(f"参考文件不存在：{fp}")
    s = get_tos_settings()
    sha = sha256_file(fp)
    suffix = (fp.suffix or "").lower()
    key = f"{s['prefix']}cache/{sha}{suffix}"
    if not tos_object_exists(s["bucket"], key):
        upload_file_to_tos(fp, key, mimetypes.guess_type(str(fp))[0] or "")
    return generate_tos_presigned_get_url(s["bucket"], key)


def seedance_content_from_prompt(prompt: str, data: dict, tab: dict, pid: str = ""):
    content = [{"type": "text", "text": prompt or ""}]
    # V24：@素材 候选池改为全项目素材库（不再限于当前标签页引用素材），与美术/分镜一致。
    allowed_assets = [a for a in (data.get("assets") or []) if a and not a.get("temporary") and a.get("name")]
    aliases = referenced_aliases(prompt, data, allowed_assets)
    assets_by_name = {str(a.get("name", "")).lower(): a for a in allowed_assets}
    video_count = 0
    for alias in aliases:
        asset = assets_by_name.get(str(alias).lower())
        if not asset:
            raise ValueError(f"@{alias} 不是有效的参考素材名，请检查素材名或先在参考素材中上传该素材")
        if asset.get("asset_id") not in tab.setdefault("referenced_assets", []):
            tab["referenced_assets"].append(asset.get("asset_id"))
        fp = public_to_local(asset.get("file_path"))
        url = asset.get("file_path") or ""
        cat = asset.get("category") or ""
        if cat == "视频":
            # 视频参考素材：Seedance 2.0 只收公网 URL，自动上传 TOS 后用预签名 URL。
            video_count += 1
            if video_count > 3:
                raise ValueError("Seedance 参考视频最多 3 个，请减少 prompt 中 @ 引用的视频素材数量。")
            url = ensure_asset_remote_url(pid, asset, require_video=True)
            if not is_http_url(url):
                raise ValueError(f"参考视频“{asset.get('name')}”未能生成公网 URL，请检查 TOS 配置。")
            content.append({"type": "video_url", "video_url": {"url": url}, "role": "reference_video"})
        elif cat == "音频":
            # 音频参考：启用 TOS 时上传后走预签名 URL；大 base64 会把 Ark 请求体撑爆（10054 被断开）。
            if fp and fp.exists() and not is_http_url(url):
                url = ensure_local_file_remote_url(fp) if get_tos_settings()["enabled"] else local_to_data_url(fp)
            content.append({"type": "audio_url", "audio_url": {"url": url}, "role": "reference_audio"})
        else:
            # 图片参考：同样优先 TOS 预签名 URL；未启用 TOS 才回退内嵌 base64。
            if fp and fp.exists() and not is_http_url(url):
                url = ensure_local_file_remote_url(fp) if get_tos_settings()["enabled"] else local_to_data_url(fp)
            content.append({"type": "image_url", "image_url": {"url": url}, "role": "reference_image"})
    return content


def sanitize_payload_for_record(payload):
    """生成记录里不保存任何内嵌的 base64 数据（输入图片/音视频已有本地文件引用）。
    只保留轻量元信息（model/ratio/duration 等），把 content 里的 data URL 剥成占位符，
    避免 project.json 随每次生成被 base64 撑大几 MB 导致页面越用越卡。"""
    if not isinstance(payload, dict):
        return payload
    slim = {}
    for k, v in payload.items():
        if k == "content" and isinstance(v, list):
            new_content = []
            for item in v:
                if not isinstance(item, dict):
                    new_content.append(item)
                    continue
                it = dict(item)
                for url_key in ("image_url", "audio_url", "video_url"):
                    holder = it.get(url_key)
                    if isinstance(holder, dict) and isinstance(holder.get("url"), str):
                        u = holder["url"]
                        if u.startswith("data:"):
                            h = dict(holder)
                            h["url"] = "[内嵌素材已省略]"
                            it[url_key] = h
                        elif u.startswith("http") and "?" in u and "x-tos-" in u.lower():
                            # TOS 预签名 URL：记录里只留对象路径，去掉会过期且含签名的查询参数。
                            h = dict(holder)
                            h["url"] = u.split("?", 1)[0] + "?[预签名参数已省略]"
                            it[url_key] = h
                new_content.append(it)
            slim[k] = new_content
        elif isinstance(v, str) and v.startswith("data:"):
            slim[k] = "[内嵌素材已省略]"
        else:
            slim[k] = v
    return slim


def seedance_payload(prompt: str, settings: dict, data: dict, tab: dict, pid: str = ""):
    duration = parse_positive_int(settings.get("video_duration"), 8) or 8
    model = normalize_video_model(settings.get("video_model") or DEFAULT_VIDEO_MODEL)
    if model == FAST_VIDEO_MODEL and str(settings.get("video_resolution") or "").lower() == "1080p":
        settings = dict(settings)
        settings["video_resolution"] = "720p"
    payload = {
        "model": model,
        "content": seedance_content_from_prompt(prompt, data, tab, pid),
        "ratio": settings.get("video_ratio") or "16:9",
        "resolution": settings.get("video_resolution") or "720p",
        "duration": duration,
        "generate_audio": bool(settings.get("video_generate_audio", True)),
        "watermark": bool(settings.get("video_watermark", False)),
    }
    seed = str(settings.get("video_seed") or "").strip()
    if seed and re.fullmatch(r"\d+", seed):
        payload["seed"] = int(seed)
    if model == V25_VIDEO_MODEL:
        # seedance2.5 严格按官方示例只发 model/content/ratio/duration/generate_audio/watermark：
        # 官方示例不含 resolution 与 seed，多发未列出的字段有被 Ark 拒绝的风险（用户确认的取舍）。
        # 分辨率下拉对 2.5 仅参与本地费用预估，不进请求体。
        payload.pop("resolution", None)
        payload.pop("seed", None)
    return payload


def submit_and_wait_seedance(api_key: str, payload: dict, max_polls=120, interval=10, on_created=None):
    created = ark_json_request("POST", "/contents/generations/tasks", api_key, payload, timeout=180)
    task_id = created.get("id") or created.get("task_id") or created.get("taskId")
    if not task_id:
        raise RuntimeError("Ark did not return a task id: " + json.dumps(created, ensure_ascii=False)[:500])
    if callable(on_created):
        try:
            on_created(task_id, created)
        except Exception as cb_exc:
            print("Seedance running-status callback failed:", cb_exc, flush=True)
    last = None
    for n in range(max_polls):
        time.sleep(interval if n else 2)
        detail = ark_json_request("GET", f"/contents/generations/tasks/{task_id}", api_key, None, timeout=60)
        last = detail
        status = str(detail.get("status") or detail.get("state") or "").lower()
        if status in ("succeeded", "completed", "success"):
            vu = deep_video_url(detail)
            if not vu:
                raise RuntimeError("任务成功但未解析到视频 URL: " + json.dumps(detail, ensure_ascii=False)[:800])
            return task_id, vu, detail
        if status in ("failed", "error", "cancelled", "canceled"):
            err = detail.get("error") or detail.get("message") or detail
            raise SeedanceTaskError("Seedance 任务失败: " + json.dumps(err, ensure_ascii=False)[:1200], task_id=task_id, task_detail=detail)
    raise SeedanceTaskError("等待 Seedance 任务超时: " + task_id + " last=" + json.dumps(last, ensure_ascii=False)[:800], task_id=task_id, task_detail=last)

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}")

    def _headers(self, content_type="application/json; charset=utf-8"):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        if content_type:
            self.send_header("Content-Type", content_type)

    def send_json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self._headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, fp: Path, mime=None):
        if not fp.exists() or not fp.is_file():
            self.send_json(404, {"error": "file not found"})
            return
        mime_type = mime or mimetypes.guess_type(str(fp))[0] or "application/octet-stream"
        size = fp.stat().st_size
        range_header = self.headers.get("Range")
        if range_header and mime_type.startswith("video/"):
            m = re.match(r"bytes=(\d*)-(\d*)", range_header)
            if m:
                start = int(m.group(1) or 0)
                end = int(m.group(2) or size - 1)
                end = min(end, size - 1)
                if start <= end:
                    self.send_response(206)
                    self._headers(mime_type)
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                    self.send_header("Content-Length", str(end - start + 1))
                    self.end_headers()
                    with fp.open("rb") as f:
                        f.seek(start)
                        remaining = end - start + 1
                        while remaining > 0:
                            chunk = f.read(min(1024 * 256, remaining))
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            remaining -= len(chunk)
                    return
        data = fp.read_bytes()
        self.send_response(200)
        self._headers(mime_type)
        if mime_type.startswith("video/"):
            self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_download(self, fp: Path, filename=None, mime="application/zip"):
        if not fp.exists() or not fp.is_file():
            self.send_json(404, {"error": "file not found"})
            return
        data = fp.read_bytes()
        filename = safe_file_name(filename or fp.name, "download.zip")
        if not filename.lower().endswith(".zip"):
            filename += ".zip"
        self.send_response(200)
        self._headers(mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{urllib.request.pathname2url(filename)}")
        self.end_headers()
        self.wfile.write(data)

    def read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception as e:
            raise ValueError(f"invalid json body: {e}")

    def do_OPTIONS(self):
        self.send_response(204)
        self._headers(None)
        self.end_headers()

    def begin_project_mutation(self, pid):
        # 获取该工程的互斥锁，并在锁内“重新”读取一份最新工程数据返回。与随后的
        # save_project(pid, data) 配对，使整个“读取->修改->保存”串行化，从而不会把并发
        # 生成刚写回磁盘的视频/图片覆盖吞掉（例如一边在生成视频、一边点“新增子栏”）。
        # 锁在 do_POST 的 finally 里统一释放，任何异常都不会导致锁泄漏。
        lk = project_mutation_lock(pid)
        lk.acquire()
        if not hasattr(self, "_held_locks"):
            self._held_locks = []
        self._held_locks.append(lk)
        return load_project(pid)

    def _release_held_locks(self):
        # 释放本次请求 begin_project_mutation 期间持有的所有工程锁。
        locks = getattr(self, "_held_locks", None)
        if not locks:
            return
        while locks:
            lk = locks.pop()
            try:
                lk.release()
            except Exception:
                pass

    def do_GET(self):
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        qs = parse_qs(parsed.query)
        try:
            if path in ("/", "/index.html"):
                self.send_file(ROOT / "index.html", "text/html; charset=utf-8")
            elif path == "/api/config":
                self.send_json(200, load_config())
            elif path == "/api/parents":
                self.api_parents()
            elif path == "/api/projects":
                self.api_projects(qs.get("parent", [""])[0])
            elif path == "/api/project/load":
                self.api_project_load(qs.get("project", [""])[0])
            elif path == "/api/project/export":
                self.api_project_export(qs.get("project", [""])[0], qs.get("user_name", [""])[0])
            elif path == "/api/assets/package/export":
                self.api_asset_package_export(qs.get("project", [""])[0], qs.get("user_name", [""])[0])
            elif path == "/api/assets/list":
                self.api_assets_list(qs.get("project", [""])[0])
            elif path == "/api/usage":
                self.send_json(200, {"usage": get_user_usage(qs.get("user", [""])[0])})
            elif path.startswith("/input/"):
                rel = path[len("/input/"):]
                fp = (INPUT_DIR / rel).resolve()
                if INPUT_DIR.resolve() not in fp.parents and fp != INPUT_DIR.resolve():
                    self.send_json(403, {"error": "invalid path"})
                else:
                    self.send_file(fp)
            elif path.startswith("/output/image/"):
                rel = path[len("/output/image/"):]
                fp = (OUTPUT_IMAGE_DIR / rel).resolve()
                if OUTPUT_IMAGE_DIR.resolve() not in fp.parents and fp != OUTPUT_IMAGE_DIR.resolve():
                    self.send_json(403, {"error": "invalid path"})
                else:
                    self.send_file(fp)
            elif path.startswith("/output/video/"):
                rel = path[len("/output/video/"):]
                fp = (OUTPUT_VIDEO_DIR / rel).resolve()
                if OUTPUT_VIDEO_DIR.resolve() not in fp.parents and fp != OUTPUT_VIDEO_DIR.resolve():
                    self.send_json(403, {"error": "invalid path"})
                else:
                    self.send_file(fp)
            elif path.startswith("/temp/"):
                rel = path[len("/temp/"):]
                fp = (PROJECTS_DIR / rel).resolve()
                if PROJECTS_DIR.resolve() not in fp.parents and fp != PROJECTS_DIR.resolve():
                    self.send_json(403, {"error": "invalid path"})
                else:
                    self.send_file(fp)
            else:
                self.send_json(404, {"error": "not found"})
        except Exception as e:
            print("GET ERROR", e)
            self.send_json(500, {"error": str(e)})

    def do_POST(self):
        parsed = urlsplit(self.path)
        path = parsed.path
        self._held_locks = []
        try:
            if path == "/api/config/save":
                self.send_json(200, save_config(self.read_body()))
            elif path == "/api/parents/create":
                self.api_parent_create()
            elif path == "/api/parent/delete":
                self.api_parent_delete()
            elif path == "/api/parent/rename":
                self.api_parent_rename()
            elif path == "/api/projects/create":
                self.api_project_create()
            elif path == "/api/project/delete":
                self.api_project_delete()
            elif path == "/api/project/rename":
                self.api_project_rename()
            elif path == "/api/project/save":
                self.api_project_save()
            elif path == "/api/project/import":
                self.api_project_import()
            elif path == "/api/project/import/stream":
                self.api_project_import_stream()
            elif path == "/api/project/merge":
                self.api_project_merge()
            elif path == "/api/assets/package/import":
                self.api_asset_package_import()
            elif path == "/api/assets/package/import/stream":
                self.api_asset_package_import_stream()
            elif path == "/api/user/set_name":
                self.api_user_set_name()
            elif path == "/api/assets/upload":
                self.api_asset_upload()
            elif path == "/api/assets/group/create":
                self.api_asset_group_create()
            elif path == "/api/assets/group/rename":
                self.api_asset_group_rename()
            elif path == "/api/assets/group/delete":
                self.api_asset_group_delete()
            elif path == "/api/assets/group/reorder":
                self.api_asset_group_reorder()
            elif path == "/api/assets/delete":
                self.api_asset_delete()
            elif path == "/api/assets/rename":
                self.api_asset_rename()
            elif path == "/api/assets/move":
                self.api_asset_move()
            elif path == "/api/assets/bulk_delete":
                self.api_assets_bulk_delete()
            elif path == "/api/asset/temp_upload":
                self.api_temp_upload()
            elif path == "/api/scene/add":
                self.api_scene_add()
            elif path == "/api/scene/insert":
                self.api_scene_insert()
            elif path == "/api/scene/delete":
                self.api_scene_delete()
            elif path == "/api/scene/renumber":
                self.api_scene_renumber()
            elif path == "/api/shot/add":
                self.api_shot_add()
            elif path == "/api/shot/insert":
                self.api_shot_insert()
            elif path == "/api/shot/delete":
                self.api_shot_delete()
            elif path == "/api/shot/reorder_drag":
                self.api_shot_reorder()
            elif path == "/api/submit":
                self.api_submit()
            elif path == "/api/chat/message":
                self.api_chat_message()
            elif path == "/api/video/generate":
                self.api_video_generate()
            elif path == "/api/video/delete":
                self.api_image_delete()
            elif path == "/api/image/generate":
                self.api_video_generate()
            elif path == "/api/image/chat":
                self.api_video_generate()
            elif path == "/api/image/delete":
                self.api_image_delete()
            elif path == "/api/storage/delete_videos":
                self.api_storage_delete_images()
            elif path == "/api/storage/download_videos":
                self.api_storage_download_images()
            elif path == "/api/storage/rename_video":
                self.api_storage_rename_image()
            elif path == "/api/storage/delete_images":
                self.api_storage_delete_images()
            elif path == "/api/storage/download_images":
                self.api_storage_download_images()
            elif path == "/api/storage/rename_image":
                self.api_storage_rename_image()
            elif path == "/api/storyboard/add_candidate":
                self.api_storyboard_add()
            elif path == "/api/storyboard/upload_candidate":
                self.api_storyboard_upload()
            elif path == "/api/storyboard/remove_candidate":
                self.api_storyboard_remove()
            else:
                self.send_json(404, {"error": "not found"})
        except Exception as e:
            print("POST ERROR", e)
            self.send_json(500, {"error": str(e)})
        finally:
            self._release_held_locks()

    def api_snapshots_list(self, pid):
        pid = safe_name(pid or "")
        if not pid:
            self.send_json(400, {"error": "missing project"}); return
        self.send_json(200, {"logs": read_operation_logs(pid, 1000), "snapshots": list_project_snapshots(pid)})

    def api_parents(self):
        # V32: parent projects are shared by 美术 / 分镜 / 视频.
        # The current module only controls which child list appears on the right.
        items = collect_shared_parent_items()
        self.send_json(200, {"parents": items, "root": str(ROOT), "module_type": PROJECT_MODULE_TYPE})

    def api_parent_create(self):
        body = self.read_body()
        name = safe_name(body.get("name") or "未命名父项目", "父级项目")
        pid = name
        i = 2
        while shared_parent_exists(pid):
            pid = f"{name}_{i}"
            i += 1
        ensure_parent_across_modules(pid, name)
        parent = next((p for p in collect_shared_parent_items() if p.get("parent_id") == pid), parent_meta(pid))
        self.send_json(200, {"parent": parent})

    def api_parent_delete(self):
        body = self.read_body()
        parent_id = safe_name(body.get("parent") or "", "")
        if not parent_id:
            self.send_json(400, {"error": "missing parent"}); return
        delete_parent_across_modules(parent_id)
        self.send_json(200, {"ok": True})

    def api_parent_rename(self):
        body = self.read_body()
        parent_id = safe_name(body.get("parent") or "", "")
        new_name = safe_name(body.get("new_name") or body.get("name") or "", "")
        if not parent_id:
            self.send_json(400, {"error": "missing parent"}); return
        if not new_name:
            self.send_json(400, {"error": "请输入新的父项目名称"}); return
        rename_parent_across_modules(parent_id, new_name)
        parent = next((p for p in collect_shared_parent_items() if p.get("parent_id") == parent_id), parent_meta(parent_id))
        self.send_json(200, {"ok": True, "parent": parent})

    def api_projects(self, parent_id=""):
        items = []
        if parent_id:
            parent_id = safe_name(parent_id, "父级项目")
            for d in sorted(list_child_project_dirs(parent_id), key=lambda p: p.stat().st_mtime, reverse=True):
                items.append(project_meta(make_project_ref(parent_id, d.name)))
        else:
            # legacy fallback: old single-level projects
            for d in sorted(PROJECTS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
                if d.is_dir() and (d / "project.json").exists():
                    items.append(project_meta(d.name))
        self.send_json(200, {"projects": items, "root": str(ROOT)})

    def api_project_merge(self):
        body = self.read_body()
        pids = body.get("projects") or body.get("source_pids") or []
        if isinstance(pids, str):
            pids = [pids]
        pids = [safe_name(p) for p in pids if p]
        target_name = body.get("name") or body.get("target_name") or ""
        importer = body.get("user_name") or "未命名用户"
        try:
            result = merge_projects_local(pids, target_name=target_name, importer_name=importer)
            self.send_json(200, {"ok": True, **result})
        except Exception as e:
            self.send_json(400, {"error": str(e)})

    def api_project_create(self):
        body = self.read_body()
        parent_id = safe_name(body.get("parent") or "", "")
        name = safe_name(body.get("name") or "未命名子项目", "子项目")
        if parent_id:
            if not parent_json_path(parent_id).exists():
                ensure_parent_across_modules(parent_id, parent_id)
            child_id = name
            i = 2
            while project_path(make_project_ref(parent_id, child_id)).exists():
                child_id = f"{name}_{i}"
                i += 1
            data = create_project_data(child_id, parent_id, body.get("scene_start"), body.get("scene_end"))
            ensure_project_dirs(data["project_id"])
            write_json_file(project_json_path(data["project_id"]), data)
            append_operation_log(data["project_id"], "create_child_project", user_name="系统", parent_id=parent_id, child_id=child_id)
            create_project_snapshot(data["project_id"], "project_created", data=data, user_name="系统")
            self.send_json(200, {"project": project_meta(data["project_id"]), "data": data})
        else:
            pid = name
            i = 2
            while project_path(pid).exists():
                pid = f"{name}_{i}"
                i += 1
            data = create_project_data(pid, None, body.get("scene_start"), body.get("scene_end"))
            write_json_file(project_json_path(pid), data)
            self.send_json(200, {"project": project_meta(pid), "data": data})

    def api_project_rename(self):
        body = self.read_body()
        pid = safe_name(body.get("project") or "", "")
        new_name = safe_name(body.get("new_name") or body.get("name") or "", "")
        if not pid:
            self.send_json(400, {"error": "missing project"}); return
        if not new_name:
            self.send_json(400, {"error": "请输入新的子项目名称"}); return
        data = self.begin_project_mutation(pid)
        data["project_name"] = new_name
        data["updated_at"] = now_str()
        saved = save_project(pid, data)
        append_operation_log(saved["project_id"], "rename_project_display_name", user_name="系统", new_name=new_name)
        self.send_json(200, {"ok": True, "data": saved, "project": project_meta(saved["project_id"])})

    def api_project_load(self, pid):
        pid = safe_name(pid)
        data = load_project(pid)
        self.send_json(200, {"project": project_meta(pid), "data": data})

    def api_project_export(self, pid, user_name=""):
        pid = safe_name(pid)
        if not pid:
            self.send_json(400, {"error": "missing project"}); return
        zip_path, manifest = create_project_export_zip(pid, user_name)
        self.send_download(zip_path, zip_path.name, "application/zip")

    def _stream_body_to_file(self, dest, chunk_size=1024 * 1024):
        """按 Content-Length 分块把上传体流式写入磁盘临时文件，全程只占用一个块的内存。"""
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0:
            raise ValueError("空的上传内容")
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        remaining = length
        with open(dest, "wb") as fh:
            while remaining > 0:
                data = self.rfile.read(min(chunk_size, remaining))
                if not data:
                    break
                fh.write(data)
                remaining -= len(data)
        if remaining > 0:
            raise ValueError("上传中断：接收到的数据不完整，请重试")

    def api_project_import_stream(self):
        qs = parse_qs(urlsplit(self.path).query)
        filename = (qs.get("filename") or [""])[0]
        user_name = (qs.get("user_name") or [""])[0]
        target_parent = (qs.get("target_parent") or qs.get("parent") or [""])[0]
        IMPORTS_DIR.mkdir(parents=True, exist_ok=True)
        tmp = IMPORTS_DIR / f"_import_{uuid.uuid4().hex}.zip"
        try:
            self._stream_body_to_file(tmp)
            result = import_project_package_from_zip_path(tmp, filename, user_name, target_parent)
            self.send_json(200, {"ok": True, **result})
        finally:
            try:
                tmp.unlink()
            except Exception:
                pass

    def api_asset_package_import_stream(self):
        qs = parse_qs(urlsplit(self.path).query)
        project = (qs.get("project") or [""])[0]
        filename = (qs.get("filename") or [""])[0]
        user_name = (qs.get("user_name") or [""])[0]
        IMPORTS_DIR.mkdir(parents=True, exist_ok=True)
        tmp = IMPORTS_DIR / f"_asset_import_{uuid.uuid4().hex}.zip"
        try:
            self._stream_body_to_file(tmp)
            result = import_asset_package_from_zip_path(project, tmp, filename, user_name)
            self.send_json(200, {"ok": True, **result})
        finally:
            try:
                tmp.unlink()
            except Exception:
                pass

    def api_project_import(self):
        body = self.read_body()
        result = import_project_package(body.get("dataUrl") or "", body.get("filename") or "", body.get("user_name") or "", body.get("target_parent") or body.get("parent") or "")
        self.send_json(200, {"ok": True, **result})

    def api_asset_package_export(self, pid, user_name=""):
        pid = safe_name(pid)
        if not pid:
            self.send_json(400, {"error": "missing project"}); return
        zip_path, manifest = create_asset_package_zip(pid, user_name)
        self.send_download(zip_path, zip_path.name, "application/zip")

    def api_asset_package_import(self):
        body = self.read_body()
        result = import_asset_package_into_project(body.get("project") or "", body.get("dataUrl") or "", body.get("filename") or "", body.get("user_name") or "")
        self.send_json(200, {"ok": True, **result})

    def api_project_save(self):
        body = self.read_body()
        pid = safe_name(body.get("project") or body.get("project_id") or "project")
        data = body.get("data") or body
        data.pop("apiKey", None)
        _lock = project_mutation_lock(pid)
        _lock.acquire()
        try:
            # 前端保存的是整份工程快照；若此刻后台并发生成刚把新视频/新图片写回磁盘，而
            # 客户端内存里还没有这条结果，直接整份覆盖就会把它吞掉。这里在锁内重新读取磁盘
            # 上的生成结果做并集补齐（只增不减，删除仍走专门接口），避免生成结果丢失。
            try:
                on_disk = load_project(pid)
            except Exception:
                on_disk = None
            if on_disk is not None:
                merge_generated_media_preserve(data, on_disk)
            saved = save_project(pid, data)
            append_operation_log(saved["project_id"], "project_save", user_name="系统", stats=project_stats(saved))
        finally:
            _lock.release()
        self.send_json(200, {"ok": True, "data": saved, "project": project_meta(saved["project_id"])})

    def api_project_delete(self):
        body = self.read_body()
        pid = safe_name(body.get("project") or "")
        if not pid:
            self.send_json(400, {"error": "missing project"}); return
        for p in (project_path(pid), INPUT_DIR / safe_name(pid), OUTPUT_IMAGE_DIR / safe_name(pid), OUTPUT_VIDEO_DIR / safe_name(pid)):
            if p.exists():
                shutil.rmtree(p)
        self.send_json(200, {"ok": True})

    def api_user_set_name(self):
        body = self.read_body()
        name = normalize_user_name(body.get("user_name") or body.get("name") or "")
        data = read_json_file(USERS_PATH, {"users": {}}) or {"users": {}}
        data.setdefault("users", {})[name] = {"user_name": name, "updated_at": now_str()}
        write_json_file(USERS_PATH, data)
        self.send_json(200, {"ok": True, "user": {"user_name": name}, "usage": get_user_usage(name)})

    def api_assets_list(self, pid):
        data = load_project(safe_name(pid))
        self.send_json(200, {"assets": data.get("assets", []), "asset_groups": data.get("asset_groups", [])})


    def api_asset_group_create(self):
        body = self.read_body()
        pid = safe_name(body.get("project") or "")
        cat = body.get("category") or "人物"
        if cat not in GROUPED_CATEGORIES:
            self.send_json(400, {"error": "该分类不支持素材组"}); return
        name = normalize_group_name(body.get("name") or "", cat + "组")
        data = self.begin_project_mutation(pid)
        ensure_asset_groups(data)
        if any(g.get("category") == cat and g.get("name", "").lower() == name.lower() for g in data.get("asset_groups", [])):
            self.send_json(400, {"error": "同一分类下已存在同名素材组"}); return
        orders = [int(g.get("sort_order") or 0) for g in data.get("asset_groups", []) if g.get("category") == cat]
        group = {"group_id": new_id("grp"), "category": cat, "name": name, "sort_order": (max(orders) + 1) if orders else 1, "expanded": True, "created_at": now_str()}
        data.setdefault("asset_groups", []).append(group)
        save_project(pid, data)
        self.send_json(200, {"ok": True, "group": group, "data": data})

    def api_asset_group_rename(self):
        body = self.read_body()
        pid = safe_name(body.get("project") or "")
        gid = body.get("group_id") or ""
        name = normalize_group_name(body.get("new_name") or "", "素材组")
        data = self.begin_project_mutation(pid)
        g = get_asset_group_by_id(data, gid)
        if not g:
            self.send_json(404, {"error": "素材组不存在"}); return
        cat = g.get("category")
        if any(x.get("group_id") != gid and x.get("category") == cat and x.get("name", "").lower() == name.lower() for x in data.get("asset_groups", [])):
            self.send_json(400, {"error": "同一分类下已存在同名素材组"}); return
        g["name"] = name
        save_project(pid, data)
        self.send_json(200, {"ok": True, "group": g, "data": data})

    def api_asset_group_delete(self):
        body = self.read_body()
        pid = safe_name(body.get("project") or "")
        gid = body.get("group_id") or ""
        data = self.begin_project_mutation(pid)
        g = get_asset_group_by_id(data, gid)
        if not g:
            self.send_json(404, {"error": "素材组不存在"}); return
        ids = [a.get("asset_id") for a in data.get("assets", []) if a.get("group_id") == gid]
        remove_assets_by_ids(data, ids)
        data["asset_groups"] = [x for x in data.get("asset_groups", []) if x.get("group_id") != gid]
        ensure_asset_groups(data)
        save_project(pid, data)
        self.send_json(200, {"ok": True, "deleted_assets": len(ids), "data": data})

    def api_asset_group_reorder(self):
        body = self.read_body()
        pid = safe_name(body.get("project") or "")
        cat = body.get("category") or "人物"
        order = body.get("group_order") or []
        data = self.begin_project_mutation(pid)
        ensure_asset_groups(data)
        rank = {gid: i + 1 for i, gid in enumerate(order)}
        for g in data.get("asset_groups", []):
            if g.get("category") == cat and g.get("group_id") in rank:
                g["sort_order"] = rank[g.get("group_id")]
        ensure_asset_groups(data)
        save_project(pid, data)
        self.send_json(200, {"ok": True, "data": data})

    def api_asset_upload(self):
        body = self.read_body()
        pid = safe_name(body.get("project") or "")
        cat = body.get("category") or "人物"
        group_id = body.get("group_id") or ""
        if cat not in CATEGORIES:
            self.send_json(400, {"error": "invalid category"}); return
        data = self.begin_project_mutation(pid)
        group = None
        raw, ext = parse_data_url(body.get("dataUrl") or "")
        requested_name = body.get("name") or Path(body.get("filename") or "素材").stem
        out_dir = INPUT_DIR / safe_name(pid) / cat
        out_dir.mkdir(parents=True, exist_ok=True)
        existing_names = {a.get("name") for a in data.get("assets", []) if not a.get("temporary")}
        name = unique_name_with_paren(existing_names, requested_name)
        out = out_dir / f"{name}{ext}"
        while out.exists():
            name = unique_name_with_paren(existing_names | {name}, requested_name)
            out = out_dir / f"{name}{ext}"
        out.write_bytes(raw)
        rel = out.relative_to(INPUT_DIR).as_posix()
        asset = {"asset_id": new_id("asset"), "name": out.stem, "category": cat, "file_path": "/input/" + rel, "created_at": now_str()}
        if group:
            asset["group_id"] = group.get("group_id")
        data.setdefault("assets", []).append(asset)
        save_project(pid, data)
        append_operation_log(pid, "asset_upload", category=cat, asset_id=asset.get("asset_id"), asset_name=asset.get("name"))
        self.send_json(200, {"ok": True, "asset": asset, "data": data})

    def api_asset_delete(self):
        body = self.read_body()
        pid = safe_name(body.get("project") or "")
        asset_id = body.get("asset_id") or ""
        data = self.begin_project_mutation(pid)
        remove_assets_by_ids(data, [asset_id])
        save_project(pid, data)
        append_operation_log(pid, "project_operation", stats=project_stats(data))
        self.send_json(200, {"ok": True, "data": data})

    def api_asset_rename(self):
        body = self.read_body()
        pid = safe_name(body.get("project") or "")
        asset_id = body.get("asset_id") or ""
        new_name = safe_file_name(body.get("new_name") or "", "素材")
        data = self.begin_project_mutation(pid)
        target = get_asset_by_id(data, asset_id)
        old_asset_name = target.get("name") if target else ""
        if not target:
            self.send_json(404, {"error": "asset not found"}); return
        if asset_name_exists(data, new_name, asset_id):
            self.send_json(400, {"error": "素材名已存在，素材名需要全局唯一"}); return
        fp = public_to_local(target.get("file_path"))
        rollback_pair = None  # (新文件, 旧文件)：磁盘已改名但落库失败时回滚，避免图片丢失
        if fp and fp.exists():
            final_name = new_name
            new_fp = fp.with_name(final_name + fp.suffix)
            if new_fp != fp:
                safe_replace_file(fp, new_fp)
                rollback_pair = (new_fp, fp)
            rel = new_fp.relative_to(INPUT_DIR).as_posix()
            target["file_path"] = "/input/" + rel
            target["name"] = new_fp.stem
        else:
            target["name"] = new_name
        try:
            mention_updates = replace_at_mentions_in_project(data, old_asset_name, target.get("name"))
            save_project(pid, data)
        except Exception:
            if rollback_pair is not None:
                _new_fp, _old_fp = rollback_pair
                if _new_fp.exists() and not _old_fp.exists():
                    try:
                        os.replace(str(_new_fp), str(_old_fp))
                    except Exception:
                        pass
            raise
        self.send_json(200, {"ok": True, "data": data, "asset": target, "old_name": old_asset_name, "new_name": target.get("name"), "mention_updates": mention_updates})

    def api_asset_move(self):
        body = self.read_body()
        pid = safe_name(body.get("project") or "")
        ids = body.get("asset_ids") or []
        cat = body.get("category") or "人物"
        group_id = body.get("group_id") or ""
        if cat not in CATEGORIES:
            self.send_json(400, {"error": "invalid category"}); return
        data = self.begin_project_mutation(pid)
        ensure_asset_groups(data)
        group = None
        if cat in GROUPED_CATEGORIES:
            group = get_asset_group_by_id(data, group_id)
            if not group or group.get("category") != cat:
                self.send_json(400, {"error": "请先选择有效的素材组"}); return
        out_dir = INPUT_DIR / safe_name(pid) / cat
        out_dir.mkdir(parents=True, exist_ok=True)
        for aid in ids:
            a = get_asset_by_id(data, aid)
            if not a or a.get("temporary"):
                continue
            if a.get("category") == cat:
                if group:
                    a["group_id"] = group.get("group_id")
                else:
                    a.pop("group_id", None)
                continue
            old_fp = public_to_local(a.get("file_path"))
            new_name = a.get("name") or "素材"
            ext = old_fp.suffix if old_fp and old_fp.exists() else ".png"
            new_fp = out_dir / f"{new_name}{ext}"
            if old_fp and old_fp.exists():
                safe_replace_file(old_fp, new_fp)
                rel = new_fp.relative_to(INPUT_DIR).as_posix()
                a["file_path"] = "/input/" + rel
            a["name"] = new_name
            a["category"] = cat
            if group:
                a["group_id"] = group.get("group_id")
            else:
                a.pop("group_id", None)
        save_project(pid, data)
        self.send_json(200, {"ok": True, "data": data})

    def api_assets_bulk_delete(self):
        body = self.read_body()
        pid = safe_name(body.get("project") or "")
        ids = set(body.get("asset_ids") or [])
        data = self.begin_project_mutation(pid)
        remove_assets_by_ids(data, ids)
        save_project(pid, data)
        self.send_json(200, {"ok": True, "data": data})

    def api_temp_upload(self):
        body = self.read_body()
        pid = safe_name(body.get("project") or "")
        shot_id = body.get("shot_id") or ""
        tab_id = body.get("tab_id") or None
        data = self.begin_project_mutation(pid)
        scene, shot, tab = find_tab(data, shot_id, tab_id)
        raw, ext = parse_data_url(body.get("dataUrl") or "")
        requested_name = body.get("name") or Path(body.get("filename") or "临时素材").stem
        existing_names = {a.get("name") for a in data.get("assets", []) if a.get("temporary")}
        name = unique_name_with_paren(existing_names, requested_name)
        out_dir = project_path(pid) / "temp_refs" / shot_id / tab.get("tab_id")
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{name}{ext}"
        while out.exists():
            name = unique_name_with_paren(existing_names | {name}, requested_name)
            out = out_dir / f"{name}{ext}"
        out.write_bytes(raw)
        public = f"/temp/{pid}/temp_refs/{shot_id}/{tab.get('tab_id')}/{out.name}"
        asset = {"asset_id": new_id("temp"), "name": out.stem, "category": "临时", "file_path": public, "created_at": now_str(), "temporary": True}
        data.setdefault("assets", []).append(asset)
        tab.setdefault("referenced_assets", []).append(asset["asset_id"])
        save_project(pid, data)
        append_operation_log(pid, "temp_asset_upload", category="临时", asset_id=asset.get("asset_id"), asset_name=asset.get("name"), shot_id=shot_id, tab_id=tab.get("tab_id"))
        self.send_json(200, {"ok": True, "asset": asset, "data": data})

    def api_scene_add(self):
        body = self.read_body(); pid = safe_name(body.get("project") or "")
        data = self.begin_project_mutation(pid)
        next_num = max_scene_number(data.get("scenes", [])) + 1
        s = make_scene(len(data.get("scenes", [])) + 1, next_num)
        s["shots"].append(make_shot(1, data.get("project_settings")))
        data.setdefault("scenes", []).append(s)
        save_project(pid, data)
        self.send_json(200, {"ok": True, "data": data})

    def api_scene_insert(self):
        body = self.read_body(); pid = safe_name(body.get("project") or "")
        data = self.begin_project_mutation(pid)
        next_num = max_scene_number(data.get("scenes", [])) + 1
        s = make_scene(len(data.get("scenes", [])) + 1, next_num)
        s["shots"].append(make_shot(1, data.get("project_settings")))
        data.setdefault("scenes", []).append(s)
        save_project(pid, data)
        self.send_json(200, {"ok": True, "data": data})

    def api_scene_renumber(self):
        body = self.read_body(); pid = safe_name(body.get("project") or "")
        scene_id = body.get("scene_id") or ""
        new_number = parse_positive_int(body.get("scene_number"), None)
        if new_number is None:
            self.send_json(400, {"error": "SC 编号只能填写大于 0 的数字"}); return
        data = self.begin_project_mutation(pid)
        target = None
        for scene in data.get("scenes", []):
            if scene.get("scene_id") == scene_id:
                target = scene
            elif scene_number_from_scene(scene, None) == new_number:
                self.send_json(400, {"error": f"SC-{new_number:04d} 已存在，不能重复"}); return
        if not target:
            self.send_json(404, {"error": "scene not found"}); return
        target["scene_number"] = new_number
        target["scene_code"] = scene_code_from_number(new_number)
        save_project(pid, data)
        self.send_json(200, {"ok": True, "data": data})

    def api_scene_delete(self):
        body = self.read_body(); pid = safe_name(body.get("project") or "")
        scene_id = body.get("scene_id") or ""
        data = self.begin_project_mutation(pid)
        target_code = None
        keep = []
        for s in data.get("scenes", []):
            if s.get("scene_id") == scene_id:
                target_code = s.get("scene_code")
            else:
                keep.append(s)
        if target_code:
            folder = OUTPUT_IMAGE_DIR / safe_name(pid) / target_code
            if folder.exists():
                shutil.rmtree(folder)
        data["scenes"] = keep
        save_project(pid, data)
        self.send_json(200, {"ok": True, "data": data})

    def api_shot_add(self):
        body = self.read_body(); pid = safe_name(body.get("project") or "")
        scene_id = body.get("scene_id") or ""
        data = self.begin_project_mutation(pid)
        scene = find_scene(data, scene_id)
        scene.setdefault("shots", []).append(make_shot(len(scene.get("shots", [])) + 1, data.get("project_settings")))
        save_project(pid, data)
        self.send_json(200, {"ok": True, "data": data})

    def api_shot_insert(self):
        body = self.read_body(); pid = safe_name(body.get("project") or "")
        scene_id = body.get("scene_id") or ""; after_id = body.get("after_shot_id")
        data = self.begin_project_mutation(pid); scene = find_scene(data, scene_id)
        shots = scene.get("shots", [])
        idx = 0
        if after_id:
            for i, shot in enumerate(shots):
                if shot.get("shot_id") == after_id:
                    idx = i + 1; break
        shots.insert(idx, make_shot(idx + 1, data.get("project_settings")))
        scene["shots"] = shots
        save_project(pid, data)
        self.send_json(200, {"ok": True, "data": data})

    def api_shot_delete(self):
        body = self.read_body(); pid = safe_name(body.get("project") or "")
        shot_id = body.get("shot_id") or ""
        data = self.begin_project_mutation(pid)
        for scene in data.get("scenes", []):
            shots = scene.get("shots", [])
            target = None
            for shot in shots:
                if shot.get("shot_id") == shot_id:
                    target = shot; break
            if target:
                folder = OUTPUT_IMAGE_DIR / safe_name(pid) / scene.get("scene_code") / shot_id
                if folder.exists():
                    shutil.rmtree(folder)
                scene["shots"] = [s for s in shots if s.get("shot_id") != shot_id]
                if not scene["shots"]:
                    scene["shots"].append(make_shot(1, data.get("project_settings")))
                break
        save_project(pid, data)
        self.send_json(200, {"ok": True, "data": data})

    def api_shot_reorder(self):
        body = self.read_body(); pid = safe_name(body.get("project") or "")
        scene_id = body.get("scene_id") or ""; order = body.get("shot_order") or []
        data = self.begin_project_mutation(pid); scene = find_scene(data, scene_id)
        by_id = {s.get("shot_id"): s for s in scene.get("shots", [])}
        new_shots = [by_id[i] for i in order if i in by_id]
        for s in scene.get("shots", []):
            if s.get("shot_id") not in order:
                new_shots.append(s)
        for idx, shot in enumerate(new_shots, 1):
            shot["sort_order"] = idx
        scene["shots"] = new_shots
        save_project(pid, data)
        self.send_json(200, {"ok": True, "data": data})

    def api_submit(self):
        body = self.read_body()
        pid = safe_name(body.get("project") or "")
        shot_id = body.get("shot_id") or ""
        tab_id = body.get("tab_id") or None
        user_text = (body.get("message") or "").strip()
        user_name = normalize_user_name(body.get("user_name") or "未命名用户")
        api_key = body.get("api_key") or self.headers.get("Authorization", "").replace("Bearer", "").strip()
        selected_base_id = body.get("base_image_id") or None
        explicit = body.get("mode") or "auto"
        data = load_project(pid)
        scene, shot, tab = find_tab(data, shot_id, tab_id)
        redo_id = body.get("redo_message_id") or ""
        if explicit == "chat":
            mode = "chat"
        elif explicit in ("generate", "regenerate"):
            mode = "generate"
        else:
            mode = infer_submit_mode(tab, user_text, selected_base_id)
        if mode == "chat":
            return self._perform_chat(pid, data, scene, shot, tab, user_text, api_key, user_name, redo_id)
        return self._perform_generate(pid, data, scene, shot, tab, user_text, api_key, explicit, selected_base_id, user_name, redo_id)

    def _perform_chat(self, pid, data, scene, shot, tab, user_text, api_key, user_name, redo_id=""):
        tab_assets = []
        for aid in tab.get("referenced_assets", []):
            asset = get_asset_by_id(data, aid)
            if asset:
                tab_assets.append(asset)
        names = referenced_aliases(user_text, data, tab_assets)
        referenced_assets = []
        for name in names:
            asset = next((a for a in tab_assets if str(a.get("name", "")).lower() == str(name).lower()), None)
            if asset:
                referenced_assets.append(asset)
        # Include already referenced assets from the current tab for context.
        for asset in tab_assets:
            if asset not in referenced_assets:
                referenced_assets.append(asset)
        try:
            tokens = 0
            if api_key:
                reply, tokens = call_openai_chat(api_key, tab, user_text, referenced_assets)
            else:
                assets_text = "、".join([a.get("name", "") for a in referenced_assets])
                reply = "我理解你的需求了。"
                if assets_text:
                    reply += f" 当前已引用素材有：{assets_text}。"
                reply += "你可以继续补充人物、构图、镜头、风格等要求；如果描述已经清楚，也可以直接发送让系统开始生图。"
            # 锁内“重新读取->追加消息->保存”，避免与并发生成/结构操作互相覆盖吞结果。
            _lock = project_mutation_lock(pid)
            _lock.acquire()
            try:
                latest = load_project(pid)
                scene2, shot2, tab2, user_msg_id = apply_user_message_for_submit(latest, shot.get("shot_id"), tab.get("tab_id"), user_text, redo_id)
                tab2.setdefault("messages", []).append({"message_id": new_id("msg"), "role": "assistant", "content": reply, "time": now_str(), "kind": "chat", "reply_to_message_id": user_msg_id})
                update_context_summary(tab2, user_text)
                usage = record_usage(user_name, "chat", CHAT_MODEL, tokens, pid, shot2.get("shot_id"), tab2.get("tab_id"), scene=scene2, shot=shot2) if tokens else get_user_usage(user_name)
                append_operation_log(pid, "chat_submit", user_name=user_name, scene_code=scene2.get("scene_code"), shot_code=shot2.get("shot_code"), shot_id=shot2.get("shot_id"), tab_id=tab2.get("tab_id"), message=user_text)
                save_project(pid, latest)
            finally:
                _lock.release()
            self.send_json(200, {"ok": True, "data": latest, "reply": reply, "used_mock": not bool(api_key), "mode": "chat", "usage": usage})
        except Exception as e:
            self.send_json(500, {"error": str(e), "data": load_project(pid), "usage": get_user_usage(user_name)})

    def _perform_generate(self, pid, data, scene, shot, tab, user_text, api_key, explicit_mode, selected_base_id, user_name, redo_id=""):
        tab_assets = []
        for aid in tab.get("referenced_assets", []):
            asset = get_asset_by_id(data, aid)
            if asset:
                tab_assets.append(asset)
        names = referenced_aliases(user_text, data, tab_assets)
        referenced_lookup = {str(a.get("name", "")).lower(): a for a in tab_assets}
        for name in names:
            if str(name or '').lower() not in referenced_lookup:
                raise ValueError(f"@{name} 不在当前标签页引用素材中，请先把它加入当前标签页引用素材后再使用")
        settings = tab.get("settings") or data.get("project_settings") or project_default_settings(load_config()["global_defaults"])
        operation, base_id = decide_operation(tab, user_text, explicit_mode, selected_base_id)
        image_inputs = []
        if operation == "edit" and base_id:
            _tab, img = find_generated_image_in_shot(shot, base_id)
            if img:
                fp = public_to_local(img.get("file_path"))
                if fp and fp.exists():
                    image_inputs.append(fp)
        referenced_assets = []
        for aid in tab.get("referenced_assets", []):
            a = get_asset_by_id(data, aid)
            if a:
                referenced_assets.append(a)
                fp = public_to_local(a.get("file_path"))
                if fp and fp.exists():
                    image_inputs.append(fp)
        dedup = []
        seen = set()
        for fp in image_inputs:
            key = str(fp)
            if key not in seen:
                dedup.append(fp); seen.add(key)
        image_inputs = dedup[:16]
        latest_text = user_text or "请根据当前聊天上下文和已引用素材生成图片。"
        prompt = build_context_prompt(tab, latest_text, referenced_assets, operation)
        shot_dir = OUTPUT_IMAGE_DIR / safe_name(pid) / scene.get("scene_code") / shot.get("shot_id") / tab.get("tab_id")
        model = settings.get("image_model", "gpt-image-2")
        try:
            tokens = 0
            if api_key:
                image_bytes, tokens = call_openai_image(api_key, "edit" if operation == "edit" and image_inputs else "generate", model, prompt, settings, image_inputs)
                ext = ".png"
            else:
                image_bytes, ext = make_mock_svg(pid, scene.get("scene_code"), shot.get("shot_code"), latest_text, settings)
            filename = next_image_filename(shot_dir, ext)
            out = shot_dir / filename
            out.write_bytes(image_bytes)
            file_url = "/output/image/" + out.relative_to(OUTPUT_IMAGE_DIR).as_posix()
            img = {
                "image_id": new_id("img"), "file_path": file_url, "created_at": now_str(), "operation": operation,
                "base_image_id": base_id if operation == "edit" else None, "user_message": user_text or latest_text,
                "prompt": prompt, "settings": dict(settings), "source_message_id": None,
                "tab_id": tab.get("tab_id"),
            }
            # 锁内“重新读取->追加生成结果->保存”，避免与并发生成/结构操作互相覆盖吞结果。
            _lock = project_mutation_lock(pid)
            _lock.acquire()
            try:
                latest = load_project(pid)
                scene2, shot2, tab2, user_msg_id = apply_user_message_for_submit(latest, shot.get("shot_id"), tab.get("tab_id"), user_text, redo_id)
                img["source_message_id"] = user_msg_id
                tab2.setdefault("generated_images", []).append(img)
                tab2["current_base_image_id"] = img["image_id"]
                update_context_summary(tab2, latest_text)
                tab2.setdefault("messages", []).append({"message_id": new_id("msg"), "role": "assistant", "content": "已生成 1 张图片。", "time": now_str(), "operation": operation, "kind": "generate", "image_id": img["image_id"], "reply_to_message_id": user_msg_id})
                usage = record_usage(user_name, "image", model, tokens, pid, shot2.get("shot_id"), tab2.get("tab_id"), scene=scene2, shot=shot2) if tokens else get_user_usage(user_name)
                append_operation_log(pid, "image_generate_success", user_name=user_name, scene_code=scene2.get("scene_code"), shot_code=shot2.get("shot_code"), shot_id=shot2.get("shot_id"), tab_id=tab2.get("tab_id"), prompt=latest_text, output_path=img.get("file_path"), image_id=img.get("image_id"), operation=operation, model=model)
                create_project_snapshot(pid, "image_generate_success", data=latest, user_name=user_name)
                save_project(pid, latest)
            finally:
                _lock.release()
            self.send_json(200, {"ok": True, "data": latest, "image": img, "used_mock": not bool(api_key), "mode": "generate", "usage": usage})
        except Exception as e:
            self.send_json(500, {"error": str(e), "data": load_project(pid), "usage": get_user_usage(user_name)})

    def api_video_generate(self):
        body = self.read_body()
        pid = safe_name(body.get("project") or "")
        shot_id = body.get("shot_id") or ""
        tab_id = body.get("tab_id") or None
        user_text = (body.get("message") or body.get("prompt") or "").strip()
        api_key = body.get("api_key") or ""
        user_name = normalize_user_name(body.get("user_name") or "未命名用户")
        redo_id = body.get("redo_message_id") or ""
        payload = None
        cost_details = None
        debug_context = {
            "project": pid,
            "shot_id": shot_id,
            "tab_id": tab_id,
            "user_name": user_name,
            "prompt": user_text,
            "redo_message_id": redo_id,
        }
        if not user_text:
            self.send_json(400, {"error": "提示词不能为空", "error_cn": "提示词不能为空。请先填写本次视频生成的描述。"}); return
        if not api_key:
            self.send_json(400, {"error": "请先填写火山方舟 Ark API Key", "error_cn": "请先在右上角填写火山方舟 Ark API Key。"}); return
        try:
            # payload 构建会触发视频参考素材上传 TOS 并写入 asset["remote"]，因此在同一把
            # 项目级锁内完成「读取项目 -> 构建 payload -> 持久化 remote」，避免与并发视频任务
            # 互相覆盖；慢上传仅首次发生一次，Seedance 轮询仍在锁外不阻塞并发。
            _build_lock = project_mutation_lock(pid)
            _build_lock.acquire()
            try:
                data = load_project(pid)
                scene, shot, tab = find_tab(data, shot_id, tab_id)
                debug_context.update({
                    "scene_code": scene.get("scene_code"),
                    "shot_code": shot.get("shot_code"),
                    "tab_name": tab.get("tab_name"),
                    "referenced_assets_before_submit": tab.get("referenced_assets") or [],
                    "asset_count": len(data.get("assets") or []),
                })
                settings = {**project_default_settings(load_config()["global_defaults"]), **(tab.get("settings") or {})}
                if normalize_video_model(settings.get("video_model")) == FAST_VIDEO_MODEL and str(settings.get("video_resolution") or "").lower() == "1080p":
                    settings["video_resolution"] = "720p"
                cost_details = estimate_seedance_cost(user_text, settings, data)
                payload = seedance_payload(user_text, settings, data, tab, pid)
                save_project(pid, data)
            finally:
                _build_lock.release()
            debug_context["settings"] = sanitize_for_log(settings)
            debug_context["cost_details"] = sanitize_for_log(cost_details)
            started_at = now_str()
            gen_t0 = time.monotonic()

            def persist_running_status(task_id="", created_response=None):
                """Persist running state so refresh/reopen keeps the generating video visible."""
                try:
                    _lock = project_mutation_lock(pid)
                    _lock.acquire()
                    try:
                        running_data = load_project(pid)
                        _scene, _shot, _tab = find_tab(running_data, shot_id, tab_id)
                        _tab["draft_prompt"] = user_text
                        status = {
                            "state": "running",
                            "time": now_str(),
                            "started_at": started_at,
                            "task_id": task_id or "",
                            "message": "视频正在生成中。刷新页面后会保留正在生成状态，任务完成后会自动写回结果。",
                            "prompt": user_text,
                            "settings": sanitize_for_log(dict(settings)),
                            "cost_details": sanitize_for_log(cost_details),
                        }
                        if created_response is not None:
                            status["created_response"] = sanitize_for_log(created_response)
                        _tab["last_video_status"] = status
                        save_project(pid, running_data)
                    finally:
                        _lock.release()
                except Exception as status_exc:
                    print("Failed to persist Seedance running status:", status_exc, flush=True)

            persist_running_status()
            print_seedance_request_debug(debug_context, payload)
            task_id, remote_url, detail = submit_and_wait_seedance(api_key, payload, on_created=persist_running_status)
            gen_seconds = round(time.monotonic() - gen_t0, 1)
            _lock = project_mutation_lock(pid)
            _lock.acquire()
            try:
                latest = load_project(pid)
                scene2, shot2, tab2, user_msg_id = apply_user_message_for_submit(latest, shot_id, tab_id, user_text, redo_id)
                tab2["draft_prompt"] = user_text
                # @ 引用只允许使用当前标签页引用素材，不再从全局素材库自动补引用。
                scene_code = safe_file_name(scene2.get("scene_code") or "SC", "SC")
                out_dir = OUTPUT_VIDEO_DIR / safe_name(pid) / scene_code / shot2.get("shot_id") / tab2.get("tab_id")
                out = download_video_to_project(remote_url, out_dir, settings.get("video_resolution"), settings.get("video_ratio"))
                file_url = "/output/video/" + out.relative_to(OUTPUT_VIDEO_DIR).as_posix()
                thumb = create_video_thumbnail(out)
                thumb_url = public_thumb_url(thumb)
                vid = {
                    "image_id": new_id("vid"),
                    "video_id": None,
                    "media_type": "video",
                    "file_path": file_url,
                    "thumb_path": thumb_url,
                    "remote_url": remote_url,
                    "task_id": task_id,
                    "created_at": now_str(),
                    "operation": "seedance_generate",
                    "user_message": user_text,
                    "prompt": user_text,
                    "payload": sanitize_payload_for_record(payload),
                    "settings": dict(settings),
                    "cost_details": cost_details,
                    "source_message_id": user_msg_id,
                    "tab_id": tab2.get("tab_id"),
                    "generation_seconds": gen_seconds,
                }
                vid["video_id"] = vid["image_id"]
                # 任务11：生成结果视频自动转存 TOS，便于日后再次作为参考视频
                # （Ark 原始 remote_url 可能短期过期；本地文件 + TOS remote 双保险）。失败不影响本次生成。
                try:
                    _tos = get_tos_settings()
                    if _tos.get("enabled") and _tos.get("upload_generated_videos"):
                        _sha = sha256_file(out)
                        _key = build_tos_generated_video_key(pid, vid["image_id"], out, _sha)
                        _meta = upload_file_to_tos(out, _key, mimetypes.guess_type(str(out))[0] or "video/mp4")
                        _meta["sha256"] = _sha
                        vid["remote"] = _meta
                except Exception as _tos_exc:
                    vid["remote_upload_error"] = str(_tos_exc)
                    print("生成视频转存 TOS 失败：", _tos_exc, flush=True)
                tab2.setdefault("generated_images", []).append(vid)
                tab2["generated_videos"] = tab2.get("generated_images", [])
                tab2["current_base_image_id"] = vid["image_id"]
                tab2["last_video_status"] = {
                    "state": "success", "time": now_str(), "task_id": task_id,
                    "cost_details": cost_details, "file_path": file_url,
                    "generation_seconds": gen_seconds,
                    "message": f"视频生成完成，本次预估消耗 ¥{cost_details.get('estimated_cny', 0):.4f}。"
                }
                tab2.setdefault("messages", []).append({"message_id": new_id("msg"), "role": "assistant", "content": "已生成 1 条视频。", "time": now_str(), "operation": "seedance_generate", "kind": "generate", "image_id": vid["image_id"], "video_id": vid["video_id"], "reply_to_message_id": user_msg_id})
                usage = record_usage(user_name, "video", cost_details.get("model_id") or normalize_video_model(settings.get("video_model")), cost_details.get("estimated_tokens") or 0, pid, shot2.get("shot_id"), tab2.get("tab_id"), scene=scene2, shot=shot2, cny=cost_details.get("estimated_cny") or 0, cost_details=cost_details)
                save_project(pid, latest)
            finally:
                _lock.release()
            self.send_json(200, {"ok": True, "data": latest, "video": vid, "image": vid, "task_id": task_id, "remote_url": remote_url, "mode": "generate", "usage": usage, "cost_details": cost_details})
        except Exception as e:
            report = build_seedance_error_report(e, payload=payload, context=debug_context)
            print_seedance_error_debug(report)
            err_data = None
            try:
                _lock = project_mutation_lock(pid)
                _lock.acquire()
                try:
                    err_data = load_project(pid) if pid else None
                    if err_data:
                        _scene, _shot, _tab = find_tab(err_data, shot_id, tab_id)
                        _tab["last_video_status"] = {
                            "state": "error",
                            "time": now_str(),
                            "message": report.get("message_cn") or "视频生成失败。",
                            "error": report.get("exception") or "",
                            "error_detail_text": report.get("detail_text") or "",
                            "error_report": sanitize_for_log(report),
                        }
                        save_project(pid, err_data)
                finally:
                    _lock.release()
            except Exception:
                err_data = load_project(pid) if pid else None
            self.send_json(500, {
                "error": report.get("exception") or str(e),
                "error_cn": report.get("message_cn") or explain_seedance_error(str(e)),
                "error_detail_text": report.get("detail_text") or str(e),
                "error_report": sanitize_for_log(report),
                "data": err_data,
                "usage": get_user_usage(user_name),
            })

    def api_storage_delete_images(self):
        body = self.read_body()
        pid = safe_name(body.get("project") or "")
        ids = body.get("image_ids") or []
        if isinstance(ids, str):
            ids = [ids]
        data = self.begin_project_mutation(pid)
        deleted = delete_generated_images_by_ids(data, ids, delete_files=True)
        save_project(pid, data)
        self.send_json(200, {"ok": True, "deleted": deleted, "data": data})

    def api_storage_rename_image(self):
        body = self.read_body()
        pid = safe_name(body.get("project") or "")
        image_id = body.get("image_id") or ""
        new_name = body.get("new_name") or "图片"
        data = self.begin_project_mutation(pid)
        final_name, old_name, new_url = rename_generated_image_by_id(data, image_id, new_name)
        if not final_name:
            self.send_json(404, {"error": "image not found"}); return
        save_project(pid, data)
        self.send_json(200, {"ok": True, "data": data, "image_id": image_id, "old_name": old_name, "new_name": final_name, "file_path": new_url})

    def api_storage_download_images(self):
        body = self.read_body()
        pid = safe_name(body.get("project") or "")
        ids = set(body.get("image_ids") or [])
        data = load_project(pid)
        items = []
        for scene, shot, tab, img in iter_generated_image_records(data):
            if img.get("image_id") in ids:
                fp = public_to_local(img.get("file_path"))
                if fp and fp.exists() and fp.is_file():
                    name = safe_file_name(f"{scene.get('scene_code')}_{shot.get('shot_code')}_{display_name_for_image(img)}", "image") + fp.suffix
                    items.append((fp, name))
        if not items:
            self.send_json(404, {"error": "no valid images"}); return
        out = EXPORTS_DIR / f"storage_images_{safe_name(pid)}_{stamp()}.zip"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            used = set()
            for fp, name in items:
                arc = name
                i = 2
                while arc in used:
                    stem = Path(name).stem; suf = Path(name).suffix
                    arc = f"{stem}_{i}{suf}"; i += 1
                used.add(arc)
                z.write(fp, arc)
        self.send_download(out, out.name, "application/zip")

    def api_chat_message(self):
        return self.api_submit()

    def api_image_generate(self):
        return self.api_submit()

    def api_image_delete(self):
        body = self.read_body()
        pid = safe_name(body.get("project") or "")
        shot_id = body.get("shot_id") or ""
        image_id = body.get("image_id") or ""
        data = self.begin_project_mutation(pid)
        scene, shot = find_shot(data, shot_id)
        removed = False
        for tab in shot.get("tabs", []):
            before = len(tab.get("generated_images", []))
            tab["generated_images"] = [img for img in tab.get("generated_images", []) if img.get("image_id") != image_id]
            if len(tab.get("generated_images", [])) != before:
                removed = True
                tab["generated_videos"] = tab.get("generated_images", [])
                if tab.get("current_base_image_id") == image_id:
                    tab["current_base_image_id"] = tab["generated_images"][-1].get("image_id") if tab.get("generated_images") else None
        shot["storyboard_candidates"] = [c for c in shot.get("storyboard_candidates", []) if c.get("image_id") != image_id]
        shot["status"] = "confirmed" if shot.get("storyboard_candidates") else "unconfirmed"
        if not removed:
            self.send_json(404, {"error": "image not found"}); return
        # Do not delete the physical file from output/image.
        save_project(pid, data)
        self.send_json(200, {"ok": True, "data": data})

    def api_storyboard_add(self):
        body = self.read_body(); pid = safe_name(body.get("project") or "")
        shot_id = body.get("shot_id") or ""; image_id = body.get("image_id") or ""
        data = self.begin_project_mutation(pid); scene, shot = find_shot(data, shot_id)
        _tab, img = find_generated_image_in_shot(shot, image_id)
        if not img:
            self.send_json(404, {"error": "image not found"}); return
        if not any(c.get("image_id") == image_id for c in shot.get("storyboard_candidates", [])):
            shot.setdefault("storyboard_candidates", []).append({"image_id": image_id, "file_path": img.get("file_path"), "thumb_path": img.get("thumb_path", ""), "media_type": img.get("media_type") or ("video" if str(img.get("file_path") or "").lower().endswith((".mp4", ".mov", ".webm", ".m4v")) else "image"), "review_status": "pending", "created_at": now_str()})
        shot["status"] = "confirmed"
        save_project(pid, data)
        self.send_json(200, {"ok": True, "data": data})

    def api_storyboard_upload(self):
        body = self.read_body(); pid = safe_name(body.get("project") or "")
        shot_id = body.get("shot_id") or ""; tab_id = body.get("tab_id") or None
        data = self.begin_project_mutation(pid); scene, shot, tab = find_tab(data, shot_id, tab_id)
        raw, ext = parse_data_url(body.get("dataUrl") or "")
        name = safe_file_name(body.get("name") or Path(body.get("filename") or "分镜图").stem, "分镜图")
        media_is_video = ext.lower() in (".mp4", ".mov", ".webm", ".m4v")
        out_root = OUTPUT_VIDEO_DIR if media_is_video else OUTPUT_IMAGE_DIR
        shot_dir = out_root / safe_name(pid) / scene.get("scene_code") / shot.get("shot_id") / tab.get("tab_id")
        filename = next_image_filename(shot_dir, ext)
        out = shot_dir / filename
        out.write_bytes(raw)
        file_url = ("/output/video/" if media_is_video else "/output/image/") + out.relative_to(out_root).as_posix()
        thumb_url = public_thumb_url(create_video_thumbnail(out)) if media_is_video else ""
        img = {"image_id": new_id("vid" if media_is_video else "img"), "video_id": None, "file_path": file_url, "thumb_path": thumb_url, "created_at": now_str(), "operation": "manual_storyboard", "user_message": name, "prompt": "手动添加分镜视频" if media_is_video else "手动添加分镜图", "settings": dict(tab.get("settings") or {}), "tab_id": tab.get("tab_id"), "media_type": "video" if media_is_video else "image"}
        img["video_id"] = img["image_id"]
        tab.setdefault("generated_images", []).append(img)
        shot.setdefault("storyboard_candidates", []).append({"image_id": img["image_id"], "file_path": file_url, "thumb_path": thumb_url, "media_type": img.get("media_type"), "review_status": "pending", "created_at": now_str()})
        shot["status"] = "confirmed"
        save_project(pid, data)
        self.send_json(200, {"ok": True, "data": data, "image": img})

    def api_storyboard_remove(self):
        body = self.read_body(); pid = safe_name(body.get("project") or "")
        shot_id = body.get("shot_id") or ""; image_id = body.get("image_id") or ""
        data = self.begin_project_mutation(pid); scene, shot = find_shot(data, shot_id)
        shot["storyboard_candidates"] = [c for c in shot.get("storyboard_candidates", []) if c.get("image_id") != image_id]
        if not shot["storyboard_candidates"]:
            shot["status"] = "unconfirmed"
        save_project(pid, data)
        self.send_json(200, {"ok": True, "data": data})


if __name__ == "__main__":
    print(f"Dilan video workflow tool running at http://localhost:{PORT}")
    print(f"Root: {ROOT}")
    http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
