import unittest
from datetime import date

from edogawa_stadium_gemini import (
    discover_media_urls,
    find_month_article,
    validate_result,
)


class DiscoveryTests(unittest.TestCase):
    def test_find_month_article(self):
        html = '''<a href="/old/">2025年11月 大会等予定表</a>
        <a href="/2026-11/">2026(令和8)年 11月 大会等予定表</a>'''
        self.assertEqual(
            find_month_article(html, 2026, 11, "https://example.jp/base/"),
            "https://example.jp/2026-11/",
        )

    def test_extracts_images_and_pdf_and_deduplicates(self):
        html = '''<img src="/a.jpg"><img data-src="/b.webp">
        <a href="/c.pdf">PDF</a><a href="/a.jpg">same</a>
        <img src="data:image/png;base64,xxx">'''
        self.assertEqual(
            discover_media_urls(html, "https://example.jp/article/"),
            ["https://example.jp/a.jpg", "https://example.jp/b.webp", "https://example.jp/c.pdf"],
        )


class ValidationTests(unittest.TestCase):
    def base(self):
        return {
            "venue": "江戸川区球場", "year": 2026, "month": 11,
            "events": [{
                "date": "2026-11-02", "weekday": "月", "title": "全部個人タクシー軟式野球大会",
                "event_type": "大会", "status": "開催予定",
                "start_time": None, "end_time": None, "notes": "",
                "confidence": 0.98,
            }], "warnings": [],
        }

    def test_valid_unknown_times_are_allowed(self):
        self.assertEqual(validate_result(self.base(), 2026, 11), [])

    def test_rejects_weekday_mismatch(self):
        data = self.base()
        data["events"][0]["weekday"] = "火"
        self.assertTrue(any("曜日が日付と一致" in x for x in validate_result(data, 2026, 11)))

    def test_rejects_low_confidence(self):
        data = self.base()
        data["events"][0]["confidence"] = 0.5
        self.assertTrue(any("人手確認" in x for x in validate_result(data, 2026, 11)))

    def test_rejects_wrong_month(self):
        data = self.base()
        data["events"][0]["date"] = "2026-12-02"
        self.assertTrue(any("対象月外" in x for x in validate_result(data, 2026, 11)))

    def test_rejects_invalid_time(self):
        data = self.base()
        data["events"][0]["start_time"] = "25:90"
        self.assertTrue(any("時刻形式" in x for x in validate_result(data, 2026, 11)))

    def test_rejects_duplicate_event(self):
        data = self.base()
        data["events"].append(dict(data["events"][0]))
        self.assertTrue(any("重複" in x for x in validate_result(data, 2026, 11)))

    def test_rejects_end_before_start(self):
        data = self.base()
        data["events"][0]["start_time"] = "15:00"
        data["events"][0]["end_time"] = "09:00"
        self.assertTrue(any("終了時刻" in x for x in validate_result(data, 2026, 11)))


if __name__ == "__main__":
    unittest.main()
