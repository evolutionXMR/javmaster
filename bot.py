import discord
from discord import app_commands
from discord.ext import tasks
import aiohttp
import asyncio
import os
import json
import xml.etree.ElementTree as ET
import re
import html
from urllib.parse import quote

# 【核心改动】从 config.py 导入所有配置参数
try:
    from config import (
        TOKEN, REPORT_CHANNEL_ID, GOPEED_URL, GOPEED_TOKEN,
        DATA_DIR, WATCHLIST_FILE, BOT_TASKS_FILE, CLEANUP_PATH
    )
except ImportError:
    print("❌ 错误：找不到 config.py 文件，请确保它在 bot.py 同级目录下。")
    exit(1)

class DownloadBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()
        print("✅ Slash commands synced!")
        if not code_search_scheduler.is_running():
            code_search_scheduler.start()

bot = DownloadBot()

# --- 异步数据读写辅助 ---
async def load_json(filepath):
    def _load():
        if os.path.exists(filepath):
            try:
                with open(filepath, 'r', encoding='utf-8') as f: return json.load(f)
            except: pass
        return []
    return await asyncio.to_thread(_load)

async def save_json(filepath, data):
    def _save():
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, 'w', encoding='utf-8') as f: json.dump(data, f, indent=4)
    await asyncio.to_thread(_save)

# --- 异步 API 接口与工具 ---
async def gopeed_api(endpoint, method='GET', data=None):
    url = f"{GOPEED_URL}/{endpoint}"
    headers = {'X-Api-Token': GOPEED_TOKEN, 'Content-Type': 'application/json'}
    async with aiohttp.ClientSession() as session:
        try:
            if method == 'GET':
                async with session.get(url, headers=headers, timeout=10) as resp: return await resp.json()
            elif method == 'POST':
                async with session.post(url, headers=headers, json=data, timeout=10) as resp: return await resp.json()
            elif method == 'DELETE':
                async with session.delete(url, headers=headers, timeout=10) as resp:
                    if resp.status in [200, 204]:
                        try: return await resp.json()
                        except: return {"code": 0}
                    return {"error": f"HTTP {resp.status}"}
        except Exception as e: return {"error": str(e)}

def parse_size_to_bytes(size_str):
    match = re.search(r"([\d\.]+)\s*([KMGT])", size_str, re.I)
    if not match: return 0
    val, unit = float(match.group(1)), match.group(2).upper()
    if unit == 'K': return val * 1024
    elif unit == 'M': return val * 1024**2
    elif unit == 'G': return val * 1024**3
    elif unit == 'T': return val * 1024**4
    return val

def format_size(bytes_val):
    if bytes_val is None: return "0 B"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_val < 1024: return f"{bytes_val:.2f} {unit}"
        bytes_val /= 1024
    return f"{bytes_val:.2f} PB"

# --- JAV 女优新作监控辅助 ---
ACTRESS_WATCHLIST_FILE = os.environ.get('ACTRESS_WATCHLIST_FILE', f'{DATA_DIR}/actress_watchlist.json')
from config import JAVBUS_BASE_URL
MAX_SEEK_ACTRESSES = 5
MAX_SEEK_WORKS = 10

# 常见简体/误写 -> 日文/繁体名转换。先覆盖高频字符，匹配不到再给出错误。
NAME_CHAR_MAP = str.maketrans({
    '宫': '宮', '泽': '沢', '边': '辺', '濑': '瀬', '樱': '桜', '亚': '亜',
    '爱': '愛', '叶': '葉', '广': '広', '滨': '浜', '岛': '島', '绪': '緒',
    '实': '実', '阳': '陽', '优': '優', '梦': '夢', '遥': '遥', '龙': '龍',
    '冈': '岡', '条': '条', '乡': '郷', '飞': '飛', '鸟': '鳥', '铃': '鈴',
    '会': '會', '与': '與', '馆': '館', '采': '彩',
})

# 明确处理改名/常见误写；查询时会把这些候选全部拉一遍，选择发售日最新的一组。
NAME_ALIAS_VARIANTS = {
    '河北采花': ['河北彩花', '河北彩伽'],
    '河北彩花': ['河北彩花', '河北彩伽'],
    '河北彩伽': ['河北彩伽', '河北彩花'],
}

COMPILATION_PATTERNS = re.compile(
    r'(総集|全作品|BEST|ベスト|complete|コンプリート|合集|総集編|\d+時間|\d+H|永久保存版)',
    re.I
)

CODE_RE = re.compile(r'\b[A-Z]{2,8}-\d{2,5}\b')
DATE_RE = re.compile(r'\d{4}-\d{2}-\d{2}')


def normalize_actress_query(name: str) -> str:
    return re.sub(r'\s+', ' ', (name or '').strip())


def actress_query_variants(name: str):
    base = normalize_actress_query(name)
    mapped = base.translate(NAME_CHAR_MAP)
    seed = [base, mapped]
    seed.extend(NAME_ALIAS_VARIANTS.get(base, []))
    seed.extend(NAME_ALIAS_VARIANTS.get(mapped, []))
    variants = []
    for item in seed:
        for v in [item, item.replace(' ', '')]:
            if v and v not in variants:
                variants.append(v)
    return variants


def javbus_headers():
    return {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36',
        'Accept-Language': 'zh-CN,zh;q=0.9,ja;q=0.8,en;q=0.7',
    }


def strip_tags(raw: str) -> str:
    raw = re.sub(r'<(script|style).*?</\1>', ' ', raw or '', flags=re.S | re.I)
    raw = re.sub(r'<[^>]+>', ' ', raw)
    return re.sub(r'\s+', ' ', html.unescape(raw)).strip()


def parse_javbus_list(page_text: str):
    results = []
    for href, block in re.findall(r'<a class="movie-box" href="([^"]+)">(.*?)</a>', page_text, re.S):
        title_m = re.search(r'<img[^>]+title="([^"]*)"', block)
        img_m = re.search(r'<img[^>]+src="([^"]+)"', block)
        dates = re.findall(r'<date>(.*?)</date>', block)
        code = html.unescape(dates[0]).strip() if dates else ''
        release_date = html.unescape(dates[1]).strip() if len(dates) > 1 else ''
        title = html.unescape(title_m.group(1)).strip() if title_m else strip_tags(block)
        image = html.unescape(img_m.group(1)).strip() if img_m else ''
        if image.startswith('//'):
            image = 'https:' + image
        elif image.startswith('/'):
            image = JAVBUS_BASE_URL.rstrip('/') + image
        if not code or not CODE_RE.search(code):
            continue
        results.append({
            'code': code,
            'date': release_date,
            'title': title,
            'url': href,
            'image': image,
            'is_compilation': bool(COMPILATION_PATTERNS.search(title)),
        })
    return results


async def fetch_text(session, url: str):
    async with session.get(url, headers=javbus_headers(), timeout=20) as resp:
        if resp.status != 200:
            return None, resp.status
        return await resp.text(), resp.status


async def fetch_actress_works(query: str, limit: int = 5):
    """Return (canonical_name, works, matched_query, warning)."""
    query = normalize_actress_query(query)
    if not query:
        return None, [], None, '名字不能为空。'

    matches = []
    async with aiohttp.ClientSession() as session:
        for variant in actress_query_variants(query):
            url = f"{JAVBUS_BASE_URL}/search/{quote(variant, safe='')}&type=1"
            text, status = await fetch_text(session, url)
            if not text:
                continue
            works = parse_javbus_list(text)
            if not works:
                continue

            # 详情页更可信：从第一条作品中提取站点显示的演员名，作为 canonical name。
            canonical = variant
            detail_text, _ = await fetch_text(session, works[0]['url'])
            if detail_text:
                actor_candidates = re.findall(r'<a[^>]+href="[^"]*/star/[^"]+"[^>]*>(.*?)</a>', detail_text, re.S)
                clean_actors = [strip_tags(a) for a in actor_candidates if strip_tags(a)]
                for actor in clean_actors:
                    if variant in actor or actor in variant or query in actor:
                        canonical = actor
                        break
                else:
                    if clean_actors:
                        canonical = clean_actors[-1]
            first_date = works[0].get('date') or '0000-00-00'
            matches.append((first_date, canonical, works[:limit], variant))

    if matches:
        # 多个别名都能命中时，选择最新发售日，避免“河北彩花”旧名漏掉“河北彩伽”新作。
        matches.sort(key=lambda x: x[0], reverse=True)
        _, canonical, works, variant = matches[0]
        return canonical, works, variant, None

    return None, [], None, f"找不到女优 `{query}`。请尽量使用日文名，例如 `星宮一花`；如果是简体名我会自动尝试常见转换。"


async def load_actress_watchlist():
    data = await load_json(ACTRESS_WATCHLIST_FILE)
    if isinstance(data, list):
        # 新格式：[{name, aliases, latest_seen}]
        normalized = []
        for item in data:
            if isinstance(item, str):
                normalized.append({'name': item, 'aliases': [item], 'latest_seen': None})
            elif isinstance(item, dict) and item.get('name'):
                item.setdefault('aliases', [item['name']])
                item.setdefault('latest_seen', None)
                normalized.append(item)
        return normalized
    return []


async def save_actress_watchlist(items):
    await save_json(ACTRESS_WATCHLIST_FILE, items)


async def add_code_to_watchlist(code: str):
    code = (code or '').upper().strip()
    if not CODE_RE.fullmatch(code):
        return False, f"番号格式不正确：{code}"
    watchlist = await load_json(WATCHLIST_FILE)
    if not isinstance(watchlist, list):
        watchlist = []
    normalized = [str(c).upper() for c in watchlist]
    if code in normalized:
        return False, f"`{code}` 已经在番号 watchlist 里。"
    watchlist.append(code)
    await save_json(WATCHLIST_FILE, watchlist)
    return True, f"✅ 已加入番号 watchlist：`{code}`"


def format_work_line(work):
    tag = ' 🧩合集/总集' if work.get('is_compilation') else ''
    return f"• `{work['code']}` | {work.get('date') or '未知日期'} | {work.get('title', '')[:90]}{tag}\n  {work.get('url', '')}"


def chunk_messages(header, sections, limit=1900):
    messages, cur = [], header
    for section in sections:
        addition = ('\n\n' if cur != header else '\n') + section
        if len(cur) + len(addition) > limit:
            messages.append(cur)
            cur = header + '\n' + section
        else:
            cur += addition
    if cur.strip():
        messages.append(cur)
    return messages

# --- 视图 UI 类 ---
class TaskDeleteView(discord.ui.View):
    def __init__(self, tasks):
        super().__init__(timeout=120)
        for t in tasks[:25]:
            name = t.get('name', 'Unknown')
            short_name = name[:15] + "..." if len(name) > 15 else name
            btn = discord.ui.Button(label=f"🗑️ 删 {short_name}", style=discord.ButtonStyle.danger, custom_id=f"del_{t.get('id')}")
            btn.callback = self.make_callback(t.get('id'), name)
            self.add_item(btn)

    def make_callback(self, task_id, full_name):
        async def callback(interaction: discord.Interaction):
            for child in self.children:
                if child.custom_id == f"del_{task_id}":
                    child.disabled = True
                    child.style = discord.ButtonStyle.secondary
                    child.label = "已删除"
            await interaction.response.edit_message(view=self)
            await gopeed_api(f"tasks/{task_id}?force=true", method="DELETE")
            await interaction.followup.send(f"✅ 已强制删除任务并清理本地文件: **{full_name}**", ephemeral=True)
        return callback

class SeekAddCodeView(discord.ui.View):
    def __init__(self, works):
        super().__init__(timeout=300)
        seen = set()
        for work in works[:25]:
            code = (work.get('code') or '').upper().strip()
            if not code or code in seen:
                continue
            seen.add(code)
            btn = discord.ui.Button(
                label=f"➕ {code}"[:80],
                style=discord.ButtonStyle.primary,
                custom_id=f"watch_code_{code}",
            )
            btn.callback = self.make_callback(code)
            self.add_item(btn)

    def make_callback(self, code):
        async def callback(interaction: discord.Interaction):
            added, msg = await add_code_to_watchlist(code)
            if added:
                for child in self.children:
                    if getattr(child, 'custom_id', None) == f"watch_code_{code}":
                        child.disabled = True
                        child.style = discord.ButtonStyle.success
                        child.label = f"✅ {code}"
                await interaction.response.edit_message(view=self)
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        return callback

class CleanConfirmView(discord.ui.View):
    def __init__(self, files_to_delete):
        super().__init__(timeout=60)
        self.files_to_delete = files_to_delete

    @discord.ui.button(label="确认清理", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children: child.disabled = True
        await interaction.response.edit_message(content="⏳ 正在清理中...", view=self)
        def _do_clean():
            count = 0
            for f in self.files_to_delete:
                try: os.remove(f); count += 1
                except: pass
            return count
        deleted_count = await asyncio.to_thread(_do_clean)
        await interaction.edit_original_response(content=f"✅ 清理完成！删除了 **{deleted_count}** 个文件。", view=None)

    @discord.ui.button(label="取消", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children: child.disabled = True
        await interaction.response.edit_message(content="🛑 清理已取消。", view=None)

# ================= 核心业务逻辑 =================

async def perform_nyaa_check():
    watchlist = await load_json(WATCHLIST_FILE)
    if not watchlist: return 0, ["列表为空。"]
    bot_tasks = await load_json(BOT_TASKS_FILE)
    if isinstance(bot_tasks, list): bot_tasks = {tid: "" for tid in bot_tasks}

    pushed, logs = 0, []
    successful_codes = [] 

    async with aiohttp.ClientSession() as session:
        for code in watchlist:
            url = f"https://sukebei.nyaa.si/?page=rss&q={code}&c=0_0&f=0"
            try:
                async with session.get(url, timeout=15) as resp:
                    if resp.status != 200:
                        logs.append(f"⚠️ `{code}`: 请求失败({resp.status})")
                        continue
                    root = ET.fromstring(await resp.text())
                    candidates = []
                    for item in root.findall('./channel/item'):
                        title = item.find('title').text if item.find('title') is not None else ""
                        link = item.find('link').text
                        size_node, seeders = None, 0
                        for child in item:
                            if child.tag.endswith('size'): size_node = child
                            elif child.tag.endswith('seeders'):
                                try: seeders = int(child.text)
                                except: pass
                        if size_node is not None and parse_size_to_bytes(size_node.text) > 4 * 1024**3:
                            score = 0
                            title_lower = title.lower()
                            if 'offkab' in title_lower: score += 10000
                            if '[fhd]' in title_lower or '[fhdc]' in title_lower: score += 1000
                            score += seeders
                            candidates.append({'title': title, 'link': link, 'size_text': size_node.text, 'score': score, 'seeders': seeders})
                    
                    if candidates:
                        candidates.sort(key=lambda x: x['score'], reverse=True)
                        best_match = candidates[0]
                        # Use app_core so scheduled / manual Discord checks share the same
                        # Gopeed URL, token, and download path from the Web settings page.
                        import app_core
                        res = await app_core.push_resource_to_gopeed(best_match['link'])
                        if res and res.get('code') == 0:
                            bot_tasks[res.get('data')] = code 
                            tag_info = []
                            if 'offkab' in best_match['title'].lower(): tag_info.append("offkab")
                            if '[fhd]' in best_match['title'].lower() or '[fhdc]' in best_match['title'].lower(): tag_info.append("FHD")
                            tag_str = f"[{','.join(tag_info)}]" if tag_info else "[普通]"
                            logs.append(f"✅ `{code}`: {tag_str} 已推送 | 大小:{best_match['size_text']} | 做种:{best_match['seeders']}")
                            successful_codes.append(code)
                            pushed += 1
                        else: logs.append(f"❌ `{code}`: 推送失败")
                    else: logs.append(f"🔍 `{code}`: 无符合 >4GB 的资源")
            except Exception as e: logs.append(f"❌ `{code}`: 检查出错 ({e})")
                
    await save_json(BOT_TASKS_FILE, bot_tasks)
    if successful_codes:
        watchlist = [c for c in watchlist if c not in successful_codes]
        await save_json(WATCHLIST_FILE, watchlist)
    return pushed, logs

# ================= 定时与指令 =================

async def send_code_check_report(pushed, logs, title=None):
    channel = bot.get_channel(REPORT_CHANNEL_ID)
    if not channel:
        return
    if title:
        report = f"**{title}**\n" + "\n".join(logs)
    elif pushed > 0:
        report = f"**🕒 自动番号检查报告**\n" + "\n".join(logs)
    else:
        report = f"**💤 自动番号检查完毕，无新货。**\n" + "\n".join(logs)
    await channel.send(report[:1990])


@tasks.loop(minutes=1)
async def code_search_scheduler():
    """Settings-driven replacement for the old hardcoded 02:00 bot check."""
    import app_core
    settings = await app_core.get_settings()
    state = await app_core.get_resource_state()
    if not app_core.scheduler_due(settings, state, "code_search"):
        return
    pushed, logs = await perform_nyaa_check()
    await app_core.scheduler_mark_run("code_search")
    await send_code_check_report(pushed, logs)


@code_search_scheduler.before_loop
async def before_code_search_scheduler():
    await bot.wait_until_ready()

@bot.tree.command(name="add", description="批量添加代号")
async def add_code(interaction: discord.Interaction, code: str):
    codes = code.upper().replace('，', ' ').replace(',', ' ').split()
    added, exist, errors = [], [], []
    for c in codes:
        ok, msg = await add_code_to_watchlist(c)
        if ok:
            added.append(c)
        elif '已经' in msg:
            exist.append(c)
        else:
            errors.append(msg)
    msg = f"✅ 添加: `{' '.join(added)}`" if added else ""
    if exist: msg += f"\n⚠️ 已有: `{' '.join(exist)}`"
    if errors: msg += "\n❌ " + "\n❌ ".join(errors)
    await interaction.response.send_message(msg or "没有可添加的番号。")

@bot.tree.command(name="remove", description="批量移除代号")
async def remove_code(interaction: discord.Interaction, code: str):
    codes = code.upper().replace('，', ' ').replace(',', ' ').split()
    watchlist = await load_json(WATCHLIST_FILE)
    removed, not_found = [], []
    for c in codes:
        if c in watchlist: watchlist.remove(c); removed.append(c)
        else: not_found.append(c)
    await save_json(WATCHLIST_FILE, watchlist)
    msg = f"✅ 移除: `{' '.join(removed)}`" if removed else ""
    if not_found: msg += f"\n❌ 缺席: `{' '.join(not_found)}`"
    await interaction.response.send_message(msg)

@bot.tree.command(name="list", description="显示监控列表")
async def list_codes(interaction: discord.Interaction):
    watchlist = await load_json(WATCHLIST_FILE)
    await interaction.response.send_message(f"**监控中:**\n" + "\n".join(f"- `{c}`" for c in watchlist) if watchlist else "列表为空")

@bot.tree.command(name="check", description="手动执行择优检查")
async def check_nyaa(interaction: discord.Interaction):
    await interaction.response.defer()
    pushed, logs = await perform_nyaa_check()
    await interaction.followup.send(f"**📡 检查完毕！推送了 {pushed} 个任务**\n" + "\n".join(logs)[:1900])

@bot.tree.command(name="watch", description="添加 JAV 女优到新作 watchlist（支持常见简体名自动匹配）")
@app_commands.describe(name="女优名字，例如：星宫一花 / 星宮一花 / 河北彩花")
async def watch_actress(interaction: discord.Interaction, name: str):
    await interaction.response.defer()
    canonical, works, matched_query, warning = await fetch_actress_works(name, limit=1)
    if warning:
        return await interaction.followup.send(f"❌ {warning}")

    watchlist = await load_actress_watchlist()
    for item in watchlist:
        aliases = set(item.get('aliases', [])) | {item.get('name')}
        if canonical in aliases or normalize_actress_query(name) in aliases:
            latest = works[0]['code'] if works else item.get('latest_seen')
            item['latest_seen'] = item.get('latest_seen') or latest
            if normalize_actress_query(name) not in item['aliases']:
                item['aliases'].append(normalize_actress_query(name))
            await save_actress_watchlist(watchlist)
            return await interaction.followup.send(
                f"⚠️ `{canonical}` 已在女优 watchlist。当前最新识别：`{latest}`"
            )

    latest_seen = works[0]['code'] if works else None
    watchlist.append({
        'name': canonical,
        'aliases': sorted(set([canonical, normalize_actress_query(name), matched_query])),
        'latest_seen': latest_seen,
    })
    await save_actress_watchlist(watchlist)
    preview = format_work_line(works[0]) if works else '暂无作品预览'
    await interaction.followup.send(
        f"✅ 已添加女优监控：**{canonical}**\n"
        f"匹配关键词：`{matched_query}`\n"
        f"当前最新：\n{preview}"
    )


@bot.tree.command(name="seek", description="查询女优最新作品；不填名字则查 watchlist 前 5 位")
@app_commands.describe(name="可选：女优名字；不填则使用女优 watchlist", count="可选：每位女优返回几部作品，默认 5，最多 10")
async def seek_actress(interaction: discord.Interaction, name: str | None = None, count: app_commands.Range[int, 1, MAX_SEEK_WORKS] = 5):
    await interaction.response.defer()

    targets = []
    if name:
        canonical, works, matched_query, warning = await fetch_actress_works(name, limit=count)
        if warning:
            return await interaction.followup.send(f"❌ {warning}")
        targets.append((canonical, works, matched_query))
    else:
        watchlist = await load_actress_watchlist()
        if not watchlist:
            return await interaction.followup.send("女优 watchlist 为空。请先用 `/watch name:女优名` 添加。")
        for item in watchlist[:MAX_SEEK_ACTRESSES]:
            canonical, works, matched_query, warning = await fetch_actress_works(item['name'], limit=count)
            if warning:
                targets.append((item['name'], [], '匹配失败'))
            else:
                targets.append((canonical, works, matched_query))

    first = True
    for canonical, works, matched_query in targets:
        prefix = "**🔎 JAV 女优最新作品查询**\n" if first else ""
        first = False
        if not works:
            await interaction.followup.send(f"{prefix}### {canonical}\n❌ 没有拉到作品。")
            continue
        selected = works[:count]
        body = '\n'.join(format_work_line(w) for w in selected)
        msg = f"{prefix}### {canonical}  最新 {min(count, len(selected))} 部\n匹配：`{matched_query}`\n{body}\n\n点下面按钮可把对应番号加入 `/add` 使用的番号 watchlist。"
        await interaction.followup.send(msg[:1900], view=SeekAddCodeView(selected))

@bot.tree.command(name="dlist", description="下载进度与管理")
async def dlist(interaction: discord.Interaction):
    await interaction.response.defer()
    res = await gopeed_api('tasks?status=running')
    tasks_list = res.get('data', [])
    if not tasks_list: return await interaction.followup.send("无运行中任务。")
    msg = ["**📥 下载中:**"]
    for t in tasks_list:
        task_id = t.get('id')
        d = (await gopeed_api(f"tasks/{task_id}")).get('data', {})
        stats = (await gopeed_api(f"tasks/{task_id}/stats")).get('data', {}) if task_id else {}
        progress = d.get('progress', {})
        meta = d.get('meta', {})
        total_size = (meta.get('res', {}) or {}).get('size') or (t.get('meta', {}).get('res', {}) or {}).get('size') or 0
        downloaded = progress.get('downloaded', 0)
        prog = (downloaded / total_size * 100) if total_size else 0
        active_peers = stats.get('activePeers', 0)
        total_peers = stats.get('totalPeers', 0)
        name = (t.get('name') or d.get('name') or '未命名任务')[:60]
        msg.append(
            f"• **{name}** - {prog:.1f}% | ↓ {format_size(progress.get('speed', 0))}/s | "
            f"↑ {format_size(progress.get('uploadSpeed', 0))}/s | 已下 {format_size(downloaded)} / {format_size(total_size)} | "
            f"{active_peers}/{total_peers}"
        )
    await interaction.followup.send("\n".join(msg), view=TaskDeleteView(tasks_list))

@bot.tree.command(name="done", description="最近完成的任务")
async def ddone(interaction: discord.Interaction):
    await interaction.response.defer()
    res = await gopeed_api('tasks?status=done')
    msg = ["**✅ 最近完成:**"]
    for t in res.get('data', [])[:15]: msg.append(f"• {t.get('name')}")
    await interaction.followup.send("\n".join(msg) if len(msg)>1 else "无完成记录")

@bot.tree.command(name="clean", description="精准清理：基于已完成任务匹配文件夹，并避开归档目录")
async def clean_tasks(interaction: discord.Interaction):
    await interaction.response.defer()
    
    # 1. 取得 Gopeed 已下载完成的列表
    res = await gopeed_api('tasks?status=done')
    if 'error' in res: 
        return await interaction.followup.send("❌ 无法连接到 Gopeed API。")
        
    done_tasks = res.get('data', [])
    if not done_tasks:
        return await interaction.followup.send("✅ Gopeed 目前没有已完成的任务。")

    bot_tasks = await load_json(BOT_TASKS_FILE)
    if isinstance(bot_tasks, list): bot_tasks = {tid: "" for tid in bot_tasks}
        
    def _find_junk():
        to_del = []
        # 需要避开的文件夹名称 (统一用小写进行防呆匹配)
        excluded_dirs = ['javsorted', 'chinese_sorted', '国产']
        # 坚决不碰的元数据白名单
        protected_exts = ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.nfo', '.srt', '.ass', '.vtt')
        
        for t in done_tasks:
            task_name = t.get('name', '')
            task_id = t.get('id')
            
            # 2. 取得番号 (优先从数据库拿，拿不到就用正则从名字里硬抠)
            code = bot_tasks.get(task_id, '')
            if not code:
                match = re.search(r'[a-zA-Z]{2,5}-\d{3,4}', task_name)
                if match: code = match.group(0)
            
            if not code or len(code) < 3:
                continue
                
            code_lower = code.lower()
            
            # 3. 去 NAS 里找对应的文件夹
            for root, dirs, files in os.walk(CLEANUP_PATH):
                # 【核心优化】：动态修剪目录树，只要遇到这几个名字的文件夹，直接跳过，根本不进去扫描！
                dirs[:] = [d for d in dirs if d.lower() not in excluded_dirs]
                
                # 只处理子文件夹，不处理根目录
                if root.rstrip('/') == CLEANUP_PATH.rstrip('/'):
                    continue
                    
                # 找到了名字包含这个番号的文件夹！
                if code_lower in os.path.basename(root).lower():
                    # 4. 找到不是“主要视频”的文件并列入删除清单
                    for f in files:
                        p = os.path.join(root, f)
                        try:
                            f_lower = f.lower()
                            # 保护伞：剧照/字幕/NFO 绝对不删
                            if f_lower.endswith(protected_exts):
                                continue
                            # 主要视频判定：大于 400MB 的正片绝对不删
                            if os.path.getsize(p) >= 400 * 1024**2:
                                continue 
                            
                            # 剩下的杂鱼文件（几十MB的广告MP4、网址快捷方式、txt等），全部拉出来
                            if p not in to_del:
                                to_del.append(p)
                        except Exception:
                            pass
                            
        return list(set(to_del))

    files = await asyncio.to_thread(_find_junk)
    if not files: 
        return await interaction.followup.send("✅ 干净得很！刚下载完的任务文件夹里没有发现垃圾文件。")
        
    preview = "\n".join([os.path.basename(f) for f in files[:10]])
    if len(files) > 10: preview += f"\n...及其他 {len(files)-10} 个文件"
    
    warning_msg = f"⚠️ **精准扫描完成！在下载文件夹中揪出 {len(files)} 个杂鱼文件，确认删除？**\n```text\n{preview}\n```"
    await interaction.followup.send(warning_msg, view=CleanConfirmView(files))
    
    
  
@bot.event
async def on_ready(): print(f'✅ {bot.user} 已上线')

async def start_discord_bot():
    await bot.start(TOKEN)

if __name__ == '__main__':
    bot.run(TOKEN)