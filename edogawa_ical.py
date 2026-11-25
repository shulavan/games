import re
import time
import hashlib
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

# ==========================================
# 連携先URL定義（全施設再検証済み）
# ==========================================
BASE_URL = "https://www.city.edogawa.tokyo.jp/cgi-bin/event_cal_multi/calendar.cgi"
LIBRARY_EVENT_URL = "https://www.library.city.edogawa.tokyo.jp/toshow/event/"
TOWERHALL_EVENT_URL = "https://www.towerhall.jp/events/list/"
BUNKACENTER_EVENT_URL = "https://edogawa-bunkacenter.jp/calendar-host-fiscal/"
BUNKACENTER_CAL_URL = "https://edogawa-bunkacenter.jp/calendar/"
SPORTS_KINTONE_URL = "https://2f01466f.viewer.kintoneapp.com/public/2288685cd0240ea5379b822175def1d520d606ea6a20c595b5c7602fc6c29368"
KASAI_KAIHIN_PARK_URL = "https://kasaikaihinpark.com/event/"
KASAI_RINKAI_PARK_URL = "https://www.tokyo-park.or.jp/park/kasairinkai/news/index.html"
CHIKAHAKU_EVENT_URL = "https://www.chikahaku.jp/event/"
EDOGAWA_ZOO_EVENT_URL = "https://www.edogawa-kankyozaidan.jp/zoo/event/"
PONYLAND_EVENT_URL = "https://www.edogawa-kankyozaidan.jp/ponyland/event/"
KIKIS_MUSEUM_EVENT_URL = "https://kikismuseum.jp/category/event/"
KASAI_AQUARIUM_URL = "https://www.tokyo-zoo.net/zoo/kasai/"

OUTPUT_ICS = "edogawa_events.ics"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
JST = timezone(timedelta(hours=9), name="JST")

# 除外する純粋なニュース・休館案内・UIラベル（イベント名に含まれる「休館中特別公演」等は除外しない）
EXCLUDE_KEYWORDS = (
    "【重要】",
    "休館日のお知らせ",
    "臨時休館のお知らせ",
    "休園します",
    "一時閉鎖のお知らせ",
    "メンテナンス作業のお知らせ",
    "機関誌",
    "どうぶつと動物園",
    "SEA LIFE NEWS",
    "鳥類園ニュースレター",
    "リスト表示",
    "詳細を見る",
    "もっと見る",
    "掲載申込書",
    "サイトリニューアル",
    "正門の位置が変わります",
    "休館日",
    "休園日",
    "開催日",
)

# 江戸川区内主要施設・全12図書館・公園・文化施設の緯度経度マッピング
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
    "一之江抹香亭": (35.686124, 139.882514, "一之江抹香亭（東京都江戸川区一之江5-13-16）"),
    "新川さくら館": (35.668124, 139.878912, "新川さくら館（東京都江戸川区船堀7-15-12）"),
}

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})


def resolve_location(text):
    """Find matching facility coordinates (lat, lon, full_name) from event text."""
    for kw in sorted(FACILITY_LOCATIONS.keys(), key=len, reverse=True):
        if kw in text:
            return FACILITY_LOCATIONS[kw]
    return None


def fetch_html(url, params=None):
    """Fetch HTML content safely with session, timeout, and encoding fallback."""
    try:
        resp = SESSION.get(url, params=params, timeout=25)
        resp.raise_for_status()
        if resp.encoding is None or resp.encoding.lower() == "iso-8859-1":
            resp.encoding = resp.apparent_encoding or "utf-8"
        return resp.text
    except Exception as e:
        print(f"Warning: Failed to fetch {url}: {e}")
        return None


def is_noise_or_news(title, block_text=""):
    """Return True only for pure news posts, closure notices, or navigation labels."""
    t = title.strip()
    if not t:
        return True
    if any(kw in t for kw in EXCLUDE_KEYWORDS):
        return True
    # 公開日 + 「ニュース」パターンの除外 (例: 2026.11.14 ニュース ...)
    if re.search(r"20\d{2}[./年]\d{1,2}[./月]\d{1,2}日?\s*ニュース", f"{t} {block_text}"):
        return True
    # タイトルが日付・年号のみの場合 (例: 2027-01-24, 2026年)
    if re.fullmatch(r"\d{4}[-/年.]\d{1,2}[-/月.]\d{1,2}日?", t) or re.fullmatch(r"\d{4}年", t):
        return True
    return False


def clean_event_title(raw_title):
    """Remove leading publication dates and boilerplate prefixes from event titles."""
    t = raw_title.strip()
    # 先頭の公開日表記を削除 (例: "2026.11.21 ", "2026/11/21 ", "2026年11月21日 ")
    t = re.sub(r"^20\d{2}[./年]\d{1,2}[./月]\d{1,2}日?\s*", "", t)
    # 先頭の定型ラベルを削除 (例: "イベント ", "主催共催公演 ", "特別展")
    t = re.sub(r"^(?:イベント|主催共催公演|共催公演|特別展|企画展)\s*", "", t)
    # 末尾の「申込要(ONLINE)」「申込要」のくっつきをスペースで整形
    t = re.sub(r"申込要(?:\(ONLINE\))?$", "", t).strip()
    return t or raw_title.strip()


def extract_image_from_element(element, base_url):
    """Extract a valid thumbnail image URL directly from a list item HTML element if present."""
    if not element:
        return None
    for img in element.find_all("img", src=True):
        src = img["src"].strip()
        if not src or src.startswith("data:"):
            continue
        full_img_url = urljoin(base_url, src)
        lower_url = full_img_url.lower()
        if any(skip in lower_url for skip in ("logo", "icon", "btn", "spacer", "loading", "arrow", "sns", "ico_", "mark")):
            continue
        if re.search(r"\.(jpg|jpeg|png|webp|gif)(\?|$)", lower_url):
            return full_img_url
    return None


def extract_first_image_from_page(page_url):
    """Fetch an event detail page and return the first valid image URL if found."""
    if not page_url or page_url.startswith(SPORTS_KINTONE_URL):
        return None
    html = fetch_html(page_url)
    if not html:
        return None
    try:
        soup = BeautifulSoup(html, "html.parser")
        og_img = soup.find("meta", property="og:image")
        if og_img and og_img.get("content"):
            candidate = urljoin(page_url, og_img["content"].strip())
            if re.match(r"^https?://", candidate, re.I) and "noimage" not in candidate.lower():
                return candidate

        main_area = soup.find(["main", "article"]) or soup.body or soup
        for img in main_area.find_all("img", src=True):
            src = img["src"].strip()
            if not src or src.startswith("data:"):
                continue
            full_img_url = urljoin(page_url, src)
            lower_url = full_img_url.lower()
            if any(skip in lower_url for skip in ("logo", "icon", "btn", "spacer", "loading", "arrow", "sns", "ico_", "mark", "qr")):
                continue
            if re.search(r"\.(jpg|jpeg|png|webp|gif)(\?|$)", lower_url):
                return full_img_url
    except Exception:
        pass
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


def parse_edogawa_city_calendar(year, month):
    """Scrape all events from Edogawa City official calendar (both calendar view and list view)."""
    events = {}

    # 1. カレンダー形式 (type=1) から日別イベントを取得
    html = fetch_html(BASE_URL, params={"type": "1", "year": str(year), "month": str(month)})
    if html:
        soup = BeautifulSoup(html, "html.parser")
        for td in soup.find_all("td"):
            # セル内の先頭付近にある日付表示 (例: "1日", "23日勤労感謝の日") を探す
            first_text = td.get_text(" ", strip=True)
            day_match = re.match(r"^(\d{1,2})日", first_text)
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
                href = a_tag["href"]
                if "calendar.cgi" in href and "more" in href:
                    continue
                full_url = urljoin(BASE_URL, href)
                raw_title = a_tag.get_text(" ", strip=True)
                if not raw_title or is_noise_or_news(raw_title):
                    continue
                title = clean_event_title(raw_title)

                cat_span = li.find("span")
                category = cat_span.get_text(strip=True) if cat_span else "区イベント"
                if not category or "申込" in category:
                    category = "区イベント"

                if full_url not in events:
                    events[full_url] = {
                        "title": title,
                        "url": full_url,
                        "category": category,
                        "venue": "",
                        "dates": set(),
                        "image_url": extract_image_from_element(li, BASE_URL),
                    }
                events[full_url]["dates"].add(current_date)

    # 2. 一覧形式 (type=2) からも補完取得（カレンダーセルで「もっと見る」に隠れた分も回収）
    list_html = fetch_html(BASE_URL, params={"type": "2", "year": str(year), "month": str(month)})
    if list_html:
        soup2 = BeautifulSoup(list_html, "html.parser")
        for block in soup2.find_all(["tr", "li", "div"]):
            a_tag = block.find("a", href=True)
            if not a_tag:
                continue
            href = a_tag["href"]
            # 江戸川区イベント詳細ページURLパターン (/event/ や .html) のみ対象
            if not re.search(r"/(event|bunka|kosodate|kenko|sports|kurashi)/.*\.html", href):
                continue
            full_url = urljoin(BASE_URL, href)
            raw_title = a_tag.get_text(" ", strip=True)
            if not raw_title or len(raw_title) < 3 or is_noise_or_news(raw_title):
                continue
            title = clean_event_title(raw_title)
            block_text = block.get_text(" ", strip=True)
            parsed_dates = extract_dates_from_text(block_text, default_year=year)
            if not parsed_dates:
                continue

            if full_url not in events:
                events[full_url] = {
                    "title": title,
                    "url": full_url,
                    "category": "区イベント",
                    "venue": "",
                    "dates": set(),
                    "image_url": extract_image_from_element(block, BASE_URL),
                }
            events[full_url]["dates"].update(parsed_dates)

    return list(events.values())


def extract_dates_from_text(text, default_year=None):
    """Extract event dates from Japanese text while avoiding leading publication dates."""
    now = datetime.now(JST)
    today = now.date()
    base_year = default_year or now.year

    # 記事先頭の公開日 (例: "2026.11.13 " や "2026/11/10 ") を除去してから開催日を抽出
    cleaned_text = re.sub(r"^\s*20\d{2}[./年]\d{1,2}[./月]\d{1,2}日?\s*", "", text)

    parsed_dates = set()
    # 範囲表記 (例: 2026年12月15日～2027年3月14日 / 12/15～12/17) の展開にも対応
    range_matches = re.findall(
        r"(?:(\d{4})[年/\.])?(\d{1,2})[月/\.](\d{1,2})日?(?:\s*\([月火水木金土日祝・]+\))?\s*[～〜\-－]\s*(?:(\d{4})[年/\.])?(\d{1,2})[月/\.](\d{1,2})日?",
        cleaned_text,
    )
    for y1, m1, d1, y2, m2, d2 in range_matches:
        try:
            sy = int(y1) if y1 else (base_year if int(m1) >= now.month - 2 else base_year + 1)
            start_dt = datetime(sy, int(m1), int(d1), tzinfo=JST).date()
            ey = int(y2) if y2 else (sy if int(m2) >= int(m1) else sy + 1)
            end_dt = datetime(ey, int(m2), int(d2), tzinfo=JST).date()
            if end_dt >= today - timedelta(days=14) and (end_dt - start_dt).days <= 120:
                cur = max(start_dt, today - timedelta(days=7))
                while cur <= end_dt:
                    parsed_dates.add(cur)
                    cur += timedelta(days=1)
        except ValueError:
            pass

    # 単発日付の抽出
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
                y = base_year if m >= now.month - 2 else base_year + 1
            candidate = datetime(y, m, d, tzinfo=JST).date()
            if today - timedelta(days=14) <= candidate <= today + timedelta(days=180):
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

    containers = soup.find_all(["article", "tr", "li", "dd", "div"], class_=re.compile(r"event|post|news|item|list|card|tribe", re.I))
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
        # 一覧ページ自身や年別インデックス・ページネーションへのリンクを除外
        if (
            full_url.rstrip("/") == url.rstrip("/")
            or "/page/" in full_url
            or re.search(r"/news/\d{4}/index\.html$", full_url)
        ):
            continue

        raw_title = link.get_text(" ", strip=True)
        if not raw_title or len(raw_title) < 3 or raw_title in ("詳細はこちら", "もっと見る", "一覧へ", "詳細を見る", "チケット購入", "開催日"):
            heading = block.find(["h2", "h3", "h4", "dt", "p"])
            if heading:
                candidate_title = heading.get_text(" ", strip=True)
                if candidate_title not in ("開催日", "2026年", "2027年"):
                    raw_title = candidate_title
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
                "image_url": extract_image_from_element(block, url),
            }
        events[full_url]["dates"].update(parsed_dates)
        if venue:
            events[full_url]["venue"] = venue

    return list(events.values())


def parse_library_events():
    """Scrape events from Edogawa City Library (all 12 branches)."""
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
        parsed_dates = extract_dates_from_text(block_text, default_year=now.year)
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
                "image_url": extract_image_from_element(block, LIBRARY_EVENT_URL),
            }
        events[full_url]["dates"].update(parsed_dates)
        if venue and not events[full_url]["venue"]:
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
     
