# 帝蓝工作流 · Claude Code 开发必读

AI 视频/图片生成桌面工具（Windows 本地运行，中文界面）。仓库只有一个程序：**整合版**（文件夹 `帝蓝工作流-整合版-V38`），含 **美术** 与 **视频** 两个模块。完整背景、版本史、子系统设计见根目录 **《开发交接文档.md》**（改动前先读它）。

> V47 起已整体移除：数据中心、统一审核端两个程序；分镜(image)模块；整合版内的数据中心上传下载与三轮审核/审核备注功能。工程包/素材包的**本地导出与导入**保留。不要把这些已删子系统当作现存功能来改。

## 铁律（用户明确约定，违反会返工）

1. **每次修改都要把产品版本号 +1**（当前 **V47**）。只改"显示字符串"，共 10 处 6 个文件：两端 `tools/{material,video}/index.html` 的 `<title>` 与顶部横幅（各 2 处）、整合版 `README.md`（1）、`integrated_server.py`（3：docstring/server_version/启动打印）、`start.bat`（1）、`tools/material/start.bat`（1）。**文件夹名固定 `-V38` 不许改名**。`TOS_配置与验收说明.md` 里的"V41 起"是特性起始版本，保留不动。
2. **AK/SK 绝不写入任何 JSON/代码/localStorage**，只走环境变量 `VOLC_TOS_ACCESS_KEY_ID` / `VOLC_TOS_SECRET_ACCESS_KEY`（兼容读 `TOS_ACCESS_KEY_ID`/`TOS_SECRET_ACCESS_KEY`）。设置页提供"写入 Windows 用户环境变量"的功能，写入后需重启程序生效。
3. **两端页面只加载 index.html 的内联 `<script>`**。`tools/*/script.js` 是历史死文件——往里写代码页面根本不会执行（V36 曾因此"点击生成无反应"）。
4. **不要在本地预检/拦截 Ark（火山方舟）的规则**（音频时长、素材数量等）。V38 加过预检、V39 被用户要求撤销：本地猜规则会误拦，以官方接口报错为准。
5. 模型标识（claude-*）不得出现在提交信息、代码、任何推送产物中。
6. 提交信息用中文，写清"根因 + 修复 + 验证"。当前开发分支：`claude/stoic-fermat-g9avon`（新会话按其环境指定的分支即可）。

## 架构速览

- 网关 `帝蓝工作流-整合版-V38/integrated_server.py`（**:8787**），按 HTTP Referer/路径把请求代理到两个子服务：**material :8790（美术）/ video :8789（视频 Seedance）**。
- 子服务 `tools/{material,video}/image_server.py`（各 ~5000+ 行，`ThreadingHTTPServer`，**两端高度同构**——改公共逻辑通常两份都要改，用脚本批量替换并断言命中次数）。
- 前端 = 各端单文件 `tools/*/index.html`（内联脚本）。
- 数据：`tools/<模块>/projects/<项目>/project.json`（结构 scenes→shots→tabs→{messages, generated_images, generated_videos(同一数组镜像), last_video_status, referenced_assets, draft_prompt, settings}）；素材在 `input/`，生成结果在 `output/image|video/`。美术端另有 `material_workspaces{character,scene,object}` 工作区槽 + `active_material_workspace`（权威归属字段，防串槽）。父项目跨两模块共享（`shared_project_index`，`PROJECT_MODULE_KEYS=["material","video"]`）。
- 工程包/素材包：本地导出 ZIP（`/api/project/export`、`/api/assets/package/export`）与本地导入（含 V41 流式导入路径），旧包里的审核备注/分镜数据导入时被忽略，工程数据里的历史 review 字段为惰性数据不影响运行。
- 运行：双击 `整合版/安装.bat`（建 .venv 装依赖）→ `start.bat`。

## 并发不变量（V42–V44 血泪教训，新代码必须遵守）

后端（两端 image_server.py）：
- 每项目一把 `project_mutation_lock(pid)`（RLock）。**任何"load_project → 改 → save_project"的处理器**必须用 `self.begin_project_mutation(pid)`（锁内重读最新数据），锁由 `do_POST` 的 `finally: self._release_held_locks()` 统一释放。
- 生成类路径（api_video_generate / _perform_*）：慢的外部调用（Seedance 轮询、AI 请求）在锁外，"重读→写结果→保存"在锁内。
- `/api/project/save`（前端整份快照覆盖）在锁内先 `merge_generated_media_preserve`：磁盘上的生成结果/消息**按 id 只增不减**并回客户端数据；`last_video_status` 不得从终态（success/error）退回 running。

前端（两端 index.html）：
- **凡采纳服务器整份工程数据**，必须走 `adoptProject(newData, removedIds)`（视频端）或 `materialUnionGenMedia`（美术端，跨全部工作区槽）：并集补回本地已知生成结果；`removedIds` 传入刚删除的 id 防"复活"；**视图状态（scene.expanded、shot.active_tab_id）以本会话内存为准**；视频端无在飞任务时终态不被退回 running。禁止裸写 `project = r.data`。
- `saveProject()` 是**单飞 + 合并**（最多 1 个在飞 + 1 个待发，发送时取最新 project）。不要绕过它直接 POST /api/project/save。
- 删除生成结果必须走专门删除接口，并把被删 id 传给 `updateFromServer(r, ...ids)`。
- 审核已移除，但 `sceneLocked/canEditScene/canEditShot/assertEditScene/assertEditShot` 保留为"永远可编辑"的同名 stub 供全页调用——不要删掉这些 stub。

## 性能约定（V44）

- 生成视频 `<video>` 一律 `preload="none"`；封面走服务器缩略图或 `__posterCache`（无封面老视频每轮最多补截 8 条）。
- `renderAll()` 只渲染 `currentScreen` 对应页面（workspace/assets/storage 三选一），切页时 `showScreen` 会补渲染。

## 每次改动后的验证（都在仓库根目录执行）

```bash
# 1) 后端语法
python3 -m py_compile 帝蓝工作流-整合版-V38/tools/{material,video}/image_server.py 帝蓝工作流-整合版-V38/integrated_server.py
# 2) 前端语法：提取内联 <script> 逐块 node --check（正则 <script(?![^>]*\bsrc=)[^>]*>(.*?)</script> DOTALL）
# 3) 行为验证：用正则从 index.html 提取真实函数到 Node、stub 依赖后跑断言；
#    后端并发用 importlib 加载 image_server.py、把 PROJECTS_DIR 等指到临时目录后 threading 压测。
```
沙箱能 import 测本地逻辑，**连不上** TOS/Ark 外网域名。
