#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Material Tool - independent material projects + character/scene/object workspaces.
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
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, parse_qs, unquote

PORT = int(os.environ.get("DILAN_CHILD_PORT", "8787"))
OPENAI_BASE = "https://api.openai.com/v1"
GPT_RESPONSES_MODEL = "gpt-5.5"
GPT_IMAGE_MODEL_OPTION = "gpt生图"
# V24：新增 Gemini 图片模型（Nano Banana 系列）。下拉显示名 -> 实际 model id。
NANO_BANANA_PRO_OPTION = "Nano Banana Pro"
NANO_BANANA_2_OPTION = "Nano Banana 2"
GEMINI_MODEL_ID = {
    NANO_BANANA_PRO_OPTION: "gemini-3-pro-image",
    NANO_BANANA_2_OPTION: "gemini-3.1-flash-image",
}
IMAGE_MODEL_OPTIONS = [GPT_IMAGE_MODEL_OPTION, NANO_BANANA_PRO_OPTION, NANO_BANANA_2_OPTION]
GEMINI_HOST = "https://generativelanguage.googleapis.com"
# 图像模型在不同 API 版本下的可用性不一致，按 v1beta 优先、v1 兜底依次尝试。
GEMINI_API_VERSIONS = ["v1beta", "v1"]
GEMINI_BASE = GEMINI_HOST + "/v1beta"  # 兼容旧引用
# 各 Gemini 模型支持的分辨率（Nano Banana 2 额外支持 512）。
GEMINI_IMAGE_SIZES = {
    NANO_BANANA_PRO_OPTION: ["1K", "2K", "4K"],
    NANO_BANANA_2_OPTION: ["512", "1K", "2K", "4K"],
}
# 模型不可用时的降级链：Pro 不可用→退 Flash→退 2.5 Flash。下拉显示名 -> 候选 model id 顺序。
GEMINI_MODEL_FALLBACKS = {
    NANO_BANANA_PRO_OPTION: ["gemini-3-pro-image", "gemini-3.1-flash-image", "gemini-2.5-flash-image"],
    NANO_BANANA_2_OPTION: ["gemini-3.1-flash-image", "gemini-2.5-flash-image"],
}
_GEMINI_MODELS_CACHE = {}  # api_key -> {"version": set(model_id 支持 generateContent)}
GPT_RESPONSES_SEARCH_DEFAULT = "auto"
PROJECT_MODULE_TYPE = "material"
PROJECT_MODULE_LABEL = "美术模块"
PROJECT_PACKAGE_TYPE = "dilan_project_package"
LEGACY_PROJECT_PACKAGE_TYPE = "image_storyboard_child_project_full"
PROJECT_MODULE_LABELS = {"material": "美术模块", "image": "分镜模块", "video": "视频模块"}
ROOT = Path(__file__).resolve().parent
SHARED_PROJECT_INDEX_DIR = ROOT.parent.parent / "shared_project_index"
SHARED_PARENTS_JSON = SHARED_PROJECT_INDEX_DIR / "parents.json"
import sys as _sys
if str(ROOT.parent) not in _sys.path:
    _sys.path.insert(0, str(ROOT.parent))
import datacenter_common as dcc
PROJECT_MODULE_KEYS = ["material", "image", "video"]
PROJECT_MODULE_UI_LABELS = {"material": "美术", "image": "分镜", "video": "视频"}
PROJECTS_DIR = ROOT / "projects"
INPUT_DIR = ROOT / "input"
OUTPUT_DIR = ROOT / "output"
OUTPUT_IMAGE_DIR = OUTPUT_DIR / "material"
CONFIG_PATH = ROOT / "config.json"
USERS_PATH = ROOT / "users.json"
USAGE_PATH = ROOT / "usage.json"
EXPORTS_DIR = ROOT / "exports"
IMPORTS_DIR = ROOT / "imports"
PROJECT_SEP = "__CHILD__"

CATEGORIES = ["人物", "场景", "道具"]
GROUPED_CATEGORIES = set()  # V23：取消素材组，人物/场景/道具均平铺
USD_PER_1K_TOKENS = 0.005
DEFAULT_CONFIG = {
    "global_defaults": {
        "image_model": GPT_IMAGE_MODEL_OPTION,
        "image_ratio": "16:9",
        "image_resolution": "1K",
        "image_quality": "medium",
        "gpt_responses_search": GPT_RESPONSES_SEARCH_DEFAULT,
        "default_scene_count": 1,
        "default_shot_count_per_scene": 1,
        "expand_all_scenes": True,
        "show_asset_panel": True,
        "output_rule": "output/material/{project}/{scene_code}/{shot_id}/",
        "input_rule": "input/{project}/{category}/",
        "usd_per_1k_tokens": USD_PER_1K_TOKENS,
    }
}

for d in (PROJECTS_DIR, INPUT_DIR, OUTPUT_DIR, OUTPUT_IMAGE_DIR, EXPORTS_DIR, IMPORTS_DIR):
    d.mkdir(parents=True, exist_ok=True)

SIZE_MAP = {
    "1K": {"1:1": "1024x1024", "4:3": "1024x768", "16:9": "1536x864", "9:16": "864x1536", "21:9": "1792x768"},
    "2K": {"1:1": "2048x2048", "4:3": "2048x1536", "16:9": "2560x1440", "9:16": "1440x2560", "21:9": "2560x1080"},
    "4K": {"1:1": "4096x4096", "4:3": "4096x3072", "16:9": "3840x2160", "9:16": "2160x3840", "21:9": "4096x1755"},
}

INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
AT_PATTERN = re.compile(r"@([^\s@]+)")


def normalize_image_model(value=None):
    """V24：支持 gpt生图 / Nano Banana Pro / Nano Banana 2，未知值回落 gpt生图。"""
    v = str(value or "").strip()
    if v in IMAGE_MODEL_OPTIONS:
        return v
    # 兼容可能传入的实际 model id
    for opt, mid in GEMINI_MODEL_ID.items():
        if v == mid:
            return opt
    return GPT_IMAGE_MODEL_OPTION


def is_gemini_image_model(model_option):
    return model_option in GEMINI_MODEL_ID


def gemini_aspect_ratio(settings):
    return str((settings or {}).get("image_ratio") or "16:9")


def gemini_image_size(model_option, settings):
    res = str((settings or {}).get("image_resolution") or "1K")
    allowed = GEMINI_IMAGE_SIZES.get(model_option) or ["1K", "2K", "4K"]
    return res if res in allowed else "1K"


def _gemini_extract_b64(obj):
    """从 generateContent 响应中取第一张非思考图片的 base64。"""
    cands = obj.get("candidates") if isinstance(obj, dict) else None
    if not isinstance(cands, list):
        return ""
    for c in cands:
        content = (c or {}).get("content") or {}
        for part in content.get("parts", []) or []:
            if not isinstance(part, dict):
                continue
            if part.get("thought"):
                continue
            inline = part.get("inlineData") or part.get("inline_data")
            if isinstance(inline, dict) and inline.get("data"):
                return inline.get("data")
    # 兜底：允许思考图（极少数情况只有思考图）
    for c in cands:
        content = (c or {}).get("content") or {}
        for part in content.get("parts", []) or []:
            inline = (part or {}).get("inlineData") or (part or {}).get("inline_data")
            if isinstance(inline, dict) and inline.get("data"):
                return inline.get("data")
    return ""


def _gemini_usage_tokens(obj, prompt, settings, image_paths):
    usage = obj.get("usageMetadata") if isinstance(obj, dict) else None
    if isinstance(usage, dict):
        t = usage.get("totalTokenCount") or usage.get("candidatesTokenCount")
        if t:
            try:
                return int(t)
            except Exception:
                pass
    return estimate_image_tokens(prompt, settings, image_paths or [])


def _gemini_build_parts(prompt, image_paths):
    parts = [{"text": prompt or ""}]
    for fp in [p for p in (image_paths or []) if p][:14]:  # 最多 14 张参考图
        try:
            raw = Path(fp).read_bytes()
        except Exception:
            continue
        mime = mimetypes.guess_type(str(fp))[0] or "image/png"
        parts.append({"inline_data": {"mime_type": mime, "data": base64.b64encode(raw).decode("ascii")}})
    return parts


def _gemini_request(version, model_id, body, api_key):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        GEMINI_HOST + "/" + version + "/models/" + model_id + ":generateContent",
        data=data, method="POST",
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=600) as resp:
        return json.loads(resp.read().decode("utf-8"))


def list_gemini_models(api_key, version):
    """调用 models.list，返回该版本下支持 generateContent 的 model id 集合（去掉 models/ 前缀）。
    结果按 (api_key, version) 缓存，避免每次生图都列举。出错返回 None（表示未知，不据此过滤）。"""
    cache_key = (api_key, version)
    if cache_key in _GEMINI_MODELS_CACHE:
        return _GEMINI_MODELS_CACHE[cache_key]
    try:
        req = urllib.request.Request(
            GEMINI_HOST + "/" + version + "/models?pageSize=200",
            headers={"x-goog-api-key": api_key},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            obj = json.loads(resp.read().decode("utf-8"))
    except Exception:
        _GEMINI_MODELS_CACHE[cache_key] = None
        return None
    ok = set()
    for m in (obj.get("models") or []):
        name = str(m.get("name") or "")
        if name.startswith("models/"):
            name = name[len("models/"):]
        methods = m.get("supportedGenerationMethods") or m.get("supportedActions") or []
        if not methods or ("generateContent" in methods):
            if name:
                ok.add(name)
    _GEMINI_MODELS_CACHE[cache_key] = ok
    _GEMINI_MODELS_CACHE[(api_key, version, "all")] = {
        (m.get("name") or "")[len("models/"):] if str(m.get("name") or "").startswith("models/") else (m.get("name") or "")
        for m in (obj.get("models") or [])
    }
    return ok


def _gemini_model_candidates(api_key, model_option):
    """返回 [(version, model_id), ...] 的尝试顺序：
    先按 models.list 过滤出当前 Key 实际可用、且支持 generateContent 的模型；
    若列举失败（None），则不过滤、按降级链原样尝试。"""
    chain = GEMINI_MODEL_FALLBACKS.get(model_option) or [GEMINI_MODEL_ID.get(model_option) or "gemini-3.1-flash-image"]
    out = []
    seen = set()
    for version in GEMINI_API_VERSIONS:
        avail = list_gemini_models(api_key, version)
        for mid in chain:
            if avail is None or mid in avail:
                key = (version, mid)
                if key not in seen:
                    seen.add(key)
                    out.append(key)
    # 兜底：若过滤后为空（例如 list 成功但没有任何生图模型命中），仍按原链 × 版本尝试一次，让真实错误暴露。
    if not out:
        for version in GEMINI_API_VERSIONS:
            for mid in chain:
                out.append((version, mid))
    return out


def call_gemini_image(api_key, model_option, prompt, settings, image_paths=None):
    """调用 Gemini 原生图像生成（Nano Banana）。参考图走 inline_data。返回 (image_bytes, tokens)。

    三层自适应，确保在不同账号/版本/接口下尽量出图（依据官方 404 排查建议）：
      1) 端点版本：v1beta 优先、v1 兜底；
      2) 模型可用性：用 models.list 过滤当前 Key 可用且支持 generateContent 的模型，
         不可用则按降级链 Pro→3.1 Flash→2.5 Flash 自动切换；
      3) 请求体字段：imageConfig → responseModalities+imageConfig → responseFormat → 最简(仅 contents)。
    """
    parts = _gemini_build_parts(prompt, image_paths)
    contents = [{"role": "user", "parts": parts}]
    ar = gemini_aspect_ratio(settings)
    sz = gemini_image_size(model_option, settings)

    def payloads():
        return [
            {"contents": contents, "generationConfig": {"imageConfig": {"aspectRatio": ar, "imageSize": sz}}},
            {"contents": contents, "generationConfig": {"responseModalities": ["TEXT", "IMAGE"], "imageConfig": {"aspectRatio": ar, "imageSize": sz}}},
            {"contents": contents, "generationConfig": {"responseModalities": ["TEXT", "IMAGE"], "responseFormat": {"image": {"aspectRatio": ar, "imageSize": sz}}}},
            {"contents": contents},
        ]

    last_err = ""
    tried = []
    for version, model_id in _gemini_model_candidates(api_key, model_option):
        for body in payloads():
            try:
                obj = _gemini_request(version, model_id, body, api_key)
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", errors="replace")
                last_err = f"[{version}/{model_id}] HTTP {e.code}: {detail[:400]}"
                if e.code == 400 and ("Unknown name" in detail or "Cannot find field" in detail or "Invalid JSON payload" in detail):
                    continue  # 字段问题：换更简单的 payload
                if e.code == 404 and ("is not found" in detail or "not supported" in detail or "NOT_FOUND" in detail):
                    tried.append(f"{version}/{model_id}:404")
                    break  # 模型/版本不可用：换下一个 (version, model)
                if e.code in (401, 403):
                    raise RuntimeError(f"Gemini 生图失败（密钥无权限或无效）{last_err}")
                if e.code == 429:
                    raise RuntimeError(f"Gemini 生图失败（配额/速率限制）{last_err}")
                # 其它错误：换 payload 再试
                continue
            except Exception as e:
                last_err = f"[{version}/{model_id}] {e}"
                continue
            b64 = _gemini_extract_b64(obj)
            if b64:
                return base64.b64decode(b64), _gemini_usage_tokens(obj, prompt, settings, image_paths)
            last_err = f"[{version}/{model_id}] 无图片返回：" + json.dumps(obj, ensure_ascii=False)[:300]
        else:
            continue
        # 内层 break（404）后继续下一个 (version, model)
        continue
    hint = ""
    if tried:
        hint = "（已尝试：" + "、".join(tried) + "）"
    raise RuntimeError("Gemini 生图失败：当前 API Key 在 v1beta/v1 下均无法用所选模型出图" + hint + "。最后错误：" + last_err)


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
    return f"命名框-{num:04d}"


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
    merged["global_defaults"]["image_model"] = normalize_image_model(merged["global_defaults"].get("image_model"))
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
    merged["global_defaults"]["output_rule"] = "output/material/{project}/{scene_code}/{shot_id}/"
    merged["global_defaults"]["input_rule"] = "input/{project}/{category}/"
    merged["global_defaults"]["image_model"] = normalize_image_model(merged["global_defaults"].get("image_model"))
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
        "image_model": normalize_image_model(defaults.get("image_model")),
        "image_ratio": defaults.get("image_ratio", "16:9"),
        "image_resolution": defaults.get("image_resolution", "1K"),
        "image_quality": defaults.get("image_quality", "medium"),
        "gpt_responses_search": defaults.get("gpt_responses_search", GPT_RESPONSES_SEARCH_DEFAULT),
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
        "generated_images": [],
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
    """Per-project lock so concurrent generate/chat/save operations serialize
    their read-modify-write of the project JSON. Without it, parallel image
    generation tasks could lose each other's appended images (last-write-wins).
    The slow model call stays OUTSIDE this lock so generations still run in
    parallel; only the brief reload->append->save section is serialized."""
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
    tab["settings"]["image_model"] = normalize_image_model(tab["settings"].get("image_model"))
    tab.setdefault("referenced_assets", [])
    tab.setdefault("messages", [])
    for m in tab.get("messages", []):
        m.setdefault("message_id", new_id("msg"))
    tab.setdefault("generated_images", [])
    for gi in tab.get("generated_images", []):
        gi.setdefault("image_id", new_id("img"))
    tab.setdefault("current_base_image_id", None)
    tab.setdefault("context_summary", "")
    tab.setdefault("draft_prompt", "")
    return tab


def ensure_shot_tabs(shot, default_settings):
    tabs = shot.get("tabs")
    if not isinstance(tabs, list) or not tabs:
        migrated = make_tab(1, shot.get("settings") or default_settings)
        for k in ("referenced_assets", "messages", "generated_images", "current_base_image_id", "context_summary", "draft_prompt"):
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
    for k in ("settings", "referenced_assets", "messages", "generated_images", "current_base_image_id", "context_summary", "draft_prompt"):
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
    data["project_settings"]["image_model"] = normalize_image_model(data["project_settings"].get("image_model"))
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
        scene["scene_code"] = safe_name(scene.get("scene_code") or scene_code_from_number(num), scene_code_from_number(num))
        normalized_scenes.append(scene)
        fallback_next = max(fallback_next + 1, num + 1)
    scenes = sorted(normalized_scenes, key=lambda s: scene_number_from_scene(s, 999999))
    for si, scene in enumerate(scenes, 1):
        scene["sort_order"] = si
        scene["scene_number"] = scene_number_from_scene(scene, si)
        scene["scene_code"] = safe_name(scene.get("scene_code") or scene_code_from_number(scene["scene_number"]), scene_code_from_number(scene["scene_number"]))
        scene.setdefault("expanded", True)
        scene["time_start"] = normalize_time_obj(scene.get("time_start"))
        scene["time_end"] = normalize_time_obj(scene.get("time_end"))
        normalize_scene_review(scene)
        shots = sorted(scene.get("shots", []), key=lambda x: int(x.get("sort_order") or 999999))
        for hi, shot in enumerate(shots, 1):
            shot.setdefault("shot_id", new_id("shot"))
            shot["sort_order"] = hi
            shot["shot_code"] = safe_name(shot.get("shot_code") or f"{hi:02d}", f"{hi:02d}")
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
    base = OUTPUT_IMAGE_DIR / safe_name(project_id, "project")
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
    # 美术模块：当前激活的 scenes 里没有时，到三套工作台 scenes 里全局查找，
    # 避免“在人物区生成、前端已切到道具区”时按 shot_id 找不到导致 shot not found。
    wss = data.get("material_workspaces")
    if isinstance(wss, dict):
        for key in ("character", "scene", "object"):
            ws = wss.get(key) or {}
            for scene in ws.get("scenes", []) or []:
                for shot in scene.get("shots", []) or []:
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


IMAGE_LABELS_CN = [
    "图一", "图二", "图三", "图四", "图五", "图六", "图七", "图八",
    "图九", "图十", "图十一", "图十二", "图十三", "图十四", "图十五", "图十六",
]
START_IMAGE_WORDS = ["开始生图", "开始生成", "开始出图", "正式生图", "确认生图", "确认生成", "出图吧", "生成吧"]
UNKNOWN_MENTION_PATTERN = re.compile(r"@([^\s@，。！？、；：,.!?;:()（）【】\[\]{}<>《》]+)")

# --------------------------------------------------------------------------- #
# 文档素材（仅道具类目）：本地上传 / OpenAI Files 懒上传 / @文档N 引用（V21）
# --------------------------------------------------------------------------- #
DOCUMENT_ASSET_CATEGORY = "道具"
DOCUMENT_LABELS_CN = [
    "文档一", "文档二", "文档三", "文档四", "文档五", "文档六", "文档七", "文档八",
    "文档九", "文档十", "文档十一", "文档十二", "文档十三", "文档十四", "文档十五", "文档十六",
]
DOCUMENT_EXTS = {".pdf", ".doc", ".docx", ".txt", ".md", ".csv", ".xls", ".xlsx"}
DOCUMENT_MIME_BY_EXT = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
MAX_DOCUMENT_MB = 50
MAX_DOCS_PER_MESSAGE = 5


def doc_label_for_index(index: int) -> str:
    if 1 <= index <= len(DOCUMENT_LABELS_CN):
        return DOCUMENT_LABELS_CN[index - 1]
    return f"文档{index}"


def is_document_asset(asset) -> bool:
    if not isinstance(asset, dict):
        return False
    if asset.get("asset_type") == "document":
        return True
    ext = Path(str(asset.get("file_path") or "")).suffix.lower()
    return ext in DOCUMENT_EXTS


def document_ext_from_name(filename: str) -> str:
    ext = Path(str(filename or "")).suffix.lower()
    return ext if ext in DOCUMENT_EXTS else ""


def parse_document_data_url(data_url, filename):
    """解析文档 dataUrl：按文件名扩展校验白名单与大小，返回 (raw, ext, mime)。"""
    if not data_url or "," not in data_url:
        raise ValueError("missing dataUrl")
    ext = document_ext_from_name(filename)
    if not ext:
        raise ValueError("不支持的文档类型，仅支持：" + "、".join(sorted(DOCUMENT_EXTS)))
    raw = base64.b64decode(str(data_url).split(",", 1)[1])
    if not raw:
        raise ValueError("文档内容为空")
    if len(raw) > MAX_DOCUMENT_MB * 1024 * 1024:
        raise ValueError(f"文档超过 {MAX_DOCUMENT_MB}MB 限制：{filename}")
    return raw, ext, DOCUMENT_MIME_BY_EXT.get(ext, "application/octet-stream")


def api_key_fingerprint(api_key: str) -> str:
    return hashlib.sha256(str(api_key or "").encode("utf-8")).hexdigest()[:12]


def upload_file_to_openai(api_key, fp: Path, original_name="", mime=""):
    """multipart 上传本地文件到 OpenAI Files API（purpose=user_data），返回 file_id。"""
    boundary = "----DilanDocBoundary" + uuid.uuid4().hex
    fname = str(original_name or fp.name).replace('"', "'").replace("\r", " ").replace("\n", " ")
    head = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"purpose\"\r\n\r\nuser_data\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{fname}\"\r\n"
        f"Content-Type: {mime or 'application/octet-stream'}\r\n\r\n"
    ).encode("utf-8")
    body = head + fp.read_bytes() + f"\r\n--{boundary}--\r\n".encode("utf-8")
    req = urllib.request.Request(
        OPENAI_BASE + "/files", data=body, method="POST",
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            obj = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI 文件上传失败 HTTP {e.code}: {detail[:600]}")
    except Exception as e:
        raise RuntimeError(f"OpenAI 文件上传失败: {e}")
    fid = obj.get("id")
    if not fid:
        raise RuntimeError("OpenAI 文件上传未返回 file id: " + json.dumps(obj, ensure_ascii=False)[:400])
    return fid


def persist_asset_openai_fields(pid, asset):
    """把 asset 上的 OpenAI 文件字段独立写回 project.json，避免被调用方后续整体保存覆盖丢失。"""
    try:
        latest = load_project(pid)
    except Exception:
        return
    target = get_asset_by_id(latest, asset.get("asset_id"))
    if not target:
        return
    for k in ("openai_file_id", "openai_file_key_fp", "openai_file_status", "openai_file_error", "openai_uploaded_at"):
        if k in asset:
            target[k] = asset.get(k)
    try:
        save_project(pid, latest)
    except Exception:
        pass


def ensure_openai_file_id(api_key, pid, data, asset, force=False):
    """懒上传：文档第一次被 @ 时上传 OpenAI Files API；file_id 绑定 API Key 指纹持久化并复用。"""
    if not api_key:
        raise ValueError("missing OpenAI API Key")
    fp = public_to_local(asset.get("file_path") or "")
    if not fp or not fp.exists():
        raise RuntimeError(f"文档「{asset.get('name')}」的本地文件不存在，无法提交给 GPT")
    if fp.stat().st_size > MAX_DOCUMENT_MB * 1024 * 1024:
        raise RuntimeError(f"文档「{asset.get('name')}」超过 {MAX_DOCUMENT_MB}MB 限制")
    key_fp = api_key_fingerprint(api_key)
    if (not force) and asset.get("openai_file_id") and asset.get("openai_file_key_fp") == key_fp:
        return asset.get("openai_file_id")
    asset["openai_file_status"] = "openai_uploading"
    try:
        fid = upload_file_to_openai(api_key, fp, asset.get("original_name") or fp.name,
                                    asset.get("mime_type") or DOCUMENT_MIME_BY_EXT.get(fp.suffix.lower(), ""))
    except Exception as e:
        asset["openai_file_status"] = "failed"
        asset["openai_file_error"] = str(e)[:500]
        persist_asset_openai_fields(pid, asset)
        raise
    asset["openai_file_id"] = fid
    asset["openai_file_key_fp"] = key_fp
    asset["openai_file_status"] = "openai_ready"
    asset["openai_file_error"] = ""
    asset["openai_uploaded_at"] = now_str()
    persist_asset_openai_fields(pid, asset)
    return fid


def run_with_document_retry(api_key, pid, data, doc_assets, runner):
    """确保每个文档都有可用 openai_file_id 后执行 runner(files)。
    若失败且错误信息包含某个 file_id（OpenAI 侧文件被删/失效的典型表现），强制重传一次后重试。"""
    def _files():
        return [{"file_id": a.get("openai_file_id"),
                 "name": a.get("original_name") or ((a.get("name") or "文档") + Path(str(a.get("file_path") or "")).suffix)}
                for a in doc_assets]
    for a in doc_assets:
        ensure_openai_file_id(api_key, pid, data, a)
    try:
        return runner(_files())
    except RuntimeError as e:
        msg = str(e)
        stale = [a for a in doc_assets if a.get("openai_file_id") and a.get("openai_file_id") in msg]
        if not stale:
            raise
        for a in stale:
            ensure_openai_file_id(api_key, pid, data, a, force=True)
        return runner(_files())




def clean_generation_command_text(text: str) -> str:
    """Remove the trigger words such as 开始生图 while keeping the actual request."""
    s = str(text or "")
    for w in START_IMAGE_WORDS:
        s = s.replace(w, "")
    s = re.sub(r"^[\s，。！？、；：,.!?;:：-]+", "", s)
    s = re.sub(r"[\s，。！？、；：,.!?;:：-]+$", "", s)
    return s.strip()


def is_only_generation_command(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    cleaned = clean_generation_command_text(raw)
    return not cleaned


def select_effective_generation_text(thread, user_text):
    """If the user only says 开始生图, use the latest real user requirement in this tab."""
    current_clean = clean_generation_command_text(user_text)
    if current_clean:
        return current_clean
    for m in reversed(thread.get("messages", []) or []):
        if m.get("role") != "user":
            continue
        content = str(m.get("content") or "").strip()
        if not content or is_only_generation_command(content):
            continue
        return clean_generation_command_text(content) or content
    return current_clean or str(user_text or "").strip() or "开始生图"


def label_for_index(index: int) -> str:
    if 1 <= index <= len(IMAGE_LABELS_CN):
        return IMAGE_LABELS_CN[index - 1]
    return f"图{index}"


def validate_unique_tab_asset_names(tab_assets):
    seen = {}
    duplicates = []
    for a in tab_assets or []:
        name = str(a.get("name") or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in seen and name not in duplicates:
            duplicates.append(name)
        seen[key] = a
    if duplicates:
        raise ValueError("当前标签页引用素材存在同名素材：" + "、".join(duplicates) + "。请先改名，避免 @ 引用歧义。")


def parse_at_asset_mentions_for_execution(text: str, tab_assets, start_index: int = 1):
    """
    Parse @素材 by first appearance order. Final mapping keeps each image once,
    while normalized_text preserves every repeated semantic reference.
    """
    validate_unique_tab_asset_names(tab_assets)
    raw = str(text or "")
    assets = [a for a in (tab_assets or []) if a.get("name")]
    names = sorted([str(a.get("name")) for a in assets], key=len, reverse=True)
    by_lower = {str(a.get("name")).lower(): a for a in assets if a.get("name")}
    label_by_lower = {}
    mappings = []
    unknown = []
    normalized = []
    lower_raw = raw.lower()
    i = 0
    while i < len(raw):
        if raw[i] != "@":
            normalized.append(raw[i])
            i += 1
            continue
        matched_name = None
        for name in names:
            start = i + 1
            end = start + len(name)
            if lower_raw[start:end] == name.lower():
                matched_name = name
                break
        if matched_name:
            key = matched_name.lower()
            if key not in label_by_lower:
                asset = by_lower[key]
                if is_document_asset(asset):
                    seq = 1 + sum(1 for m in mappings if m.get("asset_type") == "document")
                    label = doc_label_for_index(seq)
                    a_type = "document"
                else:
                    seq = start_index + sum(1 for m in mappings if m.get("asset_type") != "document")
                    label = label_for_index(seq)
                    a_type = "image"
                label_by_lower[key] = label
                mappings.append({
                    "index": seq,
                    "label": label,
                    "asset_id": asset.get("asset_id"),
                    "asset_name": asset.get("name"),
                    "file_path": asset.get("file_path"),
                    "source": "@mention",
                    "asset_type": a_type,
                })
            normalized.append(label_by_lower[key])
            i += 1 + len(matched_name)
            continue
        m = UNKNOWN_MENTION_PATTERN.match(raw, i)
        if m:
            name = m.group(1)
            if name not in unknown:
                unknown.append(name)
            normalized.append("@" + name)
            i = m.end()
        else:
            normalized.append(raw[i])
            i += 1
    return {
        "mappings": mappings,
        "unknown_mentions": unknown,
        "normalized_text": "".join(normalized).strip(),
        "mentioned_assets_in_order": [m.get("asset_name") for m in mappings],
    }


def build_mapping_text(mappings, doc_suffix="（文档内容已作为文件输入随本条消息提供，请完整阅读）"):
    if not mappings:
        return "本轮没有通过 @ 引用任何素材图片。"
    lines = []
    for m in mappings:
        idx = m.get("index")
        label = m.get("label")
        name = m.get("asset_name") or m.get("display_name") or "未命名图片"
        if m.get("asset_type") == "document":
            lines.append(f"参考文档 / {label} = {name}{doc_suffix}")
        else:
            lines.append(f"第{idx}张图 / {label} = {name}")
    return "\n".join(lines)


def build_execution_compiler_input(thread, effective_text, execution_sheet):
    recent = []
    for m in (thread.get("messages", []) or [])[-10:]:
        role = "用户" if m.get("role") == "user" else "助手"
        content = str(m.get("content") or "").strip()
        if content:
            recent.append(f"{role}：{content}")
    docs = execution_sheet.get("input_documents") or []
    parts = [
        "请把当前对话中已经确认的生图需求，整理成一段最终生图执行要求。",
        "必须使用图一、图二、图三等编号指代图片，不要使用 @素材名。",
        "不要输出 JSON，不要输出解释，只输出可以直接交给生图模型执行的一段中文提示词。",
        "不要新增用户没有要求的主体、场景、风格或动作。",
        "如果用户要求保持不变、只修改局部、黑白线稿、色调迁移等硬性限制，必须保留并加强。",
    ]
    if docs:
        parts.append("本轮附带参考文档（已作为文件输入随本条消息提供）。请完整阅读文档内容，把其中与本轮画面相关的设定（如角色外观、服装、场景、道具、风格、色彩、构图、禁忌等）直接展开写进最终执行要求；最终执行要求必须自包含，生图模型不会收到文档原文，不要在输出中要求查阅文档。")
    parts += [
        "",
        "【近期对话】",
        "\n".join(recent) if recent else "无",
        "",
        "【最终执行映射】",
        build_mapping_text(execution_sheet.get("final_execution_mapping") or []),
        "",
    ]
    if docs:
        parts += ["【本轮附带文档】", "\n".join([f"{d.get('label')}：{d.get('asset_name')}" for d in docs]), ""]
    parts += [
        "【归一化后的本轮需求】",
        execution_sheet.get("normalized_text") or effective_text or "",
    ]
    return "\n".join(parts)


def call_gpt_execution_compiler(api_key, thread, effective_text, execution_sheet, timeout=180, document_files=None):
    if not api_key:
        return "", 0
    document_files = document_files or []
    content = []
    for f in document_files[:MAX_DOCS_PER_MESSAGE]:
        if f.get("file_id"):
            content.append({"type": "input_file", "file_id": f.get("file_id")})
    content.append({"type": "input_text", "text": build_execution_compiler_input(thread, effective_text, execution_sheet)})
    payload = {
        "model": GPT_RESPONSES_MODEL,
        "instructions": "你是帝蓝工作流的生图执行单编译器。你读取聊天上下文和随消息提供的参考文档，输出最终生图执行要求，但不生成图片。只输出最终提示词正文。",
        "input": [{"role": "user", "content": content}],
        "tools": [],
        "store": False,
    }
    try:
        obj = call_openai_responses(api_key, payload, timeout=timeout)
        text = extract_text_response(obj).strip()
        tokens = extract_usage_tokens(obj) or approx_text_tokens(json.dumps(payload, ensure_ascii=False) + text)
        return text, tokens
    except Exception as e:
        if document_files:
            raise RuntimeError(f"生图执行单编译器读取文档失败：{e}")
        return "", 0


def compile_final_image_prompt(execution_sheet, compiled_requirement=None):
    mappings = execution_sheet.get("final_execution_mapping") or []
    normalized = execution_sheet.get("normalized_text") or execution_sheet.get("effective_user_text") or ""
    requirement = (compiled_requirement or "").strip() or normalized
    lines = [
        "本次是独立的 GPT 生图执行任务。生图模型只能依据本提示词和随后的输入图片执行，不要读取、延续或想象任何未写入本提示词的聊天上下文。",
        "",
        "【最终执行映射】",
        build_mapping_text(mappings),
        "",
        "【用户需求归一化文本】",
        normalized or "无",
        "",
        "【最终执行要求】",
        requirement or "请根据以上映射和用户需求生成图片。",
        "",
        "【硬性执行规则】",
        "1. 所有 @素材名 已经被后端映射为图一、图二、图三等编号，生图时只使用这些编号理解图片。",
        "2. 如果同一张图在用户需求中被多次提到，图片只输入一次，但语义上必须按多次提到的关系执行，例如“图一保持图一的脸不变”。",
        "3. 只使用本轮实际输入的图片，不要使用未输入的当前标签页其他素材。",
        "4. 不要擅自改变用户要求保持不变的构图、主体、身份、动作、位置、镜头或内容。",
        "5. 如果用户要求只修改某一项，例如色调、衣服、背景、线条颜色等，就只修改该项，其余部分尽量保持稳定。",
    ]
    return "\n".join(lines).strip()


def build_gpt_image_execution_sheet(thread, user_text, tab_assets, operation, base_entry=None, api_key=None, pid=None, data=None):
    effective_text = select_effective_generation_text(thread, user_text)
    cleaned_text = clean_generation_command_text(effective_text) or effective_text
    mappings = []
    input_images = []
    start_index = 1
    normalized_prefix = ""
    if base_entry:
        label = label_for_index(1)
        mappings.append({
            "index": 1,
            "label": label,
            "asset_id": base_entry.get("image_id"),
            "asset_name": base_entry.get("name") or "当前编辑基准图",
            "file_path": base_entry.get("file_path"),
            "source": "base_image",
        })
        input_images.append(base_entry.get("file_path"))
        start_index = 2
        normalized_prefix = f"{label}是当前编辑基准图。"
    parsed = parse_at_asset_mentions_for_execution(cleaned_text, tab_assets, start_index=start_index)
    unknown = parsed.get("unknown_mentions") or []
    if unknown:
        raise ValueError("、".join([f"@{x}" for x in unknown]) + " 不在当前标签页引用素材中，请先把它加入当前标签页引用素材后再使用。")
    mappings.extend(parsed.get("mappings") or [])
    for m in parsed.get("mappings") or []:
        input_images.append(m.get("file_path"))
    normalized_text = parsed.get("normalized_text") or cleaned_text
    if normalized_prefix:
        normalized_text = normalized_prefix + "\n" + normalized_text
    execution_sheet = {
        "mode": "gpt_image",
        "operation": operation,
        "raw_user_text": user_text or "",
        "effective_user_text": cleaned_text,
        "mentioned_assets_in_order": parsed.get("mentioned_assets_in_order") or [],
        "final_execution_mapping": mappings,
        "normalized_text": normalized_text,
        "input_images": input_images,
        "created_at": now_str(),
        "version": "V23-image-api-sheet",
    }
    # V23：不再经过 gpt-5.5 编译器，最终提示词直接由图N映射后的归一化文本组装，交给 Image API。
    final_prompt = compile_final_image_prompt(execution_sheet, normalized_text)
    execution_sheet["compiled_requirement"] = normalized_text
    execution_sheet["final_prompt"] = final_prompt
    execution_sheet["compiler_tokens"] = 0
    return execution_sheet


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
    item = data.get("users", {}).get(name) or {"user_name": name, "tokens": 0, "usd": 0.0}
    return {"user_name": name, "tokens": int(item.get("tokens") or 0), "usd": round(float(item.get("usd") or 0), 6)}


def record_usage(user_name, kind, model, tokens, project_id=None, shot_id=None, tab_id=None, scene=None, shot=None):
    user_name = normalize_user_name(user_name)
    tokens = max(0, int(tokens or 0))
    usd = round(tokens / 1000 * current_usd_per_1k(), 6)
    data = load_usage()
    users = data.setdefault("users", {})
    item = users.setdefault(user_name, {"user_name": user_name, "tokens": 0, "usd": 0.0})
    item["tokens"] = int(item.get("tokens") or 0) + tokens
    item["usd"] = round(float(item.get("usd") or 0) + usd, 6)
    item["updated_at"] = now_str()
    event = {
        "time": now_str(), "user_name": user_name, "kind": kind, "model": model,
        "tokens": tokens, "usd": usd, "project_id": project_id, "shot_id": shot_id, "tab_id": tab_id,
    }
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





def gpt_responses_search_mode(settings=None):
    settings = settings or {}
    mode = str(settings.get("gpt_responses_search") or GPT_RESPONSES_SEARCH_DEFAULT).strip().lower()
    if mode in ("off", "false", "0", "关闭", "none", "no"):
        return "off"
    if mode in ("required", "force", "强制", "always"):
        return "required"
    return "auto"


def should_gpt_responses_generate(user_text, explicit_mode="auto"):
    # V23：gpt生图改为「输入提示词直接出图」单步模式，不再有聊天模式，任何非空输入都直接生图。
    return True


def build_gpt_responses_instructions(kind, referenced_assets=None, operation="generate"):
    assets_text = "、".join([a.get("name", "") for a in (referenced_assets or []) if a])
    base = [
        "你是帝蓝工作流里的 gpt生图 模型，是一个接近 ChatGPT 的对话式生图助手。",
        "普通输入时，你只聊天、理解、追问和整理画面方案，不要生成图片。只有用户明确输入“开始生图”时，系统才会调用生图工具。",
        "回复要使用自然中文，优先帮助用户确认主体、场景、构图、镜头、风格、光线、材质、比例和限制条件。",
        "如果用户已经描述清楚，请整理成可执行的画面理解，并提示：确认后输入“开始生图”。",
        "如果启用了搜索工具，只有在用户需要真实资料、最新信息、官方信息、品牌/历史/地点参考时才搜索。",
    ]
    if assets_text:
        base.append("当前标签页引用素材：" + assets_text + "。这些素材只属于当前标签页，请在理解和生成时作为参考。")
    if kind == "image":
        if operation == "edit":
            base.append("本次用户已确认开始生图，并且提供了基准图/参考图。请基于图像继续修改，默认保留原图主体、风格、构图和角色关系，只改变用户明确要求改变的部分。")
        else:
            base.append("本次用户已确认开始生图。请根据上文对话和最新要求生成一张画面，不要输出分镜表格、页眉、页脚或非用户要求的文字。")
    return "\n".join(base)


# =========================================================================
# 火山 TOS：聊天参考图上传。TOS 配置与密钥同视频模块共用一份——配置读
# tools/video/config.json（在视频模块「全局设置」页维护），AK/SK 只读环境变量。
# 说明：OpenAI Responses 的 input_image 支持 https URL，启用 TOS 后聊天参考图
# 改走预签名 URL，把请求体里的大体积 base64 全部替换掉；gpt 生图(images/edits)
# 与 Gemini 的 API 不接受外链 URL，仍按官方要求直传文件字节，不经过 TOS。
# =========================================================================
VIDEO_TOOL_CONFIG_PATH = ROOT.parent / "video" / "config.json"


def is_http_url(url: str) -> bool:
    u = str(url or "").strip().lower()
    return u.startswith("http://") or u.startswith("https://")


def get_tos_settings():
    try:
        with open(VIDEO_TOOL_CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f) or {}
    except Exception:
        cfg = {}
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
        "ak": ak,
        "sk": sk,
    }


_TOS_CLIENT_CACHE = {}
_TOS_CLIENT_GUARD = threading.Lock()


def get_tos_client():
    s = get_tos_settings()
    if not s["enabled"]:
        raise RuntimeError("TOS 未启用：请在视频模块「全局设置」中开启 TOS。")
    if not s["bucket"]:
        raise RuntimeError("TOS bucket 未配置，请在视频模块「全局设置」中填写桶名。")
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
            raise RuntimeError("缺少火山 TOS Python SDK，请先安装依赖：pip install tos") from e
        try:
            client = tos.TosClientV2(s["ak"], s["sk"], s["endpoint"], s["region"])
        except Exception as e:
            raise RuntimeError(f"初始化 TOS 客户端失败：{e}") from e
        _TOS_CLIENT_CACHE[cache_key] = client
        return client


def upload_file_to_tos(fp: Path, key: str, content_type: str = "") -> dict:
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
    return {"provider": "tos", "bucket": bucket, "key": key, "content_type": mime, "size": fp.stat().st_size}


def tos_object_exists(bucket: str, key: str) -> bool:
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
    s = get_tos_settings()
    client = get_tos_client()
    expires = int(expires or s["presign_expire_seconds"])
    try:
        import tos
    except Exception as e:
        raise RuntimeError("缺少火山 TOS Python SDK，请先安装：pip install tos") from e
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
        raise RuntimeError(f"当前 TOS SDK 未找到匹配的 pre_signed_url 调用方式（{last_exc}）。")
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
    raise RuntimeError(f"TOS 预签名 URL 返回值无法解析：{type(result)}")


def ensure_local_file_remote_url(fp: Path) -> str:
    """内容寻址的 TOS 缓存：按 sha256 生成固定 key，同一内容只上传一次，之后只重签 URL。"""
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


def reference_image_url_for_responses(fp):
    """聊天参考图：启用 TOS 时优先预签名 URL（大幅缩小请求体）；未启用或上传失败回退 data:。"""
    try:
        if get_tos_settings()["enabled"]:
            return ensure_local_file_remote_url(Path(fp))
    except Exception as e:
        print("参考图上传 TOS 失败，回退内嵌 base64：", fp, e, flush=True)
    return local_to_data_url(fp)


def build_gpt_responses_history_input(thread, user_text, image_paths=None, prompt_override=None, document_file_ids=None):
    image_paths = image_paths or []
    document_file_ids = document_file_ids or []
    text = prompt_override or user_text or "请根据当前对话继续。"
    content = []
    for fid in document_file_ids[:MAX_DOCS_PER_MESSAGE]:
        if fid:
            content.append({"type": "input_file", "file_id": fid})
    content.append({"type": "input_text", "text": text})
    for fp in image_paths[:16]:
        try:
            content.append({"type": "input_image", "image_url": reference_image_url_for_responses(fp)})
        except Exception:
            pass
    return [{"role": "user", "content": content}]


def call_openai_responses(api_key, payload, timeout=300):
    if not api_key:
        raise ValueError("missing OpenAI API Key")
    req = urllib.request.Request(
        OPENAI_BASE + "/responses",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI Responses HTTP {e.code}: {body[:1200]}")
    except Exception as e:
        raise RuntimeError(f"OpenAI Responses request failed: {e}")


def extract_response_id(obj):
    return obj.get("id") if isinstance(obj, dict) else None


# --------------------------------------------------------------------------- #
# Image API 直连（gpt-image-2）：纯提示词 + 参考图生图，不经过聊天/编译器/文档（V23）
# --------------------------------------------------------------------------- #
GPT_IMAGE_API_MODEL = "gpt-image-2"

# gpt-image-2 合法输出尺寸（满足：边为 16 的倍数、长短比 ≤3:1、总像素 655360~8294400）。
# 把项目里的 分辨率档位 × 比例 映射到最接近的合法尺寸，避免 4K 等档位超过像素上限被 API 拒绝。
GPT_IMAGE2_SIZE_MAP = {
    "1K": {"1:1": "1024x1024", "4:3": "1024x768", "16:9": "1536x1024", "9:16": "1024x1536", "21:9": "1536x672"},
    "2K": {"1:1": "2048x2048", "4:3": "2048x1536", "16:9": "2048x1152", "9:16": "1152x2048", "21:9": "2560x1088"},
    "4K": {"1:1": "2048x2048", "4:3": "2048x1536", "16:9": "3840x2160", "9:16": "2160x3840", "21:9": "2560x1088"},
}


def gpt_image2_size(settings):
    res = str((settings or {}).get("image_resolution") or "1K")
    ratio = str((settings or {}).get("image_ratio") or "16:9")
    table = GPT_IMAGE2_SIZE_MAP.get(res) or GPT_IMAGE2_SIZE_MAP["1K"]
    return table.get(ratio) or GPT_IMAGE2_SIZE_MAP["1K"]["16:9"]


def gpt_image2_quality(settings):
    q = str((settings or {}).get("image_quality") or "medium").lower()
    return q if q in ("low", "medium", "high", "auto") else "medium"


def _image_api_extract_b64(obj):
    """从 Image API 响应中取第一张图的 base64。"""
    data = obj.get("data") if isinstance(obj, dict) else None
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict):
            return first.get("b64_json") or ""
    return ""


def _image_api_usage_tokens(obj, prompt, settings, image_paths):
    usage = obj.get("usage") if isinstance(obj, dict) else None
    if isinstance(usage, dict):
        t = usage.get("total_tokens") or usage.get("output_tokens")
        if t:
            return int(t)
    return estimate_image_tokens(prompt, settings, image_paths or [])


def call_image_api_generate(api_key, prompt, settings):
    """无参考图：POST /v1/images/generations。返回 (image_bytes, tokens)。"""
    payload = {
        "model": GPT_IMAGE_API_MODEL,
        "prompt": prompt or "",
        "size": gpt_image2_size(settings),
        "quality": gpt_image2_quality(settings),
        "n": 1,
    }
    req = urllib.request.Request(
        OPENAI_BASE + "/images/generations",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=420) as resp:
            obj = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Image API 生图失败 HTTP {e.code}: {detail[:800]}")
    b64 = _image_api_extract_b64(obj)
    if not b64:
        raise RuntimeError("Image API 未返回图片：" + json.dumps(obj, ensure_ascii=False)[:500])
    return base64.b64decode(b64), _image_api_usage_tokens(obj, prompt, settings, None)


def call_image_api_edit(api_key, prompt, settings, image_paths):
    """带参考图：POST /v1/images/edits（multipart）。image_paths 作为参考图生成新图。
    返回 (image_bytes, tokens)。"""
    boundary = "----DilanImgEdit" + uuid.uuid4().hex
    parts = []

    def add_field(name, value):
        parts.append(("--" + boundary + "\r\n").encode("utf-8"))
        parts.append((f'Content-Disposition: form-data; name="{name}"\r\n\r\n').encode("utf-8"))
        parts.append((str(value) + "\r\n").encode("utf-8"))

    add_field("model", GPT_IMAGE_API_MODEL)
    add_field("prompt", prompt or "")
    add_field("size", gpt_image2_size(settings))
    add_field("quality", gpt_image2_quality(settings))
    add_field("n", "1")
    for idx, fp in enumerate(image_paths[:16]):
        try:
            raw = Path(fp).read_bytes()
        except Exception:
            continue
        mime = mimetypes.guess_type(str(fp))[0] or "image/png"
        fname = Path(fp).name or f"ref{idx}.png"
        parts.append(("--" + boundary + "\r\n").encode("utf-8"))
        parts.append((f'Content-Disposition: form-data; name="image[]"; filename="{fname}"\r\n').encode("utf-8"))
        parts.append((f"Content-Type: {mime}\r\n\r\n").encode("utf-8"))
        parts.append(raw)
        parts.append("\r\n".encode("utf-8"))
    parts.append(("--" + boundary + "--\r\n").encode("utf-8"))
    body = b"".join(parts)
    req = urllib.request.Request(
        OPENAI_BASE + "/images/edits",
        data=body,
        method="POST",
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            obj = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Image API 编辑生图失败 HTTP {e.code}: {detail[:800]}")
    b64 = _image_api_extract_b64(obj)
    if not b64:
        raise RuntimeError("Image API 未返回图片：" + json.dumps(obj, ensure_ascii=False)[:500])
    return base64.b64decode(b64), _image_api_usage_tokens(obj, prompt, settings, image_paths)


def call_image_api(api_key, prompt, settings, image_paths=None):
    """统一入口：有参考图走 edits，无参考图走 generations。返回 (image_bytes, tokens)。"""
    image_paths = [p for p in (image_paths or []) if p]
    if image_paths:
        return call_image_api_edit(api_key, prompt, settings, image_paths)
    return call_image_api_generate(api_key, prompt, settings)



def extract_image_generation_result(obj):
    if not isinstance(obj, dict):
        return None, None
    for item in obj.get("output", []) or []:
        if isinstance(item, dict) and item.get("type") == "image_generation_call":
            result = item.get("result")
            if isinstance(result, str) and result:
                return result, item.get("id")
    b64 = extract_b64_image(obj)
    return b64, None


def call_gpt_responses_chat(api_key, thread, user_text, settings=None, referenced_assets=None, document_files=None):
    settings = settings or {}
    document_files = document_files or []
    state = thread.get("responses_state") or {}
    tools = []
    search_mode = gpt_responses_search_mode(settings)
    if search_mode != "off":
        tools.append({"type": "web_search", "search_context_size": "low"})
    instructions = build_gpt_responses_instructions("chat", referenced_assets)
    if document_files:
        doc_list = "；".join([f"{i + 1}. {f.get('name')}" for i, f in enumerate(document_files)])
        instructions += "\n本次消息附带文档：" + doc_list + "。文档已作为文件输入随本条消息提供，请完整阅读其内容并据此回答；引用文档信息时尽量注明来自哪份文档。"
    payload = {
        "model": GPT_RESPONSES_MODEL,
        "instructions": instructions,
        "input": build_gpt_responses_history_input(thread, user_text, document_file_ids=[f.get("file_id") for f in document_files]),
        "tools": tools,
        "tool_choice": "auto" if search_mode == "auto" else ("required" if search_mode == "required" else "auto"),
        "store": True,
    }
    if state.get("response_id"):
        payload["previous_response_id"] = state.get("response_id")
    obj = call_openai_responses(api_key, payload, timeout=300)
    text = extract_text_response(obj)
    if not text:
        text = "我理解了。你可以继续补充画面细节；确认后输入“开始生图”。"
    tokens = extract_usage_tokens(obj) or approx_text_tokens(json.dumps(payload, ensure_ascii=False) + text)
    return text, tokens, extract_response_id(obj)


def call_gpt_responses_image(api_key, thread, prompt, settings, operation="generate", image_paths=None, referenced_assets=None):
    """Isolated final image execution call.

    The image model does NOT receive previous_response_id, chat history, context_summary,
    web search tools, or any prior discussion. It only receives final_prompt + ordered images.
    """
    settings = settings or {}
    image_paths = image_paths or []
    size = ratio_size(settings.get("image_ratio", "16:9"), settings.get("image_resolution", "1K"))
    image_tool = {
        "type": "image_generation",
        "action": "edit" if image_paths else "generate",
        "quality": quality_for_api(settings.get("image_quality", "medium")),
        "size": size,
        "output_format": "png",
    }
    payload = {
        "model": GPT_RESPONSES_MODEL,
        "instructions": "你是帝蓝工作流的最终生图执行模型。你只能依据本次输入的最终提示词和按顺序提供的图片执行，不得读取、延续、猜测或引用任何聊天历史。必须严格遵守图一、图二、图三的映射关系。",
        "input": build_gpt_responses_history_input({}, prompt, image_paths=image_paths),
        "tools": [image_tool],
        "tool_choice": {"type": "image_generation"},
        "store": False,
    }
    try:
        obj = call_openai_responses(api_key, payload, timeout=420)
    except RuntimeError as e:
        # 某些 API 变体不接受强制 image_generation 的 tool_choice，回退为 auto 重试一次
        if "tool_choice" in str(e) or "HTTP 400" in str(e):
            payload["tool_choice"] = "auto"
            obj = call_openai_responses(api_key, payload, timeout=420)
        else:
            raise
    b64, call_id = extract_image_generation_result(obj)
    if not b64:
        # auto 模式下模型可能只回了文字（例如要求补充信息）。强制再试一次，确保出图。
        text = extract_text_response(obj)
        if payload.get("tool_choice") != {"type": "image_generation"}:
            payload["tool_choice"] = {"type": "image_generation"}
            try:
                obj = call_openai_responses(api_key, payload, timeout=420)
                b64, call_id = extract_image_generation_result(obj)
            except RuntimeError:
                pass
        if not b64:
            hint = "。生图模型未返回图片"
            if text:
                hint += "，模型回复：" + text[:300]
            raise RuntimeError("生图失败" + hint + "。请确认提示词是修改/生成图片的指令，或在输入框直接写清要求后再点开始生图。")
    tokens = extract_usage_tokens(obj) or estimate_image_tokens(prompt, settings, image_paths)
    return base64.b64decode(b64), tokens, None, call_id



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
        effective = select_effective_generation_text(thread, user_text) or user_text or ""
        edit_words = ["改", "修改", "换", "保持", "不变", "更", "远一点", "近一点", "背景", "视角", "构图", "颜色", "去掉", "消除", "删除", "去除", "加上", "添加", "放大", "缩小", "局部", "重绘", "调整"]
        if any(w in effective for w in edit_words):
            return "edit", current
        if is_only_generation_command(str(user_text or "")) and effective and effective != user_text:
            return "edit", current
    return "generate", None




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


def export_arcname_for_public_file(fp: Path):
    fp = fp.resolve()
    try:
        rel = fp.relative_to(OUTPUT_IMAGE_DIR.resolve()).as_posix()
        return "output/image/" + rel
    except Exception:
        return root_relative_arcname(fp)


def add_output_public_files(file_map, base_dir: Path, role: str):
    if not base_dir.exists() or not base_dir.is_dir():
        return
    for fp in sorted(base_dir.rglob("*")):
        if not fp.is_file():
            continue
        arc = export_arcname_for_public_file(fp)
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
            "hidden_deleted_or_unassigned_usage 保存已删除 SC 或无法映射费用事件，不默认外显，但用于后期审核端查账。",
            "费用来自制作端 usage.json 记录；图片接口无官方 usage 时使用工具内估算规则。",
        ],
    }


def collect_project_export_files(project_data):
    pid = project_data.get("project_id")
    storage = safe_name(pid, "project")
    file_map = {}
    # Main project-related directories. Keep original relative paths so future import can restore easily.
    add_dir_files(file_map, project_path(pid) / "temp_refs", "temp_refs")
    add_dir_files(file_map, project_path(pid) / "review_notes", "review_notes")
    add_dir_files(file_map, INPUT_DIR / storage, "input_assets")
    add_output_public_files(file_map, OUTPUT_IMAGE_DIR / storage, "output_images")

    # Also include any directly referenced image file, even if it is outside the normal project folders.
    refs = []
    for asset in project_data.get("assets", []) or []:
        refs.append((asset.get("file_path"), "referenced_asset"))
    for scene in project_data.get("scenes", []) or []:
        for shot in scene.get("shots", []) or []:
            for c in shot.get("storyboard_candidates", []) or []:
                refs.append((c.get("file_path"), "storyboard_candidate"))
            for note in (shot.get("review_notes_by_round") or {}).values():
                if isinstance(note, dict):
                    for img in note.get("images", []) or []:
                        refs.append((img.get("file_path"), "review_note_image"))
            for tab in shot.get("tabs", []) or []:
                for img in tab.get("generated_images", []) or []:
                    refs.append((img.get("file_path"), "generated_image"))
    missing = []
    for public_path, role in refs:
        if not public_path:
            continue
        fp = public_to_local(public_path)
        if fp and fp.exists() and fp.is_file():
            file_map.setdefault(export_arcname_for_public_file(fp), {"path": fp, "role": role})
        else:
            missing.append({"path": public_path, "role": role, "reason": "file_not_found"})
    return file_map, missing


def count_project_items(project_data):
    scenes = project_data.get("scenes", []) or []
    shot_count = 0
    tab_count = 0
    generated_image_count = 0
    storyboard_candidate_count = 0
    review_note_image_count = 0
    for scene in scenes:
        for shot in scene.get("shots", []) or []:
            shot_count += 1
            storyboard_candidate_count += len(shot.get("storyboard_candidates", []) or [])
            for note in (shot.get("review_notes_by_round") or {}).values():
                if isinstance(note, dict):
                    review_note_image_count += len(note.get("images", []) or [])
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
        "review_note_image_count": review_note_image_count,
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
        "temp_refs_root": root_relative_arcname(project_path(pid) / "temp_refs"),
        "review_notes_root": root_relative_arcname(project_path(pid) / "review_notes"),
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
        if is_document_asset(a):
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
                    # 需求1：导出素材包图片文件名按「父栏-子栏」(命名框-子栏) 命名；多张待选图保留 (n) 后缀。
                    base_name = safe_file_name(f'{scene.get("scene_code") or scene.get("name") or ("未命名" + cat)}-{shot.get("shot_code") or shot.get("name") or "素材"}', "素材")
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
    pid = safe_name(project_id or "", "")
    if not pid:
        raise ValueError("missing project")
    raw = decode_data_url_bytes(data_url)
    if len(raw) < 4 or not raw.startswith(b"PK"):
        raise ValueError("请选择本工具导出的 .zip 素材包")
    data = load_project(pid)
    ensure_asset_groups(data)
    imported_at = now_str()
    imported = []
    renamed = []
    skipped = []
    created_groups = 0
    merged_groups = 0
    with zipfile.ZipFile(io.BytesIO(raw), "r") as zf:
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
    allowed_roots = [PROJECTS_DIR.resolve(), INPUT_DIR.resolve(), OUTPUT_IMAGE_DIR.resolve(), IMPORTS_DIR.resolve()]
    if not any(dest == root or root in dest.parents for root in allowed_roots):
        raise ValueError(f"导入目标路径不安全：{dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zf.open(info, "r") as src, dest.open("wb") as out:
        shutil.copyfileobj(src, out)




def _all_review_holders(data):
    """返回所有可能挂有 review_notes_by_round 的对象（场景/命名框 + 其下子栏），兼容美术三套工作台。"""
    holders = []
    wss = data.get("material_workspaces") if isinstance(data.get("material_workspaces"), dict) else {}
    scenes = []
    for key in ("character", "scene", "object"):
        ws = wss.get(key) or {}
        for s in (ws.get("scenes") or []):
            scenes.append(s)
    for s in (data.get("scenes") or []):
        scenes.append(s)
    for s in scenes:
        holders.append(s)
        for sh in (s.get("shots") or []):
            holders.append(sh)
    return holders


def _note_round_span(holder):
    """返回该对象涉及的轮次列表 1..N（综合 review_notes_by_round 与 review.rounds / current_round）。"""
    keys = set()
    rnv = holder.get("review_notes_by_round")
    if isinstance(rnv, dict):
        for k in rnv.keys():
            try: keys.add(int(k))
            except Exception: pass
    rv = holder.get("review")
    if isinstance(rv, dict):
        try: keys.add(int(rv.get("current_round") or 1))
        except Exception: pass
        rounds = rv.get("rounds")
        if isinstance(rounds, dict):
            for k in rounds.keys():
                try: keys.add(int(k))
                except Exception: pass
    if not keys:
        keys = {1}
    return list(range(1, max(keys) + 1))


def _ensure_note_slot(holder, r):
    nbr = holder.setdefault("review_notes_by_round", {})
    slot = nbr.get(str(r))
    if not isinstance(slot, dict):
        slot = {"text": "", "images": [], "active_index": 0}
        nbr[str(r)] = slot
    slot.setdefault("text", "")
    slot.setdefault("images", [])
    slot.setdefault("active_index", 0)
    return slot


def _review_round_note_text(holder, r):
    rv = holder.get("review")
    if isinstance(rv, dict):
        rounds = rv.get("rounds")
        if isinstance(rounds, dict):
            rd = rounds.get(str(r))
            if isinstance(rd, dict) and rd.get("note"):
                return rd.get("note")
    return ""


def _norm_note_image(im):
    if not isinstance(im, dict):
        return None
    out = dict(im)
    if not out.get("file_path") and out.get("path"):
        out["file_path"] = out["path"]
    if not out.get("file_path"):
        return None
    out.setdefault("note_image_id", new_id("noteimg"))
    out.setdefault("name", Path(str(out.get("file_path"))).stem or "审核备注图")
    return out


def reconcile_review_notes_for_import(data, from_review):
    """统一审核备注，逐轮独立、互不继承：
      1) 每个轮次文字字段归一：review.rounds[r].note -> review_notes_by_round[r].text；
      2) from_review=True（来自统一审核端的包）时，把 SC/命名框(scene)层每个轮次的备注
         镜像到其各子栏(shot)的同一轮次，仅当子栏该轮无备注时填充（不覆盖、不跨轮继承），
         使制作端按 shot 逐轮读取时都能看到。幂等。"""
    scenes = []
    wss = data.get("material_workspaces") if isinstance(data.get("material_workspaces"), dict) else {}
    for key in ("character", "scene", "object"):
        for s in (wss.get(key) or {}).get("scenes") or []:
            scenes.append(s)
    for s in (data.get("scenes") or []):
        scenes.append(s)

    def rounds_present(holder):
        ks = set()
        nbr = holder.get("review_notes_by_round")
        if isinstance(nbr, dict):
            for k in nbr.keys():
                ks.add(str(k))
        rv = holder.get("review")
        if isinstance(rv, dict) and isinstance(rv.get("rounds"), dict):
            for k in rv["rounds"].keys():
                ks.add(str(k))
        return ks

    def unify(holder):
        for r in rounds_present(holder):
            slot = _ensure_note_slot(holder, r)
            if not slot.get("text"):
                t = _review_round_note_text(holder, r)
                if t:
                    slot["text"] = t
            slot["images"] = [x for x in (_norm_note_image(im) for im in slot.get("images") or []) if x]

    for sc in scenes:
        unify(sc)
        for sh in (sc.get("shots") or []):
            unify(sh)
            if from_review:
                for r in rounds_present(sc):
                    scslot = _ensure_note_slot(sc, r)
                    shslot = _ensure_note_slot(sh, r)
                    if not shslot.get("text") and not shslot.get("images"):
                        if scslot.get("text"):
                            shslot["text"] = scslot["text"]
                        if scslot.get("images"):
                            shslot["images"] = [dict(im) for im in scslot["images"]]
                        shslot["active_index"] = 0


def normalize_review_note_paths(data, new_pid):
    """把审核备注图的 file_path 统一改写为本端可服务的 /temp/<项目>/review_notes/<尾巴> 形式，
    兼容审核端扁平路径(review_notes/xxx)与制作端原有的 /temp/.../review_notes/... 形式，
    保证审核端→数据中心→制作端下载后备注图可正常显示、两端审核备注互通。"""
    prefix = project_temp_public_prefix(new_pid)
    for holder in _all_review_holders(data):
        nbr = holder.get("review_notes_by_round")
        if not isinstance(nbr, dict):
            continue
        for slot in nbr.values():
            if not isinstance(slot, dict):
                continue
            for im in (slot.get("images") or []):
                if not isinstance(im, dict):
                    continue
                fp = str(im.get("file_path") or im.get("path") or "")
                if not fp:
                    continue
                if "review_notes/" in fp:
                    tail = fp.split("review_notes/")[-1].split("?")[0].lstrip("/")
                else:
                    tail = Path(fp).name
                if tail:
                    im["file_path"] = f"{prefix}/review_notes/{tail}"


def compact_review_round_snapshots(project_data):
    """Strip heavy duplicated review round shot snapshots from imported reviewed packages.
    覆盖顶层 scenes 与美术 material_workspaces 下的 scenes，避免制作端切换审核轮次时
    用旧的 round 快照整体覆盖掉 scene.shots（含审核端后加的逐轮备注/备注图）。
    Review results/times stay in review.rounds; current scene.shots remains the editable/display copy.
    """
    removed = 0
    scenes = []
    wss = project_data.get("material_workspaces") if isinstance(project_data.get("material_workspaces"), dict) else {}
    for key in ("character", "scene", "object"):
        for s in (wss.get(key) or {}).get("scenes") or []:
            scenes.append(s)
    for s in project_data.get("scenes", []) or []:
        scenes.append(s)
    for scene in scenes:
        review = scene.get("review")
        if not isinstance(review, dict):
            continue
        rounds = review.get("rounds")
        if not isinstance(rounds, dict):
            continue
        for r in rounds.values():
            if isinstance(r, dict) and isinstance(r.get("shots"), list):
                removed += len(r.get("shots") or [])
                r.pop("shots", None)
                r["shots_snapshot_compacted"] = True
    if removed:
        project_data.setdefault("import_info", {})
        project_data["import_info"]["review_round_shots_compacted"] = True
        project_data["import_info"]["compacted_review_shot_count"] = removed
    return project_data
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


def import_project_package_into_module(module_type, data_url, filename="", importer_name=""):
    module_type = str(module_type or "").strip()
    if module_type == PROJECT_MODULE_TYPE:
        raise ValueError("工程包已经属于当前模块，无需转发导入")
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
    return mod.import_project_package(data_url, filename, importer_name, "")


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
    raw = decode_data_url_bytes(data_url)
    if len(raw) < 4 or not raw.startswith(b"PK"):
        raise ValueError("请选择由本工具导出的 .zip 工程包")
    imported_at = now_str()
    with zipfile.ZipFile(io.BytesIO(raw), "r") as zf:
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
            return import_project_package_into_module(module_type, data_url, filename, importer_name)
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
        output_prefixes = [
            f"output/image/{old_storage}/",      # public URL / older packages
            f"output/material/{old_storage}/",   # V18-V20 material package arcname
        ]
        temp_prefix = f"projects/{old_parent}/children/{old_child}/temp_refs/"
        review_prefix = f"projects/{old_parent}/children/{old_child}/review_notes/"
        copied_files = 0
        for arc, info in members:
            if arc in {"manifest.json", "export_info.json", "usage_summary.json", project_json_arc, parent_json_arc}:
                continue
            if arc.startswith(input_prefix):
                rel = arc[len(input_prefix):]
                write_zip_member_to(zf, info, INPUT_DIR / new_storage / rel)
                copied_files += 1
            elif any(arc.startswith(prefix) for prefix in output_prefixes):
                prefix = next(prefix for prefix in output_prefixes if arc.startswith(prefix))
                rel = arc[len(prefix):]
                write_zip_member_to(zf, info, OUTPUT_IMAGE_DIR / new_storage / rel)
                copied_files += 1
            elif arc.startswith(temp_prefix):
                rel = arc[len(temp_prefix):]
                write_zip_member_to(zf, info, project_path(new_pid) / "temp_refs" / rel)
                copied_files += 1
            elif arc.startswith(review_prefix):
                rel = arc[len(review_prefix):]
                write_zip_member_to(zf, info, project_path(new_pid) / "review_notes" / rel)
                copied_files += 1
            elif arc.startswith("review_notes/") or "/review_notes/" in arc:
                # 兼容审核端扁平形式(review_notes/xxx)及其它来源；按最后一个 review_notes/ 之后归位
                rel = arc.split("review_notes/")[-1].lstrip("/")
                if rel:
                    write_zip_member_to(zf, info, project_path(new_pid) / "review_notes" / rel)
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

        reconcile_review_notes_for_import(project_data, from_review=bool((manifest or {}).get("reviewed_by_unified_client")))
        normalize_review_note_paths(project_data, new_pid)
        compact_review_round_snapshots(project_data)
        normalize_project(project_data, rename_dirs=False)
        write_json_file(project_json_path(new_pid), project_data)
        return {
            "data": project_data,
            "project": project_meta(new_pid),
            "manifest": manifest,
            "copied_files": copied_files,
            "imported_as_copy": new_pid != old_pid,
        }


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
        data = fp.read_bytes()
        self.send_response(200)
        self._headers(mime or mimetypes.guess_type(str(fp))[0] or "application/octet-stream")
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

    def do_GET(self):
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        qs = parse_qs(parsed.query)
        try:
            if path in ("/", "/index.html"):
                self.send_file(ROOT / "index.html", "text/html; charset=utf-8")
            elif path == "/api/config":
                self.send_json(200, load_config())
            elif path == "/api/datacenter/config":
                self.api_datacenter_config_get()
            elif path == "/api/datacenter/projects":
                self.api_datacenter_projects()
            elif path.startswith("/api/datacenter/preview/"):
                self.api_datacenter_preview(path.rsplit("/", 1)[-1])
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
            elif path == "/api/project/merge":
                self.api_project_merge()
            elif path == "/api/datacenter/config/save":
                self.api_datacenter_config_save()
            elif path == "/api/datacenter/upload":
                self.api_datacenter_upload()
            elif path == "/api/datacenter/resolve-conflict":
                self.api_datacenter_resolve_conflict()
            elif path == "/api/datacenter/download-child":
                self.api_datacenter_download_child()
            elif path == "/api/datacenter/download-parent":
                self.api_datacenter_download_parent()
            elif path == "/api/datacenter/merge":
                self.api_datacenter_merge()
            elif path == "/api/datacenter/delete-child":
                self.api_datacenter_delete_child()
            elif path == "/api/assets/package/import":
                self.api_asset_package_import()
            elif path == "/api/review/note_image_upload":
                self.api_review_note_image_upload()
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
            elif path == "/api/image/generate":
                self.api_image_generate()
            elif path == "/api/image/chat":
                self.api_image_generate()
            elif path == "/api/image/delete":
                self.api_image_delete()
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
        data = load_project(pid)
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

    def api_project_import(self):
        body = self.read_body()
        result = import_project_package(body.get("dataUrl") or "", body.get("filename") or "", body.get("user_name") or "", body.get("target_parent") or body.get("parent") or "")
        self.send_json(200, {"ok": True, **result})

    # ---- 数据中心接入（V44） ----
    def _dc_shared_dir(self):
        return SHARED_PROJECT_INDEX_DIR

    def api_datacenter_preview(self, child_id):
        try:
            raw = dcc.preview_bytes(self._dc_shared_dir(), child_id)
            ext = "png"
            ctype = "image/png"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)
        except Exception as e:
            self.send_json(404, {"error": str(e)})

    def api_datacenter_config_get(self):
        self.send_json(200, dcc.load_dc_config(self._dc_shared_dir()))

    def api_datacenter_config_save(self):
        body = self.read_body()
        self.send_json(200, dcc.save_dc_config(self._dc_shared_dir(), body.get("url") or ""))

    def api_datacenter_projects(self):
        try:
            self.send_json(200, dcc.list_projects(self._dc_shared_dir()))
        except Exception as e:
            self.send_json(502, {"error": str(e)})

    def api_datacenter_upload(self):
        body = self.read_body()
        pid = safe_name(body.get("project") or "")
        user_name = normalize_user_name(body.get("user_name") or "未命名用户")
        on_conflict = body.get("on_conflict") or "ask"
        if not pid:
            self.send_json(400, {"error": "missing project"}); return
        try:
            zip_path, manifest = create_project_export_zip(pid, user_name)
            zip_bytes = Path(zip_path).read_bytes()
            meta = {
                "package_id": manifest.get("source_project_id") or pid,
                "parent_id": manifest.get("parent_id") or "",
                "parent_name": manifest.get("parent_name") or "",
                "module_type": manifest.get("module_type") or PROJECT_MODULE_TYPE,
                "module_label": manifest.get("module_label") or PROJECT_MODULE_LABEL,
                "child_id": manifest.get("child_id") or "",
                "child_name": manifest.get("child_name") or manifest.get("project_name") or pid,
                "base_child_name": manifest.get("child_name") or manifest.get("project_name") or pid,
                "generator_name": user_name,
                "client_type": "creator",
            }
            result = dcc.upload_package(self._dc_shared_dir(), zip_bytes, Path(zip_path).name, meta, on_conflict=on_conflict)
            self.send_json(200, result)
        except Exception as e:
            self.send_json(502, {"error": str(e)})

    def api_datacenter_resolve_conflict(self):
        body = self.read_body()
        try:
            self.send_json(200, dcc.resolve_conflict(self._dc_shared_dir(), body.get("token") or "", body.get("action") or "new_version"))
        except Exception as e:
            self.send_json(502, {"error": str(e)})

    def api_datacenter_download_child(self):
        body = self.read_body()
        cid = body.get("child_id") or ""
        if not cid:
            self.send_json(400, {"error": "missing child_id"}); return
        try:
            raw = dcc.download_child_bytes(self._dc_shared_dir(), cid)
            self.send_json(200, {"ok": True, "dataUrl": dcc.bytes_to_data_url(raw)})
        except Exception as e:
            self.send_json(502, {"error": str(e)})

    def api_datacenter_delete_child(self):
        body = self.read_body()
        cid = body.get("child_id") or ""
        if not cid:
            self.send_json(400, {"error": "missing child_id"}); return
        try:
            self.send_json(200, dcc.delete_child(self._dc_shared_dir(), cid))
        except Exception as e:
            self.send_json(502, {"error": str(e)})

    def api_datacenter_merge(self):
        body = self.read_body()
        ids = body.get("child_ids") or body.get("children") or []
        if isinstance(ids, str): ids = [ids]
        try:
            self.send_json(200, dcc.merge_packages(self._dc_shared_dir(), ids, body.get("target_name") or "", body.get("generator_name") or ""))
        except Exception as e:
            self.send_json(502, {"error": str(e)})

    def api_datacenter_download_parent(self):
        body = self.read_body()
        pid = body.get("parent_id") or ""
        if not pid:
            self.send_json(400, {"error": "missing parent_id"}); return
        try:
            self.send_json(200, {"ok": True, **dcc.download_parent_children(self._dc_shared_dir(), pid)})
        except Exception as e:
            self.send_json(502, {"error": str(e)})

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
        for p in (project_path(pid), INPUT_DIR / safe_name(pid), OUTPUT_IMAGE_DIR / safe_name(pid)):
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
        data = load_project(pid)
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
        data = load_project(pid)
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
        data = load_project(pid)
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
        data = load_project(pid)
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
        data = load_project(pid)
        upload_filename = body.get("filename") or ""
        blocked_ext = Path(str(upload_filename or "")).suffix.lower()
        if blocked_ext in {".exe", ".zip", ".rar", ".7z", ".bat", ".cmd", ".sh", ".js", ".msi", ".dll", ".scr", ".ps1", ".vbs"}:
            self.send_json(400, {"error": f"不支持上传 {blocked_ext} 文件"}); return
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
        data.setdefault("assets", []).append(asset)
        save_project(pid, data)
        append_operation_log(pid, "asset_upload", category=cat, asset_id=asset.get("asset_id"), asset_name=asset.get("name"))
        self.send_json(200, {"ok": True, "asset": asset, "data": data})

    def api_asset_delete(self):
        body = self.read_body()
        pid = safe_name(body.get("project") or "")
        asset_id = body.get("asset_id") or ""
        data = load_project(pid)
        remove_assets_by_ids(data, [asset_id])
        save_project(pid, data)
        append_operation_log(pid, "project_operation", stats=project_stats(data))
        self.send_json(200, {"ok": True, "data": data})

    def api_asset_rename(self):
        body = self.read_body()
        pid = safe_name(body.get("project") or "")
        asset_id = body.get("asset_id") or ""
        new_name = safe_file_name(body.get("new_name") or "", "素材")
        data = load_project(pid)
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
        data = load_project(pid)
        ensure_asset_groups(data)
        group = None
        if cat in GROUPED_CATEGORIES:
            group = get_asset_group_by_id(data, group_id)
            if not group or group.get("category") != cat:
                self.send_json(400, {"error": "请先选择有效的素材组"}); return
        out_dir = INPUT_DIR / safe_name(pid) / cat
        out_dir.mkdir(parents=True, exist_ok=True)
        blocked_docs = []
        for aid in ids:
            a = get_asset_by_id(data, aid)
            if not a or a.get("temporary"):
                continue
            if is_document_asset(a) and cat != DOCUMENT_ASSET_CATEGORY:
                blocked_docs.append(a.get("name") or aid)
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
        resp = {"ok": True, "data": data}
        if blocked_docs:
            resp["blocked_documents"] = blocked_docs
            resp["notice"] = "文档素材只能留在「道具」类目，已跳过：" + "、".join(blocked_docs)
        self.send_json(200, resp)

    def api_assets_bulk_delete(self):
        body = self.read_body()
        pid = safe_name(body.get("project") or "")
        ids = set(body.get("asset_ids") or [])
        data = load_project(pid)
        remove_assets_by_ids(data, ids)
        save_project(pid, data)
        self.send_json(200, {"ok": True, "data": data})

    def api_temp_upload(self):
        body = self.read_body()
        pid = safe_name(body.get("project") or "")
        shot_id = body.get("shot_id") or ""
        tab_id = body.get("tab_id") or None
        data = load_project(pid)
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
        data = load_project(pid)
        next_num = max_scene_number(data.get("scenes", [])) + 1
        s = make_scene(len(data.get("scenes", [])) + 1, next_num)
        s["shots"].append(make_shot(1, data.get("project_settings")))
        data.setdefault("scenes", []).append(s)
        save_project(pid, data)
        self.send_json(200, {"ok": True, "data": data})

    def api_scene_insert(self):
        body = self.read_body(); pid = safe_name(body.get("project") or "")
        data = load_project(pid)
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
            self.send_json(400, {"error": "命名框编号只能填写大于 0 的数字"}); return
        data = load_project(pid)
        target = None
        for scene in data.get("scenes", []):
            if scene.get("scene_id") == scene_id:
                target = scene
            elif scene_number_from_scene(scene, None) == new_number:
                self.send_json(400, {"error": f"命名框-{new_number:04d} 已存在，不能重复"}); return
        if not target:
            self.send_json(404, {"error": "scene not found"}); return
        target["scene_number"] = new_number
        target["scene_code"] = scene_code_from_number(new_number)
        save_project(pid, data)
        self.send_json(200, {"ok": True, "data": data})

    def api_scene_delete(self):
        body = self.read_body(); pid = safe_name(body.get("project") or "")
        scene_id = body.get("scene_id") or ""
        data = load_project(pid)
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
        data = load_project(pid)
        scene = find_scene(data, scene_id)
        scene.setdefault("shots", []).append(make_shot(len(scene.get("shots", [])) + 1, data.get("project_settings")))
        save_project(pid, data)
        self.send_json(200, {"ok": True, "data": data})

    def api_shot_insert(self):
        body = self.read_body(); pid = safe_name(body.get("project") or "")
        scene_id = body.get("scene_id") or ""; after_id = body.get("after_shot_id")
        data = load_project(pid); scene = find_scene(data, scene_id)
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
        data = load_project(pid)
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
        data = load_project(pid); scene = find_scene(data, scene_id)
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
        gemini_api_key = body.get("gemini_api_key") or body.get("geminiApiKey") or ""
        selected_base_id = body.get("base_image_id") or None
        explicit = body.get("mode") or "auto"
        data = load_project(pid)
        scene, shot, tab = find_tab(data, shot_id, tab_id)
        redo_id = body.get("redo_message_id") or ""
        settings = tab.get("settings") or data.get("project_settings") or project_default_settings(load_config()["global_defaults"])
        # 只保留 gpt生图：普通输入默认聊天；只有明确输入“开始生图”等指令才生成。
        if should_gpt_responses_generate(user_text, explicit):
            return self._perform_gpt_responses_generate(pid, data, scene, shot, tab, user_text, api_key, explicit, selected_base_id, user_name, redo_id, gemini_api_key=gemini_api_key)
        return self._perform_gpt_responses_chat(pid, data, scene, shot, tab, user_text, api_key, user_name, redo_id)

    def _perform_gpt_responses_chat(self, pid, data, scene, shot, tab, user_text, api_key, user_name, redo_id=""):
        settings = tab.get("settings") or data.get("project_settings") or project_default_settings(load_config()["global_defaults"])
        tab_assets = []
        for aid in tab.get("referenced_assets", []):
            asset = get_asset_by_id(data, aid)
            if asset:
                tab_assets.append(asset)
        names = referenced_aliases(user_text, data, tab_assets)
        referenced_assets = []
        missing_mentions = []
        mentioned_doc_assets = []
        for name in names:
            asset = next((a for a in tab_assets if str(a.get("name", "")).lower() == str(name).lower()), None)
            if asset:
                referenced_assets.append(asset)
                if is_document_asset(asset):
                    mentioned_doc_assets.append(asset)
            else:
                missing_mentions.append(name)
        if missing_mentions:
            raise ValueError("、".join([f"@{x}" for x in missing_mentions]) + " 不在当前标签页引用素材中，请先把它加入当前标签页引用素材后再使用。")
        if len(mentioned_doc_assets) > MAX_DOCS_PER_MESSAGE:
            raise ValueError(f"单次消息最多附带 {MAX_DOCS_PER_MESSAGE} 个文档，请减少 @文档 数量。")
        for asset in tab_assets:
            if asset not in referenced_assets:
                referenced_assets.append(asset)
        try:
            tokens = 0
            response_id = None
            if api_key:
                if mentioned_doc_assets:
                    def _run_chat(files):
                        return call_gpt_responses_chat(api_key, tab, user_text, settings, referenced_assets, document_files=files)
                    reply, tokens, response_id = run_with_document_retry(api_key, pid, data, mentioned_doc_assets, _run_chat)
                else:
                    reply, tokens, response_id = call_gpt_responses_chat(api_key, tab, user_text, settings, referenced_assets)
            else:
                assets_text = "、".join([a.get("name", "") for a in referenced_assets])
                reply = "这是 gpt生图 的聊天模式。"
                if assets_text:
                    reply += f" 当前已引用素材：{assets_text}。"
                if mentioned_doc_assets:
                    reply += " 本次 @ 了文档：" + "、".join([a.get("name", "") for a in mentioned_doc_assets]) + "（mock 模式不读取文档内容，填入 API Key 后生效）。"
                reply += "你可以继续补充画面需求；确认后输入“开始生图”。"
            _lock = project_mutation_lock(pid)
            _lock.acquire()
            try:
                latest = load_project(pid)
                scene2, shot2, tab2, user_msg_id = apply_user_message_for_submit(latest, shot.get("shot_id"), tab.get("tab_id"), user_text, redo_id)
                tab2["draft_prompt"] = user_text
                state = tab2.setdefault("responses_state", {})
                state["model"] = GPT_RESPONSES_MODEL
                state["search_mode"] = gpt_responses_search_mode(settings)
                state["last_plan"] = reply
                state["ready_to_generate"] = True
                if response_id:
                    state["response_id"] = response_id
                tab2.setdefault("messages", []).append({"message_id": new_id("msg"), "role": "assistant", "content": reply, "time": now_str(), "kind": "gpt_chat", "reply_to_message_id": user_msg_id})
                update_context_summary(tab2, user_text)
                usage = record_usage(user_name, "chat", GPT_RESPONSES_MODEL, tokens, pid, shot2.get("shot_id"), tab2.get("tab_id"), scene=scene2, shot=shot2) if tokens else get_user_usage(user_name)
                append_operation_log(pid, "gpt_responses_chat", user_name=user_name, scene_code=scene2.get("scene_code"), shot_code=shot2.get("shot_code"), shot_id=shot2.get("shot_id"), tab_id=tab2.get("tab_id"), message=user_text, model=GPT_RESPONSES_MODEL)
                save_project(pid, latest)
            finally:
                _lock.release()
            self.send_json(200, {"ok": True, "data": latest, "reply": reply, "used_mock": not bool(api_key), "mode": "chat", "usage": usage})
        except Exception as e:
            self.send_json(500, {"error": str(e), "data": load_project(pid), "usage": get_user_usage(user_name)})

    def _perform_gpt_responses_generate(self, pid, data, scene, shot, tab, user_text, api_key, explicit_mode, selected_base_id, user_name, redo_id="", gemini_api_key=""):
        gen_t0 = time.monotonic()
        # V23：@素材 候选池改为全项目素材库（不再限于当前标签页引用素材）。
        tab_assets = [a for a in (data.get("assets") or []) if a and not a.get("temporary") and a.get("name")]
        settings = tab.get("settings") or data.get("project_settings") or project_default_settings(load_config()["global_defaults"])
        operation, base_id = decide_operation(tab, user_text, explicit_mode, selected_base_id)

        # Build V28 execution sheet before image generation.
        # Normal @ assets are mapped by first @ appearance order. Only current-tab referenced assets are valid.
        effective_text_for_probe = select_effective_generation_text(tab, user_text)
        parsed_probe = parse_at_asset_mentions_for_execution(clean_generation_command_text(effective_text_for_probe) or effective_text_for_probe, tab_assets, start_index=1)
        if parsed_probe.get("unknown_mentions"):
            raise ValueError("、".join([f"@{x}" for x in parsed_probe.get("unknown_mentions")]) + " 不在当前标签页引用素材中，请先把它加入当前标签页引用素材后再使用。")

        base_entry = None
        use_base_id = selected_base_id or (base_id if not parsed_probe.get("mappings") else None)
        if use_base_id:
            _tab, base_img = find_generated_image_in_shot(shot, use_base_id)
            if base_img:
                base_entry = {
                    "image_id": base_img.get("image_id"),
                    "name": "当前编辑基准图",
                    "file_path": base_img.get("file_path"),
                }
                operation = "edit"

        execution_sheet = build_gpt_image_execution_sheet(tab, user_text, tab_assets, operation, base_entry=base_entry, api_key=api_key, pid=pid, data=data)
        image_inputs = []
        for p in execution_sheet.get("input_images") or []:
            fp = public_to_local(p)
            if fp and fp.exists():
                image_inputs.append(fp)
        dedup = []
        seen = set()
        for fp in image_inputs:
            key = str(fp)
            if key not in seen:
                dedup.append(fp); seen.add(key)
        image_inputs = dedup[:16]
        latest_text = execution_sheet.get("effective_user_text") or user_text or "开始生图"
        prompt = execution_sheet.get("final_prompt") or compile_final_image_prompt(execution_sheet)
        referenced_assets = [get_asset_by_id(data, m.get("asset_id")) for m in execution_sheet.get("final_execution_mapping", []) if m.get("source") == "@mention" and m.get("asset_type") != "document"]
        referenced_assets = [a for a in referenced_assets if a]
        shot_dir = OUTPUT_IMAGE_DIR / safe_name(pid) / scene.get("scene_code") / shot.get("shot_id") / tab.get("tab_id")
        model = normalize_image_model((settings or {}).get("image_model"))
        use_gemini = is_gemini_image_model(model)
        active_key = gemini_api_key if use_gemini else api_key
        try:
            tokens = int(execution_sheet.get("compiler_tokens") or 0)
            response_id = None
            image_call_id = None
            if active_key:
                if use_gemini:
                    image_bytes, image_tokens = call_gemini_image(active_key, model, prompt, settings, image_inputs)
                else:
                    image_bytes, image_tokens = call_image_api(active_key, prompt, settings, image_inputs)
                tokens += image_tokens or 0
                ext = ".png"
            else:
                image_bytes, ext = make_mock_svg(pid, scene.get("scene_code"), shot.get("shot_code"), prompt, settings)
            filename = next_image_filename(shot_dir, ext)
            out = shot_dir / filename
            out.write_bytes(image_bytes)
            file_url = "/output/image/" + out.relative_to(OUTPUT_IMAGE_DIR).as_posix()
            gen_seconds = round(time.monotonic() - gen_t0, 1)
            img = {
                "image_id": new_id("img"), "file_path": file_url, "created_at": now_str(), "operation": operation,
                "base_image_id": use_base_id if operation == "edit" else None, "user_message": user_text or latest_text,
                "prompt": prompt, "settings": dict(settings), "source_message_id": None,
                "tab_id": tab.get("tab_id"), "model": model, "responses_image_call_id": image_call_id,
                "execution_sheet": execution_sheet,
                "final_execution_mapping": execution_sheet.get("final_execution_mapping") or [],
                "normalized_text": execution_sheet.get("normalized_text") or "",
                "generation_seconds": gen_seconds,
            }
            _lock = project_mutation_lock(pid)
            _lock.acquire()
            try:
                latest = load_project(pid)
                scene2, shot2, tab2, user_msg_id = apply_user_message_for_submit(latest, shot.get("shot_id"), tab.get("tab_id"), user_text, redo_id)
                tab2["draft_prompt"] = user_text
                img["source_message_id"] = user_msg_id
                tab2.setdefault("generated_images", []).append(img)
                tab2["current_base_image_id"] = img["image_id"]
                update_context_summary(tab2, latest_text)
                state = tab2.setdefault("responses_state", {})
                state["model"] = GPT_RESPONSES_MODEL
                state["search_mode"] = gpt_responses_search_mode(settings)
                state["last_plan"] = prompt
                state["last_execution_sheet"] = execution_sheet
                state["ready_to_generate"] = False
                # V28: image generation is isolated; do not replace chat response_id with image response_id.
                if image_call_id:
                    state["last_image_call_id"] = image_call_id
                tab2.setdefault("messages", []).append({"message_id": new_id("msg"), "role": "assistant", "content": "gpt生图已生成 1 张图片。", "time": now_str(), "operation": operation, "kind": "gpt_generate", "image_id": img["image_id"], "reply_to_message_id": user_msg_id})
                usage = record_usage(user_name, "image", GPT_RESPONSES_MODEL + "/" + model, tokens, pid, shot2.get("shot_id"), tab2.get("tab_id"), scene=scene2, shot=shot2) if tokens else get_user_usage(user_name)
                append_operation_log(pid, "gpt_responses_image_success", user_name=user_name, scene_code=scene2.get("scene_code"), shot_code=shot2.get("shot_code"), shot_id=shot2.get("shot_id"), tab_id=tab2.get("tab_id"), prompt=latest_text, output_path=img.get("file_path"), image_id=img.get("image_id"), operation=operation, model=GPT_RESPONSES_MODEL)
                create_project_snapshot(pid, "gpt_responses_image_success", data=latest, user_name=user_name)
                save_project(pid, latest)
            finally:
                _lock.release()
            self.send_json(200, {"ok": True, "data": latest, "image": img, "used_mock": not bool(api_key), "mode": "generate", "usage": usage})
        except Exception as e:
            self.send_json(500, {"error": str(e), "data": load_project(pid), "usage": get_user_usage(user_name)})



    def api_review_note_image_upload(self):
        body = self.read_body()
        pid = safe_name(body.get("project") or "")
        scene_id = body.get("scene_id") or ""
        shot_id = body.get("shot_id") or ""
        round_no = parse_positive_int(body.get("round"), 1) or 1
        round_no = min(max(round_no, 1), 3)
        data = load_project(pid)
        scene, shot = find_shot(data, shot_id)
        if scene_id and scene.get("scene_id") != scene_id:
            self.send_json(400, {"error": "scene and shot mismatch"}); return
        raw, ext = parse_data_url(body.get("dataUrl") or "")
        requested = safe_file_name(Path(body.get("filename") or "审核备注图片").stem, "审核备注图片")
        scene_code = safe_file_name(scene.get("scene_code") or scene_code_from_number(scene_number_from_scene(scene, 1)), "SC")
        out_dir = project_path(pid) / "review_notes" / scene_code / shot_id / f"round_{round_no}"
        out_dir.mkdir(parents=True, exist_ok=True)
        name = requested
        out = out_dir / f"{name}{ext}"
        n = 2
        while out.exists():
            name = f"{requested}_{n}"
            out = out_dir / f"{name}{ext}"
            n += 1
        out.write_bytes(raw)
        rel = out.relative_to(project_path(pid)).as_posix()
        public = f"{project_temp_public_prefix(pid)}/{rel}"
        note = shot.setdefault("review_notes_by_round", {}).setdefault(str(round_no), {"text": "", "images": [], "active_index": 0})
        img = {"note_image_id": new_id("noteimg"), "file_path": public, "name": out.stem, "created_at": now_str()}
        note.setdefault("images", []).append(img)
        note["active_index"] = max(0, len(note.get("images", [])) - 1)
        save_project(pid, data)
        self.send_json(200, {"ok": True, "data": data, "image": img})


    def api_storage_delete_images(self):
        body = self.read_body()
        pid = safe_name(body.get("project") or "")
        ids = body.get("image_ids") or []
        if isinstance(ids, str):
            ids = [ids]
        data = load_project(pid)
        deleted = delete_generated_images_by_ids(data, ids, delete_files=True)
        save_project(pid, data)
        self.send_json(200, {"ok": True, "deleted": deleted, "data": data})

    def api_storage_rename_image(self):
        body = self.read_body()
        pid = safe_name(body.get("project") or "")
        image_id = body.get("image_id") or ""
        new_name = body.get("new_name") or "图片"
        data = load_project(pid)
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
        data = load_project(pid)
        scene, shot = find_shot(data, shot_id)
        removed = False
        for tab in shot.get("tabs", []):
            before = len(tab.get("generated_images", []))
            tab["generated_images"] = [img for img in tab.get("generated_images", []) if img.get("image_id") != image_id]
            if len(tab.get("generated_images", [])) != before:
                removed = True
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
        data = load_project(pid); scene, shot = find_shot(data, shot_id)
        _tab, img = find_generated_image_in_shot(shot, image_id)
        if not img:
            self.send_json(404, {"error": "image not found"}); return
        if not any(c.get("image_id") == image_id for c in shot.get("storyboard_candidates", [])):
            shot.setdefault("storyboard_candidates", []).append({"image_id": image_id, "file_path": img.get("file_path"), "review_status": "pending", "created_at": now_str()})
        shot["status"] = "confirmed"
        save_project(pid, data)
        self.send_json(200, {"ok": True, "data": data})

    def api_storyboard_upload(self):
        body = self.read_body(); pid = safe_name(body.get("project") or "")
        shot_id = body.get("shot_id") or ""; tab_id = body.get("tab_id") or None
        data = load_project(pid); scene, shot, tab = find_tab(data, shot_id, tab_id)
        raw, ext = parse_data_url(body.get("dataUrl") or "")
        name = safe_file_name(body.get("name") or Path(body.get("filename") or "素材图").stem, "素材图")
        shot_dir = OUTPUT_IMAGE_DIR / safe_name(pid) / scene.get("scene_code") / shot.get("shot_id") / tab.get("tab_id")
        filename = next_image_filename(shot_dir, ext)
        out = shot_dir / filename
        out.write_bytes(raw)
        file_url = "/output/image/" + out.relative_to(OUTPUT_IMAGE_DIR).as_posix()
        img = {"image_id": new_id("img"), "file_path": file_url, "created_at": now_str(), "operation": "manual_storyboard", "user_message": name, "prompt": "手动添加素材图", "settings": dict(tab.get("settings") or {}), "tab_id": tab.get("tab_id")}
        tab.setdefault("generated_images", []).append(img)
        shot.setdefault("storyboard_candidates", []).append({"image_id": img["image_id"], "file_path": file_url, "review_status": "pending", "created_at": now_str()})
        shot["status"] = "confirmed"
        save_project(pid, data)
        self.send_json(200, {"ok": True, "data": data, "image": img})

    def api_storyboard_remove(self):
        body = self.read_body(); pid = safe_name(body.get("project") or "")
        shot_id = body.get("shot_id") or ""; image_id = body.get("image_id") or ""
        data = load_project(pid); scene, shot = find_shot(data, shot_id)
        shot["storyboard_candidates"] = [c for c in shot.get("storyboard_candidates", []) if c.get("image_id") != image_id]
        if not shot["storyboard_candidates"]:
            shot["status"] = "unconfirmed"
        save_project(pid, data)
        self.send_json(200, {"ok": True, "data": data})


if __name__ == "__main__":
    print(f"Material tool running at http://localhost:{PORT}")
    print(f"Root: {ROOT}")
    http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
