import re
import time
import hashlib
import ssl
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin
import requests
from requests.adapters import HTTPAdapter
from bs4 import BeautifulSoup

# ==========================================
# 連携先URL定義
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

# 除外する純粋なニュース・休館案内・UIラベル・終了済み表記
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
    "お知らせ一覧",
    "ニュース一覧",
    "イベント一覧",
    "チケット購入",
    "前の月",
    "次の月",
    "すべて",
    "終了",
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


class LegacySSLAdapter(HTTPAdapter):
    """Allow legacy renegotiation for older government/library TLS endpoints."""

    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
        try:
            ctx.options |= 0x4  # SSL_OP_LEGACY_SERVER_CONNECT
        except Exception:
            pass
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


SESSION.mount("https://www.library.city.edogawa.tokyo.jp", LegacySSLAdapter())
SESSION.mount("https://library.city.edogawa.tokyo.jp", LegacySSLAdapter())


def resolve_location(text):
    """Find matching facility coordinates (lat, lon, full_name) from event text."""
    for kw in sorted(FACILITY_LOCATIONS.keys(), key=len, reverse=True):
        if kw in text:
            return FACILITY_LOCATIONS[kw]
    return None


def fetch_html(url, params=None, timeout=10, retries=1):
    """Fetch HTML content safely with retry, session, timeout, and encoding fallback."""
    last_err = None
    for attempt in range(retries + 1):
        try:
            resp = SESSION.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            if resp.encoding is None or resp.encoding.lower() == "iso-8859-1":
                resp.encoding = resp.apparent_encoding or "utf-8"
            return resp.text
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(0.4)
    print(f"Warning: Failed to fetch {url}: {last_err}")
    return None


def is_stale_year_url(url):
    """Return True if the URL explicitly points to a past year directory (e.g. /2024/, /2025/)."""
    now_year = datetime.now(JST).year
    for m in re.finditer(r"/(20\d{2})/", url):
        yr = int(m.group(1))
        if yr < now_year:
            return True
    return False


def is_noise_or_news(title, block_text="", url=""):
    """Return True only for pure news posts, closure notices, stale URLs, or navigation labels."""
    t = title.strip()
    if not t or len(t) < 3:
        return True
    if url and is_stale_year_url(url):
        return True
    if any(kw in t for kw in EXCLUDE_KEYWORDS):
        return True
    if re.search(r"20\d{2}[./年]\d{1,2}[./月]\d{1,2}日?\s*ニュース", f"{t} {block_text}"):
        return True
    if re.fullmatch(r"\d{4}[-/年.]\d{1,2}[-/月.]\d{1,2}日?", t) or re.fullmatch(r"\d{4}年", t):
        return True
    return False


def clean_event_title(raw_title):
    """Remove leading publication dates and boilerplate prefixes from event titles."""
    t = raw_title.strip()
    t = re.sub(r"^20\d{2}[./年]\d{1,2}[./月]\d{1,2}日?\s*", "", t)
    t = re.sub(r"^(?:イベント|主催共催公演|共催公演|特別展|企画展)\s*", "", t)
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
    html = fetch_html(page_url, timeout=8, retries=0)
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
    """Scrape all events from Edogawa City official calendar (calendar view and list view)."""
    events = {}

    html = fetch_html(BASE_URL, params={"type": "1", "year": str(year), "month": str(month)})
    if html:
        soup = BeautifulSoup(html, "html.parser")
        a_total = 0
        for td in soup.find_all("td"):
            cell_text = td.get_text(" ", strip=True)
            day_match = re.search(r"(?:^|\s)(\d{1,2})\s*日", cell_text)
            if not day_match:
                continue
            day = int(day_match.group(1))
            try:
                current_date = datetime(year, month, day, tzinfo=JST).date()
            except ValueError:
                continue

            for a_tag in td.find_all("a", href=True):
                a_total += 1
                href = a_tag["href"].strip()
                if not href or href.startswith("#") or "javascript:" in href:
                    continue
                if "calendar.cgi" in href and ("more" in href or "type=" in href):
                    continue
                full_url = urljoin(BASE_URL, href)
                raw_title = a_tag.get_text(" ", strip=True)
                if is_noise_or_news(raw_title, url=full_url):
                    continue
                title = clean_event_title(raw_title)

                parent = a_tag.parent
                cat_span = parent.find("span") if parent else None
                category = cat_span.get_text(strip=True) if cat_span else "区イベント"
                if not category or "申込" in category or len(category) > 15:
                    category = "区イベント"

                if full_url not in events:
                    events[full_url] = {
                        "title": title,
                        "url": full_url,
                        "category": category,
                        "venue": "",
                        "dates": set(),
                        "image_url": extract_image_from_element(parent, BASE_URL),
                    }
                events[full_url]["dates"].add(current_date)
        print(f"    [debug] 区公式({year}-{month:02d}): anchors_in_dated_td={a_total}, parsed={len(events)}")

    list_html = fetch_html(BASE_URL, params={"type": "2", "year": str(year), "month": str(month)})
    if list_html:
        list_soup = BeautifulSoup(list_html, "html.parser")
        for a_tag in list_soup.find_all("a", href=True):
            href = a_tag["href"].strip()
            if not re.search(r"/(event|bunka|kosodate|kenko|sports|kurashi)/.*\.html", href):
                continue
            full_url = urljoin(BASE_URL, href)
            raw_title = a_tag.get_text(" ", strip=True)
            if is_noise_or_news(raw_title, url=full_url):
                continue
            title = clean_event_title(raw_title)
            block = a_tag.find_parent(["li", "tr", "dd", "div", "p"]) or a_tag
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
    """Extract event dates from Japanese text while avoiding leading publication dates or ticket sales dates."""
    now = datetime.now(JST)
    today = now.date()
    base_year = default_year or now.year

    cleaned_text = re.sub(r"^\s*20\d{2}[./年]\d{1,2}[./月]\d{1,2}日?\s*", "", text)
    cleaned_text = re.sub(
        r"(?:発売日|発売開始|申込開始|受付開始|応募開始|販売開始)[：:\s]*(?:20\d{2}[年/\.]?)?\d{1,2}[月/\.]\d{1,2}日?",
        "",
        cleaned_text,
    )

    parsed_dates = set()
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
            if end_dt >= today - timedelta(days=3) and (end_dt - start_dt).days <= 120:
                cur = max(start_dt, today - timedelta(days=3))
                while cur <= end_dt:
                    parsed_dates.add(cur)
                    cur += timedelta(days=1)
        except ValueError:
            pass

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
            if today - timedelta(days=3) <= candidate <= today + timedelta(days=180):
                parsed_dates.add(candidate)
        except ValueError:
            continue
    return parsed_dates


def parse_generic_facility_events(url, category, default_venue="", timeout=10, retries=1):
    """Generic scraper for Edogawa public/cultural/park/museum event pages."""
    html = fetch_html(url, timeout=timeout, retries=retries)
    if not html:
        print(f"    [debug] {category}: html is None")
        return []

    soup = BeautifulSoup(html, "html.parser")
    events = {}
    all_links = soup.find_all("a", href=True)
    dated_candidates = 0
    now = datetime.now(JST)

    for a_tag in all_links:
        href = a_tag["href"].strip()
        if not href or href.startswith("#") or "javascript:" in href or href.startswith("mailto:"):
            continue
        full_url = urljoin(url, href)
        if (
            full_url.rstrip("/") == url.rstrip("/")
            or "/page/" in full_url
            or re.search(r"/news/\d{4}/index\.html$", full_url)
            or re.search(r"/category/", full_url)
            or re.search(r"/tag/", full_url)
            or is_stale_year_url(full_url)
        ):
            continue

        raw_title = a_tag.get_text(" ", strip=True)
        parent = a_tag.find_parent(["article", "tr", "li", "dd", "dl", "div"]) or a_tag
        if is_noise_or_news(raw_title, parent.get_text(" ", strip=True), full_url):
            heading = parent.find(["h2", "h3", "h4", "dt"]) if parent else None
            if heading:
                cand = heading.get_text(" ", strip=True)
                if not is_noise_or_news(cand, parent.get_text(" ", strip=True), full_url):
                    raw_title = cand

        block_text = parent.get_text(" ", strip=True)
        if is_noise_or_news(raw_title, block_text, full_url):
            continue

        title = clean_event_title(raw_title)
        parsed_dates = extract_dates_from_text(f"{raw_title} {block_text}")

        if not parsed_dates:
            ym_match = re.search(r"/(20\d{2})/(\d{1,2})/", full_url)
            if ym_match:
                try:
                    y, m = int(ym_match.group(1)), int(ym_match.group(2))
                    if 1 <= m <= 12 and y >= now.year:
                        fallback_d = datetime(y, m, 1, tzinfo=JST).date()
                        if fallback_d >= now.date() - timedelta(days=3):
                            parsed_dates.add(fallback_d)
                except ValueError:
                    pass

        if not parsed_dates:
            continue
        dated_candidates += 1

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
                "image_url": extract_image_from_element(parent, url),
            }
        events[full_url]["dates"].update(parsed_dates)
        if venue:
            events[full_url]["venue"] = venue

    print(f"    [debug] {category}: html_len={len(html)}, links={len(all_links)}, dated={dated_candidates}, unique={len(events)}")
    return list(events.values())


def parse_library_events():
    """Scrape events from Edogawa City Library (all 12 branches)."""
    html = fetch_html(LIBRARY_EVENT_URL, timeout=10, retries=1)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    events = {}
    now = datetime.now(JST)

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"].strip()
        if not href or href.startswith("#") or "javascript:" in href:
            continue
        full_url = urljoin(LIBRARY_EVENT_URL, href)
        if full_url.rstrip("/") == LIBRARY_EVENT_URL.rstrip("/") or is_stale_year_url(full_url):
            continue
        raw_title = a_tag.get_text(" ", strip=True)
        parent = a_tag.find_parent(["tr", "li", "dd", "dl", "div"]) or a_tag
        block_text = parent.get_text(" ", strip=True)
        if is_noise_or_news(raw_title, block_text, full_url):
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
                "image_url": extract_image_from_element(parent, LIBRARY_EVENT_URL),
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
            page.goto(SPORTS_KINTONE_URL, wait_until="networkidle", timeout=25000)
            page.wait_for_timeout(2500)
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

            if is_noise_or_news(raw_title, text, full_url):
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
                    "image_url": extract_image_from_element(row, SPORTS_KINTONE_URL),
                }
            events[key]["dates"].update(parsed_dates)
    except Exception as e:
        print(f"Warning: Failed to scrape kintone sports events: {e}")

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
    print("=== Starting Edogawa Events Aggregation ===")
    merged_events = {}

    def add_events(source_name, items):
        print(f"  -> {source_name}: {len(items)} events fetched")
        for item in items:
            url = item["url"]
            if url not in merged_events:
                merged_events[url] = item
            else:
                merged_events[url]["dates"].update(item["dates"])
                if item.get("venue") and not merged_events[url].get("venue"):
                    merged_events[url]["venue"] = item["venue"]
                if item.get("image_url") and not merged_events[url].get("image_url"):
                    merged_events[url]["image_url"] = item["image_url"]

    for year, month in get_target_months(months_ahead=3):
        items = parse_edogawa_city_calendar(year, month)
        add_events(f"江戸川区公式 ({year}-{month:02d})", items)
        time.sleep(0.4)

    add_events("江戸川区立図書館", parse_library_events())
    add_events("タワーホール船堀", parse_generic_facility_events(TOWERHALL_EVENT_URL, "船堀タワー", "タワーホール船堀"))
    add_events("江戸川区総合文化センター(予定一覧)", parse_generic_facility_events(BUNKACENTER_EVENT_URL, "総合文化センター", "江戸川区総合文化センター"))
    add_events("江戸川区総合文化センター(カレンダー)", parse_generic_facility_events(BUNKACENTER_CAL_URL, "総合文化センター", "江戸川区総合文化センター"))
    add_events("江戸川区スポーツ", parse_sports_kintone_events())
    add_events("葛西海浜公園", parse_generic_facility_events(KASAI_KAIHIN_PARK_URL, "葛西海浜公園", "葛西海浜公園"))
    add_events("葛西臨海公園", parse_generic_facility_events(KASAI_RINKAI_PARK_URL, "葛西臨海公園", "葛西臨海公園"))
    # 地下鉄博物館・自然動物園・ポニーランドは外部クラウドからの接続タイムアウトが起きやすいため短時間スキップで全体速度を優先
    add_events("地下鉄博物館", parse_generic_facility_events(CHIKAHAKU_EVENT_URL, "地下鉄博物館", "地下鉄博物館", timeout=5, retries=0))
    add_events("江戸川区自然動物園", parse_generic_facility_events(EDOGAWA_ZOO_EVENT_URL, "自然動物園", "江戸川区自然動物園", timeout=5, retries=0))
    add_events("ポニーランド", parse_generic_facility_events(PONYLAND_EVENT_URL, "ポニーランド", "篠崎ポニーランド", timeout=5, retries=0))
    add_events("魔法の文学館", parse_generic_facility_events(KIKIS_MUSEUM_EVENT_URL, "魔法の文学館", "魔法の文学館"))
    add_events("葛西臨海水族園", parse_generic_facility_events(KASAI_AQUARIUM_URL, "葛西臨海水族園", "葛西臨海水族園"))

    event_list = list(merged_events.values())
    print(f"Total unique events merged: {len(event_list)}")

    detail_checked = 0
    for ev in event_list:
        if ev.get("image_url"):
            continue
        if detail_checked >= 80:
            break
        ev["image_url"] = extract_first_image_from_page(ev["url"])
        detail_checked += 1
        time.sleep(0.1)

    ics_content = generate_ics(event_list)
    with open(OUTPUT_ICS, "w", encoding="utf-8", newline="") as f:
        f.write(ics_content)
    print(f"Successfully generated {OUTPUT_ICS} with {len(event_list)} events.")


if __name__ == "__main__":
    main()
