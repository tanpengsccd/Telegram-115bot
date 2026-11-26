# Telegram-115Bot（增强版 · Telegram + Web 控制台 + REST API）

一个基于 Python 的 115 网盘离线下载与管理工具，支持 **Telegram 机器人交互控制**、**手机/桌面自适应 Web 控制台（PWA）** 以及 **外部 REST API 调用**（FastAPI + React/Tailwind）。

> 本项目 Fork 自上游仓库 [`qiqiandfei/Telegram-115bot`](https://github.com/qiqiandfei/Telegram-115bot)，共同祖先版本（`v3.2.13`）的原始说明文档见：[上游分叉基线 README (`a5a1808`)](https://github.com/qiqiandfei/Telegram-115bot/blob/a5a1808e5856023928d26bddb0ba9c5414208621/README.md)。

---

## 核心特性与改动总结

### 1. Telegram 控制面板与交互体验
- **常驻底部唯一控制面板**：2 页布局，默认开启「对话极简模式」（除导出日志外仅保留最新一条结果消息）；短时间连发多个文件时支持 **3 秒防抖置底**（`panel_refresh_debounce_sec`），面板只重发一次不刷屏。
- **表情状态反馈**：收到授权用户消息/文件后立即标记 `👀`，处理完成变为 `👌`，异常或无法识别标记 `😢`。
- **统一任务队列（已合并重试列表）**：支持 `全部 / 进行中 / 待重试 / 已完成 / 已失败` 5 状态筛选，记录全部按钮化展示，点击查看逐条磁力明细、第 N 次尝试与下次重试时间，支持手动立即重试、单删与批量清空。
- **115 官方云端任务管理**：支持 `/cloud_tasks` 或面板分页浏览、单条删除（仅删记录 / 含源文件删除）与一键清空已完成 / 失败 / 全部记录。
- **精简聚焦**：移除电影订阅与爬虫相关命令及定时任务，专注离线下载、队列追踪与网盘管理。

### 2. 多格式文件解析与批量任务确认流
- **多格式无 500KB 限制**：纯 Python 解析 `.txt`、`.csv`、`.html`、`.htm`、`.xlsx`、`.xlsm`、`.xls`、`.torrent`，自动识别 `magnet:` / `ed2k://` / `thunder://` / `.torrent` 链接并去重。
- **多链接预览确认 + 一键默认目录**：文件匹配超过 1 条链接时先回传 `原文件名+数量V+时间.txt` 预览清单；直接提供 **「✅ 确认下载至默认文件夹」** 与 **「📂 确认并下载至其他文件夹」**（默认目录持久化至 SQLite）。
- **30 秒超时自动确认 + 取消倒计时按钮**：若 `30` 秒（`file_confirm_auto_timeout_sec` 可配，设为 `0` 关闭）内未操作，自动开始下载到默认目录；点击 **「⏸ 取消30秒自动倒计时」** 仅取消倒计时，保留任务供后续手动决定。
- **多文件并发确认隔离**：每个文件绑定独立 `confirm_id`，连续发送多个文件按任意顺序点选目录互不覆盖。

### 3. 115 批量 API 优化、自适应轮询与自动重试
- **ETA 自适应轮询 + 10 分钟无进度早停**：按剩余预计时间动态拉长轮询间隔，配合按需分页与第 1 页 3 秒共享缓存大幅降低 API 消耗。
- **批量后处理提速**：`create_strm: false` 时跳过逐项目录查询；批量任务先立即发送引用原消息的完成汇总，再后台执行递归扁平垃圾清理、100 条分批删除、`clear_cloud_task(4)` 一步清云端记录与磁力备份 TXT 上传。
- **自动重试开关（默认关闭）+ 2 倍指数退避**：可在 TG 面板第 2 页或 Web 设置页实时开关（开启时自动展示完整使用说明）；开启后每轮等待时间翻倍直到达到上限（24h / 2天 / 4天 / 8天 / 16天），成功或最终失败均引用原消息通知。
- **稳定性增强**：修复 115 `code: 10008` 重复提交死循环、`ed2k`/Base32 磁力匹配差异、Token 过期降级不崩溃以及 Telegram `HTTPXRequest` 长连接防 502 问题。

### 4. Web 管理端（手机/桌面 PWA）与外部 REST API
- **双运行模式**：默认随 `app/115bot.py` 同进程启动（共享内存进度、115 限流器、Token 与 SQLite）；也可运行 `app/web_server.py` 纯 Web + API 独立启动（无需 TG Bot Token）。
- **鉴权与文档**：Web 端使用 PBKDF2-SHA256 密码哈希 + `HttpOnly` 签名 Cookie；外部 API 支持 `X-API-Key` 或 `Authorization: Bearer <key>`，自动生成 Swagger 交互文档（`/docs`）。
- **多端响应式 + SSE 实时流**：桌面端左侧固定侧边栏，手机端底部 5-Tab 固定导航（适配 Safe Area + PWA 添加到主屏幕）；任务队列与运行日志均支持 SSE 实时推送；Web/API 下载完成可选同步推送汇总到 Telegram。

---

## 项目目录结构

```text
.
├── app/
│   ├── 115bot.py                 # 主入口（TG Bot + 内嵌 Web/API）
│   ├── web_server.py             # 纯 Web + REST API 独立启动入口
│   ├── init.py                   # 全局初始化、配置加载与数据库初始化
│   ├── config.yaml.example       # 配置文件模板
│   ├── core/                     # 115 OpenAPI、批量任务、自动重试、调度器
│   ├── handlers/                 # Telegram 命令与回调处理器
│   ├── utils/                    # 多格式链接解析、任务追踪、自适应轮询、设置与队列
│   └── web/                      # FastAPI 后端、鉴权、115 扫码、SSE 与静态产物（static/）
├── web-frontend/                 # React + Vite + Tailwind CSS + PWA 前端源码
├── config/
│   └── config.yaml.example       # 配置文件模板（复制为 config/config.yaml 使用）
├── create_tg_session_file.py     # 大视频（>20MB）转存所需的 Telethon Session 生成脚本
├── Dockerfile                    # 多阶段构建（Node 打包前端 + Python 后端）
├── Dockerfile.base               # Python 3.13 + Playwright 基础镜像
├── docker-compose.yaml           # Docker Compose 编排文件（映射 8115 端口）
├── build.sh                      # 本地镜像构建脚本
├── requirements.txt              # Python 依赖清单
└── test_*.py                     # 自动化单元测试套件（9 组）
```

---

## 完整使用流程

### 第一步：准备前置凭据

1. **申请 115 开放平台 AppID（强烈推荐）**
   - 前往 [115 开放平台](https://open.115.com/) 创建应用，审核通过后获取 `115_app_id`。
   - 填写 `115_app_id` 后即可在 Telegram 或 Web 端直接扫码授权，无需手动抓取/维护 Token。
2. **创建 Telegram 机器人（如仅使用纯 Web/API 模式可跳过）**
   - 在 Telegram 中通过 `@BotFather` 创建机器人获取 `bot_token`。
   - 通过 `@getidsbot` 获取你的数字用户 ID，填入 `allowed_user`（支持单个 ID 或列表 `[123456, 789012]`）。

---

### 第二步：准备配置文件

```bash
cp config/config.yaml.example config/config.yaml
```

按需修改 `config/config.yaml` 中的关键配置：

```yaml
log_level: info

# Telegram 基础配置（纯 Web 模式可留空）
bot_token: "your_bot_token"
allowed_user: [123456789]

# 115 开放平台（推荐配置 115_app_id，之后直接扫码授权）
115_app_id: "your_115_app_id"

# 下载完成后自动清理小广告文件
clean_policy:
  switch: "on"
  less_than: 400M

# 短时间多条链接消息自动合并间隔（毫秒）
msgs_merge_interval: 3000

# 115 分类保存目录（首个目录或上次选择的目录会作为默认保存目录）
category_folder:
  - name: movies
    display_name: 电影
    path_map:
      - name: 外语电影
        path: /影视/电影/外语电影
      - name: 华语电影
        path: /影视/电影/华语电影

# STRM 软链与 Emby（不需要生成 .strm 时建议设为 false，可大幅减少 API 请求）
create_strm: false
mount_root: /CloudNAS/115
strm_root: /media/115
emby_server: http://your_emby_server:8096
api_key: your_emby_api_key

# 交互与自动重试参数（控制面板 / Web 设置页的实时修改优先级高于此文件）
retry_max_wait_hours: 24          # 自动重试最长等待上限：24 / 48 / 96 / 192 / 384（16天）
auto_retry_enabled: false         # 自动重试机制开关（默认关闭）
panel_refresh_debounce_sec: 3     # TG 底部控制面板防抖刷新间隔（秒）
file_confirm_auto_timeout_sec: 30 # TG 多链接文件确认超时自动下载默认目录（秒，0 为关闭）

# Web 控制台与 REST API 设置
web:
  enabled: true
  host: "0.0.0.0"                 # 若仅通过本机 Nginx/1Panel 反代可改为 127.0.0.1
  port: 8115
  username: "admin"
  password: "your_web_password"   # 留空则首次启动自动生成随机密码并输出在日志中
  api_key: ""                     # 留空则首次启动自动生成，可在 Web 设置页查看/轮换
  notify_tg_on_web_download: true # Web/API 下载完成后是否同步推送结果到 Telegram
```

> **安全提醒**：切勿提交或泄露 `config/config.yaml`、`config/115_tokens.json`、`config/*.db`、`config/user_session.session`、`app/images/qrcode.png`、`*.log` 等运行期敏感文件（已配置 `.gitignore` 忽略）。

---

### 第三步：部署与启动（三种方式任选）

#### 方式 1：本地 / tmux 联合启动（推荐，TG Bot + Web + API 同进程）
1. 准备 Python 3.12+ / 3.13 虚拟环境并安装依赖：
   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```
   > 若使用 `uv`：`uv venv && uv pip install -r requirements.txt`。
   > 若精简版 Debian 缺少 `ensurepip`：先 `python3 -m venv --without-pip .venv`，再用 `get-pip.py` 安装 `pip`。
2. 如服务器无法直连 Telegram，设置代理并将 115 域名加入 `NO_PROXY` 走直连：
   ```bash
   export HTTP_PROXY="http://127.0.0.1:<代理端口>"
   export HTTPS_PROXY="http://127.0.0.1:<代理端口>"
   export NO_PROXY="localhost,127.0.0.1,::1,proapi.115.com,passportapi.115.com,qrcodeapi.115.com"
   ```
3. 启动服务：
   ```bash
   .venv/bin/python app/115bot.py
   ```

#### 方式 2：纯 Web + REST API 独立启动（无需 Telegram）
```bash
.venv/bin/python app/web_server.py
```

#### 方式 3：Docker Compose 一键部署
1. 修改 [docker-compose.yaml](docker-compose.yaml) 中的挂载卷路径（`config/`、`tmp/`、`media/`、`CloudNAS/`）与代理环境变量（如需）。
2. 本地构建并启动（[Dockerfile](Dockerfile) 采用多阶段构建，自动打包 React 前端与 Python 后端）：
   ```bash
   bash build.sh
   docker compose up -d
   ```

---

### 第四步：115 账号扫码授权

启动后若尚未授权或 Token 已过期，程序不会崩溃退出，可通过任一端直接完成扫码授权：
- **Telegram 端**：发送 `/auth`（或点击底部面板第 2 页「🔑 115 扫码授权」），用手机 115 App 扫码确认。
- **Web 端**：访问 `http://<服务器IP>:8115` 登录，在「总览」页点击右上角 **「115 扫码授权」**，用 115 App 扫码确认，页面自动完成授权并刷新账号、配额与容量信息。

---

### 第五步：日常使用流程

#### A. Telegram 端使用流程

1. **打开控制面板**：发送 `/start` 或 `/menu`，常驻底部主控面板即出现（支持翻页查看全部功能）。
2. **直接发送/转发链接**：
   - 发送单条或多条 `magnet:` / `ed2k://` / `thunder://` / `.torrent` 链接（支持纯文本、图片配文、超链接实体，短时间多条消息自动合并）。
   - 多链接文本的首行/尾行非链接文字会自动提取为 115 子文件夹名。
   - 若已记住默认目录则自动提交；否则弹出目录选择按钮。
3. **发送多格式链接文件**：
   - 直接发送 `.txt` / `.csv` / `.html` / `.htm` / `.xlsx` / `.xlsm` / `.xls` / `.torrent` 文件（无 500KB 限制）。
   - 仅含 1 条链接时直接提交；超过 1 条链接时，机器人先回复 `原文件名+数量V+时间.txt` 清单供核对：
     - **✅ 确认下载至默认文件夹**：一键开始下载到默认目录；
     - **📂 确认并下载至其他文件夹**：弹出该文件专属的分类目录选择器（连发多个文件并发确认互不干扰）；
     - **⏸ 取消30秒自动倒计时**：停止 30 秒自动下载倒计时，保留任务与手动按钮；
     - **❌ 取消下载**：放弃本次提交。
4. **队列追踪、云端清理与自动重试**：
   - **📥 任务队列（`/tasks`）**：按状态筛选，点击任务按钮查看逐条磁力明细、第 N 次尝试、下次重试时间，可手动 **🔄 立即重试** 或删除记录。
   - **☁️ 115云端任务（`/cloud_tasks`）**：分页浏览 115 官方任务，支持单条删除（仅删记录 / 含源文件）与批量清空。
   - **🔁 自动重试开关（面板第 2 页）**：默认关闭，点击开启后自动推送完整使用说明；可在 **⏱ 重试上限** 调整最长等待时长。

**Telegram 常用命令速查：**

| 命令 | 说明 |
|---|---|
| `/start`、`/menu` | 打开并置底主控面板 |
| `/status` | 查看 115 账号、VIP、离线配额与重置倒计时、网盘容量及下载目录 |
| `/tasks` | 打开统一任务队列与逐条磁力明细 |
| `/rl` | 直接打开任务队列「待重试」筛选视图 |
| `/cloud_tasks` | 管理 115 官方云端离线任务 |
| `/log` | 导出并发送最新运行日志文件（`115bot.log`） |
| `/minimal` | 切换对话极简模式 |
| `/reset_save_path` | 重置已记住的默认保存目录 |
| `/auth` | 115 开放平台扫码授权 |
| `/reload` | 热重载 `config/config.yaml` 配置 |
| `/av <番号>` | 自动检索番号磁力并提交离线下载 |
| `/sync` | 同步指定目录并创建 `.strm` 软链（⚠️ 见下方警告） |
| `/help` | 查看完整命令与操作说明 |
| `/q` | 取消当前交互会话 |

---

#### B. Web 控制台使用流程（手机端 & 桌面端）

1. **打开与安装 PWA**：
   - 浏览器访问 `http://<服务器IP>:8115`（公网访问建议通过 1Panel / Nginx 反代配置 HTTPS）。
   - 手机端（iOS Safari / Android Chrome）可点击「添加到主屏幕」，以独立 App 形态运行并自动适配底部 5-Tab 导航栏与安全区。
   - 使用 `config.yaml` 中配置的 `web.username` / `web.password` 登录（未配置密码时从首次启动日志中获取随机初始密码）。
2. **总览页**：查看 115 账号/VIP、离线配额进度条与重置倒计时、网盘剩余空间、已配置目录，支持弹窗扫码授权。
3. **下载页**：
   - 拖拽或点选上传 `.txt / .csv / .html / .htm / Excel / .torrent` 文件 → 系统解析后呈现**可勾选预览清单**，自由勾选所需链接；
   - 或在文本框粘贴链接/批量文本（可点「先识别预览」核对或直接提交）；
   - 选择保存目录（默认预选记忆目录）、填写可选子文件夹名、勾选「完成后同步推送汇总到 Telegram」→ 点击 **「开始下载」** 即入队并跳转队列页。
4. **队列页（SSE 实时同步）**：
   - 自动实时更新进度与状态，按 5 种状态筛选；
   - 点击卡片查看逐条磁力明细（手机端为底部抽屉），支持只看未完成、手动立即重试、删除记录与清空已完成/已失败记录。
5. **云端页**：分页管理 115 官方离线任务，支持单删（仅删记录 / 含源文件）与一键清空已完成 / 失败 / 全部记录。
6. **设置页**：
   - 切换**自动重试开关**（开启时直接展示完整使用说明）与最长等待上限；
   - 开关 **Web/API 下载完成同步通知 TG**、重置默认目录、热重载配置；
   - 查看/复制/重新生成 **API Key**、修改 Web 登录账号密码；
   - 内置 **实时运行日志终端（SSE）** 与一键下载完整 `115bot.log`。

---

#### C. 外部 REST API 调用流程

- **Swagger 交互文档**：`http://<服务器IP>:8115/docs`（OpenAPI JSON 位于 `/openapi.json`）。
- **鉴权方式**：请求头传入 `X-API-Key: <API_KEY>` 或 `Authorization: Bearer <API_KEY>`（在 Web「设置」页一键复制，支持随时重置）。

**核心 API 端点一览：**

| 方法与路径 | 用途 |
|---|---|
| `GET /api/v1/health` | 健康检查（免鉴权） |
| `GET /api/v1/status` | 获取 115 账号、配额、容量、目录与系统状态 |
| `POST /api/v1/115/qr/start` / `GET /api/v1/115/qr/{id}` | 启动与轮询 115 PKCE 扫码授权 |
| `POST /api/v1/files/parse` | 上传多格式链接文件，仅解析返回链接清单（不提交） |
| `POST /api/v1/files/submit` | 上传多格式链接文件，解析并直接入队下载 |
| `POST /api/v1/downloads/parse-text` | 解析原始文本中的链接与建议文件夹名 |
| `POST /api/v1/downloads/submit` | 提交链接列表或原始文本离线下载（返回 `tracker_id`） |
| `GET /api/v1/tasks` | 查询统一任务队列（支持 `filter=all\|run\|retry\|done\|fail`） |
| `GET /api/v1/tasks/live/{task_id}` | 查询进行中/近期任务的实时逐条进度 |
| `GET /api/v1/tasks/groups/{group_id}` | 查询持久化任务组明细与分页 |
| `POST /api/v1/tasks/groups/{group_id}/retry` | 手动立即重试指定任务组的失败项 |
| `DELETE /api/v1/tasks/groups/{group_id}` | 删除任务组记录（不删网盘文件） |
| `GET /api/v1/cloud/tasks?page=1` | 分页获取 115 官方云端离线任务 |
| `DELETE /api/v1/cloud/tasks/{info_hash}?delete_source=false` | 删除单条 115 云端任务（可选同时删源文件） |
| `POST /api/v1/cloud/tasks/clear` | 批量清空云端任务（`flag`: `1`全部 / `2`失败 / `4`已完成） |
| `GET/PATCH /api/v1/settings` | 读取与修改自动重试开关、上限、TG 通知与默认目录 |
| `GET /api/v1/logs` / `GET /api/v1/logs/download` | 获取最近日志或下载完整 `115bot.log` |
| `GET /api/v1/events/stream` / `GET /api/v1/logs/stream` | SSE 实时任务进度流与实时日志流（支持 `?api_key=`） |

**快速调用示例（`curl`）：**

```bash
# 1. 查询账号状态与剩余配额
curl -H "X-API-Key: <YOUR_API_KEY>" \
  "http://127.0.0.1:8115/api/v1/status"

# 2. 提交磁力/ed2k 链接到默认目录（可选关闭 TG 通知）
curl -X POST "http://127.0.0.1:8115/api/v1/downloads/submit" \
  -H "X-API-Key: <YOUR_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{
    "links": ["magnet:?xt=urn:btih:..."],
    "custom_folder_name": "合集名称",
    "notify_tg": true
  }'

# 3. 上传多格式文件并直接提交下载（支持 .txt/.csv/.html/.htm/.xlsx/.xls/.torrent）
curl -X POST "http://127.0.0.1:8115/api/v1/files/submit" \
  -H "X-API-Key: <YOUR_API_KEY>" \
  -F "file=@/path/to/links.xlsx" \
  -F "notify_tg=true"

# 4. 查询待重试队列
curl -H "X-API-Key: <YOUR_API_KEY>" \
  "http://127.0.0.1:8115/api/v1/tasks?filter=retry"
```

---

## 进阶配置说明

### 1. Telegram 大视频转存（>20MB）
Telegram Bot API 限制机器人只能直接下载 20MB 以内的文件；如需转发大于 20MB 的视频到 115 网盘，请配置 Telethon 用户客户端：
1. 在 [Telegram Development Platform](https://my.telegram.org/auth) 申请 `tg_api_id` 与 `tg_api_hash`。
2. 在 `config/config.yaml` 中填写 `bot_name`（注意字段名是 `bot_name`）、`tg_api_id`、`tg_api_hash`。
3. 在 `create_tg_session_file.py` 中填入 `API_ID` 与 `API_HASH` 后执行：
   ```bash
   .venv/bin/python create_tg_session_file.py
   ```
   按提示输入手机号与验证码，将生成的 `user_session.session` 放入 `config/` 目录即可。
> 不配置此步骤完全不影响离线下载、Web 端与 REST API 的正常使用。

### 2. 重要安全提醒
- ⚠️ **目录同步警告**：`/sync` 命令会**删除目标目录下的所有原有文件**（包括元数据），大规模同步亦可能触发 115 风控，请务必核对路径后谨慎使用。
- ⚠️ **公网暴露 Web 端**：建议在 `config.yaml` 中设置高强度 `web.password`，并通过 1Panel / Nginx 反向代理启用 HTTPS。

---

## 本地开发、前端重打包与测试

### 1. 前端重新构建（仅修改 `web-frontend/` 源码时需要）
仓库已包含编译好的静态文件于 `app/web/static/`，直接启动 Python 即可使用。修改前端后可通过 Docker 临时 Node 容器重新构建（无需宿主机安装 Node.js）：
```bash
docker run --rm -v "$(pwd)/web-frontend:/work" -w /work node:22-alpine \
  sh -c "npm install --no-audit --no-fund && npm run build"
rm -rf app/web/static && mkdir -p app/web/static && cp -r web-frontend/dist/. app/web/static/
```

### 2. 运行全套单元测试（无需真实凭证与网络）
```bash
.venv/bin/python -m compileall -q app
.venv/bin/python test_web_api.py
.venv/bin/python test_file_link_parser_and_confirm.py
.venv/bin/python test_batch_api_optimization.py
.venv/bin/python test_runtime_bugfixes.py
.venv/bin/python test_adaptive_polling_and_dedup.py
.venv/bin/python test_panel_and_reactions.py
.venv/bin/python test_startup_token_handling.py
.venv/bin/python test_txt_handler.py
.venv/bin/python test_unified_link_extraction.py
```

---

## 许可证与免责声明

本项目基于 [MIT License](LICENSE) 开源，仅供个人学习与技术研究使用，请遵守相关法律法规与网盘服务条款，使用者需自行承担使用风险。
