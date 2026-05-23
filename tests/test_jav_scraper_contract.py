
import asyncio
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import jav_scraper

async def fake_download_file(url, dest):
    dest.write_bytes(b"jpg")
    return True

async def main():
    jav_scraper._download_file = fake_download_file
    with TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        out_dir = tmp_path / "out"

        async def fake_fetch_metadata(code):
            return {
                "code": code,
                "title": "テスト作品名",
                "url": f"https://example.test/{code}",
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

        jav_scraper.fetch_metadata = fake_fetch_metadata
        source = tmp_path / "src" / "SONE-573.mp4"
        source.parent.mkdir()
        source.write_bytes(b"video")
        result = await jav_scraper.scrape_movie(source, out_dir)
        dest_dir = out_dir / "女優A" / "SONE-573"
        assert Path(result["save_dir"]) == dest_dir, result
        assert (dest_dir / "SONE-573.mp4").read_bytes() == b"video"
        assert (dest_dir / "SONE-573.nfo").exists()
        assert not (dest_dir / "movie.nfo").exists()
        assert (dest_dir / "poster.jpg").read_bytes() == b"jpg"
        assert (dest_dir / "thumb.jpg").read_bytes() == b"jpg"
        assert (dest_dir / "extrafanart").is_dir()
        assert not (out_dir / "SONE-573").exists()

        source_ch = tmp_path / "src2" / "PRWF-010ch.mp4"
        source_ch.parent.mkdir()
        source_ch.write_bytes(b"video")
        result_ch = await jav_scraper.scrape_movie(source_ch, out_dir)
        dest_ch = out_dir / "女優A" / "PRWF-010"
        assert Path(result_ch["save_dir"]) == dest_ch, result_ch
        assert result_ch["code"] == "PRWF-010"
        assert result_ch["local_code"] == "PRWF-010-C"
        assert (dest_ch / "PRWF-010-C.mp4").read_bytes() == b"video"
        root = ET.parse(dest_ch / "PRWF-010-C.nfo").getroot()
        assert "中文字幕" in [e.text for e in root.findall("tag")]


        # 2-3 actors: join all actor names with comma in the actress folder.
        async def fake_fetch_metadata_multi(code):
            return {
                "code": code,
                "title": "多人测试",
                "url": f"https://example.test/{code}",
                "cover": "",
                "date": "2026-01-02",
                "runtime": "120分鐘",
                "director": "",
                "studio": "",
                "publisher": "",
                "series": "",
                "actors": ["美咲かんな", "前田美波"],
                "genres": [],
            }
        jav_scraper.fetch_metadata = fake_fetch_metadata_multi
        source_multi = tmp_path / "src3" / "ABCD-123.mp4"
        source_multi.parent.mkdir()
        source_multi.write_bytes(b"video")
        result_multi = await jav_scraper.scrape_movie(source_multi, out_dir)
        assert Path(result_multi["save_dir"]) == out_dir / "美咲かんな,前田美波" / "ABCD-123"

        # More than 3 actors: use 多人作品 as the actor folder.
        async def fake_fetch_metadata_many(code):
            data = await fake_fetch_metadata_multi(code)
            data["actors"] = ["A", "B", "C", "D"]
            return data
        jav_scraper.fetch_metadata = fake_fetch_metadata_many
        source_many = tmp_path / "src4" / "WXYZ-456.mp4"
        source_many.parent.mkdir()
        source_many.write_bytes(b"video")
        result_many = await jav_scraper.scrape_movie(source_many, out_dir)
        assert Path(result_many["save_dir"]) == out_dir / "多人作品" / "WXYZ-456"
    print("ok")

asyncio.run(main())
