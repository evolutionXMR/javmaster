import asyncio
import hashlib
import hmac
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from urllib.parse import quote

import aiohttp

from config import (
    BOT_TASKS_FILE, CLEANUP_PATH, DATA_DIR, DEFAULT_GOPEED_DOWNLOAD_PATH,
    GOPEED_TOKEN, GOPEED_URL, JAVMASTER_RESOURCE_STATE_FILE,
    JAVMASTER_SETTINGS_FILE, WATCHLIST_FILE,
)

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
    # Gopeed connection overrides. Empty token means use config.py fallback.
    "gopeed_url": GOPEED_URL,
    "gopeed_username": "",
    "gopeed_download_path": DEFAULT_GOPEED_DOWNLOAD_PATH,
    "gopeed_token_set": bool(GOPEED_TOKEN),
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
    merged["gopeed_url"] = str(merged.get("gopeed_url") or GOPEED_URL)
    merged["gopeed_username"] = str(merged.get("gopeed_username") or "")
    merged["gopeed_download_path"] = str(merged.get("gopeed_download_path") or "/app/Downloads/video")
    merged["gopeed_token_set"] = bool(settings.get("gopeed_token") or GOPEED_TOKEN)
    # Never return persisted secrets to the browser.
    merged.pop("gopeed_token", None)
    return merged


async def save_settings(payload):
    raw = await load_json(SETTINGS_FILE, {})
    if not isinstance(raw, dict):
        raw = {}
    current = await get_settings()
    allowed = {
        "language", "discord_enabled", "auto_code_search", "auto_code_search_interval_min", "preview_images",
        "code_search_enabled", "code_search_schedule_mode", "code_search_interval_hours", "code_search_daily_time",
        "actress_search_enabled", "actress_search_schedule_mode", "actress_search_interval_hours", "actress_search_daily_time",
        "gopeed_url", "gopeed_username", "gopeed_download_path",
    }
    for key in allowed:
        if key in payload:
            raw[key] = payload[key]
            current[key] = payload[key]
    if "gopeed_token" in payload and str(payload["gopeed_token"]).strip():
        raw["gopeed_token"] = str(payload["gopeed_token"]).strip()
        raw["gopeed_token_set"] = True
        current["gopeed_token_set"] = True
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


async def get_config_summary():
    res = await gopeed_api("config")
    data = res.get("data") if isinstance(res, dict) else None
    if not isinstance(data, dict):
        return {"ok": False, "error": (res or {}).get("error") or (res or {}).get("msg") or "unknown"}
    bt = (data.get("protocolConfig") or {}).get("bt") or {}
    return {"ok": True, "downloadDir": data.get("downloadDir"), "maxRunning": data.get("maxRunning"), "bt": {"listenPort": bt.get("listenPort"), "seedKeep": bt.get("seedKeep"), "seedRatio": bt.get("seedRatio"), "seedTime": bt.get("seedTime")}}


async def list_tasks(status="running"):
    res = await gopeed_api(f"tasks?status={status}")
    data = res.get("data", []) if isinstance(res, dict) else []
    return data if isinstance(data, list) else []


async def delete_task(task_id, force=True):
    return await gopeed_api(f"tasks/{task_id}?force={str(force).lower()}", method="DELETE")


async def task_display_rows(status="running"):
    tasks = await list_tasks(status)
    rows = []
    for t in tasks:
        task_id = t.get("id")
        detail = (await gopeed_api(f"tasks/{task_id}")).get("data", {}) if task_id else {}
        stats = (await gopeed_api(f"tasks/{task_id}/stats")).get("data", {}) if task_id else {}
        progress = detail.get("progress") or t.get("progress") or {}
        meta = detail.get("meta") or t.get("meta") or {}
        total = ((meta.get("res") or {}).get("size") or 0)
        downloaded = progress.get("downloaded", 0) or 0
        pct = round(downloaded / total * 100, 1) if total else 0
        name = t.get("name") or detail.get("name") or "未命名任务"
        rows.append({
            "id": task_id,
            "name": name,
            "short_name": name[:120],
            "status": detail.get("status") or t.get("status") or status,
            "progress": pct,
            "progress_text": f"{pct}% ({format_size(downloaded)} / {format_size(total)})",
            "download_speed_text": format_size(progress.get("speed", 0)) + "/s",
            "upload_speed_text": format_size(progress.get("uploadSpeed", 0)) + "/s",
            "downloaded_text": format_size(downloaded),
            "total_size_text": format_size(total),
            "active_peers": stats.get("activePeers", 0) or 0,
            "total_peers": stats.get("totalPeers", 0) or 0,
        })
    return rows


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


async def push_resource_to_gopeed(link):
    conn = await gopeed_connection_settings(False)
    payload = {"req": {"url": link}}
    if conn.get("download_path"):
        payload["opt"] = {"path": conn["download_path"]}
    return await gopeed_api("tasks", method="POST", data=payload)


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


async def refresh_actress_works():
    """Refresh all watched actresses and update latest_seen when new works appear."""
    from bot import fetch_actress_works
    items = await get_actress_watchlist()
    updates = []
    for item in items:
        try:
            canonical, works, matched_query, warning = await fetch_actress_works(item.get("name", ""), limit=5)
            if warning or not works:
                item["last_error"] = warning or "No works"
                continue
            latest = works[0].get("code")
            old_latest = item.get("latest_seen")
            item["last_checked"] = int(time.time())
            item.pop("last_error", None)
            if latest and latest != old_latest:
                item["latest_seen"] = latest
                if old_latest:
                    updates.append({"name": item.get("name"), "old": old_latest, "latest": latest, "work": works[0], "ts": int(time.time())})
        except Exception as exc:
            item["last_error"] = str(exc)
    await save_actress_watchlist(items)
    state = await get_resource_state()
    if updates:
        state["actress_updates"] = (updates + state.get("actress_updates", []))[:50]
    state["last_actress_auto_run"] = int(time.time())
    await save_json(RESOURCE_STATE_FILE, state)
    return {"checked": len(items), "updates": updates, "watchlist": items}


async def scheduler_mark_run(prefix):
    state = await get_resource_state()
    state.setdefault("scheduler", {})[prefix] = {"last_run": int(time.time()), "last_date": time.strftime("%Y-%m-%d")}
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
