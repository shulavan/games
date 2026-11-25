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
# 優先順位1: 江戸川区役所・江戸川区公式・区内公共施設（最優先・最前面）
# ==========================================
BASE_URL = "https://www.city.edogawa.tokyo.jp/cgi-bin/event_cal_multi/calendar.cgi"
EDOGAWA_TOP_URL = "https://www.city.edogawa.tokyo.jp/"
EDOGAWA_EVENT_TOP_URL = "https://www.city.edogawa.tokyo.jp/event/"
LIBRARY_EVENT_URL = "https://www.library.city.edogawa.tokyo.jp/toshow/event/"
TOWERHALL_EVENT_URL = "https://www.towerhall.jp/events/list/"
BUNKACENTER_EVENT_URL = "https://edogawa-bunkacenter.jp/calendar-host-fiscal/"
BUNKACENTER_CAL_URL = "https://edogawa-bunkacenter.jp/calendar/"
SPORTS_KINTONE_URL = "https://2f01466f.viewer.kintoneapp.com/public/2288685cd0240ea5379b822175def1d520d606ea6a20c595b5c7602fc6c29368"
KASAI_KAIHIN_PARK_URL = "https://kasaikaihinpark.com/event/"
KASAI_RINKAI_PARK_URL = "https://www.tokyo-park.or.jp/park/kasairinkai/news/index.html"
KIKIS_MUSEUM_EVENT_URL = "https://kikismuseum.jp/category/event/"
KASAI_AQUARIUM_URL = "https://www.tokyo-zoo.net/zoo/kasai/"
SHINOZAKI_PARK_URL = "https://www.tokyo-park.or.jp/park/shinozaki/news/index.html"
OJIMA_KOMATSUGAWA_PARK_URL = "https://www.tokyo-park.or.jp/park/ojima-komatsugawa/news/index.html"
EDOGAWA_ATHLETICS_URL = "https://www.kubota-spears.com/lp_edogawa-athletics/schedule/"
EDOGAWA_BASEBALL_URL = "https://www.edogawa-3field.jp/baseball/taikaiguid/"
EDOGAWA_MYPL_EVENT_URL = "https://edogawa.mypl.net/event/"

# ==========================================
# 優先順位2: 友好都市物産展・フリーマーケット・商業施設・学校
# ==========================================
TSURUOKA_BUSSAN_URL = "https://www.city.tsuruoka.lg.jp/kanko/kankou-sonota/tokyo01.html"
TRX_FLEAMARKET_URL = "https://trx.jp/"
ARIO_KASAI_EVENT_URL = "https://kasai.ario.jp/event/"

SCHOOL_EVENT_SOURCES = [
    ("都立江戸川高校", "https://www.metro.ed.jp/edogawa-h/news/index.html", "都立江戸川高校"),
    ("都立小松川高校", "https://www.metro.ed.jp/komatsugawa-h/news/index.html", "都立小松川高校"),
    ("都立小岩高校", "https://www.metro.ed.jp/koiwa-h/news/index.html", "都立小岩高校"),
    ("都立葛西南高校", "https://www.metro.ed.jp/kasaiminami-h/news/index.html", "都立葛西南高校"),
    ("都立紅葉川高校", "https://www.metro.ed.jp/momijigawa-h/news/index.html", "都立紅葉川高校"),
    ("都立篠崎高校", "https://www.metro.ed.jp/shinozaki-h/news/index.html", "都立篠崎高校"),
]

OUTPUT_ICS = "edogawa_events.ics"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
JST = timezone(timedelta(hours=9), name="JST")

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
    "詳細検索へ",
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
    "チケット購入方法",
    "主催共催公演",
    "前の月",
    "次の月",
    "すべて",
    "一覧はこちら",
    "お知らせはこちら",
    "アーカイブ",
    "カレンダー",
    "営業時間",
    "フロアガイド",
    "ショップガイド",
    "アクセス",
    "お問い合わせ",
    "プライバシーポリシー",
    "座席表",
    "地図（こちら）",
    "連盟",
    "ホームページ",
)

MYPL_EVENT_KEYWORDS = (
    "開催",
    "イベント",
    "フェス",
    "フェスティバル",
    "マルシェ",
    "体験",
    "講座",
    "教室",
    "セミナー",
    "相談会",
    "説明会",
    "見学会",
    "見学のご案内",
    "ライブ",
    "コンサート",
    "公演",
    "舞台",
    "展示",
    "展覧会",
    "まつり",
    "祭り",
    "お餅つき",
    "餅つき",
    "出展",
    "出店",
    "オープン",
    "キャンペーン",
    "抽選会",
    "ワークショップ",
    "フリマ",
    "フリーマーケット",
)

OUT_OF_DISTRICT_KEYWORDS = (
    "埼玉県",
    "三郷市",
    "千葉県",
    "市川市",
    "浦安市",
    "船橋市",
    "松戸市",
    "横浜市",
    "川崎市",
)

PAST_REPORT_PATTERNS = (
    r"開催しました[!！。]?$",
    r"実施しました[!！。]?$",
    r"行われました[!！。]?$",
    r"終了しました[!！。]?$",
    r"参加しました[!！。]?$",
    r"を行いました[!！。]?$",
)

SCHOOL_PUBLIC_KEYWORDS = (
    "文化祭",
    "学園祭",
    "公開",
    "説明会",
    "体験",
    "公演",
    "ライブ",
    "コンサート",
    "展示",
    "フェスタ",
    "まつり",
    "バザー",
)

FACILITY_LOCATIONS = {
    "江戸川区役所": (35.706612, 139.868324, "江戸川区役所（東京都江戸川区中央1-4-1）"),
    "区役所": (35.706612, 139.868324, "江戸川区役所（東京都江戸川区中央1-4-1）"),
    "西葛西駅南口広場": (35.664182, 139.859214, "西葛西駅 南口広場（東京都江戸川区西葛西6-14）"),
    "西葛西駅 南口広場": (35.664182, 139.859214, "西葛西駅 南口広場（東京都江戸川区西葛西6-14）"),
    "西葛西駅南口": (35.664182, 139.859214, "西葛西駅 南口広場（東京都江戸川区西葛西6-14）"),
    "船堀駅北口広場": (35.684412, 139.864518, "船堀駅 北口広場（東京都江戸川区船堀3-5）"),
    "船堀駅 北口広場": (35.684412, 139.864518, "船堀駅 北口広場（東京都江戸川区船堀3-5）"),
    "船堀駅北口": (35.684412, 139.864518, "船堀駅 北口広場（東京都江戸川区船堀3-5）"),
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
    "自然動物園": (35.671312, 139.858724, "江戸川区自然動物園（東京都江戸川区北葛西3-2-1 行船公園内）"),
    "行船公園": (35.671312, 139.858724, "行船公園・平成庭園（東京都江戸川区北葛西3-2-1）"),
    "平成庭園": (35.671312, 139.858724, "行船公園・平成庭園（東京都江戸川区北葛西3-2-1）"),
    "篠崎ポニーランド": (35.714512, 139.910214, "篠崎ポニーランド（東京都江戸川区篠崎町3-12-17）"),
    "なぎさポニーランド": (35.647812, 139.873912, "なぎさポニーランド（東京都江戸川区南葛西7-3 なぎさ公園内）"),
    "葛西臨海水族園": (35.640112, 139.862214, "東京都葛西臨海水族園（東京都江戸川区臨海町6-2-3）"),
    "葛西臨海公園": (35.642514, 139.859812, "葛西臨海公園（東京都江戸川区臨海町6-2-1）"),
    "葛西海浜公園": (35.637812, 139.856214, "葛西海浜公園（東京都江戸川区臨海町6-2-4）"),
    "篠崎公園": (35.713812, 139.903514, "都立篠崎公園（東京都江戸川区上篠崎1-25-1）"),
    "大島小松川公園": (35.693215, 139.847512, "都立大島小松川公園（東京都江戸川区小松川1-7）"),
    "総合レクリエーション公園": (35.661812, 139.862514, "総合レクリエーション公園（東京都江戸川区南葛西・西葛西）"),
    "スポーツランド": (35.718512, 139.903214, "江戸川区スポーツランド（東京都江戸川区東篠崎1-8-1）"),
    "スポーツセンター": (35.668912, 139.862124, "江戸川区スポーツセンター（東京都江戸川区西葛西4-2-20）"),
    "江戸川陸上競技場": (35.666812, 139.874218, "江戸川区陸上競技場（東京都江戸川区清新町2-1-1）"),
    "陸上競技場": (35.666812, 139.874218, "江戸川区陸上競技場（東京都江戸川区清新町2-1-1）"),
    "江戸川球場": (35.719214, 139.898412, "江戸川区球場（東京都江戸川区東篠崎1-9-1）"),
    "球場": (35.719214, 139.898412, "江戸川区球場（東京都江戸川区東篠崎1-9-1）"),
    "アリオ葛西": (35.656214, 139.872518, "アリオ葛西（東京都江戸川区東葛西9-3-3）"),
    "都立江戸川高校": (35.707214, 139.863512, "東京都立江戸川高等学校（東京都江戸川区松島2-38-1）"),
    "都立小松川高校": (35.697512, 139.852114, "東京都立小松川高等学校（東京都江戸川区平井1-27-10）"),
    "都立小岩高校": (35.724812, 139.889514, "東京都立小岩高等学校（東京都江戸川区本一色3-10-1）"),
    "都立葛西南高校": (35.652514, 139.863218, "東京都立葛西南高等学校（東京都江戸川区南葛西1-11-1）"),
    "都立紅葉川高校": (35.657812, 139.875214, "東京都立紅葉川高等学校（東京都江戸川区臨海町2-1-1）"),
    "都立篠崎高校": (35.716214, 139.908512, "東京都立篠崎高等学校（東京都江戸川区東篠崎1-10-1）"),
}

CHAR_NORMALIZE_MAP = {
    ord("０"): "0",
    ord("１"): "1",
    ord("２"): "2",
    ord("３"): "3",
    ord("４"): "4",
    ord("５"): "5",
    ord("６"): "6",
    ord("７"): "7",
    ord("８"): "8",
    ord("９"): "9",
    ord("／"): "/",
    ord("．"): ".",
    ord("～"): "~",
    ord("〜"): "~",
    ord("－"): "-",
    ord("−"): "-",
}

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})


class LegacySSLAdapter(HTTPAdapter):
    """Allow legacy renegotiation for older government/library TLS endpoints."""

    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
        try:
            ctx.options |= 0x4
        except Exception:
            pass
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


SESSION.mount("https://www.library.city.edogawa.tokyo.jp", LegacySSLAdapter())
SESSION.mount("https://library.city.edogawa.tokyo.jp", LegacySSLAdapter())


def normalize_japanese_text(text):
    """Convert full-width ASCII digits and slashes/tildes to half-width for reliable date parsing."""
    if not text:
        return ""
    return text.translate(CHAR_NORMALIZE_MAP)


def is_stale_year_url(url):
    """Return True if a URL explicitly points to an old year or past fiscal year directory."""
    if not url:
        return False
    now_year = datetime.now(JST).year
    for m in re.finditer(r"/(?:news|event|calendar|topics|info)/(20\d{2})/", url):
        yr = int(m.group(1))
        if yr < now_year:
            return True
    if re.search(r"/(?:h\d{1,2}|s\d{1,2}|r[1-6])_", url.lower()):
        return True
    return False


def resolve_location(text):
    """Find matching facility coordinates (lat, lon, full_name) from event text."""
    for kw in sorted(FACILITY_LOCATIONS.keys(), key=len, reverse=True):
        if kw in text:
            return FACILITY_LOCATIONS[kw]
    return None


def fetch_html(url, params=None, timeout=10, retries=1):
    """Fetch HTML content safely with retry, session, timeout, and encoding fallback."""
    last_err = None
    extra_headers = {}
    if "city.edogawa.tokyo.jp" in url:
        extra_headers = {
            "Referer": EDOGAWA_TOP_URL,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
            "Upgrade-Insecure-Requests": "1",
        }
    for attempt in range(retries + 1):
        try:
            resp = SESSION.get(url, params=params, headers=extra_headers, timeout=timeout)
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


def discover_fallback_url(base_url, path_hints=("event", "news", "info", "calendar")):
    """Discover alternative event/news URLs from a base page when a path changes or returns 403."""
    html = fetch_html(base_url, timeout=10, retries=1)
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"].strip()
        label = a_tag.get_text(" ", strip=True)
        if any(h in href.lower() for h in path_hints) or any(
            k in label for k in ("イベント", "催し", "お知らせ", "行事", "文化", "子育て", "健康")
        ):
            cand = urljoin(base_url, href)
            if cand.rstrip("/") != base_url.rstrip("/") and "city.edogawa.tokyo.jp" in cand:
                candidates.append(cand)
    for cand in candidates:
        if "/event/" in cand or "event" in cand.lower():
            return cand
    return candidates[0] if candidates else None

def is_past_report_text(text):
    """Return True if the text describes an already completed event in past tense."""
    t = text.strip()
    return any(re.search(pat, t) for pat in PAST_REPORT_PATTERNS)


def is_mypl_event_post(title, block_text=""):
    """Return True only when a MyPl entry looks like a genuine event/催し with concrete name or date."""
    t = title.strip()
    if not t or len(t) < 8:
        return False
    if t.endswith("…") or t.endswith("..."):
        return False
    if re.search(r"^(?:【江戸川区】)?\d{1,2}月\d{1,2}日[「『\"【]?\s*$", t):
        return False
    if re.search(r"^\d+(?:\.\d+)+歳のおともだちへ", t):
        return False
    norm_t = normalize_japanese_text(t)
    has_date_in_title = bool(re.search(r"(?:\d{1,2}月\d{1,2}日|\d{1,2}/\d{1,2})", norm_t))
    has_event_kw_in_title = any(kw in t for kw in MYPL_EVENT_KEYWORDS)
    return has_date_in_title or has_event_kw_in_title


def is_noise_or_news(title, block_text="", url="", strict_mypl=False):
    """Return True for pure news posts, closure notices, stale URLs, out-of-district events, or navigation labels."""
    t = title.strip()
    if not t or len(t) < 3:
        return True
    if is_past_report_text(t):
        return True
    if any(ok in t for ok in OUT_OF_DISTRICT_KEYWORDS):
        return True
    if url and (
        is_stale_year_url(url)
        or "/archive/" in url
        or "/tickets" in url
        or "/access" in url
        or "/event/list" in url
        or url.rstrip("/").endswith("/news/index.html")
    ):
        return True
    if any(kw in t for kw in EXCLUDE_KEYWORDS):
        return True
    if re.search(r"20\d{2}[./年]\d{1,2}[./月]\d{1,2}日?\s*ニュース", f"{t} {block_text}"):
        return True
    if re.fullmatch(r"\d{4}[-/年.]\d{1,2}[-/月.]\d{1,2}日?", t) or re.fullmatch(r"\d{4}年", t):
        return True
    if strict_mypl and not is_mypl_event_post(t, block_text):
        return True
    return False


def clean_event_title(raw_title):
    """Remove leading publication dates and boilerplate prefixes from event titles."""
    t = raw_title.strip()
    t = re.sub(r"^20\d{2}[./年]\d{1,2}[./月]\d{1,2}日?\s*", "", t)
    t = re.sub(r"^(?:イベント|主催共催公演|共催公演|特別展|企画展|NEW)\s*", "", t)
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
        if any(skip in lower_url for skip in ("logo", "icon", "btn", "spacer", "loading", "arrow", "sns", "ico_", "mark", "lazy-load", "1x1")):
            continue
        if re.search(r"\.(jpg|jpeg|png|webp|gif)(\?|$)", lower_url):
            return full_img_url
    return None


def extract_first_image_from_page(page_url):
    """Fetch an event detail page and return the first valid image URL if found."""
    if not page_url or page_url.startswith(SPORTS_KINTONE_URL) or "mypl.net/shop/" in page_url:
        return None
    html = fetch_html(page_url, timeout=8, retries=0)
    if not html:
        return None
    try:
        soup = BeautifulSoup(html, "html.parser")
        og_img = soup.find("meta", property="og:image")
        if og_img and og_img.get("content"):
            candidate = urljoin(page_url, og_img["content"].strip())
            lower_cand = candidate.lower()
            if (
                re.match(r"^https?://", candidate, re.I)
                and not any(skip in lower_cand for skip in ("noimage", "logo", "sns", "icon", "dummy"))
            ):
                return candidate

        main_area = soup.find(["main", "article"]) or soup.body or soup
        for img in main_area.find_all("img", src=True):
            src = img["src"].strip()
            if not src or src.startswith("data:"):
                continue
            full_img_url = urljoin(page_url, src)
            lower_url = full_img_url.lower()
            if any(skip in lower_url for skip in ("logo", "icon", "btn", "spacer", "loading", "arrow", "sns", "ico_", "mark", "qr", "dummy", "lazy-load", "1x1")):
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


def extract_dates_from_text(text, default_year=None):
    """Extract event dates from Japanese text while avoiding leading publication dates or ticket sales dates."""
    now = datetime.now(JST)
    today = now.date()
    base_year = default_year or now.year

    norm_text = normalize_japanese_text(text)
    cleaned_text = re.sub(r"^\s*20\d{2}[./年]\d{1,2}[./月]\d{1,2}日?\s*", "", norm_text)
    cleaned_text = re.sub(
        r"(?:発売日|発売開始|申込開始|受付開始|応募開始|販売開始)[：:\s]*(?:20\d{2}[年/\.]?)?\d{1,2}[月/\.]\d{1,2}日?",
        "",
        cleaned_text,
    )

    parsed_dates = set()
    range_matches = re.findall(
        r"(?:(\d{4})[年/\.])?(\d{1,2})[月/\.](\d{1,2})日?(?:\s*\([月火水木金土日祝・]+\))?\s*[~～\-]+\s*(?:(\d{4})[年/\.])?(\d{1,2})[月/\.](\d{1,2})日?",
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


def parse_edogawa_city_calendar(year, month):
    """Scrape all events from Edogawa City official calendar (calendar view and list view)."""
    events = {}

    html = fetch_html(BASE_URL, params={"type": "1", "year": str(year), "month": str(month)})
    if html:
        soup = BeautifulSoup(html, "html.parser")
        a_total = 0
        for td in soup.find_all("td"):
            cell_text = td.get_text(" ", strip=True)
            norm_cell = normalize_japanese_text(cell_text)
            day_match = re.search(
                r"(?:^|\s|\[|【|\()(\d{1,2})\s*(?:日|\([月火水木金土日祝・]+\))",
                norm_cell,
            )
            if not day_match:
                day_match = re.match(r"^(\d{1,2})(?:\D|$)", norm_cell.strip())
            if not day_match:
                header_like = td.find(["span", "div", "p", "a", "strong", "em"])
                if header_like:
                    h_text = normalize_japanese_text(header_like.get_text(" ", strip=True))
                    day_match = re.match(r"^(\d{1,2})(?:日|\([月火水木金土日祝・]+\))?$", h_text.strip())
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
                category = cat_span.get_text(strip=True) if cat_span else "区役所・区公式"
                if not category or "申込" in category or len(category) > 15:
                    category = "区役所・区公式"

                venue = "江戸川区役所"
                for kw in sorted(FACILITY_LOCATIONS.keys(), key=len, reverse=True):
                    if kw != "江戸川区役所" and kw != "区役所" and kw in f"{title} {cell_text}":
                        venue = kw
                        break

                if full_url not in events:
                    events[full_url] = {
                        "title": title,
                        "url": full_url,
                        "category": category,
                        "venue": venue,
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
            if not re.search(r"/(event|bunka|kosodate|kenko|sports|kurashi|sangyo)/.*\.html", href):
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

            venue = "江戸川区役所"
            for kw in sorted(FACILITY_LOCATIONS.keys(), key=len, reverse=True):
                if kw != "江戸川区役所" and kw != "区役所" and kw in f"{title} {block_text}":
                    venue = kw
                    break

            if full_url not in events:
                events[full_url] = {
                    "title": title,
                    "url": full_url,
                    "category": "区役所・区公式",
                    "venue": venue,
                    "dates": set(),
                    "image_url": extract_image_from_element(block, BASE_URL),
                }
            events[full_url]["dates"].update(parsed_dates)

    return list(events.values())


def parse_tsuruoka_bussan_events():
    """Scrape Tsuruoka City (sister city) tourism & product fair dates in Edogawa City."""
    html = fetch_html(TSURUOKA_BUSSAN_URL, timeout=12, retries=1)
    if not html:
        print("    [debug] 鶴岡物産展: html is None")
        return []

    soup = BeautifulSoup(html, "html.parser")
    main_area = soup.find(["main", "article"]) or soup.find("div", id=re.compile(r"content|main", re.I)) or soup.body or soup
    h1 = soup.find("h1")
    base_title = h1.get_text(" ", strip=True) if h1 else "山形県鶴岡市 観光物産展"
    base_title = re.sub(r"\s+", " ", base_title).strip()

    image_url = extract_image_from_element(main_area, TSURUOKA_BUSSAN_URL)
    events = {}

    for block in main_area.find_all(["tr", "li", "p", "div", "section"]):
        text = block.get_text(" ", strip=True)
        if not text or len(text) > 500:
            continue
        if not any(v in text for v in ("西葛西", "船堀")):
            continue

        parsed_dates = extract_dates_from_text(text)
        if not parsed_dates:
            continue

        if "西葛西" in text and "船堀" not in text:
            venue = "西葛西駅 南口広場"
        elif "船堀" in text and "西葛西" not in text:
            venue = "船堀駅 北口広場"
        else:
            venue = "西葛西駅 南口広場"

        for d in sorted(parsed_dates):
            key = f"{TSURUOKA_BUSSAN_URL}#{d.strftime('%Y%m%d')}_{venue}"
            if key not in events:
                events[key] = {
                    "title": f"{base_title}（{venue}）",
                    "url": TSURUOKA_BUSSAN_URL,
                    "category": "物産展・友好都市",
                    "venue": venue,
                    "dates": {d},
                    "image_url": image_url,
                }

    if not events:
        full_text = main_area.get_text(" ", strip=True)
        parsed_dates = extract_dates_from_text(full_text)
        if parsed_dates:
            venue = "西葛西駅 南口広場" if "西葛西" in full_text else "船堀駅 北口広場"
            events[TSURUOKA_BUSSAN_URL] = {
                "title": f"{base_title}（{venue}）",
                "url": TSURUOKA_BUSSAN_URL,
                "category": "物産展・友好都市",
                "venue": venue,
                "dates": parsed_dates,
                "image_url": image_url,
            }

    print(f"    [debug] 鶴岡物産展: parsed={len(events)}")
    return list(events.values())


def parse_generic_facility_events(
    url,
    category,
    default_venue="",
    timeout=10,
    retries=1,
    require_keywords=None,
    area_keywords=None,
    fallback_base_url=None,
    strict_mypl=False,
):
    """Generic scraper for Edogawa public/cultural/park/commercial/school event pages with URL fallback."""
    html = fetch_html(url, timeout=timeout, retries=retries)
    if not html and fallback_base_url:
        alt_url = discover_fallback_url(fallback_base_url)
        if alt_url:
            html = fetch_html(alt_url, timeout=timeout, retries=0)
            if html:
                url = alt_url

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
        if is_noise_or_news(raw_title, parent.get_text(" ", strip=True), full_url, strict_mypl=strict_mypl):
            heading = parent.find(["h2", "h3", "h4", "dt"]) if parent else None
            if heading:
                cand = heading.get_text(" ", strip=True)
                if not is_noise_or_news(cand, parent.get_text(" ", strip=True), full_url, strict_mypl=strict_mypl):
                    raw_title = cand

        block_text = parent.get_text(" ", strip=True)
        if is_noise_or_news(raw_title, block_text, full_url, strict_mypl=strict_mypl):
            continue

        combined_text = f"{raw_title} {block_text}"
        if require_keywords and not any(kw in combined_text for kw in require_keywords):
            continue
        if area_keywords and not any(ak in combined_text for ak in area_keywords):
            continue

        title = clean_event_title(raw_title)
        title_dates = extract_dates_from_text(title)
        parsed_dates = title_dates if title_dates else extract_dates_from_text(combined_text)

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
        if is_noise_or_news(raw_title, "", full_url):
            continue

        title = clean_event_title(raw_title)
        title_dates = extract_dates_from_text(title, default_year=now.year)
        parsed_dates = title_dates if title_dates else extract_dates_from_text(block_text, default_year=now.year)
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
                "category": "区立図書館",
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

            if is_noise_or_news(raw_title, "", full_url):
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
                    "category": "区スポーツ・施設",
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
        "X-WR-CALNAME:江戸川区イベントカレンダー（区役所・公共優先）",
        "X-WR-TIMEZONE:Asia/Tokyo",
        "X-WR-CALDESC:江戸川区役所・区公式・区内公共施設を最優先に、公園・友好都市物産展・商業施設・学校のイベントをまとめたカレンダー",
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

            uid_seed = f"{ev['url']}_{ev.get('venue', '')}_{dtstart_str}_{dtend_str}"
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
    print("=== Starting Edogawa Events Aggregation (City Hall & Public Priority) ===")
    merged_events = {}

    def add_events(source_name, items, priority_rank=50):
        print(f"  -> [{priority_rank:02d}] {source_name}: {len(items)} events fetched")
        for item in items:
            item_copy = dict(item)
            item_copy["_priority"] = priority_rank
            key = f"{item_copy['url']}_{item_copy.get('venue', '')}_{item_copy['title']}"
            if key not in merged_events:
                merged_events[key] = item_copy
            else:
                merged_events[key]["dates"].update(item_copy["dates"])
                if priority_rank < merged_events[key].get("_priority", 50):
                    merged_events[key]["_priority"] = priority_rank
                    if item_copy.get("category"):
                        merged_events[key]["category"] = item_copy["category"]
                if item_copy.get("venue") and not merged_events[key].get("venue"):
                    merged_events[key]["venue"] = item_copy["venue"]
                if item_copy.get("image_url") and not merged_events[key].get("image_url"):
                    merged_events[key]["image_url"] = item_copy["image_url"]

    # ------------------------------------------
    # 最優先（Priority 01〜16）: 江戸川区役所・江戸川区公式・区内公共施設
    # ------------------------------------------
    for year, month in get_target_months(months_ahead=3):
        items = parse_edogawa_city_calendar(year, month)
        add_events(f"江戸川区役所・区公式 ({year}-{month:02d})", items, priority_rank=1)
        time.sleep(0.35)

    add_events(
        "江戸川区役所・区イベント一覧",
        parse_generic_facility_events(
            EDOGAWA_EVENT_TOP_URL,
            "区役所・区公式",
            "江戸川区役所",
            timeout=10,
            retries=1,
            fallback_base_url=EDOGAWA_TOP_URL,
        ),
        priority_rank=2,
    )
    add_events("江戸川区立図書館", parse_library_events(), priority_rank=3)
    add_events("タワーホール船堀", parse_generic_facility_events(TOWERHALL_EVENT_URL, "公共・船堀タワー", "タワーホール船堀"), priority_rank=4)
    add_events("江戸川区総合文化センター(予定一覧)", parse_generic_facility_events(BUNKACENTER_EVENT_URL, "公共・総合文化センター", "江戸川区総合文化センター"), priority_rank=5)
    add_events("江戸川区総合文化センター(カレンダー)", parse_generic_facility_events(BUNKACENTER_CAL_URL, "公共・総合文化センター", "江戸川区総合文化センター"), priority_rank=6)
    add_events("江戸川区スポーツ", parse_sports_kintone_events(), priority_rank=7)
    add_events("葛西海浜公園", parse_generic_facility_events(KASAI_KAIHIN_PARK_URL, "公共・葛西海浜公園", "葛西海浜公園"), priority_rank=8)
    add_events("葛西臨海公園", parse_generic_facility_events(KASAI_RINKAI_PARK_URL, "公共・葛西臨海公園", "葛西臨海公園", timeout=20, retries=1), priority_rank=9)
    add_events("都立篠崎公園", parse_generic_facility_events(SHINOZAKI_PARK_URL, "公共・篠崎公園", "篠崎公園", timeout=15, retries=1), priority_rank=10)
    add_events("都立大島小松川公園", parse_generic_facility_events(OJIMA_KOMATSUGAWA_PARK_URL, "公共・大島小松川公園", "大島小松川公園", timeout=15, retries=1), priority_rank=11)
    add_events("魔法の文学館", parse_generic_facility_events(KIKIS_MUSEUM_EVENT_URL, "公共・魔法の文学館", "魔法の文学館"), priority_rank=12)
    add_events("葛西臨海水族園", parse_generic_facility_events(KASAI_AQUARIUM_URL, "公共・葛西臨海水族園", "葛西臨海水族園"), priority_rank=13)
    add_events(
        "江戸川陸上競技場",
        parse_generic_facility_events(
            EDOGAWA_ATHLETICS_URL,
            "公共・江戸川陸上競技場",
            "江戸川陸上競技場",
            timeout=12,
            retries=1,
        ),
        priority_rank=14,
    )
    add_events(
        "江戸川球場",
        parse_generic_facility_events(
            EDOGAWA_BASEBALL_URL,
            "公共・江戸川球場",
            "江戸川球場",
            timeout=12,
            retries=1,
        ),
        priority_rank=15,
    )
    add_events(
        "江戸川まいぷれ",
        parse_generic_facility_events(
            EDOGAWA_MYPL_EVENT_URL,
            "地域・江戸川まいぷれ",
            "江戸川区役所",
            timeout=12,
            retries=1,
            strict_mypl=True,
        ),
        priority_rank=16,
    )

    # ------------------------------------------
    # 次点（Priority 20〜22）: 友好都市物産展・フリーマーケット・商業施設
    # ------------------------------------------
    add_events("山形県鶴岡市 観光物産展", parse_tsuruoka_bussan_events(), priority_rank=20)
    add_events(
        "フリーマーケット(江戸川区内)",
        parse_generic_facility_events(
            TRX_FLEAMARKET_URL,
            "フリマ",
            "葛西臨海公園",
            timeout=10,
            retries=1,
            area_keywords=("江戸川", "葛西", "篠崎", "小松川", "船堀", "小岩", "平井"),
        ),
        priority_rank=21,
    )
    add_events("アリオ葛西", parse_generic_facility_events(ARIO_KASAI_EVENT_URL, "商業・アリオ葛西", "アリオ葛西", timeout=12, retries=1), priority_rank=22)

    # ------------------------------------------
    # 次点（Priority 40〜45）: 学校・学園祭・公開イベント
    # ------------------------------------------
    for idx, (school_label, school_url, school_venue) in enumerate(SCHOOL_EVENT_SOURCES):
        add_events(
            school_label,
            parse_generic_facility_events(
                school_url,
                "学校・学園祭",
                school_venue,
                timeout=8,
                retries=0,
                require_keywords=SCHOOL_PUBLIC_KEYWORDS,
            ),
            priority_rank=40 + idx,
        )

    event_list = sorted(
        merged_events.values(),
        key=lambda x: (
            x.get("_priority", 50),
            min(x["dates"]) if x.get("dates") else datetime.max.date(),
        ),
    )
    print(f"Total unique events merged: {len(event_list)}")

    detail_checked = 0
    for ev in event_list:
        if ev.get("image_url"):
            continue
        if detail_checked >= 120:
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
