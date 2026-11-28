import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import edogawa_ical as app


class FakeResponse:
    def __init__(self, content):
        self.content = content
        self.status_code = 200
        self.headers = {"Content-Type": "application/pdf"}
    def raise_for_status(self):
        return None


class V3219SourceRegressionTests(unittest.TestCase):
    def test_version_and_prodid(self):
        self.assertEqual(app.APP_VERSION, "3.2.20")
        self.assertIn("PRODID:-//Edogawa Ward Events//JP v3.2.20//EN", app.build_ics([]))

    def test_annual_page_monitor_does_not_invent_exact_dates(self):
        html = '''<html><body><main><h1>区内の主なイベント</h1>
        <a href="/event/flower.html">花の祭典（5月上旬）</a>
        <a href="/event/fireworks.html">江戸川区花火大会（8月上旬）</a>
        </main></body></html>'''
        with patch.object(app, "fetch_text", return_value=(html, {"http_status": 200, "bytes": len(html)})):
            status = app.monitor_annual_events_page()
        self.assertEqual(status["status"], "ok")
        self.assertEqual(status["accepted"], 0)
        self.assertEqual(status["seasonal_only_links"], 2)

    def test_bulletin_pdf_accepts_only_explicit_registered_venue_block(self):
        import fitz
        doc = fitz.open()
        page = doc.new_page()
        page.insert_textbox((40, 40, 560, 220),
            "第56回松島土曜寄席\n【日時】2026年12月19日土14時から16時30分\n【場所】タワーホール船堀\n【定員】30人（申込順）",
            fontname="japan", fontsize=11)
        payload = doc.tobytes()
        doc.close()
        with patch.object(app, "SESSION") as session:
            session.get.return_value = FakeResponse(payload)
            events, status = app.parse_bulletin_pdf()
        self.assertEqual(status["status"], "ok")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].title, "第56回松島土曜寄席")
        self.assertEqual(events[0].venue, "タワーホール船堀")

    def test_bulletin_pdf_does_not_accept_unknown_venue(self):
        import fitz
        doc = fitz.open()
        page = doc.new_page()
        page.insert_textbox((40, 40, 560, 220),
            "地域イベント体験会\n【日時】2026年12月19日土14時から16時\n【場所】住所不明の特設会場\n【定員】30人",
            fontname="japan", fontsize=11)
        payload = doc.tobytes()
        doc.close()
        with patch.object(app, "SESSION") as session:
            session.get.return_value = FakeResponse(payload)
            events, status = app.parse_bulletin_pdf()
        self.assertEqual(events, [])
        self.assertGreater(status["rejected"].get("pdf_venue_unregistered_or_ambiguous", 0), 0)

    def test_pdf_year_era_normalization_for_reiwa_nine(self):
        import re
        text = "令和8年12月号 9年1月23日"
        text = app.era_to_western(text)
        text = re.sub(r"(?<!\d)([1-9])年(?=\s*\d{1,2}月)",
                      lambda m: f"{2018 + int(m.group(1))}年", text)
        self.assertIn("2027年1月23日", text)

    def test_bulletin_pdf_groups_separate_title_and_body_blocks_by_card(self):
        import fitz
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        # The real PDF draws each event as a card. Its title strip and body are
        # separate PDF text blocks, unlike the old one-textbox fixture.
        page.draw_rect(fitz.Rect(25, 25, 285, 205), color=(0, 0.6, 0.9), width=1)
        page.insert_textbox(fitz.Rect(35, 35, 275, 65),
            "第23回 師 鈴木春朝先生とともに歩む 一心会書展", fontname="japan", fontsize=10)
        page.insert_textbox(fitz.Rect(35, 75, 275, 195),
            "臨書の大作と『旅』をテーマにした作品を展示します。\n"
            "【日時】12月11日金～13日日10時～17時\n"
            "【場所】タワーホール船堀1階展示ホール1・2\n"
            "【後援主管部署】文化振興係", fontname="japan", fontsize=10)
        payload = doc.tobytes(); doc.close()
        with patch.object(app, "SESSION") as session:
            session.get.return_value = FakeResponse(payload)
            events, status = app.parse_bulletin_pdf()
        self.assertEqual(len(events), 1)
        self.assertIn("一心会書展", events[0].title)
        self.assertEqual(events[0].start_date, "2026-12-11")
        self.assertEqual(status["cards_seen"], 1)
        self.assertEqual(status["date_place_cards"], 1)

    def test_multi_venue_event_has_no_single_map_coordinate(self):
        from datetime import date
        ev = app.make_event(
            "第50回江戸川区特産正月用花の展示即売会",
            (date(2026, 12, 20), date(2026, 12, 20)),
            "区役所本庁舎前庭、船堀駅南口広場、JR小岩駅南口、瑞江駅南口広場、篠崎駅南口広場",
            "", "https://example.invalid/event", "test",
            description="区内5会場で開催"
        )
        self.assertIsNotNone(ev)
        self.assertTrue(ev.multi_venue)
        self.assertIsNone(ev.latitude)
        self.assertIsNone(ev.longitude)
        self.assertEqual(ev.coordinate_quality, "multi_venue_no_single_point")

    def test_bulletin_pdf_collects_all_three_previously_missing_december_events(self):
        import fitz
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        cards = [
            (fitz.Rect(20, 20, 285, 210), "第23回 師 鈴木春朝先生とともに歩む\n一心会書展",
             "【日時】12月11日金～13日日10時～17時\n※13日日は16時まで。\n【場所】タワーホール船堀1階展示ホール1・2"),
            (fitz.Rect(305, 20, 575, 210), "第11回ハギュットフェス2026",
             "【日時】12月13日日10時～15時\n【場所】グリーンパレス新館3・4階/本館4階"),
            (fitz.Rect(20, 230, 285, 420), "第56回松島土曜寄席",
             "【日時】12月19日土14時～16時30分\n【場所】松島コミュニティ会館"),
        ]
        for rect, title, body in cards:
            page.draw_rect(rect, color=(0, 0.6, 0.9), width=1)
            page.insert_textbox(fitz.Rect(rect.x0+8, rect.y0+8, rect.x1-8, rect.y0+42),
                                title, fontname="japan", fontsize=9)
            page.insert_textbox(fitz.Rect(rect.x0+8, rect.y0+50, rect.x1-8, rect.y1-8),
                                body, fontname="japan", fontsize=9)
        payload = doc.tobytes(); doc.close()
        with patch.object(app, "SESSION") as session, patch.object(
                app, "geocode_edogawa_location", return_value=(35.6980, 139.8640, "test")):
            session.get.return_value = FakeResponse(payload)
            events, status = app.parse_bulletin_pdf()
        titles = [x.title for x in events]
        self.assertEqual(len(events), 5, (titles, status))
        self.assertTrue(any("一心会書展" in x for x in titles), titles)
        self.assertTrue(any("ハギュットフェス" in x for x in titles), titles)
        self.assertTrue(any("松島土曜寄席" in x for x in titles), titles)
        exhibit = sorted((x for x in events if "一心会書展" in x.title), key=lambda x: x.start_date)
        self.assertEqual([x.start_date for x in exhibit], ["2026-12-11", "2026-12-12", "2026-12-13"])
        self.assertEqual([x.end_time for x in exhibit], ["17:00", "17:00", "16:00"])

    def test_stamp_rally_with_many_libraries_is_unmapped_not_single_pin(self):
        from datetime import date
        ev = app.make_event(
            "本でつながる えどがわ探検スタンプラリー",
            (date(2026, 12, 3), date(2026, 12, 9)),
            "西葛西図書館", "", "https://example.invalid/stamp", "test",
            description="江戸川区立図書館12館と加盟書店6店で開催"
        )
        self.assertIsNotNone(ev)
        self.assertTrue(ev.multi_venue)
        self.assertIsNone(ev.latitude)

    def test_disability_week_with_multiple_program_venues_is_unmapped(self):
        from datetime import date
        ev = app.make_event(
            "障害者週間", (date(2026, 12, 3), date(2026, 12, 9)),
            "ダイヤと花の大観覧車", "東京都江戸川区葛西臨海・海浜公園",
            "https://example.invalid/disability-week", "test",
            description="区内図書館の展示、タワーホール船堀と観覧車のライトアップを実施"
        )
        self.assertIsNotNone(ev)
        self.assertTrue(ev.multi_venue)
        self.assertIsNone(ev.latitude)

    def test_merge_never_restores_single_pin_for_multi_venue_duplicate(self):
        from datetime import date
        url = "https://example.invalid/shared-event"
        with patch.object(app, "geocode_edogawa_location", return_value=(35.6838, 139.8619, "test")):
            single = app.make_event(
                "障害者週間", (date(2026, 12, 3), date(2026, 12, 9)),
                "ダイヤと花の大観覧車", "東京都江戸川区葛西臨海・海浜公園",
                url, "区公式", description="障害者週間"
            )
        multi = app.make_event(
            "障害者週間", (date(2026, 12, 3), date(2026, 12, 9)),
            "ダイヤと花の大観覧車", "東京都江戸川区葛西臨海・海浜公園",
            url, "区内施設", description="区内図書館展示、タワーホール船堀と観覧車ライトアップ"
        )
        self.assertIsNotNone(single); self.assertIsNotNone(multi)
        merged = app.merge_events([single, multi])
        self.assertEqual(len(merged), 1)
        self.assertTrue(merged[0].multi_venue)
        self.assertIsNone(merged[0].latitude)
        self.assertIsNone(merged[0].longitude)

    def test_pdf_shorthand_date_range_with_weekdays_is_parsed_as_range(self):
        spans = app.parse_date_spans("12月11日金～13日日10時～17時", include_past=True)
        self.assertEqual([(a.isoformat(), b.isoformat()) for a, b in spans],
                         [("2026-12-11", "2026-12-13")])

    def test_matsushima_community_hall_is_registered(self):
        reg = app.lookup_venue("松島コミュニティ会館和室")
        self.assertIsNotNone(reg)
        self.assertIn("松島4-25-6", reg["address"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
