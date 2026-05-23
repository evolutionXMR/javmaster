import asyncio
import html
import os
import re
import shutil
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote, urljoin

import aiohttp

from config import JAVBUS_BASE_URL

CODE_RE = re.compile(r"\b([A-Z]{2,8}-\d{2,5}|FC2-?PPV-?\d{5,8}|FC2-?\d{5,8})(CH)?(?![A-Z0-9])", re.I)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,ja;q=0.8,en;q=0.7",
}


def _strip_tags(raw: str) -> str:
    raw = re.sub(r"<(script|style).*?</\1>", " ", raw or "", flags=re.S | re.I)
    raw = re.sub(r"<br\s*/?>", "\n", raw, flags=re.I)
    raw = re.sub(r"<[^>]+>", " ", raw)
    return re.sub(r"[ \t\r\f\v]+", " ", html.unescape(raw)).strip()


def _clean_text(raw: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(raw or "")).strip()


def _safe_name(name: str, fallback: str = "unknown", max_len: int = 110) -> str:
    name = unicodedata.normalize("NFKC", str(name or "")).strip()
    name = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        name = fallback
    return name[:max_len].rstrip(" .") or fallback


def _actor_folder_name(actors: list[str] | None) -> str:
    clean_actors = []
    for actor in actors or []:
        name = _safe_name(actor, "", 60)
        if name and name not in clean_actors:
            clean_actors.append(name)
    if not clean_actors:
        return "未知女优"
    if len(clean_actors) > 3:
        return "多人作品"
    return _safe_name(",".join(clean_actors), "未知女优", 180)

def _normalize_code(code: str) -> str:
    code = str(code or "").upper()
    code = code.replace("FC2PPV", "FC2-PPV").replace("FC2--", "FC2-")
    code = re.sub(r"FC2-(\d)", r"FC2-\1", code)
    return code


def extract_movie_code(path_or_name: str) -> str:
    text = str(path_or_name or "").upper().replace("_", "-")
    m = CODE_RE.search(text)
    if not m:
        return ""
    return _normalize_code(m.group(1))


def extract_movie_code_info(path_or_name: str) -> dict:
    text = str(path_or_name or "").upper().replace("_", "-")
    m = CODE_RE.search(text)
    if not m:
        return {"code": "", "local_code": "", "has_chinese_subtitle": False}
    code = _normalize_code(m.group(1))
    has_chinese_subtitle = bool(m.group(2))
    local_code = f"{code}-C" if has_chinese_subtitle else code
    return {"code": code, "local_code": local_code, "has_chinese_subtitle": has_chinese_subtitle}


def _first_group(pattern: str, text: str, flags=re.S | re.I) -> str:
    m = re.search(pattern, text or "", flags)
    return html.unescape(m.group(1)).strip() if m else ""


def _abs_url(url: str) -> str:
    url = html.unescape(url or "").strip()
    if url.startswith("//"):
        return "https:" + url
    return urljoin(JAVBUS_BASE_URL.rstrip("/") + "/", url)


def _label_value(page: str, zh_label: str) -> str:
    # JavBus detail blocks are usually: <p><span class="header">發行日期</span>: 2024-01-01</p>
    pattern = rf"<span[^>]*class=[\"']header[\"'][^>]*>\s*{re.escape(zh_label)}\s*:?\s*</span>(.*?)</p>"
    return _strip_tags(_first_group(pattern, page)).lstrip(":： ")


def _label_links(page: str, zh_label: str, href_part: str | None = None) -> list[str]:
    pattern = rf"<span[^>]*class=[\"']header[\"'][^>]*>\s*{re.escape(zh_label)}\s*:?\s*</span>(.*?)</p>"
    block = _first_group(pattern, page)
    if not block:
        return []
    vals = []
    for href, body in re.findall(r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", block, re.S | re.I):
        if href_part and href_part not in href:
            continue
        text = _strip_tags(body)
        if text and text not in vals:
            vals.append(text)
    if not vals:
        plain = _strip_tags(block).lstrip(":： ")
        if plain:
            vals.append(plain)
    return vals


def parse_javbus_detail(page: str, code: str, url: str) -> dict:
    title = _strip_tags(_first_group(r"<h3[^>]*>(.*?)</h3>", page))
    if title.upper().startswith(code.upper()):
        title = title[len(code):].strip(" -_") or title
    cover = _first_group(r"<a[^>]+class=[\"']bigImage[\"'][^>]+href=[\"']([^\"']+)[\"']", page)
    if not cover:
        cover = _first_group(r"<img[^>]+src=[\"']([^\"']+)[\"'][^>]*(?:class=[\"']cover[\"']|id=[\"']bigImage[\"'])", page)
    actors = []
    for body in re.findall(r"<div[^>]+class=[\"'][^\"']*star-name[^\"']*[\"'][^>]*>(.*?)</div>", page, re.S | re.I):
        name = _strip_tags(body)
        if name and name not in actors:
            actors.append(name)
    if not actors:
        actors = _label_links(page, "演員", "/star/") or _label_links(page, "演员", "/star/")
    genres = []
    for body in re.findall(r"<span[^>]+class=[\"'][^\"']*genre[^\"']*[\"'][^>]*>(.*?)</span>", page, re.S | re.I):
        genre = _strip_tags(body)
        if genre and genre not in genres:
            genres.append(genre)
    if not genres:
        genres = _label_links(page, "類別", "/genre/") or _label_links(page, "类别", "/genre/")
    return {
        "code": code.upper(),
        "title": title or code.upper(),
        "url": url,
        "cover": _abs_url(cover) if cover else "",
        "date": _label_value(page, "發行日期") or _label_value(page, "发行日期"),
        "runtime": _label_value(page, "長度") or _label_value(page, "长度"),
        "director": (_label_links(page, "導演", "/director/") or _label_links(page, "导演", "/director/") or [""])[0],
        "studio": (_label_links(page, "製作商", "/studio/") or _label_links(page, "制作商", "/studio/") or [""])[0],
        "publisher": (_label_links(page, "發行商", "/label/") or _label_links(page, "发行商", "/label/") or [""])[0],
        "series": (_label_links(page, "系列", "/series/") or [""])[0],
        "actors": actors,
        "genres": genres,
    }


async def _fetch_text(session: aiohttp.ClientSession, url: str) -> tuple[str, int]:
    async with session.get(url, headers=HEADERS, timeout=30) as resp:
        text = await resp.text(errors="ignore")
        return text, resp.status


async def fetch_metadata(code: str) -> dict:
    code = code.upper()
    base = JAVBUS_BASE_URL.rstrip("/")
    async with aiohttp.ClientSession() as session:
        urls = [f"{base}/{quote(code, safe='-')}"]
        search_url = f"{base}/search/{quote(code, safe='')}&type=1"
        page, status = await _fetch_text(session, urls[0])
        if status == 200 and ("movie" in page.lower() or code in page.upper()) and "404" not in page[:500].lower():
            return parse_javbus_detail(page, code, urls[0])
        search_page, _ = await _fetch_text(session, search_url)
        for href, block in re.findall(r'<a class="movie-box" href="([^"]+)">(.*?)</a>', search_page, re.S | re.I):
            if code in _strip_tags(block).upper():
                detail_url = _abs_url(href)
                detail_page, detail_status = await _fetch_text(session, detail_url)
                if detail_status == 200:
                    return parse_javbus_detail(detail_page, code, detail_url)
    raise ValueError(f"JavBus 未找到番号元数据: {code}")


def write_nfo(path: Path, meta: dict, video_filename: str) -> None:
    movie = ET.Element("movie")
    def add(tag, text):
        el = ET.SubElement(movie, tag)
        el.text = str(text or "")
    display_code = meta.get("local_code") or meta.get("code") or ""
    title = f"{display_code} {meta.get('title') or ''}".strip()
    add("title", title)
    add("originaltitle", meta.get("title") or title)
    add("sorttitle", display_code or title)
    add("num", display_code)
    add("id", display_code)
    add("premiered", meta.get("date") or "")
    add("releasedate", meta.get("date") or "")
    add("runtime", re.sub(r"\D+", "", str(meta.get("runtime") or "")))
    add("director", meta.get("director") or "")
    add("studio", meta.get("studio") or meta.get("publisher") or "")
    add("maker", meta.get("studio") or "")
    add("label", meta.get("publisher") or "")
    add("series", meta.get("series") or "")
    add("plot", meta.get("url") or "")
    add("fileinfo", video_filename)
    genres = list(meta.get("genres") or [])
    if meta.get("has_chinese_subtitle") and "中文字幕" not in genres:
        genres.append("中文字幕")
    for genre in genres:
        add("genre", genre)
        add("tag", genre)
    for actor_name in meta.get("actors") or []:
        actor = ET.SubElement(movie, "actor")
        ET.SubElement(actor, "name").text = actor_name
    ET.ElementTree(movie).write(path, encoding="utf-8", xml_declaration=True)


async def _download_file(url: str, dest: Path) -> bool:
    if not url:
        return False
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers={**HEADERS, "Referer": JAVBUS_BASE_URL.rstrip("/") + "/"}, timeout=60) as resp:
            if resp.status >= 400:
                return False
            data = await resp.read()
    if not data:
        return False
    dest.write_bytes(data)
    return True


def _unique_dest(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for i in range(2, 200):
        candidate = path.with_name(f"{stem}_{i}{suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"cannot find free destination name for {path}")


async def scrape_movie(source_path: str | Path, output_path: str | Path) -> dict:
    source = Path(source_path).resolve()
    output = Path(output_path).resolve()
    if not source.exists() or not source.is_file():
        raise ValueError(f"source video not found: {source}")
    code_info = extract_movie_code_info(source.name)
    if not code_info["code"]:
        code_info = extract_movie_code_info(str(source.parent))
    code = code_info["code"]
    if not code:
        raise ValueError(f"无法从文件名/目录名识别番号: {source}")
    meta = await fetch_metadata(code)
    meta["local_code"] = code_info["local_code"] or meta["code"]
    meta["has_chinese_subtitle"] = bool(code_info["has_chinese_subtitle"])
    display_code = meta["local_code"]
    actor_folder = _actor_folder_name(meta.get("actors"))
    folder_name = _safe_name(meta["code"], meta["code"], 80)
    dest_dir = output / actor_folder / folder_name
    dest_dir.mkdir(parents=True, exist_ok=True)
    extrafanart_dir = dest_dir / "extrafanart"
    extrafanart_dir.mkdir(exist_ok=True)
    dest_video = _unique_dest(dest_dir / f"{display_code}{source.suffix.lower()}")
    shutil.move(str(source), str(dest_video))
    nfo_path = dest_dir / f"{dest_video.stem}.nfo"
    write_nfo(nfo_path, meta, dest_video.name)
    image_results = {"extrafanart": str(extrafanart_dir)}
    if meta.get("cover"):
        poster = dest_dir / "poster.jpg"
        thumb = dest_dir / "thumb.jpg"
        ok = await _download_file(meta["cover"], poster)
        image_results["poster"] = str(poster) if ok else ""
        if ok:
            try:
                shutil.copy2(poster, thumb)
                image_results["thumb"] = str(thumb)
            except Exception:
                image_results["thumb"] = ""
    return {
        "ok": True,
        "code": meta["code"],
        "local_code": display_code,
        "has_chinese_subtitle": bool(meta.get("has_chinese_subtitle")),
        "title": meta.get("title"),
        "source_path": str(source_path),
        "output_path": str(output),
        "save_dir": str(dest_dir),
        "video": str(dest_video),
        "nfo": str(nfo_path),
        "images": image_results,
        "metadata_url": meta.get("url"),
    }
