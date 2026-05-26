import asyncio
import hashlib
import hmac
import json
import os
import re
import shutil
import time
from pathlib import Path
import xml.etree.ElementTree as ET
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import quote

import aiohttp

from config import (
    BOT_TASKS_FILE, CLEANUP_PATH, DATA_DIR, DEFAULT_GOPEED_DOWNLOAD_PATH,
    GOPEED_TOKEN, GOPEED_URL, JAVMASTER_RESOURCE_STATE_FILE,
    JAVMASTER_SETTINGS_FILE, WATCHLIST_FILE,
)

from jav_scraper import scrape_movie as scrape_single_movie

ACTRESS_WATCHLIST_FILE = os.environ.get("ACTRESS_WATCHLIST_FILE", f"{DATA_DIR}/actress_watchlist.json")
SETTINGS_FILE = JAVMASTER_SETTINGS_FILE
RESOURCE_STATE_FILE = JAVMASTER_RESOURCE_STATE_FILE
DOTENV_FILE = os.environ.get("JAVMASTER_DOTENV_FILE", os.path.join(os.getcwd(), ".env"))
CODE_RE = re.compile(r"\b[A-Z]{2,8}-\d{2,5}\b")
NYAA_NS = "{https://sukebei.nyaa.si/xmlns/nyaa}"
DEFAULT_SETTINGS = {
    "language": "zh",
    "discord_enabled": True,
    "bot_token_set": True,
    # Backward compatible legacy fields.
    "auto_code_search": False,
    "auto_code_search_interval_min": 30,
    # New scheduler fields.
    "code_search_enabled": False,
    "code_search_schedule_mode": "interval",  # interval | daily
    "code_search_interval_hours": 6,
    "code_search_daily_time": "09:00",
    "actress_search_enabled": False,
    "actress_search_schedule_mode": "interval",  # interval | daily
    "actress_search_interval_hours": 12,
    "actress_search_daily_time": "09:30",
    "preview_images": True,
    "auto_remove_code_after_push": False,
    # Downloader connection overrides. Empty secrets mean use saved/config fallback.
    "download_client": "gopeed",  # gopeed | qbittorrent | aria2
    "gopeed_url": GOPEED_URL,
    "gopeed_username": "",
    "gopeed_download_path": DEFAULT_GOPEED_DOWNLOAD_PATH,
    "gopeed_token_set": bool(GOPEED_TOKEN),
    "qbittorrent_url": "http://192.168.8.88:8080",
    "qbittorrent_username": "admin",
    "qbittorrent_download_path": "/downloads",
    "qbittorrent_password_set": False,
    "aria2_url": "http://192.168.8.88:6800/jsonrpc",
    "aria2_secret_set": False,
    "aria2_download_path": "/downloads",
    "scrape_output_path": "/downloads/JAV_Sorted",
    "scrape_remove_task_after_success": False,
    "jellyfin_enabled": False,
    "jellyfin_url": "",
    "jellyfin_api_key_set": False,
}
WEB_PASSWORD_SALT = "javmaster:v1:"
DEFAULT_WEB_USERNAME = os.environ.get("WEB_USERNAME", "admin")
DEFAULT_WEB_PASSWORD_HASH = hashlib.sha256((WEB_PASSWORD_SALT + os.environ.get("WEB_PASSWORD", "change-me-now")).encode("utf-8")).hexdigest()


async def load_json(path, default=None):
    def _load():
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return [] if default is None else default
        return [] if default is None else default
    return await asyncio.to_thread(_load)


async def save_json(path, data):
    def _save():
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    await asyncio.to_thread(_save)


def hash_web_password(password):
    return hashlib.sha256((WEB_PASSWORD_SALT + str(password)).encode("utf-8")).hexdigest()


async def get_web_auth_settings():
    settings = await load_json(SETTINGS_FILE, {})
    if not isinstance(settings, dict):
        settings = {}
    return {
        "username": str(settings.get("web_username") or DEFAULT_WEB_USERNAME),
        "password_hash": str(settings.get("web_password_hash") or DEFAULT_WEB_PASSWORD_HASH),
    }


async def verify_web_login(username, password):
    auth = await get_web_auth_settings()
    return hmac.compare_digest(str(username or ""), auth["username"]) and hmac.compare_digest(hash_web_password(password or ""), auth["password_hash"])


async def change_web_password(current_password, new_password, username=None):
    auth = await get_web_auth_settings()
    if not hmac.compare_digest(hash_web_password(current_password or ""), auth["password_hash"]):
        return {"ok": False, "error": "当前密码不正确"}
    new_password = str(new_password or "")
    if len(new_password) < 6:
        return {"ok": False, "error": "新密码至少需要 6 位"}
    settings = await load_json(SETTINGS_FILE, {})
    if not isinstance(settings, dict):
        settings = {}
    if username is not None and str(username).strip():
        settings["web_username"] = str(username).strip()
    else:
        settings["web_username"] = auth["username"]
    settings["web_password_hash"] = hash_web_password(new_password)
    await save_json(SETTINGS_FILE, settings)
    return {"ok": True, "username": settings["web_username"]}


async def get_settings():
    settings = await load_json(SETTINGS_FILE, {})
    if not isinstance(settings, dict):
        settings = {}
    merged = dict(DEFAULT_SETTINGS)
    merged.update(settings)
    # Migrate legacy code-search setting into the new scheduler fields when absent.
    if "code_search_enabled" not in settings:
        merged["code_search_enabled"] = bool(settings.get("auto_code_search", False))
    if "code_search_interval_hours" not in settings and settings.get("auto_code_search_interval_min"):
        try:
            merged["code_search_interval_hours"] = max(1, round(float(settings.get("auto_code_search_interval_min")) / 60, 2))
        except Exception:
            pass
    try:
        from config import TOKEN
        merged["bot_token_set"] = bool(TOKEN and not str(TOKEN).startswith("PUT_"))
    except Exception:
        merged["bot_token_set"] = False
    client = str(merged.get("download_client") or "gopeed").lower()
    if client not in {"gopeed", "qbittorrent", "aria2"}:
        client = "gopeed"
    merged["download_client"] = client
    merged["gopeed_url"] = str(merged.get("gopeed_url") or GOPEED_URL)
    merged["gopeed_username"] = str(merged.get("gopeed_username") or "")
    merged["gopeed_download_path"] = str(merged.get("gopeed_download_path") or "/app/Downloads/video")
    merged["gopeed_token_set"] = bool(settings.get("gopeed_token") or GOPEED_TOKEN)
    merged["qbittorrent_url"] = str(merged.get("qbittorrent_url") or "http://192.168.8.88:8080")
    merged["qbittorrent_username"] = str(merged.get("qbittorrent_username") or "")
    merged["qbittorrent_download_path"] = str(merged.get("qbittorrent_download_path") or "/downloads")
    merged["qbittorrent_password_set"] = bool(settings.get("qbittorrent_password"))
    merged["aria2_url"] = str(merged.get("aria2_url") or "http://192.168.8.88:6800/jsonrpc")
    merged["aria2_download_path"] = str(merged.get("aria2_download_path") or "/downloads")
    merged["aria2_secret_set"] = bool(settings.get("aria2_secret"))
    merged["scrape_output_path"] = str(merged.get("scrape_output_path") or "/downloads/JAV_Sorted")
    merged["scrape_remove_task_after_success"] = bool(merged.get("scrape_remove_task_after_success", False))
    merged["jellyfin_enabled"] = bool(merged.get("jellyfin_enabled", False))
    merged["jellyfin_url"] = str(merged.get("jellyfin_url") or "").rstrip("/")
    merged["jellyfin_api_key_set"] = bool(settings.get("jellyfin_api_key"))
    # Never return persisted secrets to the browser.
    for secret_key in ("gopeed_token", "qbittorrent_password", "aria2_secret", "jellyfin_api_key"):
        merged.pop(secret_key, None)
    return merged


async def save_settings(payload):
    raw = await load_json(SETTINGS_FILE, {})
    if not isinstance(raw, dict):
        raw = {}
    current = await get_settings()
    allowed = {
        "language", "discord_enabled", "auto_code_search", "auto_code_search_interval_min", "preview_images", "auto_remove_code_after_push",
        "code_search_enabled", "code_search_schedule_mode", "code_search_interval_hours", "code_search_daily_time",
        "actress_search_enabled", "actress_search_schedule_mode", "actress_search_interval_hours", "actress_search_daily_time",
        "download_client",
        "gopeed_url", "gopeed_username", "gopeed_download_path",
        "qbittorrent_url", "qbittorrent_username", "qbittorrent_download_path",
        "aria2_url", "aria2_download_path",
        "scrape_output_path", "scrape_remove_task_after_success",
        "jellyfin_enabled", "jellyfin_url",
    }
    for key in allowed:
        if key in payload:
            raw[key] = payload[key]
            current[key] = payload[key]
    if "gopeed_token" in payload and str(payload["gopeed_token"]).strip():
        raw["gopeed_token"] = str(payload["gopeed_token"]).strip()
        raw["gopeed_token_set"] = True
        current["gopeed_token_set"] = True
    if "qbittorrent_password" in payload and str(payload["qbittorrent_password"]).strip():
        raw["qbittorrent_password"] = str(payload["qbittorrent_password"]).strip()
        raw["qbittorrent_password_set"] = True
        current["qbittorrent_password_set"] = True
    if "aria2_secret" in payload and str(payload["aria2_secret"]).strip():
        raw["aria2_secret"] = str(payload["aria2_secret"]).strip()
        raw["aria2_secret_set"] = True
        current["aria2_secret_set"] = True
    if "jellyfin_api_key" in payload and str(payload["jellyfin_api_key"]).strip():
        raw["jellyfin_api_key"] = str(payload["jellyfin_api_key"]).strip()
        raw["jellyfin_api_key_set"] = True
        current["jellyfin_api_key_set"] = True
    if "bot_token" in payload and str(payload["bot_token"]).strip():
        await update_config_token(str(payload["bot_token"]).strip())
        raw["bot_token_set"] = True
        current["bot_token_set"] = True
    for key, value in DEFAULT_SETTINGS.items():
        raw.setdefault(key, current.get(key, value))
    await save_json(SETTINGS_FILE, raw)
    current["restart_required"] = "bot_token" in payload or "discord_enabled" in payload
    return current


def _quote_env_value(value):
    value = str(value or "")
    if not value or any(ch.isspace() or ch in value for ch in ['#', '"', "'"]):
        return json.dumps(value, ensure_ascii=False)
    return value


def set_dotenv_value(key, value):
    path = DOTENV_FILE
    lines = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    out = []
    replaced = False
    for line in lines:
        if re.match(rf"^\s*{re.escape(key)}\s*=", line):
            out.append(f"{key}={_quote_env_value(value)}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"{key}={_quote_env_value(value)}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out).rstrip() + "\n")


async def update_config_token(token):
    # Open-source safe behavior: never rewrite config.py with secrets. Store runtime
    # tokens in .env; Docker Compose reads it on the next container restart.
    await asyncio.to_thread(set_dotenv_value, "DISCORD_BOT_TOKEN", token)


def normalize_gopeed_url(url):
    url = str(url or GOPEED_URL).strip() or GOPEED_URL
    # Accept either the Gopeed root (http://host:9999) or the REST base (http://host:9999/api/v1).
    if not url.rstrip("/").endswith("/api/v1"):
        url = url.rstrip("/") + "/api/v1"
    return url


async def gopeed_connection_settings(include_secret=False, overrides=None):
    raw = await load_json(SETTINGS_FILE, {})
    if not isinstance(raw, dict):
        raw = {}
    overrides = overrides or {}
    url = normalize_gopeed_url(overrides.get("gopeed_url") or raw.get("gopeed_url") or GOPEED_URL)
    # Blank override token keeps the saved/config token; nonblank override token is used only for testing unless saved.
    token = str(overrides.get("gopeed_token") or raw.get("gopeed_token") or GOPEED_TOKEN or "").strip()
    username = str(overrides.get("gopeed_username") if overrides.get("gopeed_username") is not None else raw.get("gopeed_username") or "").strip()
    download_path = str(overrides.get("gopeed_download_path") if overrides.get("gopeed_download_path") is not None else raw.get("gopeed_download_path") or DEFAULT_GOPEED_DOWNLOAD_PATH).strip()
    data = {"url": url, "username": username, "download_path": download_path, "token_set": bool(token)}
    if include_secret:
        data["token"] = token
    return data


def gopeed_headers(conn):
    headers = {"Content-Type": "application/json"}
    if conn.get("token"):
        headers["X-Api-Token"] = conn["token"]
    return headers


async def gopeed_api(endpoint, method="GET", data=None):
    conn = await gopeed_connection_settings(include_secret=True)
    base_url = conn["url"].rstrip("/")
    url = f"{base_url}/{endpoint.lstrip('/')}"
    headers = gopeed_headers(conn)
    async with aiohttp.ClientSession() as session:
        try:
            if method == "GET":
                async with session.get(url, headers=headers, timeout=15) as resp:
                    return await resp.json()
            if method == "POST":
                async with session.post(url, headers=headers, json=data, timeout=20) as resp:
                    return await resp.json()
            if method == "PUT":
                async with session.put(url, headers=headers, json=data, timeout=20) as resp:
                    return await resp.json()
            if method == "DELETE":
                async with session.delete(url, headers=headers, timeout=20) as resp:
                    if resp.status in (200, 204):
                        try:
                            return await resp.json()
                        except Exception:
                            return {"code": 0}
                    return {"code": resp.status, "error": f"HTTP {resp.status}"}
        except Exception as exc:
            return {"error": str(exc)}
    return {"error": f"Unsupported method {method}"}


async def test_gopeed_connection(overrides=None):
    conn = await gopeed_connection_settings(include_secret=True, overrides=overrides or {})
    url = f"{conn['url'].rstrip('/')}/config"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=gopeed_headers(conn), timeout=10) as resp:
                text = await resp.text()
                try:
                    payload = json.loads(text)
                except Exception:
                    payload = {"raw": text[:200]}
                ok = resp.status == 200 and isinstance(payload, dict) and payload.get("code") in (0, None)
                data = payload.get("data") if isinstance(payload, dict) else None
                return {
                    "ok": ok,
                    "http_status": resp.status,
                    "url": conn["url"],
                    "username": conn.get("username") or "",
                    "token_set": bool(conn.get("token")),
                    "configured_download_path": conn.get("download_path") or "",
                    "gopeed_download_dir": data.get("downloadDir") if isinstance(data, dict) else None,
                    "message": "连接成功" if ok else (payload.get("msg") or payload.get("error") or f"HTTP {resp.status}" if isinstance(payload, dict) else f"HTTP {resp.status}"),
                }
    except Exception as exc:
        return {"ok": False, "url": conn["url"], "username": conn.get("username") or "", "token_set": bool(conn.get("token")), "configured_download_path": conn.get("download_path") or "", "error": str(exc), "message": str(exc)}


def normalize_jellyfin_url(url):
    return str(url or "").strip().rstrip("/")


async def jellyfin_connection_settings(include_secret=False, overrides=None):
    raw = await load_json(SETTINGS_FILE, {})
    if not isinstance(raw, dict):
        raw = {}
    overrides = overrides or {}
    url = normalize_jellyfin_url(overrides.get("jellyfin_url") if overrides.get("jellyfin_url") is not None else raw.get("jellyfin_url") or "")
    api_key = str(overrides.get("jellyfin_api_key") or raw.get("jellyfin_api_key") or "").strip()
    enabled = bool(overrides.get("jellyfin_enabled") if overrides.get("jellyfin_enabled") is not None else raw.get("jellyfin_enabled", False))
    data = {"enabled": enabled, "url": url, "api_key_set": bool(api_key)}
    if include_secret:
        data["api_key"] = api_key
    return data


def jellyfin_headers(conn):
    headers = {"Accept": "application/json"}
    if conn.get("api_key"):
        headers["X-Emby-Token"] = conn["api_key"]
    return headers


def normalize_library_code_text(value):
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def jellyfin_item_matches_code(item, code):
    code = str(code or "").upper().strip()
    if not code:
        return False
    compact = normalize_library_code_text(code)
    fields = [
        item.get("Name"), item.get("OriginalTitle"), item.get("SortName"),
        item.get("Path"), item.get("FileName"), item.get("Overview"),
    ]
    provider_ids = item.get("ProviderIds")
    if isinstance(provider_ids, dict):
        fields.extend(provider_ids.values())
    for value in fields:
        text = str(value or "").upper()
        if code in text or (compact and compact in normalize_library_code_text(text)):
            return True
    return False


async def test_jellyfin_connection(overrides=None):
    conn = await jellyfin_connection_settings(include_secret=True, overrides=overrides or {})
    if not conn.get("url"):
        return {"ok": False, "url": "", "api_key_set": bool(conn.get("api_key")), "message": "请填写 Jellyfin URL"}
    if not conn.get("api_key"):
        return {"ok": False, "url": conn["url"], "api_key_set": False, "message": "请填写 Jellyfin API Key"}
    url = f"{conn['url']}/System/Info"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=jellyfin_headers(conn), timeout=10) as resp:
                text = await resp.text()
                try:
                    payload = json.loads(text) if text else {}
                except Exception:
                    payload = {"raw": text[:200]}
                ok = resp.status == 200 and isinstance(payload, dict)
                return {
                    "ok": ok,
                    "http_status": resp.status,
                    "url": conn["url"],
                    "api_key_set": bool(conn.get("api_key")),
                    "server_name": payload.get("ServerName") or payload.get("LocalAddress") if isinstance(payload, dict) else None,
                    "version": payload.get("Version") if isinstance(payload, dict) else None,
                    "message": "连接成功" if ok else f"HTTP {resp.status}",
                }
    except Exception as exc:
        return {"ok": False, "url": conn["url"], "api_key_set": bool(conn.get("api_key")), "error": str(exc), "message": str(exc)}


async def jellyfin_search_code(code):
    conn = await jellyfin_connection_settings(include_secret=True)
    if not conn.get("enabled") or not conn.get("url") or not conn.get("api_key"):
        return {"code": code, "in_jellyfin": False, "disabled": True}
    params = {
        "Recursive": "true",
        "SearchTerm": str(code or "").upper(),
        "IncludeItemTypes": "Movie,Video",
        "Fields": "Path,ProviderIds,OriginalTitle,SortName",
        "Limit": "20",
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{conn['url']}/Items", headers=jellyfin_headers(conn), params=params, timeout=12) as resp:
                if resp.status != 200:
                    return {"code": code, "in_jellyfin": False, "error": f"HTTP {resp.status}"}
                payload = await resp.json()
    except Exception as exc:
        return {"code": code, "in_jellyfin": False, "error": str(exc)}
    items = payload.get("Items", []) if isinstance(payload, dict) else []
    for item in items:
        if isinstance(item, dict) and jellyfin_item_matches_code(item, code):
            return {"code": code, "in_jellyfin": True, "item_id": item.get("Id"), "item_name": item.get("Name")}
    return {"code": code, "in_jellyfin": False}


async def jellyfin_codes_presence(codes):
    unique = []
    seen = set()
    for code in codes or []:
        c = str(code or "").upper().strip()
        if c and c not in seen:
            seen.add(c)
            unique.append(c)
    if not unique:
        return {}
    conn = await jellyfin_connection_settings(include_secret=True)
    if not conn.get("enabled") or not conn.get("url") or not conn.get("api_key"):
        return {c: {"in_jellyfin": False, "disabled": True} for c in unique}
    results = await asyncio.gather(*(jellyfin_search_code(c) for c in unique))
    return {str(r.get("code") or "").upper(): r for r in results if isinstance(r, dict)}


def parse_size_to_bytes(size_str):
    m = re.search(r"([\d.]+)\s*([KMGT])", str(size_str or ""), re.I)
    if not m:
        return 0
    val, unit = float(m.group(1)), m.group(2).upper()
    return int(val * {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}.get(unit, 1))


def format_size(n):
    try:
        n = float(n or 0)
    except Exception:
        n = 0.0
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024:
            return f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} PB"


async def selected_download_client(override=None):
    if override is not None:
        client = str(override or "").lower()
        if client in {"gopeed", "qbittorrent", "aria2"}:
            return client
    settings = await get_settings()
    client = str(settings.get("download_client") or "gopeed").lower()
    return client if client in {"gopeed", "qbittorrent", "aria2"} else "gopeed"


def normalize_base_url(url, default=""):
    return str(url or default).strip().rstrip("/")


async def qbittorrent_connection_settings(include_secret=False, overrides=None):
    raw = await load_json(SETTINGS_FILE, {})
    if not isinstance(raw, dict):
        raw = {}
    overrides = overrides or {}
    data = {
        "url": normalize_base_url(overrides.get("qbittorrent_url") or raw.get("qbittorrent_url") or "http://192.168.8.88:8080"),
        "username": str(overrides.get("qbittorrent_username") if overrides.get("qbittorrent_username") is not None else raw.get("qbittorrent_username") or "").strip(),
        "download_path": str(overrides.get("qbittorrent_download_path") if overrides.get("qbittorrent_download_path") is not None else raw.get("qbittorrent_download_path") or "/downloads").strip(),
        "password_set": bool(overrides.get("qbittorrent_password") or raw.get("qbittorrent_password")),
    }
    if include_secret:
        data["password"] = str(overrides.get("qbittorrent_password") or raw.get("qbittorrent_password") or "")
    return data


async def aria2_connection_settings(include_secret=False, overrides=None):
    raw = await load_json(SETTINGS_FILE, {})
    if not isinstance(raw, dict):
        raw = {}
    overrides = overrides or {}
    data = {
        "url": str(overrides.get("aria2_url") or raw.get("aria2_url") or "http://192.168.8.88:6800/jsonrpc").strip(),
        "download_path": str(overrides.get("aria2_download_path") if overrides.get("aria2_download_path") is not None else raw.get("aria2_download_path") or "/downloads").strip(),
        "secret_set": bool(overrides.get("aria2_secret") or raw.get("aria2_secret")),
    }
    if include_secret:
        data["secret"] = str(overrides.get("aria2_secret") or raw.get("aria2_secret") or "")
    return data


async def qbittorrent_session(conn):
    session = aiohttp.ClientSession()
    if conn.get("username") or conn.get("password"):
        url = f"{conn['url']}/api/v2/auth/login"
        data = {"username": conn.get("username") or "", "password": conn.get("password") or ""}
        resp = await session.post(url, data=data, timeout=10)
        text = await resp.text()
        # qBittorrent Web API commonly returns 200 "Ok.", 200 "Ok", or 204 No Content
        # for successful login depending on version/config. Treat all as success.
        ok_text = text.strip().lower().rstrip(".")
        if resp.status not in (200, 204) or ok_text not in {"ok", ""}:
            await session.close()
            raise RuntimeError(f"qBittorrent login failed: HTTP {resp.status} {text[:120]}")
    return session


async def qbittorrent_api(endpoint, method="GET", data=None, overrides=None):
    conn = await qbittorrent_connection_settings(include_secret=True, overrides=overrides or {})
    session = await qbittorrent_session(conn)
    try:
        url = f"{conn['url']}/api/v2/{endpoint.lstrip('/')}"
        if method == "GET":
            async with session.get(url, params=data or None, timeout=15) as resp:
                text = await resp.text()
                if resp.status != 200:
                    return {"error": f"HTTP {resp.status}: {text[:200]}"}
                try:
                    return json.loads(text) if text else {"ok": True}
                except Exception:
                    return {"text": text}
        if method == "POST":
            async with session.post(url, data=data or {}, timeout=20) as resp:
                text = await resp.text()
                if resp.status not in (200, 204):
                    return {"error": f"HTTP {resp.status}: {text[:200]}"}
                return {"ok": True, "text": text}
    except Exception as exc:
        return {"error": str(exc)}
    finally:
        await session.close()
    return {"error": f"Unsupported method {method}"}


async def aria2_rpc(method, params=None, overrides=None):
    conn = await aria2_connection_settings(include_secret=True, overrides=overrides or {})
    params = list(params or [])
    if conn.get("secret"):
        params.insert(0, f"token:{conn['secret']}")
    payload = {"jsonrpc": "2.0", "id": "javmaster", "method": method, "params": params}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(conn["url"], json=payload, timeout=15) as resp:
                text = await resp.text()
                try:
                    data = json.loads(text)
                except Exception:
                    data = {"raw": text[:200]}
                if resp.status != 200 or data.get("error"):
                    return {"error": data.get("error") or f"HTTP {resp.status}: {text[:200]}"}
                return data.get("result")
    except Exception as exc:
        return {"error": str(exc)}


async def test_qbittorrent_connection(overrides=None):
    conn = await qbittorrent_connection_settings(include_secret=True, overrides=overrides or {})
    version = await qbittorrent_api("app/version", overrides=overrides or {})
    ok = not (isinstance(version, dict) and version.get("error"))
    version_text = version.get("text") if isinstance(version, dict) else version
    return {"ok": ok, "url": conn["url"], "username": conn.get("username") or "", "password_set": bool(conn.get("password")), "configured_download_path": conn.get("download_path") or "", "version": version_text if ok else None, "message": "连接成功" if ok else version.get("error", "unknown")}


async def test_aria2_connection(overrides=None):
    conn = await aria2_connection_settings(include_secret=True, overrides=overrides or {})
    version = await aria2_rpc("aria2.getVersion", overrides=overrides or {})
    ok = not (isinstance(version, dict) and version.get("error"))
    return {"ok": ok, "url": conn["url"], "secret_set": bool(conn.get("secret")), "configured_download_path": conn.get("download_path") or "", "version": version if ok else None, "message": "连接成功" if ok else version.get("error", "unknown")}


async def get_config_summary():
    client = await selected_download_client()
    if client == "gopeed":
        res = await gopeed_api("config")
        data = res.get("data") if isinstance(res, dict) else None
        if not isinstance(data, dict):
            return {"ok": False, "client": client, "error": (res or {}).get("error") or (res or {}).get("msg") or "unknown"}
        bt = (data.get("protocolConfig") or {}).get("bt") or {}
        return {"ok": True, "client": client, "downloadDir": data.get("downloadDir"), "maxRunning": data.get("maxRunning"), "bt": {"listenPort": bt.get("listenPort"), "seedKeep": bt.get("seedKeep"), "seedRatio": bt.get("seedRatio"), "seedTime": bt.get("seedTime")}}
    if client == "qbittorrent":
        return await test_qbittorrent_connection()
    if client == "aria2":
        return await test_aria2_connection()
    return {"ok": False, "client": client, "error": "unsupported client"}


async def list_tasks(status="running"):
    client = await selected_download_client()
    if client == "gopeed":
        statuses = ["done"] if status == "done" else ["running", "pause", "wait", "ready"]
        out = []
        seen = set()
        for st in statuses:
            res = await gopeed_api(f"tasks?status={st}")
            data = res.get("data", []) if isinstance(res, dict) else []
            if not isinstance(data, list):
                continue
            for task in data:
                key = task.get("id") or id(task)
                if key not in seen:
                    seen.add(key); out.append(task)
        return out
    if client == "qbittorrent":
        data = await qbittorrent_api("torrents/info")
        if not isinstance(data, list):
            return []
        if status == "done":
            return [t for t in data if float(t.get("progress") or 0) >= 1]
        return [t for t in data if float(t.get("progress") or 0) < 1]
    if client == "aria2":
        if status == "running":
            active = await aria2_rpc("aria2.tellActive", [["gid", "status", "totalLength", "completedLength", "downloadSpeed", "uploadSpeed", "connections", "numSeeders", "files", "bittorrent"]])
            waiting = await aria2_rpc("aria2.tellWaiting", [0, 100, ["gid", "status", "totalLength", "completedLength", "downloadSpeed", "uploadSpeed", "connections", "numSeeders", "files", "bittorrent"]])
            rows = []
            if isinstance(active, list): rows += active
            if isinstance(waiting, list): rows += [x for x in waiting if x.get("status") in {"active", "waiting", "paused"}]
            return rows
        stopped = await aria2_rpc("aria2.tellStopped", [0, 100, ["gid", "status", "totalLength", "completedLength", "downloadSpeed", "uploadSpeed", "connections", "numSeeders", "files", "bittorrent"]])
        return [x for x in stopped if x.get("status") == "complete"] if isinstance(stopped, list) else []
    return []


async def delete_task(task_id, force=True):
    client = await selected_download_client()
    task_id = str(task_id or "")
    if client == "gopeed":
        return await gopeed_api(f"tasks/{quote(task_id, safe='')}?force={str(force).lower()}", method="DELETE")
    if client == "qbittorrent":
        return await qbittorrent_api("torrents/delete", method="POST", data={"hashes": task_id, "deleteFiles": "true" if force else "false"})
    if client == "aria2":
        method = "aria2.removeDownloadResult" if not force else "aria2.forceRemove"
        return await aria2_rpc(method, [task_id])
    return {"error": "unsupported client"}


async def pause_task(task_id):
    client = await selected_download_client()
    task_id = str(task_id or "")
    if not task_id:
        return {"error": "missing task id"}
    if client == "gopeed":
        res = await gopeed_api(f"tasks/{quote(task_id, safe='')}/pause", method="PUT")
        if isinstance(res, dict) and res.get("error"):
            post_res = await gopeed_api(f"tasks/{quote(task_id, safe='')}/pause", method="POST")
            return post_res if not (isinstance(post_res, dict) and post_res.get("error")) else res
        return res
    if client == "qbittorrent":
        # qBittorrent Web API v5 renamed pause/resume to stop/start; older v4 uses pause/resume.
        res = await qbittorrent_api("torrents/pause", method="POST", data={"hashes": task_id})
        if isinstance(res, dict) and res.get("error") and ("404" in str(res.get("error")) or "Not Found" in str(res.get("error"))):
            return await qbittorrent_api("torrents/stop", method="POST", data={"hashes": task_id})
        return res
    if client == "aria2":
        return await aria2_rpc("aria2.forcePause", [task_id])
    return {"error": "unsupported client"}


async def resume_task(task_id):
    client = await selected_download_client()
    task_id = str(task_id or "")
    if not task_id:
        return {"error": "missing task id"}
    if client == "gopeed":
        res = await gopeed_api(f"tasks/{quote(task_id, safe='')}/continue", method="PUT")
        if isinstance(res, dict) and res.get("error"):
            post_res = await gopeed_api(f"tasks/{quote(task_id, safe='')}/continue", method="POST")
            return post_res if not (isinstance(post_res, dict) and post_res.get("error")) else res
        return res
    if client == "qbittorrent":
        # qBittorrent Web API v5 renamed pause/resume to stop/start; older v4 uses pause/resume.
        res = await qbittorrent_api("torrents/resume", method="POST", data={"hashes": task_id})
        if isinstance(res, dict) and res.get("error") and ("404" in str(res.get("error")) or "Not Found" in str(res.get("error"))):
            return await qbittorrent_api("torrents/start", method="POST", data={"hashes": task_id})
        return res
    if client == "aria2":
        return await aria2_rpc("aria2.unpause", [task_id])
    return {"error": "unsupported client"}


async def clear_done_tasks():
    tasks = await list_tasks("done")
    results = []
    for t in tasks:
        task_id = t.get("id") or t.get("hash") or t.get("gid")
        if not task_id:
            continue
        results.append({"id": task_id, "result": await delete_task(task_id, False)})
    return {"cleared": len(results), "results": results}


def aria2_task_name(t):
    bt = t.get("bittorrent") if isinstance(t.get("bittorrent"), dict) else {}
    info = bt.get("info") if isinstance(bt.get("info"), dict) else {}
    if info.get("name"):
        return info.get("name")
    files = t.get("files") if isinstance(t.get("files"), list) else []
    if files:
        return os.path.basename(files[0].get("path") or "") or files[0].get("path") or "未命名任务"
    return t.get("gid") or "未命名任务"


async def task_display_rows(status="running"):
    client = await selected_download_client()
    tasks = await list_tasks(status)
    rows = []
    for t in tasks:
        if client == "gopeed":
            task_id = t.get("id")
            detail = (await gopeed_api(f"tasks/{task_id}")).get("data", {}) if task_id else {}
            stats = (await gopeed_api(f"tasks/{task_id}/stats")).get("data", {}) if task_id else {}
            progress = detail.get("progress") or t.get("progress") or {}
            meta = detail.get("meta") or t.get("meta") or {}
            total = ((meta.get("res") or {}).get("size") or 0)
            downloaded = progress.get("downloaded", 0) or 0
            name = t.get("name") or detail.get("name") or "未命名任务"
            pct = round(downloaded / total * 100, 1) if total else 0
            rows.append({"id": task_id, "name": name, "short_name": name[:120], "status": detail.get("status") or t.get("status") or status, "progress": pct, "progress_text": f"{pct}% ({format_size(downloaded)} / {format_size(total)})", "download_speed_text": format_size(progress.get("speed", 0)) + "/s", "upload_speed_text": format_size(progress.get("uploadSpeed", 0)) + "/s", "downloaded_text": format_size(downloaded), "total_size_text": format_size(total), "active_peers": stats.get("activePeers", 0) or 0, "total_peers": stats.get("totalPeers", 0) or 0})
        elif client == "qbittorrent":
            total = int(t.get("size") or 0); downloaded = int(float(t.get("progress") or 0) * total); pct = round(float(t.get("progress") or 0) * 100, 1)
            rows.append({"id": t.get("hash"), "name": t.get("name") or "未命名任务", "short_name": (t.get("name") or "未命名任务")[:120], "status": t.get("state") or status, "progress": pct, "progress_text": f"{pct}% ({format_size(downloaded)} / {format_size(total)})", "download_speed_text": format_size(t.get("dlspeed", 0)) + "/s", "upload_speed_text": format_size(t.get("upspeed", 0)) + "/s", "downloaded_text": format_size(downloaded), "total_size_text": format_size(total), "active_peers": t.get("num_leechs", 0) or 0, "total_peers": t.get("num_seeds", 0) or 0})
        elif client == "aria2":
            total = int(t.get("totalLength") or 0); downloaded = int(t.get("completedLength") or 0); pct = round(downloaded / total * 100, 1) if total else 0; name = aria2_task_name(t)
            rows.append({"id": t.get("gid"), "name": name, "short_name": name[:120], "status": t.get("status") or status, "progress": pct, "progress_text": f"{pct}% ({format_size(downloaded)} / {format_size(total)})", "download_speed_text": format_size(t.get("downloadSpeed", 0)) + "/s", "upload_speed_text": format_size(t.get("uploadSpeed", 0)) + "/s", "downloaded_text": format_size(downloaded), "total_size_text": format_size(total), "active_peers": t.get("connections", 0) or 0, "total_peers": t.get("numSeeders", 0) or 0})
    return rows


MEDIA_EXTENSIONS = {'.mp4', '.mkv', '.avi', '.rmvb', '.wmv', '.mov', '.flv', '.ts', '.webm', '.iso'}
MIN_SCRAPE_VIDEO_BYTES = 4 * 1024 * 1024 * 1024


def _is_under(path, root):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except Exception:
        return False


def _container_to_scraper_path(path):
    path = str(path or '').replace('\\', '/').strip()
    if path.startswith('/video/') or path == '/video':
        path = '/downloads' + path[len('/video'):]
    if path.startswith('/app/Downloads/video/') or path == '/app/Downloads/video':
        path = '/downloads' + path[len('/app/Downloads/video'):]
    if path.startswith('/downloads/') or path == '/downloads':
        return '/data' + path[len('/downloads'):]
    if path.startswith('/data/') or path == '/data':
        return path
    return path


def _download_path_exists(path):
    try:
        return Path(path).exists()
    except Exception:
        return False


def _resolve_existing_download_source(path):
    """Normalize downloader paths to JavMaster /downloads without crossing output boundaries.

    Source must be the real completed task folder/file reported by the downloader,
    e.g. /video/SNOS-239 -> /downloads/SNOS-239. Output/holding folders such as
    toBeSorted and JAV_Sorted are never searched as source fallbacks.
    """
    normalized = _safe_download_path(path)
    if not _download_path_exists(normalized):
        raise ValueError(f'下载任务源文件夹不存在，已拒绝越界查找: {normalized}')
    return normalized


def _task_folder_for_source(source_path):
    """Return exact task folder under /downloads; never cross output boundaries."""
    source = Path(_safe_download_path(source_path)).resolve()
    downloads = Path('/downloads').resolve()
    try:
        rel = source.relative_to(downloads)
    except Exception:
        raise ValueError(f'路径不在下载目录内: {source_path}')
    if not rel.parts:
        raise ValueError('拒绝使用下载根目录作为任务目录')
    blocked_first = {'JAV_Sorted', 'MDC_Failed', 'toBeSorted', 'Chinese_Sorted'}
    if rel.parts[0] in blocked_first or any(str(part).endswith('.part') for part in rel.parts):
        raise ValueError(f'拒绝使用输出/未完成目录作为刮削源: {source_path}')
    return source if source.is_dir() else (downloads / rel.parts[0])


def _large_video_files_in_folder(folder):
    root = Path(folder).resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f'下载任务源文件夹不存在: {root}')
    files = []
    for item in root.rglob('*'):
        try:
            if not item.is_file():
                continue
            if item.suffix.lower() not in MEDIA_EXTENSIONS:
                continue
            if item.name.endswith('.part') or any(part in {'JAV_Sorted', 'MDC_Failed', 'toBeSorted', 'Chinese_Sorted'} for part in item.parts):
                continue
            size = item.stat().st_size
            if size > MIN_SCRAPE_VIDEO_BYTES:
                files.append((size, item))
        except Exception:
            continue
    files.sort(key=lambda x: x[0], reverse=True)
    return [{'path': str(path), 'size': size} for size, path in files]


def _cleanup_small_files_and_empty_dirs(task_folder, keep_min_bytes=MIN_SCRAPE_VIDEO_BYTES):
    """After successful scrape, remove only small files, then remove empty dirs.

    Never recursively delete the task folder. If a >=4GB video remains, the folder
    is intentionally kept for safety.
    """
    root = _task_folder_for_source(task_folder)
    result = {'task_folder': str(root), 'small_files_deleted': [], 'empty_dirs_deleted': [], 'task_folder_deleted': False, 'remaining': []}
    if not root.exists():
        result['task_folder_deleted'] = True
        result['reason'] = 'already_missing'
        return result
    for item in sorted(root.rglob('*'), key=lambda p: len(p.parts), reverse=True):
        try:
            if item.is_file():
                size = item.stat().st_size
                if size < keep_min_bytes:
                    item.unlink()
                    result['small_files_deleted'].append({'path': str(item), 'size': size})
            elif item.is_dir():
                try:
                    item.rmdir()
                    result['empty_dirs_deleted'].append(str(item))
                except OSError:
                    pass
        except Exception as exc:
            result.setdefault('errors', []).append({'path': str(item), 'error': str(exc)})
    try:
        root.rmdir()
        result['task_folder_deleted'] = True
    except OSError:
        try:
            result['remaining'] = [str(x) for x in root.iterdir()]
        except Exception:
            result['remaining'] = ['<unable to list>']
    return result


def _extract_code_from_path(path):
    m = CODE_RE.search(str(path or '').upper())
    return m.group(0) if m else ''


def _scrape_output_contains_code(output_path, code):
    if not code:
        return True
    root = Path(output_path).resolve()
    if not root.exists():
        return False
    code_upper = code.upper()
    try:
        for child in root.rglob('*'):
            if code_upper in child.name.upper():
                return True
    except Exception:
        return False
    return False


def _safe_download_path(path, *, allow_sorted=False):
    path = str(path or '').replace('\\', '/').strip()
    if not path:
        return ''
    # qBittorrent reports host/container paths like /video while JavMaster mounts the same root as /downloads.
    if path.startswith('/video/') or path == '/video':
        path = '/downloads' + path[len('/video'):]
    if path.startswith('/app/Downloads/video/') or path == '/app/Downloads/video':
        path = '/downloads' + path[len('/app/Downloads/video'):]
    if path.startswith('/data/') or path == '/data':
        path = '/downloads' + path[len('/data'):]
    if not (path == '/downloads' or path.startswith('/downloads/')):
        raise ValueError(f'只允许刮削下载挂载目录内的文件，当前路径: {path}')
    blocked = ['/.part']
    if not allow_sorted:
        blocked += ['/JAV_Sorted/', '/MDC_Failed/', '/toBeSorted/', '/Chinese_Sorted/']
    normalized = path.rstrip('/') + ('/' if not path.endswith('/') else '')
    if any(x in normalized for x in blocked):
        raise ValueError('该路径属于未完成或已整理目录，已拒绝刮削')
    return path


def _join_task_path(base, name):
    base = str(base or '').replace('\\', '/').rstrip('/')
    name = str(name or '').replace('\\', '/').lstrip('/')
    return f"{base}/{name}" if base and name else base or name


def _media_candidates_from_gopeed(detail):
    meta = detail.get('meta') if isinstance(detail, dict) else {}
    opts = meta.get('opts') if isinstance(meta, dict) else {}
    res = meta.get('res') if isinstance(meta, dict) else {}
    base = opts.get('path') or '/downloads'
    candidates = []
    files = res.get('files') if isinstance(res, dict) else []
    selected = opts.get('selectFiles') if isinstance(opts, dict) else None
    selected_set = {str(x) for x in selected} if isinstance(selected, list) else None
    if isinstance(files, list):
        for i, f in enumerate(files):
            if selected_set is not None and str(i) not in selected_set and str(f.get('index', '')) not in selected_set:
                continue
            name = f.get('path') or f.get('name') if isinstance(f, dict) else ''
            if name and Path(str(name)).suffix.lower() in MEDIA_EXTENSIONS:
                candidates.append(_join_task_path(base, name))
    name = detail.get('name') or res.get('name') if isinstance(res, dict) else detail.get('name')
    if not candidates and name:
        candidates.append(_join_task_path(base, name))
    return candidates


async def _task_detail_for_scrape(task_id):
    client = await selected_download_client()
    task_id = str(task_id or '')
    if not task_id:
        raise ValueError('missing task id')
    if client == 'gopeed':
        detail = (await gopeed_api(f"tasks/{quote(task_id, safe='')}")).get('data', {})
        return client, detail
    if client == 'qbittorrent':
        rows = await qbittorrent_api('torrents/info', data={'hashes': task_id})
        if isinstance(rows, list):
            for row in rows:
                if str(row.get('hash') or '').lower() == task_id.lower():
                    return client, row
    if client == 'aria2':
        return client, await aria2_rpc('aria2.tellStatus', [task_id, ['gid','status','files','bittorrent','dir']])
    raise ValueError('找不到任务或不支持当前下载器')


async def scrape_completed_task(task_id, output_path=None):
    settings = await get_settings()
    output_path = _safe_download_path(output_path or settings.get('scrape_output_path') or '/downloads/JAV_Sorted', allow_sorted=True)
    client, detail = await _task_detail_for_scrape(task_id)
    folder_candidates = []
    if client == 'gopeed':
        media_candidates = _media_candidates_from_gopeed(detail)
        folder_candidates = [str(_task_folder_for_source(_resolve_existing_download_source(x))) for x in media_candidates if x]
    elif client == 'qbittorrent':
        folder_candidates = [detail.get('root_path') or detail.get('content_path') or _join_task_path(detail.get('save_path'), detail.get('name'))]
    elif client == 'aria2':
        folder_candidates = [detail.get('dir')] if isinstance(detail, dict) else []
    folders = []
    for candidate in folder_candidates:
        if not candidate:
            continue
        folder = str(_task_folder_for_source(_resolve_existing_download_source(candidate)))
        if folder not in folders:
            folders.append(folder)
    if not folders:
        raise ValueError('没有从任务里识别到可刮削的任务文件夹')
    task_folder = folders[0]
    large_videos = _large_video_files_in_folder(task_folder)
    if not large_videos:
        raise ValueError(f'任务文件夹内没有大于 4GB 的视频文件，已拒绝刮削和清理: {task_folder}')
    source_path = large_videos[0]['path']
    scraper_result = await scrape_single_movie(source_path, output_path)
    code = _extract_code_from_path(source_path) or str(scraper_result.get('code') or '')
    if not _scrape_output_contains_code(output_path, code):
        raise ValueError(f'刮削器报告成功但没有在输出目录找到包含 {code or "源番号"} 的文件；源文件已保留')
    cleanup = _cleanup_small_files_and_empty_dirs(task_folder)
    task_list_removal = None
    if settings.get('scrape_remove_task_after_success'):
        task_list_removal = await delete_task(task_id, False)
    return {
        'task_folder': task_folder,
        'source_path': source_path,
        'large_videos': large_videos,
        'output_path': output_path,
        'scraper': scraper_result,
        'cleanup': cleanup,
        'task_list_removal': task_list_removal,
    }


async def get_code_watchlist():
    data = await load_json(WATCHLIST_FILE, [])
    return [str(x).upper() for x in data] if isinstance(data, list) else []


async def add_codes(raw):
    if isinstance(raw, str):
        raw = raw.replace("，", " ").replace(",", " ").split()
    watchlist = await get_code_watchlist()
    added, existed, errors = [], [], []
    for code in [str(x).upper().strip() for x in raw if str(x).strip()]:
        if not CODE_RE.fullmatch(code):
            errors.append(f"番号格式不正确：{code}")
        elif code in watchlist:
            existed.append(code)
        else:
            watchlist.append(code); added.append(code)
    if added:
        await save_json(WATCHLIST_FILE, watchlist)
    return {"added": added, "existed": existed, "errors": errors, "watchlist": watchlist}


async def remove_code(code):
    code = str(code).upper().strip()
    watchlist = await get_code_watchlist()
    before = len(watchlist)
    watchlist = [x for x in watchlist if x != code]
    await save_json(WATCHLIST_FILE, watchlist)
    return {"removed": before != len(watchlist), "watchlist": watchlist}


def filter_resources(resources, min_gb=None, max_gb=None, keyword=""):
    kw = (keyword or "").lower().strip()
    out = []
    for r in resources:
        size_gb = (r.get("size_bytes") or 0) / 1024**3
        if min_gb not in (None, "") and size_gb < float(min_gb):
            continue
        if max_gb not in (None, "") and size_gb > float(max_gb):
            continue
        if kw and kw not in (r.get("title", "").lower()):
            continue
        out.append(r)
    return out


async def search_sukebei(code, limit=50, min_gb=None, max_gb=None, keyword=""):
    code = str(code).upper().strip()
    url = f"https://sukebei.nyaa.si/?page=rss&q={quote(code)}&c=0_0&f=0"
    items = []
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=20) as resp:
            text = await resp.text()
    root = ET.fromstring(text)
    for idx, item in enumerate(root.findall("./channel/item")):
        title = item.findtext("title") or ""
        link = item.findtext("link") or ""
        pub_date = item.findtext("pubDate") or ""
        size_text = item.findtext(f"{NYAA_NS}size") or ""
        seeders = int(item.findtext(f"{NYAA_NS}seeders") or 0)
        leechers = int(item.findtext(f"{NYAA_NS}leechers") or 0)
        downloads = int(item.findtext(f"{NYAA_NS}downloads") or 0)
        size_bytes = parse_size_to_bytes(size_text)
        items.append({"id": f"{code}-{idx}", "code": code, "title": title, "link": link, "pubDate": pub_date, "size": size_text, "size_bytes": size_bytes, "size_text": format_size(size_bytes), "seeders": seeders, "leechers": leechers, "downloads": downloads})
    items.sort(key=lambda x: (x["seeders"], x["size_bytes"]), reverse=True)
    return filter_resources(items[: int(limit or 50)], min_gb, max_gb, keyword)


def task_matches_code(task, code):
    code = str(code or "").upper().strip()
    if not code:
        return False
    compact = re.sub(r"[^A-Z0-9]", "", code)
    haystacks = []

    def collect(obj):
        if isinstance(obj, dict):
            for key in ("name", "title", "url", "uri", "link"):
                val = obj.get(key)
                if isinstance(val, str):
                    haystacks.append(val)
            for key in ("req", "meta", "res", "files"):
                collect(obj.get(key))
        elif isinstance(obj, list):
            for item in obj:
                collect(item)

    collect(task)
    for value in haystacks:
        upper = value.upper()
        if code in upper or (compact and compact in re.sub(r"[^A-Z0-9]", "", upper)):
            return True
    return False


async def find_download_task_by_code(code):
    seen = set()
    tasks = []
    for status in ("running", "done"):
        for task in await list_tasks(status):
            if not isinstance(task, dict):
                continue
            task_id = task.get("id") or task.get("hash") or task.get("gid") or id(task)
            if task_id in seen:
                continue
            seen.add(task_id)
            tasks.append(task)
    for task in tasks:
        if task_matches_code(task, code):
            return task
    return None


async def wait_for_download_task(code, timeout=30, interval=2):
    deadline = time.time() + max(1, float(timeout or 30))
    while True:
        task = await find_download_task_by_code(code)
        if task:
            return task
        if time.time() >= deadline:
            return None
        await asyncio.sleep(interval)


async def push_resource_to_downloader(link, code=None, download_client=None):
    client = await selected_download_client(download_client)
    result = {"client": client, "pushed": None, "task_found": False, "task": None, "removed_code": False}
    if client == "gopeed":
        conn = await gopeed_connection_settings(False)
        payload = {"req": {"url": link}}
        if conn.get("download_path"):
            payload["opt"] = {"path": conn["download_path"]}
        result["pushed"] = await gopeed_api("tasks", method="POST", data=payload)
    elif client == "qbittorrent":
        conn = await qbittorrent_connection_settings(False)
        data = {"urls": link}
        if conn.get("download_path"):
            data["savepath"] = conn["download_path"]
        result["pushed"] = await qbittorrent_api("torrents/add", method="POST", data=data)
    elif client == "aria2":
        conn = await aria2_connection_settings(False)
        opts = {}
        if conn.get("download_path"):
            opts["dir"] = conn["download_path"]
        result["pushed"] = await aria2_rpc("aria2.addUri", [[link], opts])
    else:
        result["pushed"] = {"error": "unsupported client"}
    if code:
        task = await wait_for_download_task(code)
        result["task_found"] = bool(task)
        if task:
            meta = task.get("meta") if isinstance(task.get("meta"), dict) else {}
            result["task"] = {"id": task.get("id") or task.get("hash") or task.get("gid"), "name": task.get("name") or meta.get("name") or aria2_task_name(task)}
            settings = await get_settings()
            if settings.get("auto_remove_code_after_push"):
                removed = await remove_code(code)
                result["removed_code"] = bool(removed.get("removed"))
    return result


# Backward-compatible name used by existing bot/web code.
async def push_resource_to_gopeed(link, code=None):
    return await push_resource_to_downloader(link, code)


async def get_resource_state():
    data = await load_json(RESOURCE_STATE_FILE, {"codes": {}, "updates": [], "last_auto_run": None, "actress_updates": [], "scheduler": {}})
    if not isinstance(data, dict):
        data = {"codes": {}, "updates": [], "last_auto_run": None, "actress_updates": [], "scheduler": {}}
    data.setdefault("codes", {})
    data.setdefault("updates", [])
    data.setdefault("actress_updates", [])
    data.setdefault("last_auto_run", None)
    data.setdefault("scheduler", {})
    return data


async def mark_home_updates_seen():
    state = await get_resource_state()
    code_updates = len(state.get("updates", []))
    actress_updates = len(state.get("actress_updates", []))
    if code_updates or actress_updates:
        state["updates"] = []
        state["actress_updates"] = []
        state["updates_seen_at"] = int(time.time())
        await save_json(RESOURCE_STATE_FILE, state)
    return {"cleared": code_updates + actress_updates, "code_updates": code_updates, "actress_updates": actress_updates}


async def mark_actress_updates_seen():
    items = await get_actress_watchlist()
    cleared_items = 0
    now = int(time.time())
    for item in items:
        if item.get("has_new_work"):
            cleared_items += 1
        item.pop("has_new_work", None)
        item.pop("new_work_title", None)
        item.pop("new_work_date", None)
        item.pop("new_work_ts", None)
    if cleared_items:
        await save_actress_watchlist(items)
    state = await get_resource_state()
    actress_updates = len(state.get("actress_updates", []))
    if actress_updates:
        state["actress_updates"] = []
        state["actress_updates_seen_at"] = now
        await save_json(RESOURCE_STATE_FILE, state)
    return {"cleared": cleared_items + actress_updates, "actresses": cleared_items, "actress_updates": actress_updates}


SYDNEY_TZ = ZoneInfo("Australia/Sydney")


def scheduler_now():
    return datetime.now(SYDNEY_TZ)


def scheduler_due(settings, state, prefix):
    """Return True when a configured scheduler prefix is due.

    Shared by the Web GUI background loop and Discord bot so the settings page is
    the single source of truth for scheduled checks.
    """
    if not settings.get(f"{prefix}_enabled"):
        return False
    mode = str(settings.get(f"{prefix}_schedule_mode") or "interval")
    last = (state.get("scheduler") or {}).get(prefix) or {}
    now = scheduler_now()
    now_ts = int(now.timestamp())
    if mode == "daily":
        target = str(settings.get(f"{prefix}_daily_time") or "09:00")[:5]
        today = now.strftime("%Y-%m-%d")
        return last.get("last_date") != today and now.strftime("%H:%M") >= target
    try:
        hours = float(settings.get(f"{prefix}_interval_hours") or 6)
    except Exception:
        hours = 6
    return not last.get("last_run") or now_ts - int(last.get("last_run") or 0) >= max(1, hours) * 3600


async def refresh_code_resources(code=None):
    codes = [code.upper()] if code else await get_code_watchlist()
    state = await get_resource_state()
    updates = []
    for c in codes:
        try:
            resources = await search_sukebei(c, limit=50)
        except Exception as exc:
            state["codes"].setdefault(c, {})["error"] = str(exc)
            continue
        old_links = set(state["codes"].get(c, {}).get("links", []))
        new_links = [r["link"] for r in resources if r.get("link")]
        diff = [r for r in resources if r.get("link") not in old_links]
        if old_links and diff:
            updates.append({"code": c, "count": len(diff), "latest": diff[0], "ts": int(time.time())})
        state["codes"][c] = {"count": len(resources), "links": new_links, "last_checked": int(time.time()), "has_resources": bool(resources), "latest_title": resources[0]["title"] if resources else ""}
    if updates:
        state["updates"] = (updates + state.get("updates", []))[:50]
    state["last_auto_run"] = int(time.time())
    await save_json(RESOURCE_STATE_FILE, state)
    return state


async def watchlist_with_resource_state():
    codes = await get_code_watchlist()
    state = await get_resource_state()
    return [{"code": c, **state.get("codes", {}).get(c, {})} for c in codes]


async def get_actress_watchlist():
    data = await load_json(ACTRESS_WATCHLIST_FILE, [])
    normalized = []
    if not isinstance(data, list):
        return normalized
    for item in data:
        if isinstance(item, str):
            normalized.append({"name": item, "aliases": [item], "latest_seen": None})
        elif isinstance(item, dict) and item.get("name"):
            item.setdefault("aliases", [item["name"]]); item.setdefault("latest_seen", None); normalized.append(item)
    return normalized


async def save_actress_watchlist(items):
    await save_json(ACTRESS_WATCHLIST_FILE, items)


async def refresh_actress_works(delay_seconds=3):
    """Refresh watched actresses sequentially, with a small delay between sites requests.

    The delay reduces the chance of triggering upstream anti-bot throttling.
    Each item is saved after it is checked so the UI can show per-actress
    refresh/highlight state even while a long refresh is in progress.
    """
    from bot import fetch_actress_works
    items = await get_actress_watchlist()
    updates = []
    state = await get_resource_state()
    total = len(items)
    try:
        delay_seconds = max(0, float(delay_seconds or 0))
    except Exception:
        delay_seconds = 3
    for idx, item in enumerate(items):
        now = int(time.time())
        item["refresh_order"] = idx + 1
        item["last_refresh_started"] = now
        try:
            canonical, works, matched_query, warning = await fetch_actress_works(item.get("name", ""), limit=5)
            if warning or not works:
                item["last_checked"] = int(time.time())
                item["last_error"] = warning or "No works"
            else:
                latest = works[0].get("code")
                old_latest = item.get("latest_seen")
                item["last_checked"] = int(time.time())
                item.pop("last_error", None)
                if latest and latest != old_latest:
                    item["latest_seen"] = latest
                    if old_latest:
                        item["has_new_work"] = True
                        item["new_work_ts"] = int(time.time())
                        item["new_work_title"] = works[0].get("title") or ""
                        item["new_work_date"] = works[0].get("date") or ""
                        updates.append({"name": item.get("name"), "old": old_latest, "latest": latest, "work": works[0], "ts": int(time.time())})
        except Exception as exc:
            item["last_checked"] = int(time.time())
            item["last_error"] = str(exc)
        # Persist after every actress so the page can reflect refresh order and highlights.
        await save_actress_watchlist(items)
        if updates:
            state["actress_updates"] = (updates + state.get("actress_updates", []))[:50]
        state["last_actress_auto_run"] = int(time.time())
        await save_json(RESOURCE_STATE_FILE, state)
        if idx < total - 1 and delay_seconds:
            await asyncio.sleep(delay_seconds)
    return {"checked": len(items), "updates": updates, "watchlist": items, "delay_seconds": delay_seconds}


async def scheduler_mark_run(prefix):
    state = await get_resource_state()
    now = scheduler_now()
    state.setdefault("scheduler", {})[prefix] = {"last_run": int(now.timestamp()), "last_date": now.strftime("%Y-%m-%d")}
    await save_json(RESOURCE_STATE_FILE, state)
    return state["scheduler"][prefix]


async def remove_actress(name):
    name = str(name).strip()
    items = await get_actress_watchlist(); before = len(items)
    items = [x for x in items if x.get("name") != name and name not in (x.get("aliases") or [])]
    await save_actress_watchlist(items)
    return {"removed": before != len(items), "watchlist": items}


def filter_actress_works(works, since="", count=5):
    if since:
        works = [w for w in works if (w.get("date") or "0000-00-00") >= since]
    return works[: max(1, min(int(count or 5), 50))]


def find_cleanup_candidates():
    protected_exts = (".srt", ".ass", ".ssa", ".vtt", ".nfo", ".jpg", ".jpeg", ".png", ".webp")
    excluded_dirs = {"jav_sorted", "chinese_sorted", "tobesorted"}
    found = []
    for root, dirs, files in os.walk(CLEANUP_PATH):
        dirs[:] = [d for d in dirs if d.lower() not in excluded_dirs]
        if root.rstrip("/") == CLEANUP_PATH.rstrip("/"):
            continue
        if not re.search(r"[a-zA-Z]{2,5}-\d{3,5}", os.path.basename(root)):
            continue
        for f in files:
            p = os.path.join(root, f)
            try:
                lower = f.lower()
                if lower.endswith(protected_exts) or lower.endswith(".part"):
                    continue
                if os.path.getsize(p) >= 400 * 1024 ** 2:
                    continue
                found.append(p)
            except Exception:
                pass
    return sorted(set(found))


async def clean_junk_files():
    def _clean():
        deleted = []
        for p in find_cleanup_candidates():
            try:
                os.remove(p); deleted.append(p)
            except Exception:
                pass
        return deleted
    deleted = await asyncio.to_thread(_clean)
    return {"deleted_count": len(deleted), "deleted": [os.path.basename(x) for x in deleted[:50]]}


async def health_summary():
    running = await task_display_rows("running")
    done = await list_tasks("done")
    codes = await get_code_watchlist()
    actresses = await get_actress_watchlist()
    cfg = await get_config_summary()
    state = await get_resource_state()
    settings = await get_settings()
    return {"gopeed": cfg, "gopeed_connection": await gopeed_connection_settings(False), "running_count": len(running), "done_count": len(done), "code_watchlist_count": len(codes), "actress_watchlist_count": len(actresses), "resource_update_count": len(state.get("updates", [])), "updates": state.get("updates", [])[:5], "actress_updates": state.get("actress_updates", [])[:5], "scheduler": state.get("scheduler", {}), "settings": settings}
