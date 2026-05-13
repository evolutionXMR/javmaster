import asyncio
import os
import secrets
from datetime import datetime
from urllib.parse import urlparse

import aiohttp
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

import app_core

app = FastAPI(title="JavMaster")
SESSION_COOKIE = "javmaster_session"
SESSIONS = set()
auto_task = None


class CodesPayload(BaseModel):
    codes: str


class ActressPayload(BaseModel):
    name: str


class ResourcePushPayload(BaseModel):
    link: str


class SettingsPayload(BaseModel):
    language: str | None = None
    discord_enabled: bool | None = None
    bot_token: str | None = None
    auto_code_search: bool | None = None
    auto_code_search_interval_min: int | None = None
    code_search_enabled: bool | None = None
    code_search_schedule_mode: str | None = None
    code_search_interval_hours: float | None = None
    code_search_daily_time: str | None = None
    actress_search_enabled: bool | None = None
    actress_search_schedule_mode: str | None = None
    actress_search_interval_hours: float | None = None
    actress_search_daily_time: str | None = None
    gopeed_url: str | None = None
    gopeed_username: str | None = None
    gopeed_token: str | None = None
    gopeed_download_path: str | None = None
    preview_images: bool | None = None


class LoginPayload(BaseModel):
    username: str
    password: str


class PasswordPayload(BaseModel):
    current_password: str
    new_password: str
    username: str | None = None


def valid_session(request: Request):
    sid = request.cookies.get(SESSION_COOKIE)
    return bool(sid and sid in SESSIONS)


async def require_auth(request: Request):
    if not valid_session(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Login required")
    return "web"


def ok(data=None):
    return {"ok": True, "data": data}


def scheduler_due(settings, state, prefix):
    if not settings.get(f"{prefix}_enabled"):
        return False
    mode = settings.get(f"{prefix}_schedule_mode") or "interval"
    last = (state.get("scheduler") or {}).get(prefix) or {}
    now_ts = int(datetime.now().timestamp())
    if mode == "daily":
        target = str(settings.get(f"{prefix}_daily_time") or "09:00")[:5]
        today = datetime.now().strftime("%Y-%m-%d")
        return last.get("last_date") != today and datetime.now().strftime("%H:%M") >= target
    hours = float(settings.get(f"{prefix}_interval_hours") or 6)
    return not last.get("last_run") or now_ts - int(last.get("last_run") or 0) >= max(1, hours) * 3600


async def auto_search_loop():
    while True:
        try:
            settings = await app_core.get_settings()
            state = await app_core.get_resource_state()
            if scheduler_due(settings, state, "code_search"):
                await app_core.refresh_code_resources()
                await app_core.scheduler_mark_run("code_search")
            if scheduler_due(settings, state, "actress_search"):
                await app_core.refresh_actress_works()
                await app_core.scheduler_mark_run("actress_search")
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"auto_search_loop error: {exc}")
            await asyncio.sleep(300)


@app.on_event("startup")
async def startup():
    global auto_task
    auto_task = asyncio.create_task(auto_search_loop())


@app.on_event("shutdown")
async def shutdown():
    if auto_task:
        auto_task.cancel()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if not valid_session(request):
        return HTMLResponse(LOGIN_HTML)
    return HTMLResponse(INDEX_HTML)


@app.post("/api/auth/login")
async def api_login(payload: LoginPayload, response: Response):
    if not await app_core.verify_web_login(payload.username, payload.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号或密码错误")
    sid = secrets.token_urlsafe(32)
    SESSIONS.add(sid)
    response.set_cookie(SESSION_COOKIE, sid, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 14)
    return ok({"username": payload.username})


@app.post("/api/auth/logout")
async def api_logout(request: Request, response: Response):
    sid = request.cookies.get(SESSION_COOKIE)
    if sid:
        SESSIONS.discard(sid)
    response.delete_cookie(SESSION_COOKIE)
    return ok()


@app.post("/api/auth/password")
async def api_change_password(payload: PasswordPayload, _user=Depends(require_auth)):
    result = await app_core.change_web_password(payload.current_password, payload.new_password, payload.username)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "密码修改失败"))
    return ok(result)


@app.get("/api/health")
async def api_health(_user=Depends(require_auth)):
    return ok(await app_core.health_summary())


@app.get("/api/tasks/running")
async def api_running(_user=Depends(require_auth)):
    return ok(await app_core.task_display_rows("running"))


@app.get("/api/tasks/done")
async def api_done(_user=Depends(require_auth)):
    return ok(await app_core.task_display_rows("done"))


@app.post("/api/tasks/{task_id}/delete")
async def api_delete_task(task_id: str, _user=Depends(require_auth)):
    return ok(await app_core.delete_task(task_id, True))


@app.get("/api/watchlist/codes")
async def api_codes(_user=Depends(require_auth)):
    return ok(await app_core.watchlist_with_resource_state())


async def refresh_added_code_resources(codes):
    for code in codes or []:
        await app_core.refresh_code_resources(code)


@app.post("/api/watchlist/codes")
async def api_add_codes(payload: CodesPayload, background_tasks: BackgroundTasks, _user=Depends(require_auth)):
    res = await app_core.add_codes(payload.codes)
    # Return immediately so the left watchlist updates without waiting for slow Sukebei lookups.
    # Newly-added codes are refreshed in the background and the UI polls the list again shortly after.
    if res.get("added"):
        background_tasks.add_task(refresh_added_code_resources, res.get("added"))
    return ok(res)


@app.delete("/api/watchlist/codes/{code}")
async def api_remove_code(code: str, _user=Depends(require_auth)):
    return ok(await app_core.remove_code(code))


@app.post("/api/watchlist/codes/refresh")
async def api_refresh_codes(_user=Depends(require_auth)):
    return ok(await app_core.refresh_code_resources())


@app.get("/api/resources/{code}")
async def api_resources(code: str, min_gb: float | None = None, max_gb: float | None = None, keyword: str = "", _user=Depends(require_auth)):
    return ok(await app_core.search_sukebei(code, limit=50, min_gb=min_gb, max_gb=max_gb, keyword=keyword))


@app.post("/api/resources/push")
async def api_push_resource(payload: ResourcePushPayload, _user=Depends(require_auth)):
    return ok(await app_core.push_resource_to_gopeed(payload.link))


@app.get("/api/watchlist/actresses")
async def api_actresses(_user=Depends(require_auth)):
    return ok(await app_core.get_actress_watchlist())


@app.post("/api/watchlist/actresses")
async def api_add_actress(payload: ActressPayload, _user=Depends(require_auth)):
    from bot import fetch_actress_works, format_work_line, normalize_actress_query
    name = payload.name.strip()
    canonical, works, matched_query, warning = await fetch_actress_works(name, limit=1)
    if warning:
        raise HTTPException(status_code=400, detail=warning)
    items = await app_core.get_actress_watchlist()
    for item in items:
        aliases = set(item.get("aliases", [])) | {item.get("name")}
        if canonical in aliases or normalize_actress_query(name) in aliases:
            latest = works[0]["code"] if works else item.get("latest_seen")
            item["latest_seen"] = item.get("latest_seen") or latest
            alias = normalize_actress_query(name)
            if alias not in item["aliases"]:
                item["aliases"].append(alias)
            await app_core.save_actress_watchlist(items)
            return ok({"added": False, "item": item, "message": f"{canonical} 已存在"})
    item = {"name": canonical, "aliases": sorted(set([canonical, normalize_actress_query(name), matched_query])), "latest_seen": works[0]["code"] if works else None}
    items.append(item)
    await app_core.save_actress_watchlist(items)
    return ok({"added": True, "item": item, "preview": format_work_line(works[0]) if works else ""})


@app.delete("/api/watchlist/actresses/{name}")
async def api_remove_actress(name: str, _user=Depends(require_auth)):
    return ok(await app_core.remove_actress(name))


@app.post("/api/watchlist/actresses/refresh")
async def api_refresh_actresses(_user=Depends(require_auth)):
    return ok(await app_core.refresh_actress_works())


@app.get("/api/actresses/{name}/works")
async def api_actress_works(name: str, since: str = "", count: int = 5, _user=Depends(require_auth)):
    from bot import fetch_actress_works
    canonical, works, matched_query, warning = await fetch_actress_works(name, limit=max(10, min(count, 50)))
    if warning:
        raise HTTPException(status_code=400, detail=warning)
    settings = await app_core.get_settings()
    watched = set(await app_core.get_code_watchlist())
    filtered = app_core.filter_actress_works(works, since=since, count=count)
    for work in filtered:
        work["in_watchlist"] = str(work.get("code", "")).upper() in watched
        if not settings.get("preview_images", True):
            work["image"] = ""
    return ok({"canonical": canonical, "matched_query": matched_query, "preview_images": settings.get("preview_images", True), "works": filtered})


@app.get("/api/image-proxy")
async def api_image_proxy(url: str, _user=Depends(require_auth)):
    """Proxy JavBus cover images so browsers do not hit hotlink 403."""
    parsed = urlparse(url or "")
    if parsed.scheme not in {"http", "https"} or parsed.netloc not in {"www.javbus.com", "javbus.com"} or not parsed.path.startswith("/pics/"):
        raise HTTPException(status_code=400, detail="Unsupported image URL")
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
        "Referer": "https://www.javbus.com/",
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=20) as resp:
                if resp.status != 200:
                    raise HTTPException(status_code=502, detail=f"Image fetch failed: {resp.status}")
                content_type = resp.headers.get("Content-Type") or "image/jpeg"
                if not content_type.startswith("image/"):
                    raise HTTPException(status_code=502, detail="Upstream did not return an image")
                body = await resp.read()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Image fetch failed: {exc}")
    return Response(content=body, media_type=content_type, headers={"Cache-Control": "public, max-age=604800"})


@app.get("/api/settings")
async def api_get_settings(_user=Depends(require_auth)):
    return ok(await app_core.get_settings())


@app.post("/api/settings")
async def api_save_settings(payload: SettingsPayload, _user=Depends(require_auth)):
    return ok(await app_core.save_settings(payload.model_dump(exclude_none=True)))


@app.post("/api/gopeed/test")
async def api_test_gopeed(payload: SettingsPayload, _user=Depends(require_auth)):
    return ok(await app_core.test_gopeed_connection(payload.model_dump(exclude_none=True)))


@app.post("/api/check")
async def api_check(_user=Depends(require_auth)):
    from bot import perform_nyaa_check
    pushed, logs = await perform_nyaa_check()
    return ok({"pushed": pushed, "logs": logs})


@app.post("/api/clean")
async def api_clean(_user=Depends(require_auth)):
    return ok(await app_core.clean_junk_files())


LOGIN_HTML = r'''
<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>JavMaster Login</title>
<style>:root{color-scheme:dark;--bg:#0b1020;--panel:#121a2d;--muted:#92a0b8;--text:#e8eefc;--brand:#7c5cff;--danger:#ff5c7a;--line:#22304c}body{margin:0;min-height:100vh;display:grid;place-items:center;background:linear-gradient(135deg,#0b1020,#131b33);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:var(--text)}.box{width:min(420px,calc(100vw - 36px));background:rgba(18,26,45,.94);border:1px solid var(--line);border-radius:20px;padding:28px;box-shadow:0 20px 50px #0007}h1{margin:0 0 6px}.muted{color:var(--muted);margin-bottom:22px}label{display:block;margin:14px 0 6px}input{width:100%;box-sizing:border-box;background:#0d1427;color:var(--text);border:1px solid var(--line);border-radius:12px;padding:12px;font-size:16px}button{width:100%;margin-top:20px;background:var(--brand);color:#fff;border:0;border-radius:12px;padding:12px;font-weight:800;font-size:16px;cursor:pointer}.err{color:var(--danger);min-height:22px;margin-top:12px}</style></head>
<body><form class="box" id="loginForm"><h1>JavMaster</h1><div class="muted">网页登录</div><label>账号</label><input id="username" autocomplete="username" value="admin"><label>密码</label><input id="password" type="password" autocomplete="current-password" autofocus><button id="btn">登录</button><div id="err" class="err"></div></form>
<script>document.getElementById('loginForm').addEventListener('submit',async e=>{e.preventDefault();const btn=document.getElementById('btn'),err=document.getElementById('err');btn.disabled=true;err.textContent='';try{const r=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:username.value,password:password.value})});if(!r.ok)throw new Error((await r.json()).detail||'登录失败');location.href='/'}catch(ex){err.textContent=ex.message}finally{btn.disabled=false}});</script></body></html>
'''

INDEX_HTML = r'''
<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>JavMaster</title>
<style>:root{color-scheme:dark;--bg:#0b1020;--panel:#121a2d;--muted:#92a0b8;--text:#e8eefc;--brand:#7c5cff;--danger:#ff5c7a;--ok:#35d49a;--warn:#ffd166;--line:#22304c}body{margin:0;background:linear-gradient(135deg,#0b1020,#131b33);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:var(--text)}header{padding:22px 28px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:center}.wrap{padding:20px;max-width:1500px;margin:0 auto}.tabs{display:flex;gap:8px;flex-wrap:wrap}.tab{background:#18223b}.tab.active{background:var(--brand)}.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.card{background:rgba(18,26,45,.92);border:1px solid var(--line);border-radius:16px;padding:16px;box-shadow:0 10px 30px #0004}.num{font-size:30px;font-weight:800}.section{margin-top:18px}.two{display:grid;grid-template-columns:330px 1fr;gap:14px}.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}button{background:var(--brand);color:#fff;border:0;border-radius:10px;padding:9px 13px;font-weight:700;cursor:pointer}button.danger{background:var(--danger)}button.secondary{background:#263553}input,select{background:#0d1427;color:var(--text);border:1px solid var(--line);border-radius:10px;padding:10px}table{width:100%;border-collapse:collapse}th,td{border-bottom:1px solid var(--line);padding:10px;text-align:left;vertical-align:top}th{color:var(--muted);font-size:12px}.name{max-width:650px;word-break:break-all}.pill{display:inline-block;padding:2px 8px;border-radius:999px;background:#22304c;color:#cbd7f2;font-size:12px;margin:3px}.hot{border-color:var(--ok)!important;box-shadow:0 0 0 1px #35d49a55}.warn{color:var(--warn)}.ok{color:var(--ok)}.muted{color:var(--muted)}pre{white-space:pre-wrap;background:#081022;border:1px solid var(--line);padding:12px;border-radius:12px;max-height:320px;overflow:auto}.codeItem{padding:10px;border:1px solid var(--line);border-radius:12px;margin-bottom:8px;cursor:pointer}.codeItem.active{background:#263553}.page{display:none}.page.active{display:block}.spinner{display:inline-block;width:14px;height:14px;border:2px solid #ffffff55;border-top-color:#fff;border-radius:50%;animation:spin .8s linear infinite;vertical-align:-2px;margin-right:6px}.statusLine{margin-top:8px;min-height:20px}.busy{opacity:.75;cursor:wait}.workCard{display:grid;grid-template-columns:120px 1fr;gap:12px;margin-bottom:10px}.workCard.noCover{grid-template-columns:1fr}.workCover{width:120px;max-height:170px;object-fit:cover;border-radius:10px;border:1px solid var(--line);background:#081022}.workCover.empty{display:none}.addedBtn{background:#2f405f!important;color:#9fb0d1!important;cursor:not-allowed}@keyframes spin{to{transform:rotate(360deg)}}@media(max-width:900px){.grid,.two{grid-template-columns:1fr}.wrap{padding:14px}}</style></head>
<body><header><div><h1>JavMaster</h1><div class="muted" data-i18n="subtitle">Web GUI + Discord Bot 控制台</div></div><div class="tabs"><button class="tab active" onclick="showTab('home')" data-i18n="home">主页</button><button class="tab" onclick="showTab('downloads')" data-i18n="downloads">下载</button><button class="tab" onclick="showTab('codes')" data-i18n="codes">番号</button><button class="tab" onclick="showTab('actresses')" data-i18n="actresses">女优</button><button class="tab" onclick="showTab('settings')" data-i18n="settings">设置</button></div></header>
<div class="wrap">
<section id="home" class="page active"><div class="grid" id="cards"></div><div class="section card"><h2 data-i18n="updates">资源更新提示</h2><div id="updates"></div></div></section>
<section id="downloads" class="page"><div class="card"><div class="row" style="justify-content:space-between"><h2 data-i18n="runningDownloads">下载中</h2><button onclick="loadRunning()" data-i18n="refresh">刷新</button></div><div id="running"></div></div></section>
<section id="codes" class="page"><div class="two"><div class="card"><h2 data-i18n="codeWatchlist">番号列表</h2><div class="row"><input id="codesInput" placeholder="IENF-424 FSTU-029"><button id="addCodesBtn" onclick="addCodes()" data-i18n="add">添加</button></div><button id="refreshCodesBtn" class="secondary" style="margin-top:10px" onclick="refreshCodes()" data-i18n="searchAll">搜索全部</button><div id="codeStatus" class="statusLine muted"></div><div id="codeList" style="margin-top:12px"></div></div><div class="card"><h2><span id="selectedCode">-</span> <span data-i18n="resources">资源</span></h2><div class="row"><input id="minGb" type="number" step="0.1" placeholder="Min GB"><input id="maxGb" type="number" step="0.1" placeholder="Max GB"><input id="kw" placeholder="keyword"><button onclick="loadResources()" data-i18n="filter">筛选</button></div><div id="resources" style="margin-top:12px"></div></div></div></section>
<section id="actresses" class="page"><div class="two"><div class="card"><h2 data-i18n="actressWatchlist">女优列表</h2><div class="row"><input id="actressInput" placeholder="星宮一花"><button onclick="addActress()" data-i18n="add">添加</button></div><div id="actressList" style="margin-top:12px"></div></div><div class="card"><h2><span id="selectedActress">-</span> <span data-i18n="works">作品</span></h2><div class="row"><input id="since" type="date"><input id="workCount" type="number" min="1" max="50" value="5"><button onclick="loadWorks()" data-i18n="filter">筛选</button></div><div id="works" style="margin-top:12px"></div></div></div></section>
<section id="settings" class="page"><div class="card"><h2 data-i18n="settings">设置</h2><div class="row"><label data-i18n="language">语言</label><select id="language"><option value="zh">中文</option><option value="en">English</option></select></div><hr><h3>Discord Bot</h3><div class="row"><label><input id="discordEnabled" type="checkbox"> Discord Bot</label><input id="botToken" style="min-width:420px" type="password" placeholder="Bot token (leave blank to keep current)"></div><hr><h3>自动搜索番号</h3><div class="row"><label><input id="codeSearchEnabled" type="checkbox"> 启用自动搜索番号</label><select id="codeSearchMode" onchange="updateScheduleVisibility()"><option value="interval">间隔运行</option><option value="daily">每天定时</option></select><span id="codeIntervalGroup" class="row" style="display:inline-flex"><input id="codeIntervalHours" type="number" min="1" step="1" value="6"><span class="muted">小时</span></span><span id="codeDailyGroup" class="row" style="display:none"><span class="muted">每天运行时间</span><input id="codeDailyTime" type="time" value="09:00"></span></div><hr><h3>自动更新女优作品</h3><div class="row"><label><input id="actressSearchEnabled" type="checkbox"> 启用自动更新女优作品</label><select id="actressSearchMode" onchange="updateScheduleVisibility()"><option value="interval">间隔运行</option><option value="daily">每天定时</option></select><span id="actressIntervalGroup" class="row" style="display:inline-flex"><input id="actressIntervalHours" type="number" min="1" step="1" value="12"><span class="muted">小时</span></span><span id="actressDailyGroup" class="row" style="display:none"><span class="muted">每天运行时间</span><input id="actressDailyTime" type="time" value="09:30"></span></div><hr><h3>Gopeed 连接</h3><div class="row"><input id="gopeedUrl" style="min-width:360px" placeholder="Gopeed API，例如 http://192.168.8.88:9999/api/v1"><input id="gopeedUsername" placeholder="用户名（可选）"><input id="gopeedToken" type="password" placeholder="API Token，留空保持不变"><button class="secondary" onclick="testGopeed()">测试连接</button></div><div class="row"><input id="gopeedDownloadPath" style="min-width:420px" placeholder="Gopeed 默认下载地址，例如 /app/Downloads/video"><span class="muted">Gopeed 容器内路径</span></div><div class="muted" id="gopeedTokenHint"></div><div class="statusLine muted" id="gopeedTestStatus"></div><hr><div class="row"><label><input id="previewImages" type="checkbox"> <span data-i18n="previewImages">女优作品显示预览图片</span></label></div><button onclick="saveSettings()" data-i18n="save">保存</button><p class="muted" data-i18n="restartNote">Discord Token / Bot 开关需要重启容器后生效。</p></div><div class="section card"><h2 data-i18n="changePassword">修改网页登录密码</h2><div class="row"><input id="currentPassword" type="password" placeholder="当前密码"><input id="newPassword" type="password" placeholder="新密码，至少 6 位"><button onclick="changePassword()" data-i18n="changePassword">修改密码</button></div><p class="muted" data-i18n="passwordNote">默认账号 admin，默认密码 123456。修改后请用新密码重新登录。</p></div><div class="section card"><h2 data-i18n="maintenance">维护</h2><button onclick="manualCheck()">Check</button> <button onclick="refreshActresses()">刷新女优作品</button> <button onclick="logout()" data-i18n="logout">退出登录</button> <button class="danger" onclick="cleanJunk()" data-i18n="clean">清理垃圾文件</button><pre id="log">Ready.</pre></div></section>
</div>
<script>
let selectedCode=null, selectedActress=null, lang='zh';
const T={zh:{subtitle:'Web GUI + Discord Bot 控制台',home:'主页',downloads:'下载',codes:'番号',actresses:'女优',settings:'设置',updates:'资源更新提示',runningDownloads:'下载中',refresh:'刷新',codeWatchlist:'番号列表',add:'添加',searchAll:'搜索全部',resources:'资源',filter:'筛选',actressWatchlist:'女优列表',works:'作品',language:'语言',autoSearch:'自动运行番号搜索',save:'保存',restartNote:'Discord Token / Bot 开关需要重启容器后生效。',maintenance:'维护',clean:'清理垃圾文件',push:'推送下载',remove:'删除',searching:'搜索中...',adding:'添加中...',searchDone:'搜索完成',addDone:'已添加，列表已更新',changePassword:'修改网页登录密码',passwordNote:'默认账号 admin，默认密码 123456。修改后请用新密码重新登录。',logout:'退出登录',passwordChanged:'密码已修改，请重新登录',previewImages:'女优作品显示预览图片',addToWatchlist:'添加到番号列表',added:'已添加',addingCode:'添加中...'},en:{subtitle:'Web GUI + Discord Bot Console',home:'Home',downloads:'Downloads',codes:'Codes',actresses:'Actresses',settings:'Settings',updates:'Resource Updates',runningDownloads:'Running Downloads',refresh:'Refresh',codeWatchlist:'Code Watchlist',add:'Add',searchAll:'Search All',resources:'Resources',filter:'Filter',actressWatchlist:'Actress Watchlist',works:'Works',language:'Language',autoSearch:'Auto code search',save:'Save',restartNote:'Discord token / bot toggle requires container restart.',maintenance:'Maintenance',clean:'Clean junk',push:'Push',remove:'Remove',searching:'Searching...',adding:'Adding...',searchDone:'Search complete',addDone:'Added. List updated.',changePassword:'Change web login password',passwordNote:'Default username is admin and default password is 123456. Log in again after changing it.',logout:'Log out',passwordChanged:'Password changed. Please log in again.',previewImages:'Show preview images for actress works',addToWatchlist:'Add to watchlist',added:'Added',addingCode:'Adding...'}};
function tr(k){return (T[lang]||T.zh)[k]||k} function applyLang(){document.querySelectorAll('[data-i18n]').forEach(e=>e.textContent=tr(e.dataset.i18n));document.documentElement.lang=lang==='zh'?'zh-CN':'en'}
async function api(url,opts={}){const r=await fetch(url,{headers:{'Content-Type':'application/json'},...opts});if(r.status===401){location.href='/';throw new Error('Login required')}if(!r.ok)throw new Error(await r.text());return await r.json()}function esc(s){return String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}function log(x){document.getElementById('log').textContent=typeof x==='string'?x:JSON.stringify(x,null,2)}
function showTab(id){document.querySelectorAll('.page').forEach(p=>p.classList.toggle('active',p.id===id));document.querySelectorAll('.tab').forEach(b=>b.classList.remove('active'));event.target.classList.add('active'); if(id==='downloads')loadRunning(); if(id==='codes')loadCodes(); if(id==='actresses')loadActresses(); if(id==='settings')loadSettings();}
async function loadHealth(){const {data}=await api('/api/health');lang=data.settings?.language||lang;applyLang();const g=data.gopeed||{};document.getElementById('cards').innerHTML=`<div class="card"><div class="muted">Gopeed</div><div class="num ${g.ok?'ok':''}">${g.ok?'OK':'ERR'}</div><div class="muted">${esc(g.downloadDir||g.error||'')}</div></div><div class="card"><div class="muted">${tr('downloads')}</div><div class="num">${data.running_count}</div></div><div class="card"><div class="muted">${tr('codes')}</div><div class="num">${data.code_watchlist_count}</div></div><div class="card"><div class="muted">${tr('actresses')}</div><div class="num">${data.actress_watchlist_count}</div></div>`;document.getElementById('updates').innerHTML=(data.updates||[]).map(u=>`<div class="pill warn">${esc(u.code)} +${u.count}: ${esc(u.latest?.title||'')}</div>`).join('')||'<span class="muted">No updates</span>'}
function taskTable(rows){if(!rows.length)return '<p class="muted">No tasks.</p>';return `<table><thead><tr><th>任务名</th><th>进度</th><th>速度</th><th>Peers</th><th></th></tr></thead><tbody>`+rows.map(t=>`<tr><td class="name">${esc(t.short_name)}</td><td>${t.progress_text}</td><td>↓ ${t.download_speed_text}<br>↑ ${t.upload_speed_text}</td><td><span class="pill">${t.active_peers}/${t.total_peers}</span></td><td><button class="danger" onclick="deleteTask('${t.id}')">${tr('remove')}</button></td></tr>`).join('')+'</tbody></table>'}
async function loadRunning(){const {data}=await api('/api/tasks/running');document.getElementById('running').innerHTML=taskTable(data)}async function deleteTask(id){if(!confirm('Delete?'))return;log(await api(`/api/tasks/${id}/delete`,{method:'POST'}));await refreshAll()}
async function loadCodes(){const {data}=await api('/api/watchlist/codes');document.getElementById('codeList').innerHTML=data.map(c=>`<div class="codeItem ${c.has_resources?'hot':''} ${selectedCode===c.code?'active':''}" onclick="selectCode('${c.code}')"><b>${c.code}</b> <span class="pill">${c.count||0}</span><button class="danger" style="float:right" onclick="event.stopPropagation();removeCode('${c.code}')">×</button><div class="muted">${esc(c.latest_title||c.error||'')}</div></div>`).join('')||'<p class="muted">Empty</p>';if(!selectedCode&&data[0]){selectedCode=data[0].code;document.getElementById('selectedCode').textContent=selectedCode;loadResources().catch(()=>{})}}
async function selectCode(c){selectedCode=c;document.getElementById('selectedCode').textContent=c;await loadCodes();await loadResources()}async function addCodes(){const input=document.getElementById('codesInput');const btn=document.getElementById('addCodesBtn');const status=document.getElementById('codeStatus');const raw=input.value.trim();if(!raw)return;btn.disabled=true;btn.classList.add('busy');status.innerHTML=`<span class="spinner"></span>${tr('adding')}`;try{const res=await api('/api/watchlist/codes',{method:'POST',body:JSON.stringify({codes:raw})});log(res);input.value='';if(res.data?.added?.[0])selectedCode=res.data.added[0];await loadCodes();await loadHealth();status.textContent=tr('addDone');setTimeout(()=>loadCodes().catch(()=>{}),2500)}catch(e){status.textContent=e.message;log(e.message)}finally{btn.disabled=false;btn.classList.remove('busy')}}async function removeCode(c){log(await api(`/api/watchlist/codes/${encodeURIComponent(c)}`,{method:'DELETE'}));if(selectedCode===c)selectedCode=null;await loadCodes();await loadHealth()}async function refreshCodes(){const btn=document.getElementById('refreshCodesBtn');const status=document.getElementById('codeStatus');btn.disabled=true;btn.classList.add('busy');btn.innerHTML=`<span class="spinner"></span>${tr('searching')}`;status.innerHTML=`<span class="spinner"></span>${tr('searching')}`;try{const res=await api('/api/watchlist/codes/refresh',{method:'POST'});log(res);await loadCodes();await loadHealth();status.textContent=tr('searchDone')}catch(e){status.textContent=e.message;log(e.message)}finally{btn.disabled=false;btn.classList.remove('busy');btn.textContent=tr('searchAll')}}
async function loadResources(){if(!selectedCode)return;const qs=new URLSearchParams();['minGb','maxGb','kw'].forEach(id=>{const v=document.getElementById(id).value;if(v)qs.set(id==='kw'?'keyword':id.replace('Gb','_gb').toLowerCase(),v)});const {data}=await api(`/api/resources/${selectedCode}?${qs}`);document.getElementById('resources').innerHTML=`<table><thead><tr><th>Name</th><th>Size</th><th>S/L</th><th></th></tr></thead><tbody>`+data.map(r=>`<tr><td class="name"><a href="${esc(r.link)}" target="_blank">${esc(r.title)}</a></td><td>${r.size_text}</td><td>${r.seeders}/${r.leechers}</td><td><button onclick='pushResource(${JSON.stringify(r.link)})'>${tr('push')}</button></td></tr>`).join('')+'</tbody></table>'}async function pushResource(link){log(await api('/api/resources/push',{method:'POST',body:JSON.stringify({link})}))}
async function loadActresses(){const {data}=await api('/api/watchlist/actresses');document.getElementById('actressList').innerHTML=data.map(a=>`<div class="codeItem ${selectedActress===a.name?'active':''}" onclick="selectActress('${encodeURIComponent(a.name)}','${esc(a.name)}')"><b>${esc(a.name)}</b> <span class="pill">${esc(a.latest_seen||'')}</span><button class="danger" style="float:right" onclick="event.stopPropagation();removeActress('${encodeURIComponent(a.name)}')">×</button></div>`).join('')||'<p class="muted">Empty</p>';if(!selectedActress&&data[0])selectActress(encodeURIComponent(data[0].name),data[0].name)}async function selectActress(enc,name){selectedActress=decodeURIComponent(enc);document.getElementById('selectedActress').textContent=name;await loadActresses();await loadWorks()}async function addActress(){log('Searching...');log(await api('/api/watchlist/actresses',{method:'POST',body:JSON.stringify({name:document.getElementById('actressInput').value})}));document.getElementById('actressInput').value='';await loadActresses();await loadHealth()}async function removeActress(n){log(await api(`/api/watchlist/actresses/${n}`,{method:'DELETE'}));if(selectedActress===decodeURIComponent(n))selectedActress=null;await loadActresses();await loadHealth()}function workCard(w){const img=w.image?`<img class="workCover" loading="lazy" src="/api/image-proxy?url=${encodeURIComponent(w.image)}" onerror="this.remove();this.closest('.workCard').classList.add('noCover')">`:'';const btn=w.in_watchlist?`<button class="addedBtn" disabled>${tr('added')}</button>`:`<button id="workAdd_${esc(w.code)}" onclick="addWorkCode('${esc(w.code)}')">${tr('addToWatchlist')}</button>`;return `<div class="card workCard ${w.image?'':'noCover'}">${img}<div><b>${esc(w.code)}</b> <span class="pill">${esc(w.date)}</span> ${w.is_compilation?'<span class="pill warn">合集/总集</span>':''}<div>${esc(w.title)}</div><div class="row" style="margin-top:8px"><a target="_blank" href="${esc(w.url)}">link</a>${btn}</div></div></div>`}async function addWorkCode(code){const btn=document.getElementById(`workAdd_${code}`);if(btn){btn.disabled=true;btn.textContent=tr('addingCode')}const res=await api('/api/watchlist/codes',{method:'POST',body:JSON.stringify({codes:code})});log(res);await loadCodes();await loadHealth();await loadWorks()}async function loadWorks(){if(!selectedActress)return;const qs=new URLSearchParams({count:document.getElementById('workCount').value||5});if(document.getElementById('since').value)qs.set('since',document.getElementById('since').value);const {data}=await api(`/api/actresses/${encodeURIComponent(selectedActress)}/works?${qs}`);document.getElementById('works').innerHTML=data.works.map(workCard).join('')||'<p class="muted">No works</p>'}
function setScheduleGroup(prefix){const mode=document.getElementById(prefix+'SearchMode')?.value||'interval';const interval=document.getElementById(prefix+'IntervalGroup');const daily=document.getElementById(prefix+'DailyGroup');if(interval)interval.style.display=mode==='interval'?'inline-flex':'none';if(daily)daily.style.display=mode==='daily'?'inline-flex':'none'}function updateScheduleVisibility(){setScheduleGroup('code');setScheduleGroup('actress')}async function loadSettings(){const {data}=await api('/api/settings');document.getElementById('language').value=data.language||'zh';document.getElementById('discordEnabled').checked=!!data.discord_enabled;document.getElementById('codeSearchEnabled').checked=!!data.code_search_enabled;document.getElementById('codeSearchMode').value=data.code_search_schedule_mode||'interval';document.getElementById('codeIntervalHours').value=data.code_search_interval_hours||6;document.getElementById('codeDailyTime').value=data.code_search_daily_time||'09:00';document.getElementById('actressSearchEnabled').checked=!!data.actress_search_enabled;document.getElementById('actressSearchMode').value=data.actress_search_schedule_mode||'interval';document.getElementById('actressIntervalHours').value=data.actress_search_interval_hours||12;document.getElementById('actressDailyTime').value=data.actress_search_daily_time||'09:30';updateScheduleVisibility();document.getElementById('gopeedUrl').value=data.gopeed_url||'';document.getElementById('gopeedUsername').value=data.gopeed_username||'';document.getElementById('gopeedDownloadPath').value=data.gopeed_download_path||'/app/Downloads/video';document.getElementById('gopeedTokenHint').textContent=data.gopeed_token_set?'当前已保存 Gopeed API Token，留空不会更改。':'当前未保存 Gopeed API Token。';document.getElementById('previewImages').checked=data.preview_images!==false}async function saveSettings(){const body={language:document.getElementById('language').value,discord_enabled:document.getElementById('discordEnabled').checked,code_search_enabled:document.getElementById('codeSearchEnabled').checked,code_search_schedule_mode:document.getElementById('codeSearchMode').value,code_search_interval_hours:+document.getElementById('codeIntervalHours').value||6,code_search_daily_time:document.getElementById('codeDailyTime').value||'09:00',actress_search_enabled:document.getElementById('actressSearchEnabled').checked,actress_search_schedule_mode:document.getElementById('actressSearchMode').value,actress_search_interval_hours:+document.getElementById('actressIntervalHours').value||12,actress_search_daily_time:document.getElementById('actressDailyTime').value||'09:30',gopeed_url:document.getElementById('gopeedUrl').value.trim(),gopeed_username:document.getElementById('gopeedUsername').value.trim(),gopeed_download_path:document.getElementById('gopeedDownloadPath').value.trim(),preview_images:document.getElementById('previewImages').checked};const tok=document.getElementById('botToken').value;if(tok)body.bot_token=tok;const gtok=document.getElementById('gopeedToken').value;if(gtok)body.gopeed_token=gtok;log(await api('/api/settings',{method:'POST',body:JSON.stringify(body)}));document.getElementById('gopeedToken').value='';lang=body.language;applyLang();await loadHealth();await loadSettings();if(selectedActress)await loadWorks()}document.getElementById('language')?.addEventListener('change',e=>{lang=e.target.value;applyLang()});document.getElementById('codeSearchMode')?.addEventListener('change',updateScheduleVisibility);document.getElementById('actressSearchMode')?.addEventListener('change',updateScheduleVisibility);async function testGopeed(){const status=document.getElementById('gopeedTestStatus');status.innerHTML='<span class="spinner"></span>测试中...';const body={gopeed_url:document.getElementById('gopeedUrl').value.trim(),gopeed_username:document.getElementById('gopeedUsername').value.trim(),gopeed_download_path:document.getElementById('gopeedDownloadPath').value.trim()};const gtok=document.getElementById('gopeedToken').value;if(gtok)body.gopeed_token=gtok;try{const res=await api('/api/gopeed/test',{method:'POST',body:JSON.stringify(body)});const d=res.data||{};status.textContent=d.ok?`✅ 连接成功：${d.url} | Gopeed下载目录 ${d.gopeed_download_dir||'-'} | 本应用默认路径 ${d.configured_download_path||'-'}`:`❌ 连接失败：${d.message||d.error||'unknown'}`;log(res)}catch(e){status.textContent='❌ 连接失败：'+e.message;log(e.message)}}async function changePassword(){const current_password=document.getElementById('currentPassword').value,new_password=document.getElementById('newPassword').value;if(!current_password||!new_password)return log('Password required');log(await api('/api/auth/password',{method:'POST',body:JSON.stringify({current_password,new_password})}));alert(tr('passwordChanged'));await logout()}async function logout(){await fetch('/api/auth/logout',{method:'POST'});location.href='/'}async function manualCheck(){log(await api('/api/check',{method:'POST'}));await refreshAll()}async function refreshActresses(){log(await api('/api/watchlist/actresses/refresh',{method:'POST'}));await loadActresses();await loadHealth()}async function cleanJunk(){if(!confirm('Clean?'))return;log(await api('/api/clean',{method:'POST'}))}async function refreshAll(){await Promise.all([loadHealth(),loadRunning().catch(()=>{}),loadCodes().catch(()=>{}),loadActresses().catch(()=>{})])}refreshAll();setInterval(loadRunning,5000);setInterval(loadHealth,15000);
</script></body></html>
'''
