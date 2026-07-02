# 帝蓝工作流-数据中心-V36

局域网内统一的项目仓库服务端，供制作端 / 审核端共同使用。把原来靠本地压缩包
手动传来传去的流程，改成统一上传 / 下载 / 管理项目。

## 运行
1. 双击 `安装.bat`（创建 .venv 并安装依赖，本工具仅用标准库，安装很快）。
2. 双击 `start.bat` 启动。
3. 启动后控制台会打印「局域网访问」地址，例如 `http://192.168.1.100:8777`。
4. 在制作端 / 审核端的「数据中心地址」里填入该局域网地址即可。

## 结构
- `server.py`：服务端（http.server + sqlite3，纯标准库）。
- `database.sqlite`：项目索引与元数据。
- `storage/packages/`：原始工程包 zip（始终保留）。
- `storage/previews/`：预览缩略图。
- `static/`：网页管理页面。
- `config.json`：服务名称、监听端口等配置。

## 接口
- `GET  /api/projects`
- `POST /api/upload`（multipart：file + meta + on_conflict）
- `POST /api/upload/resolve-conflict`
- `GET  /api/download/child/{child_id}`
- `GET  /api/download/parent/{parent_id}`
- `DELETE /api/child/{child_id}`
- `GET  /api/preview/{child_id}`
- `GET/POST /api/config`
- `POST /api/merge`（合并功能将在后续版本开放）

## 说明
- 项目结构与制作端 / 审核端一致：父项目 → 美术 / 分镜 / 视频 → 子项目。
- 子项目展示名自动带生成者后缀（如「第一集视频_羊肉」），便于区分同名上传。
- 同名上传时返回冲突，由上传方选择「覆盖」或「生成新版本」。
