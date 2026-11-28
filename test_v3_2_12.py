import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import edogawa_ical as app
from edogawa_stadium_gemini import _month_from_title, _discover_month_pages, collect_stadium_events


class YearRegressionTests(unittest.TestCase):
    def test_explicit_base_year_never_rolls_old_month_day_into_next_year(self):
        with patch.object(app, "TODAY", date(2026, 11, 27)):
            self.assertEqual(app.parse_date_spans("11月3日", base_year=2026), [])
            self.assertEqual(app.parse_date_spans("11月3日", base_year=2026, include_past=True),
                             [(date(2026, 11, 3), date(2026, 11, 3))])

    def test_yearless_spring_date_without_source_year_still_rolls_forward(self):
        with patch.object(app, "TODAY", date(2026, 11, 27)):
            self.assertEqual(app.parse_date_spans("4月10日開催"),
                             [(date(2027, 4, 10), date(2027, 4, 10))])

    def test_old_article_publication_year_is_exposed_for_listing_context_fallback(self):
        html = """<html><head><meta property='article:published_time' content='2023-10-16T10:00:00+09:00'></head>
        <body><main><h1>イベント案内</h1><h2>内容</h2><p>詳細情報</p></main></body></html>"""
        detail = app.extract_detail(html, "https://example.jp/old-event.html")
        self.assertEqual(detail["published_year"], 2023)

    def test_old_article_publication_year_prevents_future_ghost_event(self):
        html = """<html><head><meta property='article:published_time' content='2023-10-16T10:00:00+09:00'></head>
        <body><main><h1>ノルディックウォーキング体験</h1><h2>開催日</h2><p>11月3日</p>
        <h2>場所</h2><p>葛西臨海公園</p></main></body></html>"""
        with patch.object(app, "TODAY", date(2026, 11, 27)):
            detail = app.extract_detail(html, "https://example.jp/old-event.html")
        self.assertEqual(detail["spans"], [])

    def test_2026_article_does_not_move_october_event_to_2027(self):
        html = """<html><head><meta property='article:published_time' content='2026-10-01T10:00:00+09:00'></head>
        <body><main><h1>区民スポーツ大会</h1><h2>開催日</h2><p>10月16日</p>
        <h2>場所</h2><p>江戸川区陸上競技場</p></main></body></html>"""
        with patch.object(app, "TODAY", date(2026, 11, 27)):
            detail = app.extract_detail(html, "https://example.jp/2026-event.html")
        self.assertEqual(detail["spans"], [])


class TitleRegressionTests(unittest.TestCase):
    def test_field_label_is_not_an_event_title(self):
        html = """<html><body><main><h1>主催</h1><h2>江戸川区民文化祭</h2>
        <h3>開催日</h3><p>2026年12月12日</p><h3>場所</h3><p>江戸川区民館</p></main></body></html>"""
        with patch.object(app, "TODAY", date(2026, 11, 27)):
            detail = app.extract_detail(html, "https://example.jp/event.html")
        self.assertEqual(detail["title"], "江戸川区民文化祭")
        self.assertFalse(app.is_non_event_page(detail["title"]))
        self.assertTrue(app.is_non_event_page("主催"))

    def test_asahi_csv_period_is_suppressed_even_if_it_is_a_single_range(self):
        event = app.Event(title="えどがわ朝日創業塾", start_date="2026-10-16", end_date="2026-12-11",
                          venue="タワーホール船堀", address="東京都江戸川区船堀4丁目1番1号",
                          url="https://example.jp/csv", source="CSV")
        kept, removed = app.suppress_csv_events_replaced_by_asahi([event], {"status": "ok"})
        self.assertEqual(kept, [])
        self.assertEqual(removed, 1)

    def test_asahi_course_uses_five_explicit_sessions_not_a_continuous_range(self):
        html = """<html><body><main><h1>講座</h1>
        <p>〖えどがわ朝日創業塾〗</p>
        <p>令和８年１０月１６日、１０月３０日、</p><p>１１月１３日、１１月２７日、</p><p>１２月１１日 隔週金曜日開催</p>
        <p>〖開催時間（各開催共通）〗</p><p>１８：３０から２０：３０</p></main></body></html>"""
        with patch.object(app, "TODAY", date(2026, 11, 27)), \
             patch.object(app, "fetch_text", return_value=(html, {"http_status": 200})), \
             patch.object(app, "make_event", side_effect=lambda title, span, venue, address, url, source, **kw:
                          type("Fake", (), {"title": title, "start_date": span[0].isoformat(), "end_date": span[1].isoformat(), **kw})()):
            events, status = app.parse_asahi_startup_course()
        self.assertEqual([e.start_date for e in events], ["2026-11-27", "2026-12-11"])
        self.assertEqual(status["accepted"], 2)


class StadiumGeminiTests(unittest.TestCase):
    def test_japanese_era_month_title(self):
        self.assertEqual(_month_from_title("2026(令和8)年 11月 大会等予定表", app), (2026, 11))

    def test_missing_api_key_skips_gemini_without_fallback(self):
        with patch.dict("os.environ", {}, clear=True):
            events, status = collect_stadium_events(app)
        self.assertEqual(events, [])
        self.assertEqual(status["status"], "not_configured")
        self.assertEqual(status["tier"], "free_only")

    def test_month_links_are_discovered_from_official_page(self):
        html = """<html><body><h2>2026(令和8)年 11月 大会等予定表</h2>
        <a href='/baseball/taikaiguid/2026%E4%BB%A4%E5%92%8C8%E5%B9%B4%E3%80%8011%E6%9C%88/'>大会等予定表</a>
        </body></html>"""
        pages = _discover_month_pages(html, app)
        self.assertEqual(len(pages), 1)
        self.assertEqual((pages[0]["year"], pages[0]["month"]), ("2026", "11"))


if __name__ == "__main__":
    unittest.main()
