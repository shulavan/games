import re
import time
import hashlib
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

# Constants
BASE_URL = "https://www.city.edogawa.tokyo.jp/cgi-bin/event_cal_multi/calendar.cgi"
LIBRARY_EVENT_URL = "https://www.library.city.edogawa.tokyo.jp/toshow/event/"
TOWERHALL_EVENT_URL = "https://www.towerhall.jp/events/list/"
BUNKACENTER_EVENT_URL = "https://edogawa-bunkacenter.jp/calendar/"
SPORTS_KINTONE_URL = "https://2f01466f.viewer.kintoneapp.com/public/2288685cd0240ea5379b822175def1d520d606ea6a20c595b5c7602fc6c29368"
KASAI_PARK_EVENT_URL = "https://kasaikaihinpark.com/event/"
CHIKAHAKU_EVENT_URL = "https://www.chikahaku.jp/event/"
EDOGAWA_ZOO_EVENT_URL = "https://www.edogawa-kankyozaidan.jp/zoo/event/"
PONYLAND_EVENT_URL = "https://www.edogawa-kankyozaidan.jp/ponyland/event/"
KIKIS_MUSEUM_EVENT_URL = "https://kikismuseum.jp/news/"
KASAI_AQUARIUM_URL = "https://www.tokyo-zoo.net/zoo/kasai/"
OUTPUT_ICS = "edogawa_events.ics"
USER_AGENT = "Mozilla/5.0 (compatible; EdogawaCalendarSync/1.0)"
JST = timezone(timedelta(hours=9), name="JST")

# 除外するニュース・お知らせ・システム表記キーワード
EXCLUDE_KEYWORDS = (
    "【重要】",
    "ニュース",
    "お知らせ",
    "休館日",
    "休園します",
    "一時閉鎖",
    "展示を再開",
    "展示を中止",
    "メンテナンス",
    "機関誌",
    "どうぶつと動物園",
    "SEA LIFE NEWS",
    "リスト表示",
    "詳細を見る",
    "掲載申込書",
    "サイトリニューアル",
    "正門の位置が変わります",
    "募集は終了",
    "【終了】",
)

# 江戸川区内主要施設・全12図書館・公園・文化施設の緯度経度マッピング (名称キーワード: (緯度, 経度, 正式名称/住所))
FACILITY_LOCATIONS = {
    "中央図書館": (35.706142, 139.869214, "江戸川区立中央図書館（東京都江戸川区中央3-1-3）"),
    "小岩図書館": (35.730268, 139.883718, "江戸川区立小岩図書館（東京都江戸川区東小岩6-15-2）"),
    "松江図書館": (35.697631, 139.865382, "江戸川区立松江図書館（東京都江戸川区松江2-1-10）"),
    "小松川図書館": (35.694885, 139.849215, "江戸川区立小松川図書館（東京都江戸川区平井1-11-26）"),
    "篠崎図書館": (35.706812, 139.903845, "江戸川区立篠崎図書館（東京都江戸川区篠崎町7-20-19）"),
    "葛西図書館": (35.661284, 139.865412, "江戸川区立葛西図書館（東京都江戸川区江戸川6-24-1）"),
    "西葛西図書館": (35.666941, 139.857321, "江戸川区立西葛西図書館（東京都江戸川区西葛西5-10-47）"),
    "東葛西図書館": (35.662519, 139.881423, "江戸川区立東葛西図書館（東京都江戸川区東葛西8-22-1）"),
    "東部図書館": (35.691542, 139.898124, "江戸川区立東部図書館（東京都江戸川区東瑞江1-17-1）"),
    "鹿骨コミュニティ図書館": (35.716892, 139.892415, "江戸川区立鹿骨コミュニティ図書館（東京都江戸川区鹿骨1-54-2）"),
    "清新町コミュニティ図書館": (35.654812, 139.856912, "江戸川区立清新町コミュニティ図書館（東京都江戸川区清新町1-1-34）"),
    "篠崎子ども図書館": (35.702145, 139.906128, "江戸川区立篠崎子ども図書館（東京都江戸川区篠崎町3-12-20）"),
    "魔法の文学館": (35.649124, 139.855892, "魔法の文学館（江戸川区角野栄子児童文学館・東京都江戸川区南葛西7-3-1）"),
    "角野栄子児童文学館": (35.649124, 139.855892, "魔法の文学館（江戸川区角野栄子児童文学館・東京都江戸川区南葛西7-3-1）"),
    "総合文化センター": (35.708125, 139.868514, "江戸川区総合文化センター（東京都江戸川区中央4-14-1）"),
    "タワーホール船堀": (35.683812, 139.861924, "タワーホール船堀（東京都江戸川区船堀4-1-1）"),
    "船堀タワー": (35.683812, 139.861924, "タワーホール船堀（東京都江戸川区船堀4-1-1）"),
    "グリーンパレス": (35.698912, 139.866421, "グリーンパレス（東京都江戸川区松島1-38-1）"),
    "江戸川区役所": (35.706612, 139.868324, "江戸川区役所（東京都江戸川区中央1-4-1）"),
    "地下鉄博物館": (35.663412, 139.873215, "地下鉄博物館（東京都江戸川区東葛西6-3-1 東京メトロ東西線葛西駅高架下）"),
    "自然動物園": (35.671312, 139.858724, "江戸川区自然動物園（東京都江戸川区北葛西3-2-1 行船公園内）"),
    "行船公園": (35.671312, 139.858724, "行船公園・平成庭園（東京都江戸川区北葛西3-2-1）"),
    "平成庭園": (35.671312, 139.858724, "行船公園・平成庭園（東京都江戸川区北葛西3-2-1）"),
    "篠崎ポニーランド": (35.714512, 139.910214, "篠崎ポニーランド（東京都江戸川区篠崎町3-12-17）"),
    "なぎさポニーランド": (35.647812, 139.873912, "なぎさポニーランド（東京都江戸川区南葛西7-3 なぎさ公園内）"),
    "ポニーランド": (35.714512, 139.910214, "篠崎ポニーランド（東京都江戸川区篠崎町3-12-17）"),
    "葛西臨海水族園": (35.640112, 139.862214, "東京都葛西臨海水族園（東京都江戸川区臨海町6-2-3）"),
    "葛西臨海公園": (35.642514, 139.859812, "葛西臨海公園（東京都江戸川区臨海町6-2-1）"),
    "葛西海浜公園": (35.637812, 139.856214, "葛西海浜公園（東京都江戸川区臨海町6-2-4）"),
    "総合レクリエーション公園": (35.661812, 139.862514, "総合レクリエーション公園（東京都江戸川区南葛西・西葛西）"),
    "スポーツランド": (35.718512, 139.903214, "江戸川区スポーツランド（東京都江戸川区東篠崎1-8-1）"),
    "スポーツセンター": (35.668912, 139.862124, "江戸川区スポーツセンター（東京都江戸川区西葛西4-2-20）"),
    "江戸川区球場": (35.665812, 139.861514, "江戸川区球場（東京都江戸川区西葛西7-2-1）"),
    "陸上競技場": (35.660112, 139.874512, "江戸川区陸上競技場（東京都江戸川区清新町2-1-1）"),
    "臨海球技場": (35.646212, 139.868912, "江戸川区臨海球技場（東京都江戸川区臨海町1-1-2）"),
    "水辺のスポーツガーデン": (35.684512, 139.914215, "水辺のスポーツガーデン（東京都江戸川区東篠崎2-3-1）"),
    "東部フレンドホール": (35.691542, 139.898124, "東部フレンドホール（東京都江戸川区瑞江2-5-7）"),
    "小岩アーバンプラザ": (35.736812, 139.888514, "小岩アーバンプラザ（東京都江戸川区北小岩1-17-1）"),
    "小松川さくらホール": (35.694885, 139.849215, "小松川さくらホール（東京都江戸川区平井1-11-26）"),
}


def resolve_location(text):
    """Find matching facility coordinates (lat, lon, full_name) from event text."""
    for kw in sorted(FACILITY_LOCATIONS.keys(), key=len, reverse=True):
        if kw in text:
            return FACILITY_LOCATIONS[kw]
    return None


def fetch_html(url, params=None):
    """Fetch HTML content safely with timeout and User-Agent."""
    try:
        resp = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=20)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        return resp.text
    except Exception as e:
        print(f"Warning: Failed to fetch {url}: {e}")
        return None


def is_noise_or_news(title, block_text=""):
    """Return True if the item is a general news post, closure notice, or UI navigation label."""
    combined = f"{title} {block_text}"
    if any(kw in combined for kw in EXCLUDE_KEYWORDS):
        return True
    # 公開日 + 「ニュース」パターンの除外 (例: 2026.11.14 ニュース ...)
    if re.search(r"20\d{2}[./年]\d{1,2}[./月]\d{1,2}日?\s*ニュース", combined):
        return True
    # タイトルが日付のみの場合 (例: 2027-01-24)
    if re.fullmatch(r"\d{4}[-/年.]\d{1,2}[-/月.]\d{1,2}日?", title.strip()):
        return True
    return False


def clean_event_title(raw_title):
    """Remove leading publication dates and boilerplate prefixes from event titles."""
    t = raw_title.strip()
    # 先頭の公開日表記を削除 (例: "2026.11.21 ", "2026/11/21 ", "2026年11月21日 ")
    t = re.sub(r"^20\d{2}[./年]\d{1,2}[./月]\d{1,2}日?\s*", "", t)
    # 先頭の定型ラベルを削除 (例: "イベント ", "主催共催公演 ", "【受付中】")
    t = re.sub(r"^(?:イベント|主催共催公演|共催公演|企画展|特別展)\s+", "", t)
    t = re.sub(r"^【(?:受付中|募集中|事前申込制|当日受付)】\s*", "", t)
    return t.strip() or raw_title.strip()


def extract_first_image_url(page_url):
    """Fetch an event detail page and return the first valid image URL if found."""
    if not page_url or "#" in page_url and page_url.startswith(SPORTS_KINTONE_URL):
        return None

    html = fetch_html(page_url)
    if not html:
        return None

    try:
        soup = BeautifulSoup(html, "html.parser")

        # 1. Check Open Graph image first
        og_img = soup.find("meta", property="og:image")
        if og_img and og_img.get("content"):
            candidate = urljoin(page_url, og_img["content"].strip())
            if re.match(r"^https?://", candidate, re.I):
                return candidate

        # 2. Fallback to first content image tag with an image-like URL
        for img in soup.find_all("img", src=True):
            src = img["src"].strip()
            if not src or src.startswith("data:"):
                continue
            full_img_url = urljoin(page_url, src)
            lower_url = full_img_url.lower()
            if any(skip in lower_url for skip in ("logo", "icon", "banner_btn", "spacer", "loading", "arrow", "sns")):
                continue
            if re.search(r"\.(jpg|jpeg|png|webp|gif)(\?|$)", lower_url) or "image" in lower_url or "photo" in lower_url or "upload" in lower_url:
                return full_img_url
    except Exception as e:
        print(f"Warning: Failed to parse image from {page_url}: {e}")

    return None


def get_target_months(months_ahead=3):
    """Return list of (year, month) tuples from current month to months_ahead."""
    now = datetime.now(JST)
    targets = []
    for i in range(months_ahead + 1):
        y = now.year + (now.month - 1 + i) // 12
        m = (now.month - 1 + i) % 12 + 1
        targets.append((y, m))
    return targets


def parse_month_events(year, month):
    """Scrape events from Edogawa City official calendar (calendar.cgi)."""
    html = fetch_html(BASE_URL, params={"type": "1", "year": str(year), "month": str(month)})
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    events = {}

    for td in soup.find_all(["td", "div"]):
        day_match = re.match(r"^\s*(\d{1,2})日", td.get_text(" ", strip=True))
        if not day_match:
            continue
        day = int(day_match.group(1))
        try:
            current_date = datetime(year, month, day, tzinfo=JST).date()
        except ValueError:
            continue

        for li in td.find_all("li"):
            a_tag = li.find("a", href=True)
            if not a_tag:
                continue
            full_url = urljoin(BASE_URL, a_tag["href"])
            raw_title = a_tag.get_text(" ", strip=True)
            if not raw_title or is_noise_or_news(raw_title):
                continue
            title = clean_event_title(raw_title)

            cat_span = li.find("span")
            category = cat_span.get_text(strip=True) if cat_span else "区イベント"

            if full_url not in events:
                events[full_url] = {
                    "title": title,
                    "url": full_url,
                    "category": category,
                    "venue": "",
                    "dates": set(),
                }
            events[full_url]["dates"].add(current_date)

    return list(events.values())


def extract_dates_from_text(text, default_year=None):
    """Extract future/upcoming Japanese event dates while ignoring leading publication dates."""
    now = datetime.now(JST)
    today = now.date()
    base_year = default_year or now.year

    # テキスト先頭にある記事公開日 (例: 2026.11.21 ...) を取り除いてから開催日を探す
    cleaned_text = re.sub(r"^\s*20\d{2}[./年]\d{1,2}[./月]\d{1,2}日?\s*", "", text)

    parsed_dates = set()
    matches = re.findall(r"(?:(\d{4})[年/\.])?(\d{1,2})[月/\.](\d{1,2})日?", cleaned_text)
    for y_str, m_str, d_str in matches:
        try:
            m = int(m_str)
            d = int(d_str)
            if not (1 <= m <= 12 and 1 <= d <= 31):
                continue
            if y_str:
                y = int(y_str)
            else:
                y = base_year if m >= now.month - 1 else base_year + 1
            candidate = datetime(y, m, d, tzinfo=JST).date()
            # 1ヶ月以上前の過去日付は公開日や過去ログとみなして除外
            if candidate >= today - timedelta(days=30):
                parsed_dates.add(candidate)
        except ValueError:
            continue
    return parsed_dates


def parse_generic_facility_events(url, category, default_venue=""):
    """Generic scraper for Edogawa public/cultural facility event pages."""
    html = fetch_html(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    events = {}

    containers = soup.find_all(["article", "tr", "li", "dd", "div"], class_=re.compile(r"event|post|news|item|list|card", re.I))
    if not containers:
        containers = soup.find_all(["article", "tr", "li", "dl"])

    for block in containers:
        link = block.find("a", href=True)
        if not link:
            continue
        href = link["href"]
        if href.startswith("#") or "javascript:" in href:
            continue

        full_url = urljoin(url, href)
        raw_title = link.get_text(" ", strip=True)
        if not raw_title or len(raw_title) < 3 or raw_title in ("詳細はこちら", "もっと見る", "一覧へ"):
            heading = block.find(["h2", "h3", "h4", "dt", "p"])
            if heading:
                raw_title = heading.get_text(" ", strip=True)
        if not raw_title or len(raw_title) < 3:
            continue

        block_text = block.get_text(" ", strip=True)
        if is_noise_or_news(raw_title, block_text):
            continue

        title = clean_event_title(raw_title)
        parsed_dates = extract_dates_from_text(block_text)
        if not parsed_dates:
            continue

        venue = default_venue
        for kw in sorted(FACILITY_LOCATIONS.keys(), key=len, reverse=True):
            if kw in block_text:
                venue = kw
                break

        if full_url not in events:
            events[full_url] = {
                "title": title,
                "url": full_url,
                "category": category,
                "venue": venue,
                "dates": set(),
            }
        events[full_url]["dates"].update(parsed_dates)
        if venue:
            events[full_url]["venue"] = venue

    return list(events.values())


def parse_sports_kintone_events():
    """Scrape Edogawa Sports Events (kintone public viewer) using Playwright if available."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Warning: Playwright not installed; skipping kintone sports events.")
        return []

    events = {}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=USER_AGENT)
            page.goto(SPORTS_KINTONE_URL, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(3000)
            html = page.content()
            browser.close()

        soup = BeautifulSoup(html, "html.parser")
        rows = soup.find_all(["tr", "div"], class_=re.compile(r"record|row|item|card", re.I))
        for idx, row in enumerate(rows):
            text = row.get_text(" ", strip=True)
            parsed_dates = extract_dates_from_text(text)
            if not parsed_dates:
                continue

            link = row.find("a", href=True)
            full_url = urljoin(SPORTS_KINTONE_URL, link["href"]) if link else f"{SPORTS_KINTONE_URL}#event-{idx}"
            title_el = row.find(["h2", "h3", "h4", "a", "td"])
            raw_title = title_el.get_text(" ", strip=True) if title_el else text[:50]
            if not raw_title or len(raw_title) < 2 or is_noise_or_news(raw_title, text):
                continue
            title = clean_event_title(raw_title)

            venue = "江戸川区スポーツセンター"
            for kw in sorted(FACILITY_LOCATIONS.keys(), key=len, reverse=True):
                if kw in text:
                    venue = kw
                    break

            key = f"{title}_{min(parsed_dates)}"
            if key not in events:
                events[key] = {
                    "title": title,
                    "url": full_url,
                    "category": "スポーツ",
                    "venue": venue,
                    "dates": set(),
                }
            events[key]["dates"].update(parsed_dates)
    except Exception as e:
        print(f"Warning: Failed to scrape kintone sports events: {e}")

    return list(events.values())


def parse_library_events():
    """Scrape events from Edogawa City Library."""
    html = fetch_html(LIBRARY_EVENT_URL)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    events = {}
    now = datetime.now(JST)

    containers = soup.find_all(["tr", "li", "dd", "div"], class_=re.compile(r"event|list|item|news", re.I))
    if not containers:
        containers = soup.find_all(["tr", "li", "dl"])

    for block in containers:
        link = block.find("a", href=True)
        if not link:
            continue
        href = link["href"]
        if href.startswith("#") or "javascript:" in href:
            continue

        full_url = urljoin(LIBRARY_EVENT_URL, href)
        raw_title = link.get_text(" ", strip=True)
        if not raw_title or len(raw_title) < 2:
            continue

        block_text = block.get_text(" ", strip=True)
        if is_noise_or_news(raw_title, block_text):
            continue

        title = clean_event_title(raw_title)
        date_matches = re.findall(r"(?:(\d{4})[年/\.])?(\d{1,2})[月/\.](\d{1,2})日?", block_text)
        parsed_dates = set()
        for y_str, m_str, d_str in date_matches:
            try:
                m = int(m_str)
                d = int(d_str)
                if not (1 <= m <= 12 and 1 <= d <= 31):
                    continue
                if y_str:
                    y = int(y_str)
                else:
                    y = now.year if m >= now.month - 1 else now.year + 1
                parsed_dates.add(datetime(y, m, d, tzinfo=JST).date())
            except ValueError:
                continue

        if not parsed_dates:
            continue

        venue = ""
        for kw in sorted(FACILITY_LOCATIONS.keys(), key=len, reverse=True):
            if kw in block_text:
                venue = kw
                break

        if full_url not in events:
            events[full_url] = {
                "title": title,
                "url": full_url,
                "category": "図書館",
                "venue": venue,
                "dates": set(),
            }
        events[full_url]["dates"].update(parsed_dates)
        if venue and not events[full_url]["venue"]:
            events[full_url]["venue"] = venue

    return list(events.values())


def escape_ics_text(text):
    """Escape special characters according to RFC 5545."""
    if not text:
        return ""
    text = text.replace("\\", "\\\\")
    text = text.replace(";", "\\;")
    text = text.replace(",", "\\,")
    text = text.replace("\r\n", "\\n").replace("\n", "\\n")
    return text


def fold_ics_line(line):
    """Fold lines longer than 75 octets per RFC 5545."""
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return line
    chunks = []
    current = ""
    for char in line:
        if len((current + char).encode("utf-8")) > 73:
            chunks.append(current)
            current = " " + char
        else:
            current += char
    if current:
        chunks.append(current)
    return "\r\n".join(chunks)


def generate_ics(all_events):
    """Build an RFC 5545 compliant iCalendar string from aggregated events."""
    now_utc = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Edogawa City Event Aggregator//JP",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:江戸川区イベントカレンダー（全体）",
        "X-WR-TIMEZONE:Asia/Tokyo",
        "X-WR-CALDESC:江戸川区公式ホームページおよび区内公共施設のイベントカレンダー全件まとめ",
    ]

    for ev in all_events:
        sorted_dates = sorted(ev["dates"])
        if not sorted_dates:
            continue

        spans = []
        span_start = sorted_dates[0]
        prev_date = sorted_dates[0]
        for d in sorted_dates[1:]:
            if (d - prev_date).days == 1:
                prev_date = d
            else:
                spans.append((span_start, prev_date))
                span_start = d
                prev_date = d
        spans.append((span_start, prev_date))

        for start_d, end_d in spans:
            dtstart_str = start_d.strftime("%Y%m%d")
            dtend_str = (end_d + timedelta(days=1)).strftime("%Y%m%d")

            uid_seed = f"{ev['url']}_{dtstart_str}_{dtend_str}"
            uid_hash = hashlib.sha256(uid_seed.encode("utf-8")).hexdigest()[:24]
            uid = f"{uid_hash}@edogawa-events.local"

            summary = ev["title"]
            if ev.get("category"):
                summary = f"[{ev['category']}] {summary}"

            geo_info = resolve_location(f"{ev.get('venue', '')} {ev['title']}")
            image_url = ev.get("image_url")

            description_lines = [f"詳細・申込情報: {ev['url']}"]
            if geo_info:
                lat, lon, loc_name = geo_info
                description_lines.append(f"会場: {loc_name}")
                description_lines.append(f"地図: https://maps.google.com/?q={lat},{lon}")
            if image_url:
                description_lines.append(f"画像URL: {image_url}")

            description = "\n".join(description_lines)

            vevent_lines = [
                "BEGIN:VEVENT",
                f"UID:{uid}",
                f"DTSTAMP:{now_utc}",
                f"DTSTART;VALUE=DATE:{dtstart_str}",
                f"DTEND;VALUE=DATE:{dtend_str}",
                fold_ics_line(f"SUMMARY:{escape_ics_text(summary)}"),
                fold_ics_line(f"DESCRIPTION:{escape_ics_text(description)}"),
                fold_ics_line(f"URL:{ev['url']}"),
            ]

            if geo_info:
                lat, lon, loc_name = geo_info
                vevent_lines.append(fold_ics_line(f"LOCATION:{escape_ics_text(loc_name)}"))
                vevent_lines.append(f"GEO:{lat:.6f};{lon:.6f}")

            vevent_lines.append("END:VEVENT")
            lines.extend(vevent_lines)

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def main():
    print("Fetching Edogawa City event calendar...")
    merged_events = {}

    def add_events(items):
        for item in items:
            url = item["url"]
            if url not in merged_events:
                merged_events[url] = item
            else:
                merged_events[url]["dates"].update(item["dates"])
                if item.get("venue") and not merged_events[url].get("venue"):
                    merged_events[url]["venue"] = item["venue"]

    for year, month in get_target_months(months_ahead=3):
        print(f"Scraping Edogawa City {year}-{month:02d}...")
        add_events(parse_month_events(year, month))
        time.sleep(1.0)

    print("Scraping Edogawa City Library events...")
    add_events(parse_library_events())

    print("Scraping Tower Hall Funabori events...")
    add_events(parse_generic_facility_events(TOWERHALL_EVENT_URL, "船堀タワー", "タワーホール船堀"))

    print("Scraping Edogawa Bunka Center events...")
    add_events(parse_generic_facility_events(BUNKACENTER_EVENT_URL, "総合文化センター", "江戸川区総合文化センター"))

    print("Scraping Edogawa Sports events...")
    add_events(parse_sports_kintone_events())

    print("Scraping Kasai Rinkai Park events...")
    add_events(parse_generic_facility_events(KASAI_PARK_EVENT_URL, "葛西臨海公園", "葛西臨海公園"))

    print("Scraping Tokyo Metro Museum (Chikahaku) events...")
    add_events(parse_generic_facility_events(CHIKAHAKU_EVENT_URL, "地下鉄博物館", "地下鉄博物館"))

    print("Scraping Edogawa Natural Zoo events...")
    add_events(parse_generic_facility_events(EDOGAWA_ZOO_EVENT_URL, "自然動物園", "江戸川区自然動物園"))

    print("Scraping Edogawa Ponyland events...")
    add_events(parse_generic_facility_events(PONYLAND_EVENT_URL, "ポニーランド", "篠崎ポニーランド"))

    print("Scraping Kiki's Museum of Literature events...")
    add_events(parse_generic_facility_events(KIKIS_MUSEUM_EVENT_URL, "魔法の文学館", "魔法の文学館"))

    print("Scraping Tokyo Sea Life Park (Kasai Aquarium) events...")
    add_events(parse_generic_facility_events(KASAI_AQUARIUM_URL, "葛西臨海水族園", "葛西臨海水族園"))

    event_list = list(merged_events.values())
    print(f"Total unique events collected: {len(event_list)}")

    image_cache = {}
    for idx, ev in enumerate(event_list, start=1):
        ev_url = ev.get("url", "")
        if ev_url not in image_cache:
            image_cache[ev_url] = extract_first_image_url(ev_url)
            time.sleep(0.2)
        ev["image_url"] = image_cache[ev_url]
        if idx % 25 == 0 or idx == len(event_list):
            print(f"Checked images for {idx}/{len(event_list)} events...")

    ics_content = generate_ics(event_list)
    with open(OUTPUT_ICS, "w", encoding="utf-8", newline="") as f:
        f.write(ics_content)
    print(f"Successfully generated {OUTPUT_ICS}")


if __name__ == "__main__":
    main()
