
import asyncio
import xml.etree.ElementTree as ET
from pathlib import Path

import jav_scraper


def test_scrape_movie_uses_actor_folder_code_nfo_poster_thumb_and_extrafanart(tmp_path, monkeypatch):
    src_dir = tmp_path / "src"
    out_dir = tmp_path / "out"
    src_dir.mkdir()
    source = src_dir / "SONE-573.mp4"
    source.write_bytes(b"video")

    async def fake_fetch_metadata(code):
        return {
            "code": code,
            "title": "テスト作品名",
            "url": "https://example.test/SONE-573",
            "cover": "https://example.test/cover.jpg",
            "date": "2026-01-02",
            "runtime": "120分鐘",
            "director": "",
            "studio": "",
            "publisher": "",
            "series": "",
            "actors": ["女優A"],
            "genres": ["tag1"],
        }

    async def fake_download_file(url, dest):
        dest.write_bytes(b"jpg")
        return True

    monkeypatch.setattr(jav_scraper, "fetch_metadata", fake_fetch_metadata)
    monkeypatch.setattr(jav_scraper, "_download_file", fake_download_file)

    result = asyncio.run(jav_scraper.scrape_movie(source, out_dir))

    dest_dir = out_dir / "女優A" / "SONE-573"
    assert Path(result["save_dir"]) == dest_dir
    assert (dest_dir / "SONE-573.mp4").read_bytes() == b"video"
    assert (dest_dir / "SONE-573.nfo").exists()
    assert not (dest_dir / "movie.nfo").exists()
    assert (dest_dir / "poster.jpg").read_bytes() == b"jpg"
    assert (dest_dir / "thumb.jpg").read_bytes() == b"jpg"
    assert (dest_dir / "extrafanart").is_dir()
    assert not (out_dir / "SONE-573").exists()


def test_scrape_movie_ch_suffix_uses_base_code_for_lookup_and_adds_chinese_subtitle_tag(tmp_path, monkeypatch):
    src_dir = tmp_path / "src"
    out_dir = tmp_path / "out"
    src_dir.mkdir()
    source = src_dir / "PRWF-010ch.mp4"
    source.write_bytes(b"video")
    seen = {}

    async def fake_fetch_metadata(code):
        seen["code"] = code
        return {
            "code": code,
            "title": "中文字幕版本测试",
            "url": "https://example.test/PRWF-010",
            "cover": "",
            "date": "2026-01-02",
            "runtime": "120分鐘",
            "director": "",
            "studio": "",
            "publisher": "",
            "series": "",
            "actors": ["女優B"],
            "genres": ["tag1"],
        }

    monkeypatch.setattr(jav_scraper, "fetch_metadata", fake_fetch_metadata)

    result = asyncio.run(jav_scraper.scrape_movie(source, out_dir))

    assert seen["code"] == "PRWF-010"
    assert result["local_code"] == "PRWF-010-C"
    assert result["has_chinese_subtitle"] is True
    dest_dir = out_dir / "女優B" / "PRWF-010"
    assert Path(result["save_dir"]) == dest_dir
    assert (dest_dir / "PRWF-010-C.mp4").read_bytes() == b"video"
    nfo_path = dest_dir / "PRWF-010-C.nfo"
    assert nfo_path.exists()
    root = ET.parse(nfo_path).getroot()
    assert root.findtext("num") == "PRWF-010-C"
    assert "中文字幕" in [e.text for e in root.findall("genre")]
    assert "中文字幕" in [e.text for e in root.findall("tag")]



def test_actor_folder_joins_up_to_three_actors_and_uses_many_for_more():
    assert jav_scraper._actor_folder_name(["美咲かんな", "前田美波"]) == "美咲かんな,前田美波"
    assert jav_scraper._actor_folder_name(["A", "B", "C"]) == "A,B,C"
    assert jav_scraper._actor_folder_name(["A", "B", "C", "D"]) == "多人作品"
    assert jav_scraper._actor_folder_name([]) == "未知女优"
