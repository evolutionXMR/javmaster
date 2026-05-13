# JavMaster / JAV 管理助手

[English](#english) | [中文](#中文)

---

<a id="english"></a>

## English

JavMaster is a self-hosted FastAPI + Discord.py control panel for managing JAV code watchlists, actress release checks, Sukebei resource searches, and Gopeed downloads. It can run as a web-only app or together with a Discord bot in one container.

> **Legal / adult-content notice:** this project does not include, host, or distribute media files. Use it only for lawful personal automation and follow the laws, copyright rules, and platform policies that apply in your region.

### Features

- Web GUI with login, code watchlist, actress watchlist, Gopeed task view, and settings.
- Optional Discord slash commands for watchlist, search, and download workflows.
- Gopeed integration with configurable API URL, token, and default download path.
- JavBus preview-image proxy to reduce browser hotlink / anti-leech image failures.
- Scheduler settings for code resource searches and actress new-work checks.
- Cleanup helper with protected `.part` files and excluded sorted folders.
- Docker Compose deployment with persistent `data` and `downloads` volumes.

### Quick start with Docker Compose

```bash
git clone <your-repo-url> javmaster
cd javmaster
cp .env.example .env
# Edit .env: WEB_PASSWORD, Gopeed settings, optional Discord token/channel
mkdir -p data downloads
docker compose up -d --build
```

Open `http://localhost:18080`, log in with `WEB_USERNAME` / `WEB_PASSWORD`, then adjust settings in the UI.

### Configuration

All deployment-specific settings are environment variables. Do **not** commit your `.env` file.

| Variable | Purpose | Default |
| --- | --- | --- |
| `WEB_USERNAME` / `WEB_PASSWORD` | Web login | `admin` / `change-me-now` in example |
| `DISCORD_BOT_TOKEN` | Optional Discord bot token | blank |
| `DISCORD_REPORT_CHANNEL_ID` | Optional report channel ID | `0` |
| `GOPEED_URL` | Gopeed root or `/api/v1` URL | `http://gopeed:9999/api/v1` |
| `GOPEED_TOKEN` | Gopeed `X-Api-Token` | blank |
| `GOPEED_DOWNLOAD_PATH` | Path passed to Gopeed as `opt.path` | `/downloads` |
| `DATA_DIR` | Container runtime-data directory | `/app/data` |
| `CLEANUP_PATH` | Container path scanned by cleanup | `/downloads` |
| `DATA_DIR_HOST` | Host data volume path for Compose | `./data` |
| `DOWNLOADS_HOST` | Host downloads volume path for Compose | `./downloads` |
| `JAVBUS_BASE_URL` | Metadata source base URL | `https://www.javbus.com` |

The app also supports per-file overrides such as `WATCHLIST_FILE`, `ACTRESS_WATCHLIST_FILE`, `BOT_TASKS_FILE`, `JAVMASTER_SETTINGS_FILE`, and `JAVMASTER_RESOURCE_STATE_FILE`.

### Discord bot mode

1. Set `DISCORD_BOT_TOKEN` in `.env`.
2. Set `DISCORD_REPORT_CHANNEL_ID` if you want scheduled report messages.
3. Enable Discord Bot in the web settings.
4. Restart the container after changing the token or bot enable/disable setting.

If the token is blank or Discord is disabled, JavMaster runs as Web GUI only.

### Gopeed

In the Settings tab, configure:

- Gopeed API URL, for example `http://192.168.1.10:9999` or `http://192.168.1.10:9999/api/v1`.
- Gopeed API token. The UI never echoes saved tokens back to the browser.
- Default Gopeed download path.

Use the **测试连接 / Test connection** button before saving settings or pushing tasks.

### Files that should not be committed

`.gitignore` excludes local secrets and runtime state, including:

- `.env`
- `discordbotCONFIG.txt`
- `docs/plans/`
- Runtime JSON databases such as `watchlist.json`, `javmaster_settings.json`, etc.
- `backups/`
- `.venv/`, `__pycache__/`, `*.bak*`

### Development

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python main.py
```

Syntax check:

```bash
python3 -m py_compile app_core.py bot.py config.py main.py web.py
```

### Security notes

- Change `WEB_PASSWORD` before exposing the service.
- Do not publish `.env`, Discord tokens, Gopeed tokens, or runtime JSON data.
- Use a reverse proxy with HTTPS for public access.
- Keep Gopeed tokens and Discord tokens scoped and private.

---

<a id="中文"></a>

## 中文

JavMaster 是一个自托管的 FastAPI + Discord.py 控制面板，用来管理 JAV 番号关注列表、女友 / 演员新作检查、Sukebei 资源搜索，以及 Gopeed 下载任务。它可以只作为网页面板运行，也可以在同一个容器里同时运行 Discord Bot。

> **法律 / 成人内容提示：** 本项目不包含、不托管、也不分发任何媒体文件。请仅用于合法的个人自动化，并遵守你所在地区适用的法律、版权规则和平台政策。

### 功能特性

- 带登录的 Web GUI，包含番号关注、女友 / 演员关注、Gopeed 任务查看和设置页面。
- 可选 Discord 斜杠命令，支持关注、搜索、下载等工作流。
- Gopeed 集成，可配置 API 地址、Token 和默认下载路径。
- JavBus 预览图代理，尽量避免浏览器热链 / 防盗链导致的图片加载失败。
- 可配置定时任务，用于番号资源搜索和女友 / 演员新作检查。
- 下载后清理辅助工具，保护 `.part` 未完成文件，并可排除已整理目录。
- 支持 Docker Compose 部署，`data` 与 `downloads` 持久化保存。

### Docker Compose 快速开始

```bash
git clone <your-repo-url> javmaster
cd javmaster
cp .env.example .env
# 编辑 .env：WEB_PASSWORD、Gopeed 设置、可选 Discord token/channel
mkdir -p data downloads
docker compose up -d --build
```

打开 `http://localhost:18080`，使用 `WEB_USERNAME` / `WEB_PASSWORD` 登录，然后在网页设置里调整配置。

### 配置说明

所有部署相关配置都通过环境变量设置。请**不要**提交你的 `.env` 文件。

| 变量 | 用途 | 默认值 |
| --- | --- | --- |
| `WEB_USERNAME` / `WEB_PASSWORD` | 网页登录账号 / 密码 | 示例中为 `admin` / `change-me-now` |
| `DISCORD_BOT_TOKEN` | 可选 Discord Bot Token | 空 |
| `DISCORD_REPORT_CHANNEL_ID` | 可选报告频道 ID | `0` |
| `GOPEED_URL` | Gopeed 根地址或 `/api/v1` 地址 | `http://gopeed:9999/api/v1` |
| `GOPEED_TOKEN` | Gopeed `X-Api-Token` | 空 |
| `GOPEED_DOWNLOAD_PATH` | 传给 Gopeed 的 `opt.path` 下载路径 | `/downloads` |
| `DATA_DIR` | 容器内运行数据目录 | `/app/data` |
| `CLEANUP_PATH` | 容器内清理扫描路径 | `/downloads` |
| `DATA_DIR_HOST` | Compose 使用的宿主机数据目录 | `./data` |
| `DOWNLOADS_HOST` | Compose 使用的宿主机下载目录 | `./downloads` |
| `JAVBUS_BASE_URL` | 元数据来源基础地址 | `https://www.javbus.com` |

应用也支持按文件覆盖配置，例如 `WATCHLIST_FILE`、`ACTRESS_WATCHLIST_FILE`、`BOT_TASKS_FILE`、`JAVMASTER_SETTINGS_FILE`、`JAVMASTER_RESOURCE_STATE_FILE`。

### Discord Bot 模式

1. 在 `.env` 里设置 `DISCORD_BOT_TOKEN`。
2. 如果需要定时报告消息，设置 `DISCORD_REPORT_CHANNEL_ID`。
3. 在网页设置里启用 Discord Bot。
4. 修改 Token 或启用 / 禁用 Bot 后，重启容器。

如果 Token 为空，或 Discord 功能被禁用，JavMaster 会只以 Web GUI 模式运行。

### Gopeed

在设置页面中配置：

- Gopeed API 地址，例如 `http://192.168.1.10:9999` 或 `http://192.168.1.10:9999/api/v1`。
- Gopeed API Token。网页不会把已保存的 Token 回显给浏览器。
- 默认 Gopeed 下载路径。

保存设置或推送任务前，建议先使用 **测试连接 / Test connection** 按钮确认可用。

### 不应该提交的文件

`.gitignore` 已排除本地密钥和运行状态文件，包括：

- `.env`
- `discordbotCONFIG.txt`
- `docs/plans/`
- 运行时 JSON 数据库，例如 `watchlist.json`、`javmaster_settings.json` 等
- `backups/`
- `.venv/`、`__pycache__/`、`*.bak*`

### 本地开发

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python main.py
```

语法检查：

```bash
python3 -m py_compile app_core.py bot.py config.py main.py web.py
```

### 安全提示

- 对外开放服务前，请先修改 `WEB_PASSWORD`。
- 不要公开 `.env`、Discord Token、Gopeed Token 或运行时 JSON 数据。
- 如需公网访问，建议使用带 HTTPS 的反向代理。
- Gopeed Token 和 Discord Token 应保持私密，并尽量限制权限范围。
