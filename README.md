# JavMaster

JavMaster is a self-hosted FastAPI + Discord.py control panel for managing JAV watchlists, actress release checks, Sukebei resource searches, and Gopeed downloads. It can run as a web-only app or together with a Discord bot in one container.

> Legal / adult-content notice: this project does not include or distribute media files. Use it only for lawful personal automation and follow the laws and platform rules that apply in your region.

## Features

- Web GUI with login, code watchlist, actress watchlist, Gopeed task view, and settings.
- Optional Discord slash commands for watchlist/search/download workflows.
- Gopeed integration with configurable API URL, token, and default download path.
- JavBus preview-image proxy to avoid browser hotlink failures.
- Scheduler settings for code resource searches and actress new-work checks.
- Cleanup helper with protected `.part` files and excluded sorted folders.

## Quick start with Docker Compose

```bash
git clone <your-repo-url> javmaster
cd javmaster
cp .env.example .env
# edit .env: WEB_PASSWORD, Gopeed settings, optional Discord token/channel
mkdir -p data downloads
docker compose up -d --build
```

Open `http://localhost:18080`, log in with `WEB_USERNAME` / `WEB_PASSWORD`, then adjust settings in the UI.

## Configuration

All deploy-specific settings are environme