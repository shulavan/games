#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
edogawa_ical.py
江戸川区周辺のイベント情報を収集・整形し、iCalendar (.ics) ファイルを生成する安定版スクリプト。

設計方針:
1. ノイズの体系的除外:
   - 店舗・商業・プロモーション系（キャンペーン、スタンプラリー、セール、福袋、開店/閉店、新メニュー、クーポン等）
   - 募集・手続き・行政案内系（受講生募集、職員募集、パブリックコメント、臨時回収、申請期限、受付終了等）
   - URLパス・ドメイン構造による除外（/shop/, /blog/, /news/, /column/, /recruit/, /faq/, /privacy/ 等）
   - 過去の更新日・掲載日とイベント開催日の混同防止
2. タイトル整形と重複排除:
   - 不要な記号・定型文・サイト名の除去、全角/半角正規化
   - タイトル＋開始日＋URL（または正規化キー）による重複防止
3. 堅牢な日付抽出とiCalendar出力:
   - 和暦（令和/平成）・西暦・月日表記・期間表記の抽出
   - 過去日付のフィルタリングとタイムゾーン（Asia/Tokyo）準拠のRFC 5545出力
"""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Iterable, List, Optional, Sequence, Set, Tuple
from urllib.parse import urlparse

try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python < 3.9 フォールバック
    from datetime import timezone as _tz

    def ZoneInfo(key: str):  # type: ignore
        return _tz(timedelta(hours=9), name=key)


JST = ZoneInfo("Asia/Tokyo")

# ロギング設定
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("edogawa_ical")


# =============================================================================
# ノイズ判定ルール定義（体系化）
# =============================================================================

# 1. イベントではなく「商業・キャンペーン・店舗案内・募集・行政手続き」に該当する語句
NON_EVENT_KEYWORDS: Tuple[str, ...] = (
    # キャンペーン・販促・商業
    "キャンペーン",
    "スタンプラリー",
    "ポイント還元",
    "キャッシュバック",
    "クーポン",
    "セール",
    "バーゲン",
    "クリアランス",
    "福袋",
    "抽選プレゼント",
    "プレゼント企画",
    "モニター募集",
    "試食会",
    "新発売",
    "新メニュー",
    "グランドオープン",
    "プレオープン",
    "リニューアルオープン",
    "閉店のお知らせ",
    "臨時休業",
    "営業時間変更",
    "定休日",
    "価格改定",
    "値上げ",
    # 募集・採用・申請・手続き・行政インフラ
    "受講生募集",
    "生徒募集",
    "園児募集",
    "会員募集",
    "団員募集",
    "ボランティア募集",
    "スタッフ募集",
    "アルバイト募集",
    "職員募集",
    "求人",
    "採用情報",
    "パブリックコメント",
    "意見募集",
    "臨時回収",
    "粗大ごみ",
    "ごみ収集",
    "申請受付",
    "受付終了",
    "募集終了",
    "申込終了",
    "締め切り",
    "締切",
    "入札",
    "公告",
    "制度改正",
    "お知らせ一覧",
    "更新情報",
)

# 2. タイトル末尾や文中に付きやすい定型ノイズ・サイト名・装飾
TITLE_NOISE_PATTERNS: Tuple[re.Pattern[str], ...] = (
    re.compile(r"【[^】]*(?:PR|広告|募集|終了|中止|延期|重要|お知らせ|更新)[^】]*】"),
    re.compile(r"\[[^\]]*(?:PR|広告|募集|終了|中止|延期|重要|お知らせ|更新)[^\]]*\]"),
    re.compile(r"(?:公式|公式サイト|特設ページ|詳細はこちら|詳しくはこちら)$"),
    re.compile(r"[｜|]\s*[^｜|]+$"),
    re.compile(r"\s*[-–—～〜]\s*(?:江戸川区|江戸川経済新聞|地域ニュース).*$"),
)

# 3. URLパスに含まれるとイベント詳細ページではない可能性が高いセグメント
EXCLUDED_URL_PATH_SEGMENTS: Set[str] = {
    "shop",
    "store",
    "item",
    "items",
    "product",
    "products",
    "goods",
    "blog",
    "column",
    "columns",
    "news",
    "topics",
    "information",
    "info",
    "recruit",
    "careers",
    "job",
    "jobs",
    "faq",
    "help",
    "contact",
    "about",
    "company",
    "privacy",
    "policy",
    "terms",
    "sitemap",
    "tag",
    "tags",
    "category",
    "categories",
    "author",
    "search",
}

# 4. 「更新日」「掲載日」「公開日」などイベント開催日ではない日付の直前ラベル
NON_EVENT_DATE_LABELS: Tuple[str, ...] = (
    "更新日",
    "最終更新",
    "掲載日",
    "公開日",
    "投稿日",
    "作成日",
    "発行日",
    "配信日",
    "登録日",
)


# =============================================================================
# データモデル
# =============================================================================

@dataclass
class RawEventCandidate:
    """スクレイピングやフィード等から得られた生データ候補。"""

    title: str
    url: str = ""
    description: str = ""
    location: str = ""
    raw_texts: List[str] = field(default_factory=list)
    explicit_dates: List[Tuple[date, Optional[date]]] = field(default_factory=list)
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    is_all_day: bool = True


@dataclass(frozen=True)
class CalendarEvent:
    """iCalendar出力用に正規化された確定イベント。"""

    uid: str
    title: str
    start_date: date
    end_date: date  # iCalendar DTEND (終日の場合は排他的終了日: 最終日 + 1日)
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    is_all_day: bool = True
    location: str = ""
    description: str = ""
    url: str = ""


# =============================================================================
# テキスト正規化・ノイズ判定ユーティリティ
# =============================================================================

def normalize_text(text: str) -> str:
    """Unicode正規化（NFKC）と空白の整理を行う。"""
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKC", text)
    normalized = re.sub(r"[ \t\r\f\v]+", " ", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def clean_title(raw_title: str) -> str:
    """イベントタイトルから不要な記号・定型句・末尾句読点を取り除く。"""
    title = normalize_text(raw_title)
    if not title:
        return ""

    for pattern in TITLE_NOISE_PATTERNS:
        title = pattern.sub("", title)

    # 連続する記号や末尾の句読点・記号を整理
    title = re.sub(r"\s+", " ", title).strip()
    title = re.sub(r"^[・●◆■★☆※\s]+", "", title)
    title = re.sub(r"[・●◆■★☆※!！?？。、,.\s]+$", "", title)
    return title.strip()


def clean_description(raw_desc: str, url: str = "") -> str:
    """説明文を整形し、必要に応じて参照URLを付与する。"""
    desc = normalize_text(raw_desc)
    parts: List[str] = []
    if desc:
        parts.append(desc)
    if url and url not in desc:
        parts.append(f"詳細URL: {url}")
    return "\n".join(parts).strip()


def is_excluded_url(url: str) -> bool:
    """URLのパスやクエリから、非イベントページ（店舗・ブログ・募集・管理系等）かを判定する。"""
    if not url:
        return False
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return True

    path_segments = [
        seg.lower() for seg in parsed.path.split("/") if seg and seg.strip()
    ]
    if any(seg in EXCLUDED_URL_PATH_SEGMENTS for seg in path_segments):
        return True

    lowered_full = url.lower()
    if any(
        token in lowered_full
        for token in ("/shop/", "/store/", "/blog/", "/recruit/", "/news/category/")
    ):
        return True

    return False


def is_noise_entry(title: str, description: str = "", url: str = "") -> bool:
    """
    タイトル・説明文・URLを総合評価し、カレンダーに入れるべきではないノイズかを判定する。
    """
    if is_excluded_url(url):
        return True

    norm_title = normalize_text(title)
    norm_desc = normalize_text(description)

    if not norm_title or len(norm_title) < 2:
        return True

    # タイトルが数字や記号のみの場合はノイズ
    if re.fullmatch(r"[\d\W_]+", norm_title):
        return True

    # タイトルに非イベントキーワードが含まれる場合は即除外
    if any(kw in norm_title for kw in NON_EVENT_KEYWORDS):
        return True

    # 説明文の先頭付近に強い案内・募集・閉店・キャンペーン文言がある場合も除外
    head_desc = norm_desc[:200]
    strong_desc_noise = (
        "受講生募集",
        "スタッフ募集",
        "職員募集",
        "スタンプラリー開催中",
        "キャンペーン実施中",
        "閉店しました",
        "受付は終了しました",
        "募集は終了しました",
        "臨時回収を行います",
    )
    if any(kw in head_desc for kw in strong_desc_noise):
        return True

    return False


# =============================================================================
# 和暦・日付抽出ロジック
# =============================================================================

def _era_to_year(era: str, era_year_str: str) -> Optional[int]:
    """和暦（令和・平成・昭和）と年数文字列を西暦年に変換する。"""
    if era_year_str == "元":
        year_num = 1
    else:
        try:
            year_num = int(era_year_str)
        except ValueError:
            return None

    if era == "令和":
        return 2018 + year_num
    if era == "平成":
        return 1988 + year_num
    if era == "昭和":
        return 1925 + year_num
    return None


def _strip_non_event_date_phrases(text: str) -> str:
    """
    「更新日：2026年10月1日」「掲載日 2026/11/01」のように、
    イベント開催日ではない日付表記を事前除去して誤抽出を防ぐ。
    """
    cleaned = text
    for label in NON_EVENT_DATE_LABELS:
        pattern = re.compile(
            rf"{re.escape(label)}\s*[:：]?\s*"
            r"(?:(?:令和|平成|昭和)(?:\d+|元)年|20\d{2}年|20\d{2}[/-])"
            r"\d{1,2}月?\s*[/-]?\s*\d{1,2}日?",
        )
        cleaned = pattern.sub(" ", cleaned)
    return cleaned


def _infer_year_for_month_day(month: int, day: int, reference_date: date) -> Optional[int]:
    """
    年表記が省略された「M月D日」に対して、基準日（通常は当日）を元に適切な西暦年を推定する。
    年またぎ（11〜12月に翌年1〜3月のイベントが告知されるケース）にも対応する。
    """
    candidate_years = [reference_date.year, reference_date.year + 1]
    for y in candidate_years:
        try:
            d = date(y, month, day)
        except ValueError:
            continue
        # 基準日の30日前以降〜1年半以内を妥当な範囲とする
        if d >= reference_date - timedelta(days=30) and d <= reference_date + timedelta(
            days=550
        ):
            return y
    return None


def extract_dates_from_text(
    text: str,
    reference_date: Optional[date] = None,
) -> List[Tuple[date, date]]:
    """
    テキストからイベント開催日（または開始日・終了日のペア）を抽出する。
    複数の日付が含まれる場合でも、過去日付だけに引きずられず、
    基準日以降に有効な日付（または期間）をすべて返す。

    返り値: [(start_date, inclusive_end_date), ...]
    """
    if reference_date is None:
        reference_date = datetime.now(JST).date()

    normalized = normalize_text(text)
    if not normalized:
        return []

    target_text = _strip_non_event_date_phrases(normalized)
    results: List[Tuple[date, date]] = []
    seen_pairs: Set[Tuple[date, date]] = set()

    def _add_range(start_d: date, end_d: date) -> None:
        if end_d < start_d:
            start_d, end_d = end_d, start_d
        # 完全に過去のイベントは除外（当日開催は含める）
        if end_d < reference_date:
            return
        pair = (start_d, end_d)
        if pair not in seen_pairs:
            seen_pairs.add(pair)
            results.append(pair)

    # パターンA: 西暦年月日〜西暦年月日（例: 2026年12月5日〜2026年12月6日 または 2026/12/05 - 2026/12/06）
    ymd_range_pattern = re.compile(
        r"(20\d{2})\s*[年/.-]\s*(\d{1,2})\s*[月/.-]\s*(\d{1,2})\s*日?"
        r"\s*(?:〜|～|-|–|—|から|to)\s*"
        r"(?:(20\d{2})\s*[年/.-]\s*)?(\d{1,2})\s*[月/.-]\s*(\d{1,2})\s*日?"
    )
    for m in ymd_range_pattern.finditer(target_text):
        try:
            y1, m1, d1 = int(m.group(1)), int(m.group(2)), int(m.group(3))
            y2 = int(m.group(4)) if m.group(4) else y1
            m2, d2 = int(m.group(5)), int(m.group(6))
            _add_range(date(y1, m1, d1), date(y2, m2, d2))
        except ValueError:
            continue

    # パターンB: 和暦年月日（例: 令和8年12月10日）
    era_ymd_pattern = re.compile(
        r"(令和|平成|昭和)\s*(\d+|元)\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"
    )
    for m in era_ymd_pattern.finditer(target_text):
        y = _era_to_year(m.group(1), m.group(2))
        if y is None:
            continue
        try:
            _add_range(date(y, int(m.group(3)), int(m.group(4))), date(y, int(m.group(3)), int(m.group(4))))
        except ValueError:
            continue

    # パターンC: 西暦年月日（単日）
    ymd_single_pattern = re.compile(
        r"(20\d{2})\s*[年/.-]\s*(\d{1,2})\s*[月/.-]\s*(\d{1,2})\s*日?"
    )
    for m in ymd_single_pattern.finditer(target_text):
        try:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            _add_range(d, d)
        except ValueError:
            continue

    # パターンD: 年省略の月日期間（例: 12月5日〜12月7日、12月5日〜7日）
    md_range_pattern = re.compile(
        r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*日"
        r"\s*(?:〜|～|-|–|—|から)\s*"
        r"(?:(\d{1,2})\s*月\s*)?(\d{1,2})\s*日"
    )
    for m in md_range_pattern.finditer(target_text):
        m1, d1 = int(m.group(1)), int(m.group(2))
        m2 = int(m.group(3)) if m.group(3) else m1
        d2 = int(m.group(4))
        y1 = _infer_year_for_month_day(m1, d1, reference_date)
        y2 = _infer_year_for_month_day(m2, d2, reference_date)
        if y1 and y2:
            try:
                _add_range(date(y1, m1, d1), date(y2, m2, d2))
            except ValueError:
                continue

    # パターンE: 年省略の単日月日（例: 12月15日）
    md_single_pattern = re.compile(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*日(?!\s*(?:〜|～|-|–|—|から))")
    for m in md_single_pattern.finditer(target_text):
        month, day = int(m.group(1)), int(m.group(2))
        y = _infer_year_for_month_day(month, day, reference_date)
        if y:
            try:
                d = date(y, month, day)
                _add_range(d, d)
            except ValueError:
                continue

    results.sort(key=lambda item: (item[0], item[1]))
    return results


# =============================================================================
# イベント正規化・重複排除・iCalendar (.ics) 生成
# =============================================================================

def _generate_uid(title: str, start_d: date, url: str) -> str:
    """イベントごとの一意なUIDを生成する。"""
    seed = f"{title}|{start_d.isoformat()}|{url}".encode("utf-8")
    digest = hashlib.sha256(seed).hexdigest()[:24]
    return f"{digest}@edogawa-events.local"


def _escape_ics_text(text: str) -> str:
    """RFC 5545 に準拠してテキストをエスケープする。"""
    escaped = text.replace("\\", "\\\\")
    escaped = escaped.replace(";", "\\;")
    escaped = escaped.replace(",", "\\,")
    escaped = escaped.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")
    return escaped


def _fold_ics_line(line: str, max_octets: int = 75) -> str:
    """RFC 5545 の行折り畳み（UTF-8バイト単位で75オクテット以内）を行う。"""
    raw_bytes = line.encode("utf-8")
    if len(raw_bytes) <= max_octets:
        return line

    chunks: List[str] = []
    current = bytearray()
    is_first = True

    for ch in line:
        ch_bytes = ch.encode("utf-8")
        limit = max_octets if is_first else max_octets - 1  # 先頭スペース分を考慮
        if len(current) + len(ch_bytes) > limit:
            prefix = "" if is_first else " "
            chunks.append(prefix + current.decode("utf-8"))
            current = bytearray(ch_bytes)
            is_first = False
        else:
            current.extend(ch_bytes)

    if current:
        prefix = "" if is_first else " "
        chunks.append(prefix + current.decode("utf-8"))

    return "\r\n".join(chunks)


def build_calendar_events(
    candidates: Iterable[RawEventCandidate],
    reference_date: Optional[date] = None,
) -> List[CalendarEvent]:
    """
    生候補リストからノイズを除外し、日付・タイトル・重複を整理した確定イベント一覧を返す。
    """
    if reference_date is None:
        reference_date = datetime.now(JST).date()

    events: List[CalendarEvent] = []
    seen_keys: Set[Tuple[str, date]] = set()
    excluded_count = 0

    for cand in candidates:
        title = clean_title(cand.title)
        desc_text = normalize_text(cand.description)
        loc_text = normalize_text(cand.location)
        url = (cand.url or "").strip()

        if is_noise_entry(title=title, description=desc_text, url=url):
            excluded_count += 1
            logger.debug("ノイズ除外: title=%r url=%r", cand.title, url)
            continue

        # 日付範囲の収集
        date_ranges: List[Tuple[date, date]] = []
        for s_d, e_d in cand.explicit_dates:
            end_inclusive = e_d if e_d is not None else s_d
            if end_inclusive >= reference_date:
                date_ranges.append((min(s_d, end_inclusive), max(s_d, end_inclusive)))

        if not date_ranges:
            combined_sources = [title, desc_text, loc_text] + list(cand.raw_texts)
            for src in combined_sources:
                extracted = extract_dates_from_text(src, reference_date=reference_date)
                for pair in extracted:
                    if pair not in date_ranges:
                        date_ranges.append(pair)

        if not date_ranges:
            excluded_count += 1
            logger.debug("有効な開催日が抽出できないため除外: title=%r", title)
            continue

        formatted_desc = clean_description(desc_text, url=url)

        for start_d, end_inclusive_d in date_ranges:
            dedup_key = (title.lower(), start_d)
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)

            # 終日イベントの DTEND は RFC 5545 に従い最終日の翌日を指定する
            dtend_date = end_inclusive_d + timedelta(days=1)

            events.append(
                CalendarEvent(
                    uid=_generate_uid(title, start_d, url),
                    title=title,
                    start_date=start_d,
                    end_date=dtend_date,
                    start_time=cand.start_time,
                    end_time=cand.end_time,
                    is_all_day=cand.is_all_day or (cand.start_time is None),
                    location=loc_text,
                    description=formatted_desc,
                    url=url,
                )
            )

    events.sort(key=lambda ev: (ev.start_date, ev.title))
    logger.info(
        "イベント確定件数: %d件 / ノイズ・対象外除外件数: %d件",
        len(events),
        excluded_count,
    )
    return events


def render_ics(
    events: Sequence[CalendarEvent],
    calendar_name: str = "江戸川区イベントカレンダー",
) -> str:
    """確定イベント一覧から RFC 5545 準拠の iCalendar (.ics) 文字列を生成する。"""
    now_utc = datetime.now(JST).astimezone(ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%SZ")

    lines: List[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Edogawa Event Generator//JP",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape_ics_text(calendar_name)}",
        "X-WR-TIMEZONE:Asia/Tokyo",
        "BEGIN:VTIMEZONE",
        "TZID:Asia/Tokyo",
        "BEGIN:STANDARD",
        "DTSTART:19700101T000000",
        "TZOFFSETFROM:+0900",
        "TZOFFSETTO:+0900",
        "TZNAME:JST",
        "END:STANDARD",
        "END:VTIMEZONE",
    ]

    for ev in events:
        lines.append("BEGIN:VEVENT")
        lines.append(f"UID:{_escape_ics_text(ev.uid)}")
        lines.append(f"DTSTAMP:{now_utc}")

        if ev.is_all_day or ev.start_time is None:
            lines.append(f"DTSTART;VALUE=DATE:{ev.start_date.strftime('%Y%m%d')}")
            lines.append(f"DTEND;VALUE=DATE:{ev.end_date.strftime('%Y%m%d')}")
        else:
            start_dt = datetime.combine(ev.start_date, ev.start_time)
            end_clock = ev.end_time or (
                datetime.combine(ev.start_date, ev.start_time) + timedelta(hours=1)
            ).time()
            end_dt = datetime.combine(ev.end_date - timedelta(days=1), end_clock)
            lines.append(f"DTSTART;TZID=Asia/Tokyo:{start_dt.strftime('%Y%m%dT%H%M%S')}")
            lines.append(f"DTEND;TZID=Asia/Tokyo:{end_dt.strftime('%Y%m%dT%H%M%S')}")

        lines.append(f"SUMMARY:{_escape_ics_text(ev.title)}")

        if ev.location:
            lines.append(f"LOCATION:{_escape_ics_text(ev.location)}")
        if ev.description:
            lines.append(f"DESCRIPTION:{_escape_ics_text(ev.description)}")
        if ev.url:
            lines.append(f"URL:{_escape_ics_text(ev.url)}")

        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")

    folded_lines = [_fold_ics_line(line) for line in lines]
    return "\r\n".join(folded_lines) + "\r\n"


def write_ics_file(
    events: Sequence[CalendarEvent],
    output_path: str = "edogawa_events.ics",
    calendar_name: str = "江戸川区イベントカレンダー",
) -> str:
    """iCalendar (.ics) ファイルをUTF-8で書き出し、ファイルパスを返す。"""
    ics_content = render_ics(events, calendar_name=calendar_name)
    with open(output_path, "w", encoding="utf-8", newline="") as f:
        f.write(ics_content)
    logger.info("iCalendarファイルを出力しました: %s (%d件)", output_path, len(events))
    return output_path


# =============================================================================
# 動作確認用サンプル実行
# =============================================================================

def _sample_candidates() -> List[RawEventCandidate]:
    """ノイズ混在ケースを含むサンプルデータ。"""
    return [
        RawEventCandidate(
            title="【公式】小松川千本桜まつり（2026年12月5日）｜江戸川ガイド",
            url="https://example.org/events/komatsugawa-sakura",
            description="更新日：2026年10月1日。2026年12月5日に地域交流イベントを開催します。",
            location="江戸川区小松川",
        ),
        RawEventCandidate(
            title="秋のスタンプラリーキャンペーン開催中！",
            url="https://example.org/campaign/autumn-rally",
            description="2026年12月1日〜2026年12月20日まで対象店舗でポイントを貯めよう。",
            location="江戸川区内",
        ),
        RawEventCandidate(
            title="英会話教室 受講生募集のお知らせ",
            url="https://example.org/recruit/english-class",
            description="2026年12月10日から開講する講座の受講生を募集します。",
            location="船堀コミュニティ会館",
        ),
        RawEventCandidate(
            title="〇〇ベーカリー グランドオープン",
            url="https://example.org/shop/bakery-open",
            description="2026年12月8日オープン！限定パンを販売します。",
            location="葛西駅周辺",
        ),
        RawEventCandidate(
            title="粗大ごみ・剪定枝 臨時回収のご案内",
            url="https://example.org/news/temp-collection",
            description="2026年12月12日に臨時回収を実施します。",
            location="江戸川区",
        ),
        RawEventCandidate(
            title="江戸川区民吹奏楽団 ウィンターコンサート",
            url="https://example.org/events/winter-concert",
            description="過去の開催記録：2025年12月14日。次回開催日：2026年12月19日。",
            location="タワーホール船堀",
        ),
    ]


if __name__ == "__main__":
    sample_ref = date(2026, 11, 26)
    valid_events = build_calendar_events(_sample_candidates(), reference_date=sample_ref)
    for ev in valid_events:
        print(f"[確定] {ev.start_date} -> {ev.title} ({ev.location})")
    write_ics_file(valid_events, output_path="edogawa_events.ics")
