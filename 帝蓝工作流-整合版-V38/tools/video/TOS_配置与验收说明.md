# Seedance 2.0 参考视频 · 火山 TOS 配置与验收说明（V40）

本说明配合「为视频模块 Seedance 2.0 增加引用视频素材（云端 URL）」功能。
Seedance 2.0 的参考视频（`reference_video`）只接受**公网可访问的 URL**，不接受本地路径，
也不接受 `data:video/...;base64,...`。本功能会在调用前把本地视频素材自动上传到火山 TOS，
并用**预签名 URL**提供给 Seedance；长期只保存 `bucket + key + sha256 + size`，每次调用前重新签名。

> 未配置 / 未启用 TOS（`tos_enabled=false`，默认）时，本功能不生效，
> 现有图片 / 音频 / 普通视频生成流程完全不受影响。

---

## 一、开通火山 TOS（首次）

1. 登录火山引擎控制台 → 开通「对象存储 TOS」。
2. 新建一个 **Bucket**，记下：
   - **Bucket 名**（填到 `tos_bucket`）；
   - **地域 / Region**（例如 `cn-beijing`，填到 `tos_region`）；
   - **Endpoint**（例如 `https://tos-cn-beijing.volces.com`，要和 Region 对应，填到 `tos_endpoint`）。
   - 读写权限建议保持「私有」——对外访问统一由预签名 URL 临时授权。
3. 在「访问控制 IAM / 密钥管理」创建一对 **AccessKey / SecretKey（AK/SK）**，
   赋予该 Bucket 的对象读写权限（至少 `PutObject` / `GetObject` / `HeadObject`）。
   - **AK/SK 不要写进任何项目 JSON 或代码**，只放环境变量（见下）。

## 二、安装依赖

视频模块需要火山 TOS Python SDK：

```bash
pip install tos
```

> 若启动脚本不自动安装 `requirements.txt`，请手动执行上面这条。

## 三、配置（两部分）

### 1）`tools/video/config.json` → `global_defaults`

```json
"tos_enabled": true,
"tos_bucket": "你的bucket名",
"tos_region": "cn-beijing",
"tos_endpoint": "https://tos-cn-beijing.volces.com",
"tos_prefix": "dilan-workflow/video-module/",
"tos_presign_expire_seconds": 86400,
"tos_presign_refresh_margin_seconds": 600,
"tos_upload_generated_videos": true
```

- `tos_enabled`：设为 `true` 才启用。
- `tos_upload_generated_videos`：生成出来的视频是否也自动转存 TOS（默认 `true`，便于把生成视频再当参考视频）。
- 其余按你的 bucket 实际信息填写。

### 2）环境变量（放 AK/SK）

支持两组命名（任一即可）：

```
VOLC_TOS_ACCESS_KEY_ID      或  TOS_ACCESS_KEY_ID
VOLC_TOS_SECRET_ACCESS_KEY  或  TOS_SECRET_ACCESS_KEY
```

- **Windows（推荐写进启动 bat，或系统环境变量）**：
  ```bat
  set VOLC_TOS_ACCESS_KEY_ID=你的AK
  set VOLC_TOS_SECRET_ACCESS_KEY=你的SK
  ```
  注意：`set` 设置的变量只对当前命令行窗口有效；要长期生效请在「系统属性 → 环境变量」里添加，
  或写进启动视频模块的那个 `.bat` 里、在启动 Python 之前 `set`。
- 配好后**重启视频模块服务**让环境变量生效。

---

## 四、验收清单（对应开发文档第九节）

| # | 场景 | 期望结果 |
|---|------|----------|
| 1 | 上传 mp4 到「视频」分类，prompt 里 `@视频素材名`，点生成 | 后端自动上传 TOS；`project.json` 该 asset 出现 `remote.provider="tos"`、`bucket/key/sha256/size`；Ark 请求体 `reference_video.url` 是 `https://...`，不是 `/input/...`、不是 `data:` |
| 2 | 隔天/清掉旧签名再生成 | 用 `bucket+key` **重新生成**预签名 URL，而不是用旧 URL |
| 3 | 删掉 TOS 对象、保留本地文件，再生成 | 后端用本地文件**重新上传**到 key 并更新 `remote` |
| 4 | TOS 对象和本地文件都没了 | 明确中文报错：`参考视频素材"xxx"已丢失：本地文件不存在，TOS 对象也不存在。请重新上传该素材。` |
| 5 | 生成一个视频 | 生成记录含 `remote.provider="tos"`；再次拿它当参考素材不依赖 Ark 原始 `remote_url` |
| 6 | 图片/音频参考（V40 起） | 启用 TOS 时图片、音频参考也自动上传 TOS 并以预签名 URL 传给 Ark（按内容 sha256 去重，同一文件只上传一次）；未启用 TOS 才回退内嵌 `data:`。几十 MB 的 base64 请求体会被 Ark 网关直接断开（WinError 10054），故建议保持 TOS 启用 |

未启用 TOS 却 `@` 了本地视频时，会得到友好报错：
`当前引用了本地视频素材，但 Seedance 2.0 参考视频必须使用公网 URL。请在视频模块 config.json 启用 TOS…`

---

## 五、SDK 适配说明（重要）

火山 `tos` SDK 不同版本的 `pre_signed_url` / `put_object` / `head_object` 方法签名可能不同。
本实现已做**多版本兜底**：

- 上传：优先 `put_object_from_file(bucket, key, path)`，否则退回 `put_object(bucket, key, content=bytes)`。
- 预签名：尝试 `pre_signed_url` 的位置参数与多种关键字参数组合，并兼容返回值为
  `str` / 带 `signed_url` 属性的对象 / `dict` 三种形态；GET 方法枚举优先用 `tos.HttpMethodType`。

如果你装的 SDK 版本仍报「未找到匹配的 pre_signed_url 调用方式」或「上传失败」，请把
**实际的 `tos` 版本号**（`pip show tos`）和报错贴给我，我据此把签名适配死。

---

## 六、注意事项

- TOS 会产生**存储 + 外网下行流量**费用；`tos_upload_generated_videos=true` 会把每个生成视频也存到 TOS，按需关闭。
- AK/SK 永远只放环境变量，别写进 `config.json` / `project.json` / 代码。
- 本地素材文件不会被删除，是 TOS 丢失时的兜底。

## 七、V40 起的素材上传范围

- **视频模块（Seedance）**：@ 引用的图片、音频、视频参考素材**全部**先上传 TOS、再以预签名 URL
  传给 Ark；参考文件按内容 sha256 存到 `<prefix>cache/`，同一文件只上传一次，之后只重新签 URL。
- **美术 / 分镜模块（GPT 聊天模式的参考图）**：同样走 TOS 预签名 URL（OpenAI Responses 支持
  URL 参考图）；TOS 配置与密钥**共用视频模块这一份**，不用另配。上传失败时自动回退内嵌 base64，
  不影响聊天可用性。
- **不经过 TOS 的两条链路（上游 API 限制，无法用外链 URL）**：
  - gpt 生图出图（OpenAI `images/edits`）：官方 multipart 文件直传通道，只收文件字节；
  - Nano Banana（Gemini `inline_data`）：只收 base64 / 谷歌自家文件服务。
