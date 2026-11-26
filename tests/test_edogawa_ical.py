import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import edogawa_ical as app


class DateParsingTests(unittest.TestCase):
    def test_full_width_and_japanese_era(self):
        self.assertEqual(app.parse_date_spans("令和８年１２月１２日"), [(date(2026, 12, 12), date(2026, 12, 12))])
        self.assertEqual(app.parse_date_spans("２０２６年１２月１２日"), [(date(2026, 12, 12), date(2026, 12, 12))])

    def test_leading_event_date_is_not_removed(self):
        self.assertEqual(app.parse_date_spans("2027年4月10日開催"), [(date(2027, 4, 10), date(2027, 4, 10))])

    def test_yearless_next_fiscal_spring_date(self):
        with patch.object(app, "TODAY", date(2026, 11, 26)):
            self.assertEqual(app.parse_date_spans("4月10日開催"), [(date(2027, 4, 10), date(2027, 4, 10))])

    def test_year_crossing_range(self):
        with patch.object(app, "TODAY", date(2026, 11, 26)):
            spans = app.parse_date_spans("2026年12月12日から2027年3月14日")
            self.assertEqual(spans, [(date(2026, 12, 12), date(2027, 3, 14))])

    def test_application_period_is_not_used_by_detail_parser(self):
        html = """<html><body><main><h1>テスト講座</h1>
        <h2>開催日時</h2><p>2026年12月12日（土）14時00分から15時00分</p>
        <h2>申し込み期間</h2><p>2026年11月1日から2026年12月9日</p>
        <h2>場所</h2><p>江戸川区民館</p><h2>住所</h2><p>東京都江戸川区中央1丁目1番1号</p>
        </main></body></html>"""
        with patch.object(app, "TODAY", date(2026, 11, 26)):
            got = app.extract_detail(html, "https://example.jp/test.html")
        self.assertEqual(got["spans"], [(date(2026, 12, 12), date(2026, 12, 12))])
        self.assertEqual(got["start_time"], "14:00")
        self.assertEqual(got["end_time"], "15:00")
        self.assertIn("江戸川区", got["address"])

    def test_rain_delay_date_is_not_added_as_a_second_event_date(self):
        html = """<html><body><main><h1>雨天延期テスト</h1>
        <h2>開催日時</h2>
        <p>2026年12月12日（土曜日）14時00分から15時00分</p>
        <p>（注）雨天の場合は、12月13日（日曜日）14時00分から15時00分に延期します。</p>
        <h2>場所</h2><p>なぎさポニーランド</p>
        </main></body></html>"""
        with patch.object(app, "TODAY", date(2026, 11, 26)):
            got = app.extract_detail(html, "https://example.jp/rain.html")
        self.assertEqual(got["spans"], [(date(2026, 12, 12), date(2026, 12, 12))])
        self.assertEqual((got["start_time"], got["end_time"]), ("14:00", "15:00"))

    def test_time_parser(self):
        self.assertEqual(app.parse_time_range("2026年12月12日 9時30分から14時35分"), ("09:30", "14:35"))
        self.assertEqual(app.parse_time_range("14:00～16:15"), ("14:00", "16:15"))


class LocationTests(unittest.TestCase):
    def test_edogawa_address_accepted(self):
        ok, _, reason = app.verify_edogawa_location("東京都江戸川区中央1丁目", "区民館")
        self.assertTrue(ok)
        self.assertEqual(reason, "address_edogawa")

    def test_katsushika_address_rejected_even_if_title_says_edogawa(self):
        ok, _, reason = app.verify_edogawa_location("東京都葛飾区細田1丁目", "葛飾若草幼稚園")
        self.assertFalse(ok)
        self.assertEqual(reason, "outside_address")

    def test_non_tokyo_prefectures_are_rejected_even_if_edogawa_is_also_mentioned(self):
        for prefecture in ("千葉県", "埼玉県", "愛知県", "大阪府", "沖縄県"):
            ok, _, reason = app.verify_edogawa_location(
                f"東京都江戸川区中央1丁目・{prefecture}内会場", "会場"
            )
            self.assertFalse(ok, prefecture)
            self.assertEqual(reason, "outside_address")

    def test_unknown_address_and_venue_rejected(self):
        ok, _, reason = app.verify_edogawa_location("", "未確認の会場")
        self.assertFalse(ok)
        self.assertEqual(reason, "location_unverified")

    def test_all_other_tokyo_wards_are_rejected(self):
        for ward in app.OUTSIDE_WARD_TERMS[:22]:
            ok, _, _ = app.verify_edogawa_location(f"東京都{ward}1丁目", "会場")
            self.assertFalse(ok, ward)

    def test_unknown_venue_does_not_fall_back_to_source_venue(self):
        ok, _, _ = app.verify_edogawa_location("", "葛飾区内の貸ホール", "なぎさポニーランド")
        self.assertFalse(ok)

    def test_source_default_is_allowed_only_when_location_fields_are_empty(self):
        ok, _, reason = app.verify_edogawa_location("", "", "なぎさポニーランド")
        self.assertTrue(ok)
        self.assertEqual(reason, "source_bound_venue")

    def test_source_bound_subvenue_is_allowed_for_known_in_ward_facility(self):
        ok, _, reason = app.verify_edogawa_location("", "大ホール", "タワーホール船堀")
        self.assertTrue(ok)
        self.assertEqual(reason, "source_bound_subvenue")

    def test_known_in_ward_venue_accepted(self):
        ok, reg, reason = app.verify_edogawa_location("", "なぎさポニーランド")
        self.assertTrue(ok)
        self.assertIsNotNone(reg)

    def test_detail_page_uses_registered_source_default_when_venue_is_omitted(self):
        html = """<html><body><main><h1>施設イベント</h1>
        <h2>開催日時</h2><p>2026年12月12日 10時00分から11時00分</p>
        <p>内容：イベントを開催します。</p></main></body></html>"""
        with patch.object(app, "TODAY", date(2026, 11, 26)):
            detail = app.extract_detail(html, "https://example.jp/event/1", default_venue="タワーホール船堀")
        self.assertEqual(detail["venue"], "タワーホール船堀")
        self.assertIn("江戸川区", detail["address"])
        ok, _, reason = app.verify_edogawa_location(detail["address"], detail["venue"], "タワーホール船堀")
        self.assertTrue(ok)
        self.assertEqual(reason, "address_edogawa")

    def test_explicit_outside_address_overrides_source_default_and_is_rejected(self):
        ok, _, reason = app.verify_edogawa_location("東京都葛飾区細田1丁目", "大ホール", "タワーホール船堀")
        self.assertFalse(ok)
        self.assertEqual(reason, "outside_address")


class MergeAndIcalTests(unittest.TestCase):
    def _event(self, url):
        return app.make_event("同じイベント", (date(2026, 12, 12), date(2026, 12, 12)),
                              "なぎさポニーランド", "東京都江戸川区南葛西7丁目3番", url,
                              "test", start_time="10:00", end_time="11:00")

    def test_deduplicate_across_different_urls(self):
        a, b = self._event("https://a.example/event/1"), self._event("https://b.example/event/2")
        merged = app.merge_events([a, b])
        self.assertEqual(len(merged), 1)
        self.assertEqual(len(merged[0].source_urls), 2)

    def test_ics_date_time_and_image(self):
        ev = self._event("https://a.example/event/1")
        ev.image_url = "https://example.jp/event.jpg"
        ics = app.build_ics([ev])
        self.assertIn("DTSTART:20261212T010000Z", ics)
        self.assertIn("DTEND:20261212T020000Z", ics)
        self.assertIn("GEO:", ics)
        self.assertIn("IMAGE;VALUE=URI;DISPLAY=BADGE:", ics)
        self.assertEqual(ics.count("BEGIN:VEVENT"), ics.count("END:VEVENT"))

    def test_rfc_line_fold_is_at_most_75_octets(self):
        folded = app.fold_ics("SUMMARY:" + "江戸川区" * 40)
        for line in folded.split("\r\n"):
            self.assertLessEqual(len(line.encode("utf-8")), 75)

    def test_csv_unknown_schema_is_reported_without_assuming_fields(self):
        with patch.object(app, "fetch_text", return_value=("foo,bar\n1,2\n", {"url": "x", "http_status": 200, "bytes": 8, "error": None})):
            events, status = app.parse_csv_source("https://example.jp/data.csv", "test")
        self.assertEqual(events, [])
        self.assertEqual(status["status"], "schema_unrecognized")
        self.assertEqual(status["headers"], ["foo", "bar"])

    def test_official_venue_csv_adds_coordinates_and_rejects_outside(self):
        import tempfile
        from pathlib import Path
        csv_text = "施設名,住所,緯度,経度\n江戸川区民ホール,中央1丁目,35.70,139.86\n葛飾区民ホール,東京都葛飾区細田,35.75,139.87\n"
        meta = {"url": "https://example.jp/venues.csv", "http_status": 200, "bytes": len(csv_text), "error": None}
        with tempfile.TemporaryDirectory() as td:
            venue_file = Path(td) / "venues.json"
            with patch.object(app, "VENUES", {}), patch.object(app, "VENUES_FILE", venue_file), \
                 patch.object(app, "fetch_text", return_value=(csv_text, meta)):
                status = app.parse_venue_catalog("https://example.jp/venues.csv", "venues")
                self.assertEqual(status["accepted"], 1)
                self.assertEqual(status["rejected"].get("location_not_verified"), 1)
                self.assertEqual(app.VENUES["江戸川区民ホール"]["latitude"], 35.70)
                self.assertIn("江戸川区", app.VENUES["江戸川区民ホール"]["address"])

    def test_csv_standard_schema_keeps_start_end_range(self):
        csv_text = ("市区町村コード,市区町村名,イベント名,開始日,終了日,開始時間,終了時間,場所名称,住所,URL,緯度,経度\n"
                    "131237,江戸川区,地域フェスタ,2026-12-12,2026-12-13,09:30,15:00,区民館,東京都江戸川区中央1丁目,https://example.jp/e,35.70,139.86\n")
        meta = {"url": "https://example.jp/data.csv", "http_status": 200, "bytes": len(csv_text), "error": None}
        with patch.object(app, "TODAY", date(2026, 11, 26)), patch.object(app, "fetch_text", return_value=(csv_text, meta)):
            events, status = app.parse_csv_source("https://example.jp/data.csv", "test")
        self.assertEqual(status["status"], "ok")
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0].start_date, events[0].end_date), ("2026-12-12", "2026-12-13"))
        self.assertEqual((events[0].start_time, events[0].end_time), ("09:30", "15:00"))

    def test_csv_outside_municipality_is_rejected(self):
        csv_text = ("市区町村コード,市区町村名,イベント名,開始日,終了日,場所名称,住所\n"
                    "131222,葛飾区,区民イベント,2026-12-12,2026-12-12,区民館,東京都葛飾区細田1丁目\n")
        meta = {"url": "https://example.jp/data.csv", "http_status": 200, "bytes": len(csv_text), "error": None}
        with patch.object(app, "TODAY", date(2026, 11, 26)), patch.object(app, "fetch_text", return_value=(csv_text, meta)):
            events, status = app.parse_csv_source("https://example.jp/data.csv", "test")
        self.assertEqual(events, [])
        self.assertEqual(status["rejected"].get("municipality_not_edogawa"), 1)

    def test_ics_uid_is_stable_when_title_changes(self):
        a = self._event("https://a.example/event/1")
        b = self._event("https://a.example/event/1")
        b.title = "改題されたイベント"
        self.assertEqual(app.make_uid(a), app.make_uid(b))

    def test_ics_end_at_midnight_is_next_day(self):
        ev = self._event("https://a.example/event/midnight")
        ev.start_time, ev.end_time = "22:00", "24:00"
        ics = app.build_ics([ev])
        self.assertIn("DTSTART:20261212T130000Z", ics)
        self.assertIn("DTEND:20261212T150000Z", ics)


class FeedAndSourceHealthTests(unittest.TestCase):
    def test_discovers_only_same_host_rss_atom_feeds(self):
        html = """<html><head>
        <link rel="alternate" type="application/rss+xml" href="/feed.xml">
        <link rel="alternate" type="application/atom+xml" href="https://elsewhere.example/feed">
        </head><body><a href="/rss/">RSS</a></body></html>"""
        soup = app.BeautifulSoup(html, "html.parser")
        self.assertEqual(app.discover_feed_urls(soup, "https://example.jp/events/"),
                         ["https://example.jp/feed.xml", "https://example.jp/rss/"])

    def test_parses_rss_and_atom_entry_links_without_using_published_dates(self):
        rss = """<rss version="2.0"><channel><item><title>江戸川区民まつり</title>
        <link>https://example.jp/event/1</link><pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate>
        <description>開催日は詳細ページを参照</description></item></channel></rss>"""
        entries = app.parse_feed_entries(rss, "https://example.jp/")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["url"], "https://example.jp/event/1")
        self.assertEqual(entries[0]["time_text"], "")
        atom = """<feed xmlns="http://www.w3.org/2005/Atom"><entry>
        <title>地域講座</title><link href="https://example.jp/event/2"/>
        <summary>会場詳細</summary></entry></feed>"""
        self.assertEqual(app.parse_feed_entries(atom, "https://example.jp/")[0]["url"],
                         "https://example.jp/event/2")

    def test_city_calendar_reports_fetch_failure_instead_of_generic_zero(self):
        with patch.object(app, "fetch_text", return_value=(None, {"url": "x", "http_status": None,
                                                                   "bytes": 0, "error": "timeout"})):
            events, status = app.parse_city_calendar(months=1)
        self.assertEqual(events, [])
        self.assertEqual(status["status"], "calendar_fetch_failed")

    def test_city_calendar_distinguishes_no_candidates_from_fetch_failure(self):
        html = "<html><body><p>No events</p></body></html>"
        with patch.object(app, "fetch_text", return_value=(html, {"url": "x", "http_status": 200,
                                                                   "bytes": len(html), "error": None})):
            events, status = app.parse_city_calendar(months=1)
        self.assertEqual(events, [])
        self.assertEqual(status["status"], "no_event_candidates")

    def test_city_calendar_follows_daily_more_link_and_uses_type3_dates(self):
        from urllib.parse import parse_qs, urlparse
        day_html = """<html><body><a href="/event/test-event.html">追加イベント 2026年12月1日（火曜日） 9時30分 ～ 10時30分</a></body></html>"""
        listing_html = """<html><body><a href="/cgi-bin/event_cal_multi/calendar.cgi?day=1&month=12&year=2026&type=3">もっと見る</a></body></html>"""
        detail_html = """<html><body><main><h1>追加イベント</h1><h2>場所</h2><p>なぎさポニーランド</p></main></body></html>"""
        def fake_fetch(url, params=None, timeout=15):
            meta = {"url": url, "http_status": 200, "bytes": 100, "error": None}
            if params and params.get("type") == "1":
                return "<html><body></body></html>", meta
            if params and params.get("type") == "2":
                return listing_html, meta
            if "day=1" in url and "type=3" in url:
                return day_html, meta
            if url.endswith("/event/test-event.html"):
                return detail_html, meta
            return None, {"url": url, "http_status": 404, "bytes": 0, "error": "not found"}
        with patch.object(app, "TODAY", date(2026, 11, 26)), patch.object(app, "fetch_text", side_effect=fake_fetch):
            events, status = app.parse_city_calendar(months=1)
        self.assertEqual(status["day_detail_pages_requested"], 1)
        self.assertEqual(status["day_detail_pages_success"], 1)
        self.assertEqual(status["day_detail_event_links"], 1)
        self.assertEqual(status["site_coverage"]["6"]["months_successful"], 1)
        self.assertEqual(status["site_coverage"]["7"]["months_successful"], 1)
        self.assertEqual(status["site_coverage"]["8"]["months_successful"], 1)
        self.assertEqual(status["calendar_months_successful"], 1)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].start_date, "2026-12-01")
        self.assertEqual(events[0].start_time, "09:30")
        self.assertEqual(events[0].end_time, "10:30")

    def test_map_coordinates_outside_edogawa_bbox_are_discarded(self):
        ev = app.make_event("座標テスト", (date(2026, 12, 12), date(2026, 12, 12)),
                            "会場名", "東京都江戸川区中央1丁目", "https://example.jp/1", "test",
                            lat=35.68, lon=139.55)
        self.assertIsNotNone(ev)
        self.assertIsNone(ev.latitude)
        self.assertIsNone(ev.longitude)


class QualityFilterTests(unittest.TestCase):
    def _event(self, title, start, end=None, venue="なぎさポニーランド", url="https://example.jp/event/1"):
        end = end or start
        return app.make_event(title, (date.fromisoformat(start), date.fromisoformat(end)),
                              venue, "東京都江戸川区南葛西7丁目3番", url, "test")

    def test_article_publication_date_is_not_used_as_event_date(self):
        html = """<html><body><main><h1>イベント開催のお知らせ</h1>
        <p>公開日：2026年11月26日</p><p>更新日：2026年11月27日</p>
        <p>12月10日に開催されたイベントを紹介します。</p>
        <h2>会場</h2><p>なぎさポニーランド</p></main></body></html>"""
        with patch.object(app, "TODAY", date(2026, 11, 26)):
            detail = app.extract_detail(html, "https://example.jp/news/1")
        self.assertEqual(detail["spans"], [])

    def test_news_sentence_with_future_event_cue_is_used(self):
        html = """<html><body><main><h1>冬のイベントのお知らせ</h1>
        <p>公開日：2026年11月26日</p><p>12月10日に親子体験イベントを開催します。</p>
        <h2>会場</h2><p>なぎさポニーランド</p></main></body></html>"""
        with patch.object(app, "TODAY", date(2026, 11, 26)):
            detail = app.extract_detail(html, "https://example.jp/news/1")
        self.assertEqual(detail["spans"], [(date(2026, 12, 10), date(2026, 12, 10))])

    def test_explicit_future_event_date_is_used_not_publication_date(self):
        html = """<html><body><main><h1>冬の体験会</h1>
        <p>公開日：2026年11月26日</p>
        <h2>開催日時</h2><p>2026年12月10日 10時00分から11時00分</p>
        <h2>会場</h2><p>なぎさポニーランド</p></main></body></html>"""
        with patch.object(app, "TODAY", date(2026, 11, 26)):
            detail = app.extract_detail(html, "https://example.jp/event/1")
        self.assertEqual(detail["spans"], [(date(2026, 12, 10), date(2026, 12, 10))])

    def test_past_event_is_rejected(self):
        with patch.object(app, "TODAY", date(2026, 11, 26)):
            self.assertIsNone(self._event("過去の講座", "2026-11-25"))

    def test_ongoing_event_started_yesterday_is_kept(self):
        with patch.object(app, "TODAY", date(2026, 11, 26)):
            self.assertIsNotNone(self._event("開催中の展示", "2026-11-25", "2026-11-30"))

    def test_abnormally_long_event_is_rejected(self):
        with patch.object(app, "TODAY", date(2026, 11, 26)):
            self.assertIsNone(self._event("長期誤登録", "2021-10-17", "2027-10-25"))

    def test_non_event_information_pages_are_rejected(self):
        for title in ("くらし・手続き", "よくある問い合わせQ&A", "サイトマップ",
                      "スポーツ・文化", "健康・医療・福祉", "組織図と組織一覧"):
            with self.subTest(title=title):
                self.assertTrue(app.is_non_event_page(title))
                self.assertIsNone(self._event(title, "2026-12-10"))

    def test_similar_duplicate_titles_merge_and_keep_both_urls(self):
        with patch.object(app, "TODAY", date(2026, 11, 26)):
            a = self._event("冬の親子ポニー飼育体験", "2026-12-10", url="https://a.example/event/1")
            b = self._event("冬の親子ポニー飼育体験 開催のお知らせ", "2026-12-10", url="https://b.example/event/2")
        merged = app.merge_events([a, b])
        self.assertEqual(len(merged), 1)
        self.assertEqual(set(merged[0].source_urls), {"https://a.example/event/1", "https://b.example/event/2"})

    def test_different_events_at_same_venue_and_date_are_not_merged(self):
        with patch.object(app, "TODAY", date(2026, 11, 26)):
            a = self._event("親子ポニー飼育体験", "2026-12-10", url="https://a.example/event/1")
            b = self._event("クリスマス工作教室", "2026-12-10", url="https://b.example/event/2")
        self.assertEqual(len(app.merge_events([a, b])), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
