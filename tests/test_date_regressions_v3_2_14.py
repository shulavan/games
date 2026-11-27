import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import edogawa_ical as app


class V3214DateRegressionTests(unittest.TestCase):
    def test_version_is_3214(self):
        self.assertEqual(app.APP_VERSION, "3.2.16")
        self.assertIn("PRODID:-//Edogawa Ward Events//JP v3.2.16//EN", app.build_ics([]))

    def test_multiple_date_rows_after_date_heading_are_all_retained(self):
        html = """<html><body><main><h1>家庭教育セミナー 幼児コース</h1>
        <h2>開催日時</h2>
        <p>2026年11月20日 10時00分～11時30分</p>
        <p>開催内容：親子で学びます</p><p>対象：未就学児</p>
        <p>2026年11月27日 10時00分～11時30分</p>
        <h2>会場</h2><p>子ども未来館</p></main></body></html>"""
        with patch.object(app, "TODAY", date(2026, 11, 1)):
            detail = app.extract_detail(html, "https://example.jp/event.html")
        got = [(x["span"][0], x["start_time"], x["end_time"]) for x in detail["sessions"]]
        self.assertEqual(got, [
            (date(2026, 11, 20), "10:00", "11:30"),
            (date(2026, 11, 27), "10:00", "11:30"),
        ])

    def test_archive_streaming_period_is_all_day_not_timed(self):
        html = """<html><body><main><h1>女性のSOSを見逃さないために</h1>
        <h2>アーカイブ配信期間</h2>
        <p>2026年11月26日 18時30分～2026年12月9日 20時00分</p>
        <h2>会場</h2><p>タワーホール船堀</p></main></body></html>"""
        with patch.object(app, "TODAY", date(2026, 11, 1)):
            detail = app.extract_detail(html, "https://example.jp/archive.html")
        self.assertTrue(detail["sessions"])
        self.assertTrue(all(x["start_time"] is None and x["end_time"] is None for x in detail["sessions"]))
        self.assertEqual(detail["spans"][0], (date(2026, 11, 26), date(2026, 12, 9)))

    def test_child_museum_global_navigation_before_update_date_is_removed(self):
        html = """<html><body><main><h1>おりがみヒコーキで科学する</h1>
        <p>このサイトではJavaScriptを使用したコンテンツ・機能を提供しています</p>
        <p>English 简体中文 한국어 日本語 すべての言語を見る 閉じる</p>
        <p>トップページ &gt; イベントカレンダー(子ども未来館)</p>
        <p>更新日：2026年11月1日</p><h2>開催日時</h2>
        <p>2026年12月5日 10時00分～11時00分</p><h2>会場</h2><p>子ども未来館</p>
        <p>紙飛行機を作って飛ばします。</p></main></body></html>"""
        with patch.object(app, "TODAY", date(2026, 11, 1)):
            detail = app.extract_detail(html, "https://example.jp/mirai.html", default_venue="子ども未来館")
        self.assertNotIn("English", detail["text"])
        self.assertNotIn("トップページ", detail["text"])
        self.assertIn("紙飛行機を作って飛ばします", detail["text"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
