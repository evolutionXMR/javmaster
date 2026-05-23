# JavMaster / JAV 管理助手

[中文](#中文) | [English](#english)

---

<a id="中文"></a>

## 中文

JavMaster 是一个自托管的 FastAPI + Discord.py JAV 管理面板，用来管理番号关注列表、女优 / 演员新作检查、Sukebei 资源搜索、下载器任务，以及下载完成后的影片刮削整理。它可以只作为 Web GUI 运行，也可以在同一个容器里同时运行 Discord Bot。

> **法律 / 成人内容提示：** 本项目不包含、不托管、也不分发任何媒体文件。请仅用于合法的个人自动化，并遵守你所在地区适用的法律、版权规则和平台政策。

### 功能特性

- 带登录的 Web GUI：番号关注、女优 / 演员关注、下载任务、完成任务、系统设置。
- 可选 Discord Bot：支持关注、搜索、下载、任务查看和清理等工作流。
- 多下载器支持：Gopeed、qBittorrent、Aria2，可在设置页面切换和测试连接。
- Sukebei 资源搜索与定时检查：可配置番号资源搜索和女优 / 演员新作检查计划。
- JavBus 元数据与预览图代理：尽量减少浏览器热链 / 防盗链导致的图片加载失败。
- 下载完成列表支持一键刮削：识别完成任务中的视频文件，抓取元数据，生成 NFO 与图片，并移动到整理目录。
- 刮削成功后可选自动从下载完成列表删除任务记录；删除任务记录时不会删除已整理好的影片文件。
- 下载后清理工具：保护 `.part` 未完成文件，并排除 `JAV_Sorted`、`toBeSorted`、`MDC_Failed`、`Chinese_Sorted` 等已整理 / 中间目录。
- Docker Compose 部署，支持 `data` 与 `downloads` 持久化挂载。

### 最新刮削整理规则

刮削目标目录默认为容器内：

```text
/downloads/JAV_Sorted
```

对应宿主机通常是：

```text
/mnt/Main/Himitsu/video/JAV_Sorted
```

整理后的目录结构：

```text
JAV_Sorted/
  <演员文件夹>/
    <基础番号>/
      <文件名番号>.<视频扩展名>
      <文件名番号>.nfo
      poster.jpg
      thumb.jpg
      extrafanart/
```

演员文件夹规则：

| 演员数量 | 文件夹名 |
| --- | --- |
| 0 个 | `未知女优` |
| 1 个 | `演员名` |
| 2-3 个 | 使用所有演员名，并用英文逗号 `,` 连接，例如 `美咲かんな,前田美波` |
| 超过 3 个 | `多人作品` |

普通影片示例：

```text
JAV_Sorted/
  美咲かんな,前田美波/
    ABCD-123/
      ABCD-123.mp4
      ABCD-123.nfo
      poster.jpg
      thumb.jpg
      extrafanart/
```

中文字幕影片规则：文件名或目录名里的番号如果以 `ch` 结尾，例如 `PRWF-010ch.mp4`，表示这是 `PRWF-010` 的中文字幕版本。

处理方式：

- 元数据抓取使用基础番号：`PRWF-010`
- 文件夹使用基础番号：`PRWF-010/`
- 影片和 NFO 文件名使用：`PRWF-010-C`
- NFO 中自动加入：`中文字幕`，同时作为 `<genre>` 和 `<tag>`

中文字幕示例：

```text
JAV_Sorted/
  美咲かんな,前田美波/
    PRWF-010/
      PRWF-010-C.mp4
      PRWF-010-C.nfo
      poster.jpg
      thumb.jpg
      extrafanart/
```

NFO 关键字段示例：

```xml
<num>PRWF-010-C</num>
<id>PRWF-010-C</id>
<genre>中文字幕</genre>
<tag>中文字幕</tag>
```

### Docker Compose 快速开始

```bash
git clone <your-repo-url> javmaster
cd javmaster
cp .env.example .env
# 编辑 .env：WEB_PASSWORD、下载器设置、可选 Discord token/channel
mkdir -p data downloads
docker compose up -d --build
```

打开：

```text
http://localhost:18080
```

使用 `WEB_USERNAME` / `WEB_PASSWORD` 登录，然后在网页设置里调整下载器、Discord、定时任务和刮削输出目录。

### 配置说明

部署相关配置主要通过环境变量和 Web 设置页管理。请**不要**提交你的 `.env` 文件。

| 变量 | 用途 | 默认值 |
| --- | --- | --- |
| `TZ` | 容器时区 | `Australia/Sydney` |
| `CONTAINER_NAME` | Compose 容器名 | `javmaster` |
| `PUID` / `PGID` | 容器运行 UID/GID | 示例中通常按部署环境设置 |
| `WEB_PORT` | Web 端口 | `18080` |
| `WEB_USERNAME` / `WEB_PASSWORD` | 网页登录账号 / 密码 | 示例中为 `admin` / 自行设置 |
| `DISCORD_BOT_TOKEN` | 可选 Discord Bot Token | 空 |
| `DISCORD_REPORT_CHANNEL_ID` | 可选报告频道 ID | `0` |
| `GOPEED_URL` | Gopeed 根地址或 `/api/v1` 地址 | `http://gopeed:9999/api/v1` |
| `GOPEED_TOKEN` | Gopeed `X-Api-Token` | 空 |
| `GOPEED_DOWNLOAD_PATH` | 传给 Gopeed 的下载路径 | `/downloads` |
| `DATA_DIR` | 容器内运行数据目录 | `/app/data` |
| `CLEANUP_PATH` | 容器内清理扫描路径 | `/downloads` |
| `DATA_DIR_HOST` | Compose 使用的宿主机数据目录 | `./data` |
| `DOWNLOADS_HOST` | Compose 使用的宿主机下载目录 | `./downloads` |
| `JAVBUS_BASE_URL` | 元数据来源基础地址 | `https://www.javbus.com` |

应用也支持按文件覆盖配置，例如：`WATCHLIST_FILE`、`ACTRESS_WATCHLIST_FILE`、`BOT_TASKS_FILE`、`JAVMASTER_SETTINGS_FILE`、`JAVMASTER_RESOURCE_STATE_FILE`。

### 下载器设置

在设置页面中选择当前下载器：Gopeed、qBittorrent 或 Aria2。每个下载器都有独立的连接参数和测试按钮。保存设置或推送下载任务前，建议先使用 **测试连接** 确认可用。

### Discord Bot 模式

1. 在 `.env` 中设置 `DISCORD_BOT_TOKEN`。
2. 如果需要定时报告消息，设置 `DISCORD_REPORT_CHANNEL_ID`。
3. 在网页设置里启用 Discord Bot。
4. 修改 Token 或启用 / 禁用 Bot 后，重启容器。

如果 Token 为空，或 Discord 功能被禁用，JavMaster 会只以 Web GUI 模式运行。

### 不应该提交的文件

`.gitignore` 已排除本地密钥和运行状态文件，包括 `.env`、`discordbotCONFIG.txt`、`docs/plans/`、运行时 JSON 数据库、`backups/`、`.venv/`、`__pycache__/`、`*.bak*`。

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
python3 -m py_compile app_core.py bot.py config.py main.py web.py jav_scraper.py
python3 tests/test_jav_scraper_contract.py
```

### 安全提示

- 对外开放服务前，请先修改 `WEB_PASSWORD`。
- 不要公开 `.env`、Discord Token、下载器 Token / 密码或运行时 JSON 数据。
- 如需公网访问，建议使用带 HTTPS 的反向代理。
- 下载器和 Discord 的凭据应保持私密，并尽量限制权限范围。

---

<a id="english"></a>

## English

JavMaster is a self-hosted FastAPI + Discord.py control panel for managing JAV code watchlists, actress/actor release checks, Sukebei resource searches, downloader tasks, and post-download movie scraping. It can run as a web-only app or together with a Discord bot in one container.

> **Legal / adult-content notice:** this project does not include, host, or distribute media files. Use it only for lawful personal automation and follow the laws, copyright rules, and platform policies that apply in your region.

### Features

- Login-protected Web GUI for code watchlists, actress/actor watchlists, download tasks, completed tasks, and settings.
- Optional Discord bot for watchlist, search, download, task-view, and cleanup workflows.
- Multiple downloader backends: Gopeed, qBittorrent, and Aria2, selectable and testable from the settings page.
- Sukebei resource search and scheduled checks for watched codes and actress/actor new releases.
- JavBus metadata and preview-image proxy to reduce hotlink / anti-leech image failures.
- One-click scraping from the completed-download list: detect the completed video, fetch metadata, write NFO/images, and move the movie into the sorted library.
- Optional removal of the completed downloader task record after successful scraping; this does not delete the sorted movie file.
- Cleanup helper that protects `.part` files and excludes sorted/intermediate folders such as `JAV_Sorted`, `toBeSorted`, `MDC_Failed`, and `Chinese_Sorted`.
- Docker Compose deployment with persistent `data` and `downloads` volumes.

### Current scraping layout

The default in-container scraping output is `/downloads/JAV_Sorted`. A typical host path is `/mnt/Main/Himitsu/video/JAV_Sorted`.

Sorted output layout:

```text
JAV_Sorted/
  <ACTOR_FOLDER>/
    <BASE_CODE>/
      <FILE_STEM>.<video_ext>
      <FILE_STEM>.nfo
      poster.jpg
      thumb.jpg
      extrafanart/
```

Actor folder rules:

| Actor count | Folder name |
| --- | --- |
| 0 | `未知女优` |
| 1 | actor name |
| 2-3 | all actor names joined with an ASCII comma `,`, e.g. `美咲かんな,前田美波` |
| more than 3 | `多人作品` |

Normal movie example:

```text
JAV_Sorted/
  美咲かんな,前田美波/
    ABCD-123/
      ABCD-123.mp4
      ABCD-123.nfo
      poster.jpg
      thumb.jpg
      extrafanart/
```

Chinese-subtitle rule: if the local filename or folder code ends with `ch`, for example `PRWF-010ch.mp4`, JavMaster treats it as the Chinese-subtitled local copy of `PRWF-010`.

Behavior:

- Metadata lookup uses the base code: `PRWF-010`
- The folder uses the base code: `PRWF-010/`
- The video and NFO stem use: `PRWF-010-C`
- The NFO automatically includes `中文字幕` as both `<genre>` and `<tag>`

Chinese-subtitle example:

```text
JAV_Sorted/
  美咲かんな,前田美波/
    PRWF-010/
      PRWF-010-C.mp4
      PRWF-010-C.nfo
      poster.jpg
      thumb.jpg
      extrafanart/
```

Key NFO fields:

```xml
<num>PRWF-010-C</num>
<id>PRWF-010-C</id>
<genre>中文字幕</genre>
<tag>中文字幕</tag>
```

### Quick start with Docker Compose

```bash
git clone <your-repo-url> javmaster
cd javmaster
cp .env.example .env
# Edit .env: WEB_PASSWORD, downloader settings, optional Discord token/channel
mkdir -p data downloads
docker compose up -d --build
```

Open `http://localhost:18080`, log in with `WEB_USERNAME` / `WEB_PASSWORD`, then adjust downloader, Discord, scheduler, and scraping settings in the UI.

### Configuration

Deployment-specific settings are managed through environment variables and the Web settings page. Do **not** commit your `.env` file.

| Variable | Purpose | Default |
| --- | --- | --- |
| `TZ` | Container timezone | `Australia/Sydney` |
| `CONTAINER_NAME` | Compose container name | `javmaster` |
| `PUID` / `PGID` | Container runtime UID/GID | deployment-specific |
| `WEB_PORT` | Web port | `18080` |
| `WEB_USERNAME` / `WEB_PASSWORD` | Web login | `admin` / set your own password |
| `DISCORD_BOT_TOKEN` | Optional Discord bot token | blank |
| `DISCORD_REPORT_CHANNEL_ID` | Optional report channel ID | `0` |
| `GOPEED_URL` | Gopeed root or `/api/v1` URL | `http://gopeed:9999/api/v1` |
| `GOPEED_TOKEN` | Gopeed `X-Api-Token` | blank |
| `GOPEED_DOWNLOAD_PATH` | Path passed to Gopeed | `/downloads` |
| `DATA_DIR` | Container runtime-data directory | `/app/data` |
| `CLEANUP_PATH` | Container path scanned by cleanup | `/downloads` |
| `DATA_DIR_HOST` | Host data volume path for Compose | `./data` |
| `DOWNLOADS_HOST` | Host downloads volume path for Compose | `./downloads` |
| `JAVBUS_BASE_URL` | Metadata source base URL | `https://www.javbus.com` |

The app also supports per-file overrides such as `WATCHLIST_FILE`, `ACTRESS_WATCHLIST_FILE`, `BOT_TASKS_FILE`, `JAVMASTER_SETTINGS_FILE`, and `JAVMASTER_RESOURCE_STATE_FILE`.

### Downloader settings

Select the active downloader in the settings page: Gopeed, qBittorrent, or Aria2. Each downloader has its own connection settings and test button. Use **Test connection** before saving settings or pushing download tasks.

### Discord bot mode

1. Set `DISCORD_BOT_TOKEN` in `.env`.
2. Set `DISCORD_REPORT_CHANNEL_ID` if you want scheduled report messages.
3. Enable Discord Bot in the web settings.
4. Restart the container after changing the token or bot enable/disable setting.

If the token is blank or Discord is disabled, JavMaster runs as Web GUI only.

### Files that should not be committed

`.gitignore` excludes local secrets and runtime state, including `.env`, `discordbotCONFIG.txt`, `docs/plans/`, runtime JSON databases, `backups/`, `.venv/`, `__pycache__/`, and `*.bak*`.

### Development

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python main.py
```

Syntax / contract checks:

```bash
python3 -m py_compile app_core.py bot.py config.py main.py web.py jav_scraper.py
python3 tests/test_jav_scraper_contract.py
```

### Security notes

- Change `WEB_PASSWORD` before exposing the service.
- Do not publish `.env`, Discord tokens, downloader tokens/passwords, or runtime JSON data.
- Use a reverse proxy with HTTPS for public access.
- Keep downloader and Discord credentials private and scoped where possible.
