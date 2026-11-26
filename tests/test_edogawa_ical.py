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

    def test_month_day_shorthand_range_is_parsed(self):
        with patch.object(app, "TODAY", date(2026, 11, 26)):
            self.assertEqual(app.parse_date_spans("2026年12月1日（火）～5日（土）"),
                             [(date(2026, 12, 1), date(2026, 12, 5))])
            self.assertEqual(app.parse_date_spans("12月1日から5日まで"),
                             [(date(2026, 12, 1), date(2026, 12, 5))])

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
        self.assertEqual(len(events), 2)
        self.assertEqual([(e.start_date, e.end_date) for e in events],
                         [("2026-12-12", "2026-12-12"), ("2026-12-13", "2026-12-13")])
        self.assertTrue(all((e.start_time, e.end_time) == ("09:30", "15:00") for e in events))
        self.assertTrue(all(e.priority == 9 for e in events))

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
        self.assertIsNone(ev)


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

    def test_floor_guide_is_not_an_event_page(self):
        self.assertTrue(app.is_non_event_page("フロア案内"))
        html = """<html><body><main><h1>フロア案内</h1>
        <p>1階・2階・屋上の施設を紹介します。</p></main></body></html>"""
        detail = app.extract_detail(html, "https://www.city.edogawa.tokyo.jp/example/floor.html")
        self.assertTrue(detail["is_non_event_page"])
        self.assertEqual(detail["spans"], [])

    def test_other_information_pages_are_not_events(self):
        for title in ("外国の方向け生活情報", "区内の主なイベント", "子育て・教育",
                      "まちづくり・環境", "施設情報"):
            with self.subTest(title=title):
                self.assertTrue(app.is_non_event_page(title))

    def test_ueno_zoo_is_rejected_as_actual_venue_even_with_kasai_default(self):
        ok, _, reason = app.verify_edogawa_location(
            "", "上野動物園内 管理事務所3階", "葛西臨海水族園"
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "outside_facility_venue")

    def test_outside_facility_mention_in_detail_body_rejects_event(self):
        html = """<html><body><main><h1>野生動物保全の報告講演会</h1>
        <h2>開催日時</h2><p>2026年12月12日 10時から11時</p>
        <h2>会場</h2><p>上野動物園内 管理事務所3階</p>
        </main></body></html>"""
        with patch.object(app, "TODAY", date(2026, 11, 26)):
            detail = app.extract_detail(html, "https://www.tokyo-zoo.net/news/event.html",
                                        default_venue="葛西臨海水族園")
        self.assertEqual(detail["venue"], "上野動物園内 管理事務所3階")
        self.assertEqual(detail["outside_facility_mention"], "上野動物園")
        self.assertIsNone(app.make_event(
            detail["title"], detail["spans"][0], detail["venue"], detail["address"],
            "https://www.tokyo-zoo.net/news/event.html", "葛西臨海水族園",
            default_venue="葛西臨海水族園", description=detail["text"]
        ))

    def test_outside_facility_body_mention_is_rejected_even_if_default_venue_is_in_ward(self):
        with patch.object(app, "TODAY", date(2026, 11, 26)):
            self.assertIsNone(app.make_event(
                "野生動物保全講演会", (date(2026, 12, 12), date(2026, 12, 12)),
                "葛西臨海水族園", "東京都江戸川区臨海町6丁目", "https://example.jp/e", "test",
                default_venue="葛西臨海水族園", description="会場は恩賜上野動物園内です。"
            ))

    def test_listing_publication_time_is_not_used_as_event_date(self):
        listing = """<html><body><article><a href="/news/1">冬のイベント開催のお知らせ</a>
        <time datetime="2026-12-10">2026年12月10日</time></article></body></html>"""
        detail = """<html><body><main><h1>冬のイベント開催のお知らせ</h1>
        <p>公開日：2026年12月10日</p><p>イベントの様子を紹介します。</p>
        <h2>会場</h2><p>タワーホール船堀</p></main></body></html>"""
        def fake_fetch(url, params=None, timeout=15):
            body = listing if url.endswith("/events/") else detail
            return body, {"url": url, "http_status": 200, "bytes": len(body), "error": None}
        with patch.object(app, "TODAY", date(2026, 11, 26)), patch.object(app, "fetch_text", side_effect=fake_fetch):
            events, status = app.parse_listing_source({
                "name": "test listing", "url": "https://example.jp/events/",
                "default_venue": "タワーホール船堀", "max_details": 10
            })
        self.assertEqual(events, [])
        self.assertEqual(status["rejected"].get("no_event_date"), 1)

    def test_ics_and_status_use_v325_version(self):
        ev = self._event("バージョン確認イベント", "2026-12-10")
        ics = app.build_ics([ev])
        self.assertIn("PRODID:-//Edogawa Ward Events//JP v3.2.6//EN", ics)
        self.assertEqual(app.APP_VERSION, "3.2.6")

    def test_output_validation_rejects_event_without_coordinates(self):
        ev = self._event("座標なし検証", "2026-12-10")
        ev.latitude = None
        ev.longitude = None
        with self.assertRaisesRegex(RuntimeError, "Missing geocoded coordinates"):
            app.validate_outputs(app.build_ics([ev]), [ev])

    def test_library_cms_uses_individual_event_heading_not_generic_h1(self):
        html = """<html><body><main><h1>イベント情報</h1><h2>東部図書館</h2>
        <h3>Bettyさんスペシャル「ぬいぐるみといっしょにおはなし会」</h3>
        <table><tr><th>日時</th><td>2026年11月28日 14時00分～15時00分</td></tr>
        <tr><th>会場</th><td>東部図書館 2階 視聴覚室</td></tr></table></main></body></html>"""
        d = app.extract_detail(html, "https://www.library.city.edogawa.tokyo.jp/toshow/event/event_10295.php")
        self.assertEqual(d["title"], "Bettyさんスペシャル「ぬいぐるみといっしょにおはなし会」")
        self.assertEqual(d["spans"][0][0], date(2026, 11, 28))
        self.assertIn("東部図書館", d["venue"])


    def test_library_ssl_failure_uses_tls12_fallback(self):
        app._FETCH_CACHE.clear()
        with patch.object(app.SESSION, "get", side_effect=app.requests.exceptions.SSLError("handshake")), \
             patch.object(app, "fetch_library_tls12", return_value=("<html>ok</html>", 14, 200)) as fallback:
            body, meta = app.fetch_text("https://www.library.city.edogawa.tokyo.jp/toshow/event/", timeout=10)
        self.assertEqual(body, "<html>ok</html>")
        self.assertEqual(meta["tls_fallback"], "TLS1.2")
        fallback.assert_called_once()
        app._FETCH_CACHE.clear()


    def test_culture_center_listing_context_provides_event_date_and_venue(self):
        listing = """<html><body><main><h1>主催共催公演</h1><div class='event-card'>
        <a href='/event/sample/'>江戸川区総合文化センター 休館中特別公演 第167回江戸川落語会</a>
        <p>2026年12月2日（水）18:30開演</p><p>会場 小松川さくらホール（東大島駅より徒歩約10分）</p>
        </div></main></body></html>"""
        detail = "<html><body><main><h1>主催共催公演</h1><p>詳細情報</p></main></body></html>"
        def fake_fetch(url, params=None, timeout=15):
            body = listing if url.endswith("/calendar/") else detail
            return body, {"url": url, "http_status": 200, "bytes": len(body), "error": None}
        with patch.object(app, "TODAY", date(2026, 11, 26)), patch.object(app, "fetch_text", side_effect=fake_fetch):
            events, status = app.parse_listing_source({
                "name": "culture center", "url": "https://example.jp/calendar/", "default_venue": None,
                "category": "文化・公演", "priority": 2, "max_details": 10,
                "date_from_context": True, "venue_from_context": True,
                "context_ancestors": True, "prefer_listing_title": True
            })
        self.assertEqual(len(events), 1)
        self.assertIn("第167回江戸川落語会", events[0].title)
        self.assertEqual(events[0].start_date, "2026-12-02")
        self.assertEqual(events[0].venue, "小松川さくらホール")


    def test_map_json_uses_same_generated_at_as_source_status(self):
        ev = app.make_event("生成時刻確認", (date(2026, 12, 10), date(2026, 12, 10)),
                            "なぎさポニーランド", "東京都江戸川区南葛西7丁目3番", "https://example.jp/e", "test")
        generated_at = "2026-11-26T17:56:46.705209+09:00"
        payload = app.to_map_json([ev], {"generated_at": generated_at, "sources": {}}, mode="live")
        self.assertEqual(payload["generated_at"], generated_at)



class V322RegressionTests(unittest.TestCase):
    def test_real_city_csv_headers_map_to_actual_address_url_image_fields(self):
        headers = ["都道府県コード", "都道府県", "市区町村", "イベント名", "開催日", "終了日",
                   "場所名称（おでかけスポット）", "町名番地", "イベント画像のURL", "ホームページURL", "PR文"]
        mapped = app._map_csv_columns(headers)
        self.assertEqual(mapped["city"], "市区町村")
        self.assertEqual(mapped["address"], "町名番地")
        self.assertEqual(mapped["venue"], "場所名称（おでかけスポット）")
        self.assertEqual(mapped["url"], "ホームページURL")
        self.assertEqual(mapped["image"], "イベント画像のURL")
        self.assertEqual(mapped["description"], "PR文")

    def test_real_city_csv_row_is_accepted_with_actual_headers(self):
        csv_text = ("都道府県コード,都道府県,市区町村,イベント名,開催日,終了日,開始時間,終了時間,"
                    "場所名称（おでかけスポット）,町名番地,緯度,経度,イベント画像のURL,ホームページURL,PR文\n"
                    "13,東京都,江戸川区,テスト講座,2026-12-12,2026-12-12,10:00,11:00,"
                    "テスト会場,中央1丁目,35.70,139.86,https://example.jp/img.jpg,https://example.jp/event,区内講座\n")
        meta = {"url": "https://example.jp/city.csv", "http_status": 200, "bytes": len(csv_text), "error": None}
        with patch.object(app, "TODAY", date(2026, 11, 26)), patch.object(app, "fetch_text", return_value=(csv_text, meta)):
            events, status = app.parse_csv_source("https://example.jp/city.csv", "city")
        self.assertEqual(status["status"], "ok")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].address, "東京都江戸川区中央1丁目")
        self.assertEqual(events[0].url, "https://example.jp/event")
        self.assertEqual(events[0].image_url, "https://example.jp/img.jpg")

    def test_real_tokyo_opendata_headers_do_not_map_city_code_as_address(self):
        headers = ["全国地方公共団体コード", "地方公共団体名", "イベント名", "開始日", "終了日",
                   "コンテンツURL", "場所名称", "所在地_全国地方公共団体コード",
                   "所在地_連結表記", "所在地_都道府県", "所在地_市区町村", "緯度", "経度", "説明"]
        mapped = app._map_csv_columns(headers)
        self.assertEqual(mapped["address"], "所在地_連結表記")
        self.assertEqual(mapped["start"], "開始日")
        self.assertEqual(mapped["url"], "コンテンツURL")
        self.assertEqual(mapped["city"], "所在地_市区町村")

    def test_weekly_course_is_expanded_to_individual_days(self):
        with patch.object(app, "TODAY", date(2026, 11, 26)):
            spans = app.expand_recurring_spans("毎週木曜日（全4回）", [(date(2026, 11, 26), date(2026, 12, 31))])
        self.assertEqual([x[0] for x in spans], [date(2026, 11, 26), date(2026, 12, 3), date(2026, 12, 10), date(2026, 12, 17)])
        self.assertTrue(all(a == b for a, b in spans))

    def test_monthly_nth_weekday_is_expanded(self):
        with patch.object(app, "TODAY", date(2026, 11, 1)):
            spans = app.expand_recurring_spans("毎月第3日曜日（全3回）", [(date(2026, 11, 1), date(2027, 1, 31))])
        self.assertEqual([x[0] for x in spans], [date(2026, 11, 15), date(2026, 12, 20), date(2027, 1, 17)])

    def test_multi_day_span_over_duration_limit_is_not_expanded(self):
        with patch.object(app, "TODAY", date(2026, 11, 26)):
            got = app.expand_recurring_spans("長期開催", [(date(2026, 1, 1), date(2027, 2, 1))])
        self.assertEqual(got, [])

    def test_multi_day_csv_keeps_low_priority_when_only_last_day_is_future(self):
        csv_text = ("市区町村コード,市区町村名,イベント名,開始日,終了日,場所名称,住所,URL,緯度,経度\n"
                    "131237,江戸川区,複数日イベント,2026-12-12,2026-12-13,区民館,東京都江戸川区中央1丁目,https://example.jp/e,35.70,139.86\n")
        meta = {"url": "https://example.jp/data.csv", "http_status": 200, "bytes": len(csv_text), "error": None}
        with patch.object(app, "TODAY", date(2026, 12, 13)), patch.object(app, "fetch_text", return_value=(csv_text, meta)):
            events, _ = app.parse_csv_source("https://example.jp/data.csv", "test")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].start_date, "2026-12-13")
        self.assertEqual(events[0].priority, 9)

    def test_multi_day_event_is_split_into_daily_records(self):
        with patch.object(app, "TODAY", date(2026, 11, 26)):
            got = app.expand_recurring_spans("2026年12月1日から12月5日まで開催",
                                             [(date(2026, 12, 1), date(2026, 12, 5))])
        self.assertEqual([x[0] for x in got], [date(2026, 12, d) for d in range(1, 6)])
        self.assertTrue(all(a == b for a, b in got))

    def test_weekly_total_count_counts_past_sessions_before_filtering(self):
        with patch.object(app, "TODAY", date(2026, 11, 26)):
            got = app.expand_recurring_spans("毎週木曜日（全36回）",
                    [(date(2026, 4, 9), date(2027, 3, 11))])
        self.assertEqual(len(got), 3)
        self.assertEqual([x[0] for x in got], [date(2026, 11, 26), date(2026, 12, 3), date(2026, 12, 10)])

    def test_kazenoko_official_detail_fixture_expands_each_future_thursday(self):
        html = """<html><body><main><h1>令和8年度ぜんそく児のための風の子水泳教室（スポーツセンター）</h1>
        <h2>開催日時</h2><ul><li>2026年4月9日（木曜日）から2027年3月11日（木曜日）</li></ul>
        <p>毎週木曜日(全36回)16時15分から18時</p><p>ただし、夏季・冬季・春季の休暇期間は除く</p>
        <h2>場所</h2><p>スポーツセンター</p><h2>住所</h2><p>江戸川区西葛西4丁目2番20号</p>
        </main></body></html>"""
        with patch.object(app, "TODAY", date(2026, 11, 26)):
            detail = app.extract_detail(html, "https://www.city.edogawa.tokyo.jp/event/2026kazenoko.html")
            got = app.expand_recurring_spans(detail["text"], detail["spans"])
        self.assertEqual(detail["spans"], [(date(2026, 4, 9), date(2027, 3, 11))])
        self.assertEqual((detail["start_time"], detail["end_time"]), ("16:15", "18:00"))
        self.assertEqual(detail["venue"], "スポーツセンター")
        self.assertEqual(got[0][0], date(2026, 11, 26))
        self.assertEqual(got[-1][0], date(2027, 3, 11))
        self.assertTrue(all(d.weekday() == 3 and a == d for d, a in got))

    def test_vacation_clause_without_exact_dates_does_not_truncate_weekly_series(self):
        with patch.object(app, "TODAY", date(2026, 11, 26)):
            got = app.expand_recurring_spans(
                "毎週木曜日（全36回）16時15分から18時。ただし、夏季・冬季・春季の休暇期間は除く",
                [(date(2026, 4, 9), date(2027, 3, 11))])
        self.assertEqual(got[0][0], date(2026, 11, 26))
        self.assertEqual(got[-1][0], date(2027, 3, 11))
        self.assertGreater(len(got), 3)

    def test_explicit_weekday_closure_is_removed(self):
        with patch.object(app, "TODAY", date(2026, 11, 26)):
            got = app.expand_recurring_spans("毎日開催。月曜日は休館日です。",
                    [(date(2026, 11, 26), date(2026, 12, 2))])
        self.assertNotIn((date(2026, 11, 30), date(2026, 11, 30)), got)
        self.assertEqual(len(got), 6)

    def test_explicit_closed_date_range_is_removed(self):
        with patch.object(app, "TODAY", date(2026, 11, 26)):
            got = app.expand_recurring_spans("開催期間 2026年12月1日から12月5日。休館日：2026年12月3日",
                    [(date(2026, 12, 1), date(2026, 12, 5))])
        self.assertEqual([x[0] for x in got], [date(2026, 12, 1), date(2026, 12, 2), date(2026, 12, 4), date(2026, 12, 5)])

    def test_closure_date_does_not_remove_entire_event_range_on_same_line(self):
        with patch.object(app, "TODAY", date(2026, 11, 26)):
            got = app.expand_recurring_spans(
                "開催期間 2026年12月1日から5日、休館日：12月3日",
                [(date(2026, 12, 1), date(2026, 12, 5))])
        self.assertEqual([x[0] for x in got],
                         [date(2026, 12, 1), date(2026, 12, 2), date(2026, 12, 4), date(2026, 12, 5)])

    def test_event_title_containing_休館中_is_not_treated_as_closed_day(self):
        with patch.object(app, "TODAY", date(2026, 11, 26)):
            got = app.expand_recurring_spans("休館中特別公演 第167回江戸川落語会 2026年12月2日開催",
                    [(date(2026, 12, 2), date(2026, 12, 2))])
        self.assertEqual(got, [(date(2026, 12, 2), date(2026, 12, 2))])

    def test_approximate_dates_are_not_converted_to_fake_calendar_dates(self):
        with patch.object(app, "TODAY", date(2026, 11, 26)):
            for text in ("11月上旬開催", "12月下旬予定", "来年夏開催予定"):
                self.assertEqual(app.parse_date_spans(text), [], text)

    def test_contact_page_title_is_rejected(self):
        self.assertTrue(app.is_non_event_page("このページに関するお問い合わせ"))

    def test_ics_and_map_share_generation_identity_v325(self):
        ev = app.make_event("同一生成ID", (date(2026, 12, 10), date(2026, 12, 10)),
                            "なぎさポニーランド", "東京都江戸川区南葛西7丁目3番",
                            "https://example.jp/e", "test")
        stamp = "2026-11-26T20:00:00+09:00"
        execution_id = "abc123456789abcd"
        ics = app.build_ics([ev], stamp, execution_id)
        payload = app.to_map_json([ev], {"generated_at": stamp, "execution_id": execution_id, "sources": {}})
        self.assertIn("X-EDOGAWA-EXECUTION-ID:" + execution_id, ics)
        self.assertIn("X-EDOGAWA-GENERATED-AT:" + stamp, ics)
        self.assertEqual(payload["execution_id"], execution_id)
        self.assertEqual(payload["generated_at"], stamp)
        self.assertEqual(payload["app_version"], "3.2.6")

    def test_geocoder_accepts_only_gsi_result_identified_as_edogawa(self):
        class FakeResponse:
            def raise_for_status(self): pass
            def json(self):
                return [{"properties": {"title": "東京都江戸川区中央一丁目"},
                         "geometry": {"coordinates": [139.86, 35.70]}}]
        with patch.object(app, "_GEOCODE_CACHE", {}), patch.object(app, "_GEOCODE_CACHE_LOADED", True), \
             patch.object(app.SESSION, "get", return_value=FakeResponse()):
            lat, lon, provider = app.geocode_edogawa_location("東京都江戸川区中央1丁目", "")
        self.assertEqual((lat, lon, provider), (35.70, 139.86, "GSI"))

    def test_geocoder_strips_homepage_suffix_before_query(self):
        class FakeResponse:
            def raise_for_status(self): pass
            def json(self):
                return [{"properties": {"title": "東京都江戸川区中央三丁目"},
                         "geometry": {"coordinates": [139.86, 35.70]}}]
        with patch.object(app, "_GEOCODE_CACHE", {}), patch.object(app, "_GEOCODE_CACHE_LOADED", True), \
             patch.object(app.SESSION, "get", return_value=FakeResponse()) as mocked:
            app.geocode_edogawa_location("江戸川区中央三丁目4番18号 ホームページ はあとポート", "はあとポート")
        self.assertIn("中央三丁目4番18号", mocked.call_args.kwargs["params"]["q"])
        self.assertNotIn("ホームページ", mocked.call_args.kwargs["params"]["q"])

    def test_geocoder_rejects_non_edogawa_result(self):
        class FakeResponse:
            def raise_for_status(self): pass
            def json(self):
                return [{"properties": {"title": "東京都葛飾区小菅一丁目"},
                         "geometry": {"coordinates": [139.82, 35.75]}}]
        with patch.object(app, "_GEOCODE_CACHE", {}), patch.object(app, "_GEOCODE_CACHE_LOADED", True), \
             patch.object(app.SESSION, "get", return_value=FakeResponse()):
            lat, lon, provider = app.geocode_edogawa_location("東京都江戸川区の未確定住所", "")
        self.assertEqual((lat, lon, provider), (None, None, None))

    def test_make_event_never_emits_without_coordinates(self):
        with patch.object(app, "geocode_edogawa_location", return_value=(None, None, None)):
            event = app.make_event("座標不明イベント", (date(2026, 12, 1), date(2026, 12, 1)),
                                   "未登録会場", "東京都江戸川区未確認", "https://example.jp/e", "test")
        self.assertIsNone(event)


class V323RegressionTests(unittest.TestCase):
    def _event(self, *, source, category="地域イベント", url="https://www.city.edogawa.tokyo.jp/event/a.html",
               day="2026-11-28", start=None, end=None, venue="会場A", image=None,
               title="野菜の収穫体験", description="詳細"):
        return app.Event(title=title, start_date=day, end_date=day, venue=venue,
                         address="東京都江戸川区中央一丁目1番1号", url=url, source=source, category=category,
                         source_urls=[url], start_time=start, end_time=end, image_url=image,
                         latitude=35.70, longitude=139.86, coordinate_quality="test",
                         priority=1, description=description)

    def test_url_normalization_removes_tracking_and_trailing_slash(self):
        a = "https://www.city.edogawa.tokyo.jp/event/a.html/?utm_source=mail&fbclid=abc"
        b = "https://city.edogawa.tokyo.jp/event/a.html"
        self.assertEqual(app.normalize_event_url(a), app.normalize_event_url(b))

    def test_official_html_and_open_data_merge_good_fields(self):
        html = self._event(source="江戸川区公式イベントカレンダー", category="区公式",
                           start="10:00", end="12:00", venue="篠崎コミュニティ会館",
                           image="https://www.city.edogawa.tokyo.jp/images/logo.png", description="HTML詳細")
        csv = self._event(source="江戸川区イベントカレンダーCSV", category="区公式オープンデータ",
                          url="https://city.edogawa.tokyo.jp/event/a.html?utm_medium=csv",
                          venue="", image="https://city.edogawa.tokyo.jp/images/flyer-123.jpg", description="CSV本文")
        merged = app.merge_events([csv, html])
        self.assertEqual(len(merged), 1)
        ev = merged[0]
        self.assertEqual((ev.start_time, ev.end_time), ("10:00", "12:00"))
        self.assertEqual(ev.venue, "篠崎コミュニティ会館")
        self.assertEqual(ev.description, "HTML詳細")
        self.assertEqual(ev.image_url, "https://city.edogawa.tokyo.jp/images/flyer-123.jpg")
        self.assertEqual(ev.category, "区公式")

    def test_same_url_different_dates_are_not_merged(self):
        a = self._event(source="江戸川区公式イベントカレンダー", category="区公式", day="2026-11-28")
        b = self._event(source="江戸川区イベントカレンダーCSV", category="区公式オープンデータ", day="2026-11-29")
        self.assertEqual(len(app.merge_events([a, b])), 2)

    def test_same_day_distinct_times_are_not_merged(self):
        a = self._event(source="江戸川区公式イベントカレンダー", category="区公式", start="10:00", end="11:00")
        b = self._event(source="江戸川区イベントカレンダーCSV", category="区公式オープンデータ", start="14:00", end="15:00")
        self.assertEqual(len(app.merge_events([a, b])), 2)

    def test_application_closed_events_are_rejected(self):
        self.assertTrue(app.is_non_event_page("【申込は終了しました】活き活きけんこう塾"))
        self.assertTrue(app.is_non_event_page("募集終了 ピックルボール"))
        self.assertTrue(app.is_non_event_page("受付終了 省エネ講座"))

    def test_annual_calendar_index_is_rejected(self):
        self.assertTrue(app.is_non_event_page("イベントカレンダー", url="https://www.city.edogawa.tokyo.jp/e004/sports/kankomidokoro/event/index.html"))
        self.assertTrue(app.is_non_event_page("区内の主なイベント", url="https://www.city.edogawa.tokyo.jp/e004/sports/kankomidokoro/event/index.html"))

    def test_location_and_description_footer_cleanup(self):
        self.assertEqual(app.clean_location_text("篠崎コミュニティ会館 集会室1・2 江戸川区上篠崎4丁目21番8号 ホームページ リズム運動体験教室"),
                         "篠崎コミュニティ会館 集会室1・2 江戸川区上篠崎4丁目21番8号")
        text = "講座の内容\n開催日時 10:00\nイベント情報をiPhone・iPad端末のカレンダーに取り込めます。\nこのページに関するお問い合わせ"
        self.assertEqual(app.clean_description_text(text), "講座の内容\n開催日時 10:00")

    def test_make_event_does_not_accept_closed_or_annual_page(self):
        self.assertIsNone(app.make_event("【申込は終了しました】講座", (date(2026, 12, 1), date(2026, 12, 1)),
                                         "会場", "東京都江戸川区中央一丁目", "https://example.jp/a", "test"))
        self.assertIsNone(app.make_event("イベントカレンダー", (date(2026, 12, 1), date(2026, 12, 1)),
                                         "会場", "東京都江戸川区中央一丁目", "https://www.city.edogawa.tokyo.jp/e004/sports/kankomidokoro/event/index.html", "test"))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class Version324RegressionTests(unittest.TestCase):
    def test_csv_row_with_official_html_detail_url_is_suppressed(self):
        csv_text = (
            "イベント名,開催日,終了日,会場,住所,URL\n"
            "リズム運動体験教室,2026年11月27日,2026年12月31日,篠崎コミュニティ会館,"
            "東京都江戸川区篠崎町7丁目,https://www.city.edogawa.tokyo.jp/event/r8taiken8.html\n"
        )
        meta = {"url": "https://example.jp/data.csv", "http_status": 200,
                "bytes": len(csv_text), "error": None}
        official = {app.normalize_event_url("https://www.city.edogawa.tokyo.jp/event/r8taiken8.html")}
        with patch.object(app, "fetch_text", return_value=(csv_text, meta)):
            events, status = app.parse_csv_source("https://example.jp/data.csv", "test CSV",
                                                  suppress_urls=official)
        self.assertEqual(events, [])
        self.assertEqual(status["suppressed_official_html_rows"], 1)
        self.assertEqual(status["rejected"]["official_html_has_same_detail_url"], 1)

    def test_mypl_detail_uses_main_content_and_extracts_official_url(self):
        html = """<html><body>
        <div class='global-nav'>地域情報 グルメ ショッピング メニュー</div>
        <div id='event-detail'><h1>マッチングアプリSTARTセミナー</h1>
        <h2>開催日時</h2><p>2026年11月28日 14:00～17:00</p>
        <h2>会場</h2><p>タワーホール船堀</p>
        <h2>住所</h2><p>東京都江戸川区船堀4丁目1番1号</p>
        <p>イベント公式サイト：<a href='https://www.city.edogawa.tokyo.jp/event/matching.html'>公式ページ</a></p>
        <p>セミナーの内容を紹介します。</p></div>
        </body></html>"""
        with patch.object(app, "TODAY", date(2026, 11, 26)):
            detail = app.extract_detail(html, "https://edogawa.mypl.net/event/123")
        self.assertEqual(detail["spans"], [(date(2026, 11, 28), date(2026, 11, 28))])
        self.assertEqual(detail["start_time"], "14:00")
        self.assertEqual(detail["end_time"], "17:00")
        self.assertEqual(detail["official_url"], "https://www.city.edogawa.tokyo.jp/event/matching.html")
        self.assertNotIn("グルメ", detail["text"])
        self.assertNotIn("ショッピング", detail["text"])

    def test_location_access_directions_are_removed(self):
        cleaned = app.clean_location_text(
            "東京都江戸川区船堀4丁目1番1号【電車をご利用の場合】新宿駅から都営新宿線"
        )
        self.assertEqual(cleaned, "東京都江戸川区船堀4丁目1番1号")

    def test_location_combination_does_not_repeat_venue(self):
        self.assertEqual(
            app.combine_location("タワーホール船堀", "東京都江戸川区船堀4丁目1番1号 タワーホール船堀"),
            "東京都江戸川区船堀4丁目1番1号 タワーホール船堀",
        )
        self.assertEqual(
            app.combine_location("船堀駅北口駅前広場", "船堀駅 北口広場(東京都江戸川区船堀3-5)"),
            "船堀駅 北口広場(東京都江戸川区船堀3-5)",
        )

    def test_title_fingerprint_strips_official_date_suffix(self):
        self.assertEqual(
            app._title_fingerprint("マッチングアプリSTARTセミナーを開催します(令和8年11月28日)"),
            app._title_fingerprint("マッチングアプリSTARTセミナー"),
        )

    def test_mypl_event_deduplicates_against_official_url_alias(self):
        official = app.make_event("マッチングアプリSTARTセミナーを開催します(令和8年11月28日)",
                                  (date(2026, 12, 12), date(2026, 12, 12)), "なぎさポニーランド",
                                  "東京都江戸川区南葛西7丁目3番",
                                  "https://www.city.edogawa.tokyo.jp/event/matching.html", "区公式")
        mypl = app.make_event("マッチングアプリSTARTセミナー",
                              (date(2026, 12, 12), date(2026, 12, 12)), "なぎさポニーランド",
                              "東京都江戸川区南葛西7丁目3番",
                              "https://edogawa.mypl.net/event/123", "地域・まいぷれ")
        self.assertIsNotNone(official)
        self.assertIsNotNone(mypl)
        mypl.source_urls.append("https://www.city.edogawa.tokyo.jp/event/matching.html")
        merged = app.merge_events([mypl, official])
        self.assertEqual(len(merged), 1)
        self.assertIn("https://edogawa.mypl.net/event/123", merged[0].source_urls)
        self.assertIn("https://www.city.edogawa.tokyo.jp/event/matching.html", merged[0].source_urls)

    def test_ics_uses_v325_prodid_and_nonduplicated_location(self):
        ev = app.make_event("会場表記テスト", (date(2026, 12, 12), date(2026, 12, 12)),
                            "タワーホール船堀", "東京都江戸川区船堀4丁目1番1号 タワーホール船堀",
                            "https://example.jp/event", "test")
        self.assertIsNotNone(ev)
        ics = app.build_ics([ev])
        self.assertIn("PRODID:-//Edogawa Ward Events//JP v3.2.6//EN", ics)
        unfolded = ics.replace("\r\n ", "")
        self.assertIn("LOCATION:東京都江戸川区船堀4丁目1番1号 タワーホール船堀", unfolded)
        self.assertNotIn("タワーホール船堀 タワーホール船堀", unfolded)


class Version325RegressionTests(unittest.TestCase):
    def test_mypl_official_url_alias_matches_same_filename_and_title(self):
        official = app.make_event("マッチングアプリSTARTセミナーを開催します(令和8年11月28日)",
                                  (date(2026, 11, 28), date(2026, 11, 28)), "タワーホール船堀",
                                  "東京都江戸川区船堀4丁目1番1号",
                                  "https://www.city.edogawa.tokyo.jp/event/seminar081128.html", "区公式")
        mypl = app.make_event("マッチングアプリSTARTセミナー",
                              (date(2026, 11, 28), date(2026, 11, 28)), "タワーホール船堀",
                              "東京都江戸川区船堀4丁目1番1号",
                              "https://edogawa.mypl.net/event/00000477661/", "地域・まいぷれ")
        mypl.source_urls.append("https://www.city.edogawa.tokyo.jp/e093/kosodate/deaikekkon/deai/app/seminar081128.html")
        merged = app.merge_events([mypl, official])
        self.assertEqual(len(merged), 1)
        self.assertIn("https://edogawa.mypl.net/event/00000477661/", merged[0].source_urls)

    def test_same_day_official_duplicate_with_minor_address_difference_merges(self):
        a = app.make_event("総合相談会", (date(2026, 12, 4), date(2026, 12, 4)), "江戸川区役所",
                           "東京都江戸川区中央四丁目24番19号",
                           "https://www.city.edogawa.tokyo.jp/event/sodankai1204.html", "区公式")
        b = app.make_event("総合相談会", (date(2026, 12, 4), date(2026, 12, 4)), "江戸川区役所",
                           "東京都江戸川区中央四丁目24番18号",
                           "https://www.city.edogawa.tokyo.jp/event/sodankai124.html", "区公式")
        self.assertEqual(len(app.merge_events([a, b])), 1)

    def test_same_day_official_same_title_different_venue_does_not_merge(self):
        a = app.Event("総合相談会", "2026-12-04", "2026-12-04", "江戸川区役所",
                      "東京都江戸川区中央四丁目24番19号",
                      "https://www.city.edogawa.tokyo.jp/event/sodankai1204.html",
                      source="区公式", source_urls=["https://www.city.edogawa.tokyo.jp/event/sodankai1204.html"])
        b = app.Event("総合相談会", "2026-12-04", "2026-12-04", "小岩区民館",
                      "東京都江戸川区東小岩六丁目9番14号",
                      "https://www.city.edogawa.tokyo.jp/event/sodankai124.html",
                      source="区公式", source_urls=["https://www.city.edogawa.tokyo.jp/event/sodankai124.html"])
        self.assertEqual(len(app.merge_events([a, b])), 2)

    def test_public_transport_access_is_removed_from_location(self):
        cleaned = app.clean_location_text(
            "東京都江戸川区東篠崎1丁目8番1号【公共交通機関】都営新宿線篠崎駅南口よりバス"
        )
        self.assertEqual(cleaned, "東京都江戸川区東篠崎1丁目8番1号")

    def test_mypl_nested_main_column_excludes_outer_site_menu(self):
        html = """<html><body><div id='page-wrapper'><div class='global-nav'>地元暮らしをちょっぴり楽しく グルメ 全て 居酒屋</div>
        <div class='main-contents'><div class='event-detail'><h1>はれやか発声講座</h1>
        <h2>開催日時</h2><p>2026年12月5日 10:00～11:00</p><h2>会場</h2><p>篠崎コミュニティ会館</p>
        <h2>住所</h2><p>東京都江戸川区篠崎町7丁目</p><p>講座の内容です。</p></div></div>
        </div></body></html>"""
        with patch.object(app, "TODAY", date(2026, 11, 27)):
            detail = app.extract_detail(html, "https://edogawa.mypl.net/event/12345/")
        self.assertIn("講座の内容です", detail["text"])
        self.assertNotIn("居酒屋", detail["text"])
        self.assertNotIn("地元暮らし", detail["text"])


class Version326RegressionTests(unittest.TestCase):
    def test_people_index_url_is_rejected_even_if_listing_title_is_people(self):
        url = "https://www.city.edogawa.tokyo.jp/e027/kosodate/kenzenikusei/mirai/index.html"
        self.assertTrue(app.is_non_event_page("People", url=url))

    def test_people_title_is_rejected(self):
        self.assertTrue(app.is_non_event_page("People"))

    def test_myplue_navigation_before_event_title_is_removed(self):
        html = """<html><body><main>
        <div class='site-menu'>文字サイズ ジャンルから探す 場所で探す 居酒屋・ダイニングバー グルメ
        美容・健康 ショッピング</div>
        <section class='event-detail'><h1>はれやか発声講座</h1>
        <h2>開催日時</h2><p>2026年12月5日 10:00～11:00</p>
        <h2>会場</h2><p>篠崎コミュニティ会館</p>
        <h2>住所</h2><p>東京都江戸川区篠崎町7丁目</p>
        <p>声を出して健康づくりを楽しむ講座です。</p></section></main></body></html>"""
        with patch.object(app, "TODAY", date(2026, 11, 27)):
            detail = app.extract_detail(html, "https://edogawa.mypl.net/event/12345/")
        self.assertIn("声を出して健康づくり", detail["text"])
        self.assertNotIn("文字サイズ", detail["text"])
        self.assertNotIn("居酒屋・ダイニングバー", detail["text"])
        self.assertEqual(detail["title"], "はれやか発声講座")

    def test_myplue_transport_access_does_not_leak_into_location(self):
        value = "東京都江戸川区篠崎町7丁目【公共交通機関】都営新宿線篠崎駅から京成バスで約5分"
        self.assertEqual(app.clean_location_text(value), "東京都江戸川区篠崎町7丁目")
