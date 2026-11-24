

# 修改点：
- [x] 支持多用户配置（`allowed_user` 支持单个用户 ID 或用户 ID 列表）
- [x] 支持多个磁力 / ed2k / 迅雷链接批量添加离线任务，支持短时间多条消息自动合并（`msgs_merge_interval`）
- [x] 支持上传 `.txt` 文件统一提取链接并批量离线下载
- [x] 批量下载时自动识别文本【首行 + 尾行文字】作为文件夹名，下载完成后支持 `移动到[文件夹名]` 批量整理
- [x] 新增批量任务 SQLite 持久化与失败任务定时重试，重复离线任务（`code 10008`）自动删除记录并重提
- [x] 115 API 增加线程安全速率限制（1 req/s）与分批删除（每批 100 条 + 失败重试）
- [x] 优化本地启动与 115 授权容错：自动补齐 `app` 子模块路径；115 Token 过期/解除授权时降级运行不崩溃，支持直接发 `/auth` 扫码重授权（并修复扫码等待阶段 `status` 判空异常）
- [x] 移植上游 Telegram 连接稳定性优化：`HTTPXRequest` 显式代理与 keep-alive 回收防 502、`concurrent_updates(4)`、`allowed_updates=Update.ALL_TYPES` 防按钮无响应、全局错误与原始回调日志
---------------------
# 以下为原 版本的README 说明 
<div align="center">
    <h1>115Bot - Telegram 机器人</h1>
    <p>简体中文 | <a href="./README_EN.md">[English]</a> </p>
</div>

一个基于 Python 的 Telegram 机器人，用于管理和控制 115 网盘，支持离线下载、视频上传、目录同步等功能。

## Tg讨论群

使用问题 & Bug反馈

[加入](https://t.me/+FTPNla_7SCc3ZWVl)

## 部署&使用

📖[部署&使用](https://github.com/qiqiandfei/Telegram-115bot/wiki)


### 目录结构
```
.
├── app
│   ├── 115bot.py                 # 程序入口脚本
│   ├── config.yaml.example       # 配置文件模板
│   ├── core                      # 核心功能
│   ├── handlers                  # Telegram handlers
│   ├── images                    # 图片
│   ├── init.py                   # 初始化脚本
│   └── utils                     # 有用的工具
├── build.sh                      # 本地构建脚本
├── config                        # 配置目录
├── create_tg_session_file.py     # 创建tg_session脚本
├── docker-compose.yaml           # docker-compose
├── Dockerfile                    
├── Dockerfile.base
├── legacy                        # 历史遗留
├── LICENSE
├── README_EN.md
├── README.md
├── requirements.txt              # 项目依赖
```

## 使用指南

### 基本命令

- `/start`   - 显示帮助信息
- `/auth`    - 115 授权设置
- `reload`   - 重载配置
- `/dl`      - 添加离线下载
- `/rl`      - 重试列表
- `/av`      - 番号下载
- `/sm`      - 订阅电影
- `/sync`    - 同步目录并创建软链
- `/q`       - 取消当前会话

### 115 开放平台申请

**强烈建议申请 115 开放平台以获得更好的使用体验！**
- 申请地址：[115开放平台](https://open.115.com/)
- 审核通过后将 `115_app_id` 填入配置文件中

如不想使用 115 开放平台，请使用之前的镜像版本 `qiqiandfei/115-bot:v2.3.7`

### 视频下载配置

由于 Telegram Bot API 限制，无法下载超过 20MB 的视频文件。如需下载大视频，请配置 Telegram 客户端：

#### 配置方法
Telegram API申请地址：[Telegram Development Platform](https://my.telegram.org/auth)

申请成功后可以获取到tg_api_id和tg_api_hash

确保配置文件中以下三个参数配置正确：
```
# bot_name
bot_name: "@yourbotname"

# telegram 的api信息
tg_api_id: 1122334
tg_api_hash: 1yh3j4k9dsk0fj3jdufnwrhf62j1k33f
```
**生成 user_session的方法**
1. 修改create_tg_session_file.py中的 API_ID 和 API_HASH
2. 运行脚本：python create_tg_session_file.py
3. 按照提示输入手机号和验证码
4. 将生成的 user_session.session 文件放到 config 目录

> **注意**：如果不配置此步骤，机器人仍可正常运行，只是无法处理超过 20MB 的视频文件。

### 重要提醒

⚠️ **同步功能警告**：`/sync` 命令会**删除目标目录下的所有文件**，包括元数据。大规模同步操作可能触发 115 网盘风控机制，请谨慎使用！
## 本地调试

### 1. 准备 Python 虚拟环境（Python 3.12+ / 3.13）

方式一（使用 `uv`，推荐）：
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv venv
uv pip install -r requirements.txt
```

方式二（使用标准库 `venv` + `pip`）：
```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

> 若系统缺少 `python3-venv` / `ensurepip`（如精简版 Debian 13），可用 `--without-pip` 建环境后通过 `get-pip.py` 安装 `pip`：
> ```bash
> rm -rf .venv && python3 -m venv --without-pip .venv
> curl -fsSL https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
> .venv/bin/python /tmp/get-pip.py && rm -f /tmp/get-pip.py
> .venv/bin/pip install -r requirements.txt
> ```
> 若宿主机升级过 Python 主版本（如 3.9 → 3.13）导致旧 `.venv` 报 `ModuleNotFoundError`，删除 `.venv` 按上述命令重建即可。

### 2. 准备配置文件
```bash
cp config/config.yaml.example config/config.yaml
```
按需填写 `bot_token`、`allowed_user`、`115_app_id` 等字段：
- 调试建议将 `log_level` 设为 `debug`（日志会同时输出到终端与 `config/115bot.log`）。
- 如需大视频上传功能，注意字段名为 `bot_name`（不是 `bote_name`），并按上文生成 `config/user_session.session`。
- 切勿提交 `config/config.yaml`、`config/115_tokens.json`、`config/*.db`、`app/images/qrcode.png`、`*.log` 等敏感/运行期文件（已加入 `.gitignore`）。

### 3. 代理连通性自检（如服务器无法直连 Telegram）
先用 3 秒快速确认本地代理（如 mihomo）可访问 Telegram Bot API：
```bash
curl -I --max-time 5 -x http://127.0.0.1:<代理端口> https://api.telegram.org
```
看到返回 HTTP 状态码即表示代理正常；可直连 Telegram 的机器可跳过代理配置。

### 4. 前台启动调试
项目启动时已自动将 `app`、`app/core`、`app/utils`、`app/handlers` 加入 `sys.path`，无需手动拼接 `PYTHONPATH`。建议将 115 域名加入 `NO_PROXY` 走直连：
```bash
export HTTP_PROXY="http://127.0.0.1:<代理端口>"
export HTTPS_PROXY="http://127.0.0.1:<代理端口>"
export NO_PROXY="localhost,127.0.0.1,::1,proapi.115.com,passportapi.115.com,qrcodeapi.115.com"

.venv/bin/python app/115bot.py
```
按 `Ctrl+C` 即可随时停止。

### 5. 启动验收与 Telegram 实测
观察终端日志依次出现以下信息即说明启动正常：
1. `✅ Auth处理器已注册` 等全部 9 个处理器注册完成
2. `Scheduler started`（定时任务调度器启动）
3. `Bot菜单命令已设置!`（Telegram Bot API 连通正常，无 `Timed out`）

随后在 Telegram 中向机器人发送命令验证：
- 发送 `/start`：确认能正常收到使用手册回复；
- 若日志提示 115 Token 过期或授权已解除（如 `40140116`），机器人会自动降级保持在线，直接发送 `/auth` 接收二维码并用 115 App 扫码完成重新授权，重启后即可正常显示空间与配额欢迎消息。

### 6. 日志追踪、断点调试与单元测试
- **实时查看日志文件**：另开终端执行 `tail -f config/115bot.log`。
- **终端断点调试**：在目标代码行插入 `breakpoint()`，前台运行 `.venv/bin/python app/115bot.py` 即可进入交互式 `pdb`。
- **VS Code / code-server 调试**：将 Python 解释器选为 `.venv/bin/python`，直接调试 `app/115bot.py` 并在 `launch.json` 的 `env` 中配置上述代理变量。
- **运行本地单元测试**（无需真实凭证与网络）：
  ```bash
  .venv/bin/python -m compileall -q app
  .venv/bin/python test_startup_token_handling.py
  .venv/bin/python test_rate_limiter_simple.py
  .venv/bin/python test_folder_extraction.py
  ```

## 许可证
```
MIT License

Copyright (c) 2025 qiqiandfei

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## 免责声明
本项目仅供学习和研究使用，请遵守相关法律法规，不得用于商业用途。使用者需自行承担使用风险！

如果这个项目对您有帮助，请献上一个⭐！

## Buy me a coffee~
![请我喝咖啡](https://alist.qiqiandfei.fun:8843/d/Syncthing/yufei/%E4%B8%AA%E4%BA%BA/%E8%B5%9E%E8%B5%8F.png)