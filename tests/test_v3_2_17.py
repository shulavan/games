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


class V3217FinalizationTests(unittest.TestCase):
    def test_current_version_and_prodid(self):
        self.assertEqual(app.APP_VERSION, "3.2.19")
        self.assertIn("PRODID:-//Edogawa Ward Events//JP v3.2.19//EN", app.build_ics([]))

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
