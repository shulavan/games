#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""江戸川区限定イベント収集・iCal/JSON生成器。

優先順位:
  1. 江戸川区公式イベントCSV / 東京都オープンデータCSV
  2. 江戸川区公式イベントカレンダー (12か月)
  3. 区内公共施設・公園・図書館・まいぷれ等の補完情報

厳格な区内判定:
  - 住所に江戸川区が明記されている、または
  - 会場が edogawa_venues.json の区内施設辞書に一致する、または
  - 区内施設が運営するイベント専用ページで会場を特定できる
  イベント名や主催者名だけでは区内と判定しない。

重要: 公式CSVの実列名は配信元の仕様変更に備えて実行時に検出する。
       未知の列構成を推測で処理せず、source_status.json に列名を記録する。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
import time
import threading
import unicodedata
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse, parse_qs, urlencode
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
JST = ZoneInfo("Asia/Tokyo")
UTC = timezone.utc
TODAY = datetime.now(JST).date()

OUTPUT_ICS = ROOT / "edogawa_events.ics"
OUTPUT_JSON = ROOT / "edogawa_events.json"
OUTPUT_STATUS = ROOT / "source_status.json"
OUTPUT_PREVIEW_ICS = ROOT / "edogawa_events_preview.ics"
OUTPUT_PREVIEW_JSON = ROOT / "edogawa_events_preview.json"
OUTPUT_PREVIEW_STATUS = ROOT / "source_status_preview.json"
VENUES_FILE = ROOT / "edogawa_venues.json"

CITY_BASE = "https://www.city.edogawa.tokyo.jp/cgi-bin/event_cal_multi/calendar.cgi"
CITY_CALENDAR_SITES = ((6, "江戸川区公式"), (7, "子ども未来館"), (8, "プロテクトミライ"))
CITY_EVENT_CSV = "https://www.city.edogawa.tokyo.jp/shared/system/event_csv/event_info.csv"
TOKYO_EVENT_CSV = "https://www.opendata.metro.tokyo.lg.jp/edogawa/131237_event.csv"
TOKYO_FACILITY_CSV = "https://www.opendata.metro.tokyo.lg.jp/edogawa/131237_public_facility.csv"
TOKYO_EVENTSPACE_CSV = "https://www.opendata.metro.tokyo.lg.jp/edogawa/131237_edogawaku_chiikinoeventspace.csv"
# 江戸川区公式「地図データ」。公共施設・公園などの座標を会場マスタに補完する。
CITY_MAP_CSV_SOURCES = [
    ("江戸川区地図データ・区役所事務所", "https://www.city.edogawa.tokyo.jp/documents/4408/20251211_kuyakusyo.csv"),
    ("江戸川区地図データ・図書館", "https://www.city.edogawa.tokyo.jp/documents/4408/20251121_library.csv"),
    ("江戸川区地図データ・スポーツ施設", "https://www.city.edogawa.tokyo.jp/documents/4408/20251211_sports.csv"),
    ("江戸川区地図データ・文化コミュニティ施設", "https://www.city.edogawa.tokyo.jp/documents/4408/20251211_bunka.csv"),
    ("江戸川区地図データ・健康施設", "https://www.city.edogawa.tokyo.jp/documents/4408/20251211_hoken.csv"),
    ("江戸川区地図データ・公園", "https://www.city.edogawa.tokyo.jp/documents/4408/20251211_kouen.csv"),
    ("江戸川区地図データ・親水公園", "https://www.city.edogawa.tokyo.jp/documents/4408/20251211_sinsuikouen.csv"),
    ("江戸川区地図データ・じゃぶじゃぶ池", "https://www.city.edogawa.tokyo.jp/documents/4408/20251211_jabujabu.csv"),
    ("江戸川区地図データ・レクリエーション施設", "https://www.city.edogawa.tokyo.jp/documents/4408/20251211_reku.csv"),
    ("江戸川区地図データ・小学校", "https://www.city.edogawa.tokyo.jp/documents/4408/20251211_elementary_schools.csv"),
    ("江戸川区地図データ・中学校", "https://www.city.edogawa.tokyo.jp/documents/4408/20251211_juniorhigh_schools.csv"),
    ("江戸川区地図データ・都立私立学校", "https://www.city.edogawa.tokyo.jp/documents/4408/20251211_toritsu_shiritsukou.csv"),
    ("江戸川区地図データ・銭湯", "https://www.city.edogawa.tokyo.jp/documents/4408/20251211_sento.csv"),
]
KVIEWER_PUBLIC_URL = (
    "https://2f01466f.viewer.kintoneapp.com/public/"
    "2288685cd0240ea5379b822175def1d520d606ea6a20c595b5c7602fc6c29368"
)

USER_AGENT = (
    "Mozilla/5.0 (compatible; EdogawaEventCalendar/2.0; "
    "+https://github.com/shulavan/games)"
)
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "ja,en-US;q=0.8,en;q=0.6"})
_FETCH_CACHE: dict[str, tuple[str, dict[str, Any]]] = {}
_FETCH_CACHE_LOCK = threading.Lock()

EXCLUDED_TEXT = (
    "詳細検索へ", "一覧はこちら", "もっと見る", "ニュース一覧", "イベント一覧",
    "お知らせ一覧", "アクセス", "フロアガイド", "チケット購入", "募集終了",
)
APPLICATION_DATE_TERMS = (
    "申込期間", "申し込み期間", "申込開始", "申込締切", "申込期限", "受付期間",
    "受付開始", "受付締切", "応募期間", "応募開始", "応募締切", "販売期間",
    "発売日", "更新日", "掲載日", "公開日", "投稿日", "募集期間",
)
EVENT_DATE_HEADINGS = ("開催日時", "開催日", "開催期間", "日時", "日程", "実施日時")
VENUE_HEADINGS = ("場所", "会場", "開催場所", "実施場所", "施設")
ADDRESS_HEADINGS = ("住所", "所在地", "会場住所", "開催場所住所")
STOP_HEADINGS = (
    "申し込み", "申込み", "申込", "場所", "会場", "住所", "所在地", "内容",
    "対象", "定員", "費用", "料金", "問合せ", "問い合わせ", "主催", "備考",
    "関連資料", "関連リンク", "このページに関するお問い合わせ",
)
EVENT_WORDS = (
    "イベント", "開催", "フェス", "フェスタ", "まつり", "祭り", "講座", "教室",
    "体験", "コンサート", "公演", "展示", "展覧会", "マルシェ", "大会", "セミナー",
    "ワークショップ", "見学会", "説明会", "マルシェ", "音楽会", "発表会",
    "物産展", "フリーマーケット", "フリマ",
)
# 江戸川区以外の東京都23区・近隣自治体を明示。会場住所に含まれる場合は必ず除外。
OUTSIDE_WARD_TERMS = (
    # 江戸川区以外の東京23区
    "千代田区", "中央区", "港区", "新宿区", "文京区", "台東区", "墨田区",
    "江東区", "品川区", "目黒区", "大田区", "世田谷区", "渋谷区", "中野区",
    "杉並区", "豊島区", "北区", "荒川区", "板橋区", "練馬区", "足立区", "葛飾区",
    # 東京都以外の全都道府県。区内外が混在する記載も安全側に倒して除外。
    "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県",
    "茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "神奈川県",
    "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県", "岐阜県",
    "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県",
    "奈良県", "和歌山県", "鳥取県", "島根県", "岡山県", "広島県", "山口県",
    "徳島県", "香川県", "愛媛県", "高知県", "福岡県", "佐賀県", "長崎県",
    "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県",
    # 江戸川区周辺の市区名（都道府県名が省略されるページにも対応）
    "江戸川区外", "市川市", "浦安市", "船橋市", "松戸市", "三郷市", "八潮市",
    "横浜市", "川崎市",
)

# CSV列名はデータ提供元で表記揺れがあるため、別名候補を複数持つ。
CSV_ALIASES = {
    "title": ("イベント名", "催し名", "催事名", "イベントタイトル", "タイトル", "名称", "件名", "name", "title", "event_name"),
    "start": ("開催日", "開催日時", "開催開始日", "開催期間", "開始日", "開始日時", "日付", "start_date", "start", "開始日", "event_date", "日程"),
    "end": ("終了日", "終了日時", "開催終了日", "end_date", "end"),
    "venue": ("場所名称", "場所・名称", "会場", "開催場所", "場所", "施設名", "施設名称", "会場名", "venue", "place"),
    "address": ("住所", "所在地", "会場住所", "開催場所住所", "address", "住所詳細"),
    "city": ("市区町村名", "市町村名", "自治体名", "city_name", "city"),
    "city_code": ("市区町村コード", "全国地方公共団体コード", "localgov_code", "city_code"),
    "pref": ("都道府県名", "都道府県", "prefecture", "pref"),
    "url": ("詳細URL", "URL", "ホームページ", "関連URL", "リンク", "url", "web"),
    "image": ("画像URL", "写真URL", "サムネイル", "画像", "image_url", "image"),
    "description": ("説明", "内容", "概要", "イベント内容", "description", "summary"),
    "lat": ("緯度", "latitude", "lat"),
    "lon": ("経度", "longitude", "lon", "lng"),
    "start_time": ("開始時刻", "開始時間", "開始時間", "start_time"),
    "end_time": ("終了時刻", "終了時間", "end_time"),
}

@dataclass
class Event:
    title: str
    start_date: str
    end_date: str
    venue: str
    address: str
    url: str
    category: str = "地域イベント"
    source: str = ""
    source_urls: list[str] = field(default_factory=list)
    start_time: str | None = None
    end_time: str | None = None
    image_url: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    coordinate_quality: str | None = None
    priority: int = 4
    description: str = ""
    verified_location: bool = True

    @property
    def all_day(self) -> bool:
        return not (self.start_time and self.end_time)


def norm(s: Any) -> str:
    if s is None:
        return ""
    t = unicodedata.normalize("NFKC", str(s))
    t = t.replace("\u3000", " ")
    return re.sub(r"\s+", " ", t).strip()


def era_to_western(text: str) -> str:
    def repl(m: re.Match) -> str:
        era = m.group(1)
        yr = int(m.group(2))
        base = {"令和": 2018, "R": 2018, "平成": 1988, "H": 1988, "昭和": 1925, "S": 1925}.get(era, 0)
        return f"{base + yr}年"
    return re.sub(r"(令和|平成|昭和|R|H|S)\s*(\d{1,2})\s*年", repl, norm(text), flags=re.I)


def _year_for_month(month: int, today: date = TODAY, base_year: int | None = None) -> int:
    """年なし日付は過去側に倒さず、今後365日以内の最も近い日付を選ぶ。"""
    years = [today.year - 1, today.year, today.year + 1, today.year + 2]
    candidates = []
    for y in years:
        if base_year and y < base_year - 1:
            continue
        try:
            d = date(y, month, 1)
        except ValueError:
            continue
        if d >= today.replace(day=1) - timedelta(days=1) and d <= today + timedelta(days=400):
            candidates.append(y)
    future = [y for y in candidates if date(y, month, 1) >= today - timedelta(days=1)]
    if future:
        return min(future)
    return max(candidates) if candidates else today.year


def _parse_date_token(token: str, base_year: int | None = None) -> date | None:
    t = era_to_western(token)
    t = norm(t).replace("年 ", "年")
    m = re.search(r"(?:(20\d{2})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*日?", t)
    if m:
        y = int(m.group(1)) if m.group(1) else (base_year or _year_for_month(int(m.group(2))))
        try:
            d = date(y, int(m.group(2)), int(m.group(3)))
            if not m.group(1) and d < TODAY - timedelta(days=30):
                d = date(y + 1, d.month, d.day)
            return d
        except ValueError:
            return None
    m = re.search(r"(20\d{2})[./-](\d{1,2})[./-](\d{1,2})", t)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def parse_date_spans(raw: str, base_year: int | None = None) -> list[tuple[date, date]]:
    """開催日欄にある日付を範囲/単日として取得。申込・掲載日欄には使わない。"""
    text = era_to_western(raw)
    text = norm(text).replace("（", "(").replace("）", ")")
    pat = re.compile(r"(?:(20\d{2})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*日?")
    found: list[tuple[date, int, int]] = []
    for m in pat.finditer(text):
        y = int(m.group(1)) if m.group(1) else None
        mo, day = int(m.group(2)), int(m.group(3))
        if not (1 <= mo <= 12 and 1 <= day <= 31):
            continue
        yy = y or (base_year or _year_for_month(mo))
        try:
            d = date(yy, mo, day)
        except ValueError:
            continue
        if y is None and d < TODAY - timedelta(days=30):
            try:
                d = date(yy + 1, mo, day)
            except ValueError:
                continue
        found.append((d, m.start(), m.end()))
    # ISO形式も補完
    for m in re.finditer(r"(20\d{2})[./-](\d{1,2})[./-](\d{1,2})", text):
        try:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            found.append((d, m.start(), m.end()))
        except ValueError:
            pass
    found.sort(key=lambda x: x[1])
    # 重複 token (和暦変換後とISOなど) を整理
    unique = []
    seen = set()
    for d, a, b in found:
        if d not in seen:
            unique.append((d, a, b)); seen.add(d)
    spans: list[tuple[date, date]] = []
    i = 0
    while i < len(unique):
        d1, _, e1 = unique[i]
        if i + 1 < len(unique):
            d2, s2, _ = unique[i + 1]
            sep = text[e1:s2]
            if re.search(r"(?:から|～|〜|~|\-|−|―|ー|／)\s*$", sep) or re.search(r"^\s*(?:から|～|〜|~|\-|−|―)", sep):
                # 12月→1月など、年なしの年越し範囲を補正
                if d2 < d1 and re.search(r"\d{4}\s*年", text[e1:s2]) is False:
                    try: d2 = date(d2.year + 1, d2.month, d2.day)
                    except ValueError: pass
                spans.append((d1, d2)); i += 2; continue
        spans.append((d1, d1)); i += 1
    return [(a, b) for a, b in spans if b >= TODAY and a <= TODAY + timedelta(days=550)]


def parse_time_range(raw: str) -> tuple[str | None, str | None]:
    t = norm(raw)
    patterns = [
        r"(?P<h1>\d{1,2})\s*時\s*(?P<m1>\d{1,2})?\s*分?\s*(?:から|～|〜|~|－|-)\s*(?P<h2>\d{1,2})\s*時\s*(?P<m2>\d{1,2})?\s*分?",
        r"(?P<h1>\d{1,2}):(?P<m1>\d{2})\s*(?:から|～|〜|~|－|-)\s*(?P<h2>\d{1,2}):(?P<m2>\d{2})",
    ]
    for p in patterns:
        m = re.search(p, t)
        if m:
            h1, m1 = int(m.group("h1")), int(m.group("m1") or 0)
            h2, m2 = int(m.group("h2")), int(m.group("m2") or 0)
            if 0 <= h1 <= 23 and 0 <= h2 <= 24 and 0 <= m1 < 60 and 0 <= m2 < 60:
                return f"{h1:02d}:{m1:02d}", f"{h2:02d}:{m2:02d}"
    return None, None


def load_venues() -> dict[str, dict[str, Any]]:
    try:
        return json.loads(VENUES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}

VENUES = load_venues()


def lookup_venue(text: str) -> dict[str, Any] | None:
    t = norm(text)
    for alias in sorted(VENUES, key=len, reverse=True):
        if alias and alias in t:
            v = dict(VENUES[alias]); v.setdefault("alias", alias)
            return v
    return None


def contains_outside_region(address: str) -> bool:
    a = norm(address)
    return any(term in a for term in OUTSIDE_WARD_TERMS)


def _compact_address(value: str) -> str:
    return re.sub(r"[\s　、,。・‐‑‒–—―ー－/]+", "", norm(value))


def verify_edogawa_location(address: str, venue: str, default_venue: str | None = None) -> tuple[bool, dict[str, Any] | None, str]:
    """開催場所そのものが江戸川区内と裏付けられる場合だけ許可する。"""
    a, v = norm(address), norm(venue)
    if contains_outside_region(a):
        return False, None, "outside_address"
    if contains_outside_region(v):
        return False, None, "outside_venue"
    if a and "江戸川区" in a:
        return True, lookup_venue(v) or lookup_venue(a), "address_edogawa"

    reg = lookup_venue(v) or lookup_venue(a)
    if reg and "江戸川区" in norm(reg.get("address", "")):
        if not a:
            return True, reg, "known_venue"
        # 住所欄が町名・番地だけの場合、既知施設の住所末尾と一致する場合だけ許可。
        ra = _compact_address(reg.get("address", ""))
        ca = _compact_address(a)
        if ca and (ca in ra or ra.endswith(ca)):
            return True, reg, "known_venue_address_match"
        return False, None, "address_conflicts_or_incomplete"

    # 施設専用のイベント一覧で、詳細側にも会場/住所の記載がない場合に限り既定会場を使う。
    if default_venue and not a:
        default_reg = lookup_venue(default_venue)
        subvenue_terms = ("大ホール", "小ホール", "展示ホール", "ホール", "会議室", "体育館", "校庭",
                          "教室", "講堂", "館内", "本館", "施設内", "公園内", "園内", "園内各所",
                          "広場", "多目的室", "研修室", "スタジオ", "校内")
        is_internal_space = bool(v) and any(term in v for term in subvenue_terms)
        if default_reg and "江戸川区" in norm(default_reg.get("address", "")):
            if not v:
                return True, default_reg, "source_bound_venue"
            if is_internal_space:
                return True, default_reg, "source_bound_subvenue"
    if a:
        return False, None, "address_not_edogawa"
    return False, None, "location_unverified"

def section_after_heading(lines: list[str], headings: Iterable[str], stop: Iterable[str] = STOP_HEADINGS) -> str:
    headset = tuple(norm(x) for x in headings)
    stopset = tuple(norm(x) for x in stop)
    for i, line in enumerate(lines):
        n = norm(line).strip("：:")
        for h in headset:
            if n == h:
                collected = []
                for nxt in lines[i + 1:i + 7]:
                    nn = norm(nxt)
                    if not nn:
                        continue
                    if nn.rstrip("：:") in stopset or any(nn.startswith(x) and len(nn) > len(x) for x in stopset):
                        break
                    collected.append(nn)
                return " ".join(collected)
            if n.startswith(h + "：") or n.startswith(h + ":"):
                return n[len(h) + 1:].strip()
    return ""


def section_lines_after_heading(lines: list[str], headings: Iterable[str],
                                stop: Iterable[str] = STOP_HEADINGS) -> list[str]:
    """見出し配下の行を保持して返す。開催日の補足・雨天予備日を誤登録しないために使用。"""
    headset = tuple(norm(x) for x in headings)
    stopset = tuple(norm(x) for x in stop)
    for i, line in enumerate(lines):
        n = norm(line).strip("：:")
        if n in headset:
            collected = []
            for nxt in lines[i + 1:i + 10]:
                nn = norm(nxt)
                if not nn:
                    continue
                if nn.rstrip("：:") in stopset or any(nn.startswith(x) and len(nn) > len(x) for x in stopset):
                    break
                collected.append(nn)
            return collected
        for h in headset:
            if n.startswith(h + "：") or n.startswith(h + ":"):
                return [n[len(h) + 1:].strip()]
    return []


def is_contingency_date_line(line: str) -> bool:
    n = norm(line)
    return any(word in n for word in ("雨天の場合", "雨天時", "雨天中止", "荒天の場合", "荒天時",
                                      "予備日", "延期の場合", "延期となる場合", "延期します",
                                      "振替日", "予備日程", "中止の場合"))


def extract_detail(html: str, page_url: str, default_venue: str | None = None) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    for el in soup.find_all(["nav", "footer", "header", "aside", "script", "style"]):
        el.decompose()
    main = soup.find(["main", "article"]) or soup.find(id=re.compile("main|content", re.I)) or soup.body or soup
    title_el = main.find("h1") or soup.find("h1") or main.find(["h2", "h3"])
    title = norm(title_el.get_text(" ", strip=True)) if title_el else ""
    lines = [norm(x) for x in main.get_text("\n", strip=True).splitlines() if norm(x)]
    date_lines = section_lines_after_heading(lines, EVENT_DATE_HEADINGS, stop=STOP_HEADINGS)
    if not date_lines:
        date_lines = [x for x in lines if not any(k in x for k in APPLICATION_DATE_TERMS)]
    date_text = " ".join(x for x in date_lines if not is_contingency_date_line(x))
    spans = parse_date_spans(date_text)
    # 時刻は開催日時欄だけから取る。申込期間・開館時間を誤って開催時刻にしない。
    time_start, time_end = parse_time_range(date_text)
    venue_text = section_after_heading(lines, VENUE_HEADINGS, stop=ADDRESS_HEADINGS + STOP_HEADINGS)
    address_text = section_after_heading(lines, ADDRESS_HEADINGS, stop=("内容", "対象", "定員", "費用", "料金", "問合せ", "主催", "備考"))
    venue_reg = lookup_venue(venue_text) or lookup_venue(title) or lookup_venue(" ".join(lines[:100]))
    # 固定施設の専用イベント一覧では、詳細記事に会場欄がないことがある。
    # その場合だけ、取得元に設定した既知の施設会場を住所マスタに結びつける。
    if not venue_reg and not venue_text and default_venue:
        venue_reg = lookup_venue(default_venue)
    venue = venue_text or (venue_reg.get("name") if venue_reg else "") or (default_venue or "")
    address = address_text
    if not address and venue_reg:
        address = venue_reg.get("address", "")
    image_url = None
    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        image_url = urljoin(page_url, og["content"].strip())
    if not image_url:
        for img in main.find_all("img"):
            src = img.get("src") or img.get("data-src") or img.get("data-original")
            if not src or src.startswith("data:"):
                continue
            candidate = urljoin(page_url, src)
            if not re.search(r"(?:logo|icon|btn|dummy|noimage|1x1)", candidate, re.I):
                image_url = candidate
                break
    return {"title": title, "date_text": date_text, "spans": spans,
            "start_time": time_start, "end_time": time_end, "venue": venue,
            "address": address, "image_url": image_url, "text": " ".join(lines),
            "venue_registry": venue_reg}


def fetch_text(url: str, params: dict[str, Any] | None = None, timeout: int = 15) -> tuple[str | None, dict[str, Any]]:
    """成功ページを実行中だけキャッシュし、一時的な通信障害のみ1回再試行する。"""
    query = urlencode(sorted((params or {}).items()), doseq=True)
    cache_key = url + ("?" + query if query else "")
    with _FETCH_CACHE_LOCK:
        cached = _FETCH_CACHE.get(cache_key)
    if cached:
        body, cached_meta = cached
        return body, {**cached_meta, "cache_hit": True}

    meta = {"url": url, "http_status": None, "bytes": 0, "error": None,
            "attempts": 0, "cache_hit": False}
    transient_statuses = {429, 500, 502, 503, 504}
    for attempt in range(2):
        meta["attempts"] = attempt + 1
        try:
            r = SESSION.get(url, params=params, timeout=timeout)
            meta["http_status"] = r.status_code
            if r.status_code in transient_statuses and attempt == 0:
                time.sleep(0.5)
                continue
            r.raise_for_status()
            r.encoding = r.apparent_encoding or "utf-8"
            body = r.text
            meta["bytes"] = len(r.content)
            with _FETCH_CACHE_LOCK:
                _FETCH_CACHE[cache_key] = (body, dict(meta))
            return body, meta
        except Exception as e:
            meta["error"] = f"{type(e).__name__}: {e}"
            if attempt == 0 and (meta.get("http_status") is None or meta.get("http_status") in transient_statuses):
                time.sleep(0.5)
                continue
            return None, meta
    return None, meta


def _calendar_day(td) -> int | None:
    text = norm(td.get_text(" ", strip=True))
    m = re.match(r"^(\d{1,2})(?:\s*日|\D|$)", text)
    if m and 1 <= int(m.group(1)) <= 31:
        return int(m.group(1))
    for node in td.find_all(["span", "div", "p", "a", "strong"]):
        m = re.fullmatch(r"(\d{1,2})(?:日)?", norm(node.get_text(" ", strip=True)))
        if m and 1 <= int(m.group(1)) <= 31:
            return int(m.group(1))
    return None


def clean_title(title: str) -> str:
    t = norm(title)
    t = re.sub(r"^(?:\d{1,2}月\d{1,2}日?\s*)+", "", t)
    t = re.sub(r"\s*(?:開催のお知らせ|イベント情報)$", "", t)
    return t.strip(" ・|｜")


def make_event(title: str, span: tuple[date, date], venue: str, address: str, url: str,
               source: str, category: str = "地域イベント", default_venue: str | None = None,
               start_time: str | None = None, end_time: str | None = None,
               image_url: str | None = None, description: str = "", priority: int = 4,
               lat: float | None = None, lon: float | None = None) -> Event | None:
    ok, registry, _why = verify_edogawa_location(address, venue, default_venue=default_venue)
    if not ok:
        return None
    # 公開ソース由来のURLでも、出力するリンク・画像はHTTP(S)だけに制限する。
    if url and urlparse(url).scheme.lower() not in ("http", "https"):
        return None
    if image_url:
        image_parsed = urlparse(str(image_url).strip())
        if image_parsed.scheme.lower() not in ("http", "https") or not image_parsed.netloc:
            image_url = None
    if registry:
        venue = venue or registry.get("name", "")
        address = address or registry.get("address", "")
        if lat is None: lat = registry.get("latitude")
        if lon is None: lon = registry.get("longitude")
        quality = registry.get("coordinate_quality") if lat is not None and lon is not None else None
    else:
        quality = "csv_coordinates" if lat is not None and lon is not None else None
    # 地図の誤ピン防止。江戸川区の概略範囲外の座標は採用しない。
    if lat is not None or lon is not None:
        if lat is None or lon is None or not (35.5 <= lat <= 35.9 and 139.7 <= lon <= 140.0):
            lat = lon = None
            quality = None
    return Event(title=clean_title(title), start_date=span[0].isoformat(), end_date=span[1].isoformat(),
                 venue=norm(venue), address=norm(address), url=url, category=category,
                 source=source, source_urls=[url] if url else [], start_time=start_time,
                 end_time=end_time, image_url=image_url, latitude=lat, longitude=lon,
                 coordinate_quality=quality, priority=priority, description=description,
                 verified_location=True)


def event_key(ev: Event) -> str:
    raw = "|".join((norm(ev.title).casefold(), ev.start_date, ev.end_date,
                    norm(ev.venue).casefold(), ev.start_time or "", ev.end_time or ""))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def merge_events(events: Iterable[Event]) -> list[Event]:
    merged: dict[str, Event] = {}
    for ev in events:
        k = event_key(ev)
        if k not in merged:
            merged[k] = ev
            continue
        old = merged[k]
        old.source_urls = sorted(set(old.source_urls + ev.source_urls))
        if ev.priority < old.priority:
            old.priority, old.category, old.source = ev.priority, ev.category, ev.source
        if not old.image_url and ev.image_url: old.image_url = ev.image_url
        if (old.latitude is None or old.longitude is None) and ev.latitude is not None and ev.longitude is not None:
            old.latitude, old.longitude = ev.latitude, ev.longitude
            old.coordinate_quality = ev.coordinate_quality
        if not old.description and ev.description: old.description = ev.description
    return sorted(merged.values(), key=lambda e: (e.start_date, e.start_time or "99:99", e.priority, e.title))


def _parse_csv_reader(raw: str):
    try:
        dialect = csv.Sniffer().sniff(raw[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(raw.lstrip("\ufeff")), dialect=dialect)
    return reader, (reader.fieldnames or [])


def _map_csv_columns(headers: list[str]) -> dict[str, str | None]:
    normalized = {norm(h).casefold(): h for h in headers}
    mapped: dict[str, str | None] = {}
    for field_name, aliases in CSV_ALIASES.items():
        match = next((normalized[norm(a).casefold()] for a in aliases
                      if norm(a).casefold() in normalized), None)
        if not match:
            for h in headers:
                hn = norm(h).casefold()
                if any(norm(a).casefold() in hn for a in aliases if len(a) >= 3):
                    match = h
                    break
        mapped[field_name] = match
    # よくあるCMSの英語camelCase/列名の揺れを補完。内容値は推測せずヘッダー名だけで判定。
    if not mapped.get("title"):
        mapped["title"] = next((h for h in headers if "event" in norm(h).casefold()
                                and any(x in norm(h).casefold() for x in ("title", "name"))), None)
    if not mapped.get("start"):
        mapped["start"] = next((h for h in headers if ("start" in norm(h).casefold() and "date" in norm(h).casefold())
                                or norm(h).casefold() in ("eventdate", "event_date")), None)
    if not mapped.get("end"):
        mapped["end"] = next((h for h in headers if "end" in norm(h).casefold() and "date" in norm(h).casefold()), None)
    if not mapped.get("venue"):
        mapped["venue"] = next((h for h in headers if any(x in norm(h).casefold() for x in ("venue", "place", "facility"))), None)
    return mapped


def _csv_date_span(start_raw: str, end_raw: str) -> tuple[date, date] | None:
    starts = parse_date_spans(start_raw) if start_raw else []
    ends = parse_date_spans(end_raw) if end_raw else []
    if starts:
        start_date = starts[0][0]
        end_date = ends[-1][1] if ends else starts[-1][1]
        if end_date < start_date:
            return None
        if end_date <= TODAY + timedelta(days=550):
            return start_date, end_date
    combined = parse_date_spans(" ".join(x for x in (start_raw, end_raw) if x))
    return combined[0] if combined else None


def parse_csv_source(url: str, name: str, priority: int = 1) -> tuple[list[Event], dict[str, Any]]:
    raw, meta = fetch_text(url, timeout=30)
    status = {"name": name, **meta, "status": "failed", "candidate_rows": 0,
              "accepted": 0, "detail_lookups": 0, "rejected": {}, "headers": []}
    if raw is None:
        status["error"] = meta.get("error") or "download_failed"
        return [], status
    reader, headers = _parse_csv_reader(raw)
    status["headers"] = headers
    mapped = _map_csv_columns(headers)
    status["mapped_columns"] = mapped
    if not mapped.get("title") or not (mapped.get("start") or mapped.get("end")):
        status["status"] = "schema_unrecognized"
        status["error"] = "イベント名または開催日列を特定できません。既存データは上書きしません。"
        return [], status

    events: list[Event] = []
    detail_cache: dict[str, dict[str, Any]] = {}
    for row in reader:
        status["candidate_rows"] += 1
        def get(k: str) -> str:
            col = mapped.get(k)
            return norm(row.get(col, "")) if col else ""

        title, venue, address = get("title"), get("venue"), get("address")
        start_raw, end_raw = get("start"), get("end")
        span = _csv_date_span(start_raw, end_raw)
        if not title or not span:
            status["rejected"]["no_title_or_date"] = status["rejected"].get("no_title_or_date", 0) + 1
            continue

        municipality = get("city")
        municipality_code = re.sub(r"\D", "", get("city_code"))
        prefecture = get("pref")
        if municipality and "江戸川区" not in municipality:
            status["rejected"]["municipality_not_edogawa"] = status["rejected"].get("municipality_not_edogawa", 0) + 1
            continue
        if municipality_code and municipality_code != "131237":
            status["rejected"]["municipality_code_not_edogawa"] = status["rejected"].get("municipality_code_not_edogawa", 0) + 1
            continue
        if prefecture and "東京都" not in prefecture:
            status["rejected"]["prefecture_not_tokyo"] = status["rejected"].get("prefecture_not_tokyo", 0) + 1
            continue
        if (municipality and "江戸川区" in municipality or municipality_code == "131237") and address and "江戸川区" not in address:
            address = "東京都江戸川区" + address

        url_v = get("url") or url
        image = get("image") or None
        description = get("description")
        # オープンデータで場所が省略される場合、レコードの詳細URLからのみ補完する。
        ok, _, _ = verify_edogawa_location(address, venue)
        if not ok and url_v != url and url_v.startswith(("https://", "http://")) and url_v not in detail_cache and status["detail_lookups"] < 80:
            dhtml, dmeta = fetch_text(url_v, timeout=12)
            status["detail_lookups"] += 1
            if dhtml:
                detail_cache[url_v] = extract_detail(dhtml, url_v)
            else:
                detail_cache[url_v] = {"error": dmeta.get("error")}
        detail = detail_cache.get(url_v, {})
        if detail:
            venue = venue or detail.get("venue", "")
            address = address or detail.get("address", "")
            image = image or detail.get("image_url")
            description = description or detail.get("text", "")[:1000]
        # CSV座標は緯度経度の両方が存在し、住所が区内と確認できるときだけ採用。
        try:
            lat = float(get("lat")) if get("lat") else None
            lon = float(get("lon")) if get("lon") else None
        except ValueError:
            lat = lon = None
        time_s, time_e = get("start_time") or None, get("end_time") or None
        if not (time_s and time_e):
            time_s, time_e = parse_time_range(start_raw + " " + end_raw)
        ev = make_event(title, span, venue, address, url_v, name,
                        category="区公式オープンデータ", default_venue=None,
                        start_time=time_s, end_time=time_e, image_url=image,
                        description=description, priority=priority, lat=lat, lon=lon)
        if ev:
            events.append(ev)
            status["accepted"] += 1
        else:
            status["rejected"]["outside_or_unverified_location"] = status["rejected"].get("outside_or_unverified_location", 0) + 1
    status["status"] = "ok" if events else "zero_valid_rows"
    return events, status


def parse_venue_catalog(url: str, name: str) -> dict[str, Any]:
    """東京都OpenDataの会場/公共施設CSVを会場辞書へ反映する。区内住所を確認できる行だけ採用。"""
    raw, meta = fetch_text(url, timeout=30)
    status = {"name": name, **meta, "status": "failed", "candidate_rows": 0,
              "accepted": 0, "headers": [], "rejected": {}}
    if raw is None:
        status["error"] = meta.get("error") or "download_failed"
        return status
    reader, headers = _parse_csv_reader(raw)
    status["headers"] = headers
    # 施設データセットごとの名称・住所・座標名の揺れに対応
    normalized = {norm(h).casefold(): h for h in headers}
    def find_col(candidates: tuple[str, ...]) -> str | None:
        for a in candidates:
            if norm(a).casefold() in normalized:
                return normalized[norm(a).casefold()]
        for h in headers:
            hn = norm(h).casefold()
            if any(norm(a).casefold() in hn for a in candidates if len(a) >= 4):
                return h
        return None
    c_name = find_col(("施設名", "施設名称", "場所名称", "会場名", "会場名称", "イベント会場名",
                       "図書館名", "公園名", "学校名", "施設の名称", "名称", "name"))
    c_addr = find_col(("所在地", "住所", "施設所在地", "会場住所", "所在地住所", "住所（所在地）", "address"))
    c_city = find_col(("市区町村名", "市町村名", "自治体名", "city_name", "city"))
    c_code = find_col(("市区町村コード", "全国地方公共団体コード", "localgov_code", "city_code"))
    c_lat = find_col(("緯度", "latitude", "lat", "緯度（世界測地系）", "緯度(WGS84)"))
    c_lon = find_col(("経度", "longitude", "lon", "lng", "経度（世界測地系）", "経度(WGS84)"))
    if not c_addr:
        c_addr = next((h for h in headers if "住所" in norm(h) or "所在地" in norm(h)), None)
    if not c_lat:
        c_lat = next((h for h in headers if "緯度" in norm(h) or "latitude" in norm(h).casefold()), None)
    if not c_lon:
        c_lon = next((h for h in headers if "経度" in norm(h) or "longitude" in norm(h).casefold()), None)
    status["mapped_columns"] = {"name": c_name, "address": c_addr, "city": c_city,
                                "city_code": c_code, "lat": c_lat, "lon": c_lon}
    if not c_name or not c_addr:
        status["status"] = "schema_unrecognized"
        return status
    for row in reader:
        status["candidate_rows"] += 1
        name_value = norm(row.get(c_name, ""))
        address = norm(row.get(c_addr, ""))
        city = norm(row.get(c_city, "")) if c_city else ""
        code = re.sub(r"\D", "", norm(row.get(c_code, ""))) if c_code else ""
        if city and "江戸川区" not in city:
            status["rejected"]["municipality_not_edogawa"] = status["rejected"].get("municipality_not_edogawa", 0) + 1
            continue
        if code and code != "131237":
            status["rejected"]["municipality_code_not_edogawa"] = status["rejected"].get("municipality_code_not_edogawa", 0) + 1
            continue
        if "江戸川区" not in address and (city or code == "131237" or not city and not code):
            # この2つのCSVは江戸川区単位の公式データセット。自治体列が省略された行のみ区名を補う。
            address = "東京都江戸川区" + address
        if not name_value or "江戸川区" not in address or contains_outside_region(address):
            status["rejected"]["location_not_verified"] = status["rejected"].get("location_not_verified", 0) + 1
            continue
        try:
            lat = float(norm(row.get(c_lat, ""))) if c_lat else None
            lon = float(norm(row.get(c_lon, ""))) if c_lon else None
        except ValueError:
            lat = lon = None
        # あり得ない座標は捨て、住所情報は保持する
        if lat is not None and lon is not None and not (35.5 <= lat <= 35.9 and 139.7 <= lon <= 140.0):
            lat = lon = None
        VENUES[name_value] = {"name": name_value, "address": address,
                              "latitude": lat, "longitude": lon,
                              "coordinate_quality": "tokyo_open_data" if lat is not None and lon is not None else "address_verified_no_coordinate"}
        status["accepted"] += 1
    status["status"] = "ok" if status["accepted"] else "zero_valid_rows"
    return status

def _same_host(candidate: str, base: str) -> bool:
    try:
        c, b = urlparse(candidate).hostname, urlparse(base).hostname
        return bool(c and b and (c == b or c.endswith("." + b) or b.endswith("." + c)))
    except Exception:
        return False


def fetch_detail_job(item: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    html, meta = fetch_text(item["url"], timeout=item.get("timeout", 12))
    if not html:
        return item, {"error": meta.get("error"), "http_status": meta.get("http_status")}
    return item, {**extract_detail(html, item["url"], default_venue=item.get("default_venue")),
                  "http_status": meta.get("http_status"), "bytes": meta.get("bytes")}


def parse_city_calendar(months: int = 12) -> tuple[list[Event], dict[str, Any]]:
    """月表示(type=1)に加え、折りたたみを含む一覧表示(type=2)からも詳細URLを集める。"""
    status = {"name": "江戸川区公式イベントカレンダー", "status": "running", "months": [],
              "candidate_urls": 0, "detail_success": 0, "accepted": 0, "rejected": {},
              "calendar_view_links": 0, "list_view_links": 0,
              "calendar_months_successful": 0, "calendar_months_requested": months,
              "day_detail_pages_requested": 0, "day_detail_pages_success": 0,
              "day_detail_event_links": 0}
    candidates: dict[str, dict[str, Any]] = {}
    day_detail_pages: dict[str, date] = {}
    for i in range(months):
        y = TODAY.year + (TODAY.month - 1 + i) // 12
        m = (TODAY.month - 1 + i) % 12 + 1
        for site_id, site_label in CITY_CALENDAR_SITES:
            for view_type in (1, 2):
                html, meta = fetch_text(CITY_BASE, params={"type": str(view_type), "year": y,
                                                            "month": m, "siteid": str(site_id)}, timeout=18)
                status["months"].append({"year": y, "month": m, "siteid": site_id,
                                         "site_label": site_label, "type": view_type,
                                         "http_status": meta.get("http_status"),
                                         "bytes": meta.get("bytes"), "error": meta.get("error")})
                if not html:
                    continue
                soup = BeautifulSoup(html, "html.parser")
                # type=1の日別セルは信頼できる開催日情報として保持
                if view_type == 1:
                    for td in soup.find_all("td"):
                        day = _calendar_day(td)
                        if not day:
                            continue
                        try:
                            current = date(y, m, day)
                        except ValueError:
                            continue
                        if current < TODAY:
                            continue
                        for a in td.find_all("a", href=True):
                            href = a.get("href", "").strip()
                            if not href:
                                continue
                            title = clean_title(a.get_text(" ", strip=True))
                            full = urljoin(CITY_BASE, href)
                            if "calendar.cgi" in href:
                                # 日別「もっと見る」はtype=3。5件表示の先にある全イベントを取得する。
                                q = parse_qs(urlparse(full).query)
                                if q.get("type", [""])[0] == "3":
                                    try:
                                        dd = date(int(q.get("year", [str(y)])[0]),
                                                  int(q.get("month", [str(m)])[0]),
                                                  int(q.get("day", [str(day)])[0]))
                                        if dd >= TODAY:
                                            day_detail_pages[full] = dd
                                    except (TypeError, ValueError):
                                        pass
                                continue
                            if not title or len(title) < 3 or any(k in title for k in EXCLUDED_TEXT):
                                continue
                            path = urlparse(full).path
                            if not _same_host(full, CITY_BASE) or not ("/event/" in path or path.endswith(".html")):
                                continue
                            rec = candidates.setdefault(full, {"url": full, "title": title, "dates": set(),
                                                                "listed_spans": [], "listed_start_time": None,
                                                                "listed_end_time": None, "image": None})
                            rec["dates"].add(current)
                            status["calendar_view_links"] += 1
                            if not rec.get("image"):
                                img = td.find("img")
                                if img:
                                    src = img.get("src") or img.get("data-src")
                                    if src:
                                        rec["image"] = urljoin(CITY_BASE, src)
                else:
                    # type=2は「もっと見る」の先も含む一覧。本文の日付文字列は掲載日混在のため開催日の根拠に使わない。
                    for a in soup.find_all("a", href=True):
                        href = a.get("href", "").strip()
                        if not href:
                            continue
                        title = clean_title(a.get_text(" ", strip=True))
                        full = urljoin(CITY_BASE, href)
                        if "calendar.cgi" in href:
                            q = parse_qs(urlparse(full).query)
                            if q.get("type", [""])[0] == "3":
                                try:
                                    dd = date(int(q.get("year", [str(y)])[0]),
                                              int(q.get("month", [str(m)])[0]),
                                              int(q.get("day", ["0"])[0]))
                                    if dd >= TODAY:
                                        day_detail_pages[full] = dd
                                except (TypeError, ValueError):
                                    pass
                            continue
                        if not title or len(title) < 3 or any(k in title for k in EXCLUDED_TEXT):
                            continue
                        path = urlparse(full).path
                        if not _same_host(full, CITY_BASE) or not ("/event/" in path or path.endswith(".html")):
                            continue
                        candidates.setdefault(full, {"url": full, "title": title, "dates": set(),
                                                      "listed_spans": [], "listed_start_time": None,
                                                      "listed_end_time": None, "image": None})
                        status["list_view_links"] += 1
    # 各日のtype=3は、一覧で省略された6件目以降も含む完全な日別リスト。
    status["day_detail_pages_requested"] = len(day_detail_pages)
    def read_day_page(entry):
        page_url, page_date = entry
        page_html, meta = fetch_text(page_url, timeout=16)
        return page_url, page_date, page_html, meta
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(read_day_page, entry) for entry in day_detail_pages.items()]
        for fut in as_completed(futures):
            page_url, page_date, page_html, meta = fut.result()
            if not page_html:
                status.setdefault("day_detail_failures", []).append({"url": page_url, "error": meta.get("error")})
                continue
            status["day_detail_pages_success"] += 1
            day_soup = BeautifulSoup(page_html, "html.parser")
            for a in day_soup.find_all("a", href=True):
                href = a.get("href", "").strip()
                raw = norm(a.get_text(" ", strip=True))
                if not href or not raw or len(raw) < 3:
                    continue
                full = urljoin(page_url, href)
                path = urlparse(full).path
                if not _same_host(full, CITY_BASE) or not ("/event/" in path or path.endswith(".html")):
                    continue
                # 日別詳細リンクのテキストには開催日・時刻が記載される。開催日だけを採用。
                m_date = re.search(r"20\d{2}年\d{1,2}月\d{1,2}日", raw)
                title = raw[:m_date.start()].strip(" ・|｜") if m_date else clean_title(raw)
                title = clean_title(title)
                if not title or len(title) < 3 or any(k in title for k in EXCLUDED_TEXT):
                    continue
                rec = candidates.setdefault(full, {"url": full, "title": title, "dates": set(),
                                                    "listed_spans": [], "listed_start_time": None,
                                                    "listed_end_time": None, "image": None})
                spans_from_day = parse_date_spans(raw)
                if spans_from_day:
                    rec["listed_spans"].extend(spans_from_day)
                else:
                    rec["dates"].add(page_date)
                ts, te = parse_time_range(raw)
                if ts and not rec.get("listed_start_time"):
                    rec["listed_start_time"], rec["listed_end_time"] = ts, te
                status["day_detail_event_links"] += 1
    status["candidate_urls"] = len(candidates)
    status["day_detail_page_failures"] = len(status.get("day_detail_failures", []))
    status["day_detail_coverage_ratio"] = (
        round(status["day_detail_pages_success"] / status["day_detail_pages_requested"], 3)
        if status["day_detail_pages_requested"] else 1.0
    )
    site_coverage = {}
    for site_id, site_label in CITY_CALENDAR_SITES:
        ok_months = {(x["year"], x["month"]) for x in status["months"]
                     if x.get("siteid") == site_id and x.get("http_status") == 200}
        site_coverage[str(site_id)] = {"label": site_label, "months_successful": len(ok_months),
                                       "months_requested": months}
    status["site_coverage"] = site_coverage
    primary_months = {(x["year"], x["month"]) for x in status["months"]
                      if x.get("siteid") == 6 and x.get("http_status") == 200}
    status["calendar_months_successful"] = len(primary_months)
    jobs = [{"url": u, "title": x["title"], "default_venue": None} for u, x in candidates.items()]
    details: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(fetch_detail_job, it) for it in jobs]
        for fut in as_completed(futures):
            item, data = fut.result()
            details[item["url"]] = data

    events: list[Event] = []
    for url, item in candidates.items():
        d = details.get(url, {})
        if d.get("http_status"):
            status["detail_success"] += 1
        title = d.get("title") or item["title"]
        venue, address = d.get("venue") or "", d.get("address") or ""
        reg = d.get("venue_registry") or lookup_venue(f"{venue} {title}")
        if not venue and reg:
            venue = reg.get("name", "")
        if not address and reg:
            address = reg.get("address", "")
        ok, _, reason = verify_edogawa_location(address, venue)
        if not ok:
            status["rejected"][reason] = status["rejected"].get(reason, 0) + 1
            continue
        # 詳細の開催日を優先。詳細ページが開催日を持たないときだけ、type=1日付セルを利用。
        spans = d.get("spans") or sorted(set(item.get("listed_spans", [])))
        if not spans:
            spans = [(x, x) for x in sorted(item["dates"])]
        event_start_time = d.get("start_time") or item.get("listed_start_time")
        event_end_time = d.get("end_time") or item.get("listed_end_time")
        for span in spans:
            if span[1] < TODAY:
                continue
            ev = make_event(title, span, venue, address, url, "江戸川区公式イベントカレンダー",
                            category="区公式", start_time=event_start_time, end_time=event_end_time,
                            image_url=d.get("image_url") or item.get("image"), priority=1)
            if ev:
                events.append(ev); status["accepted"] += 1
            else:
                status["rejected"]["outside_or_unverified_location"] = status["rejected"].get("outside_or_unverified_location", 0) + 1
    if events:
        status["status"] = "ok"
    elif status["calendar_months_successful"] == 0:
        status["status"] = "calendar_fetch_failed"
    elif not candidates:
        status["status"] = "no_event_candidates"
    elif not status["detail_success"]:
        status["status"] = "detail_fetch_failed"
    else:
        status["status"] = "zero_valid_events"
    return events, status

def discover_feed_urls(soup: BeautifulSoup, base_url: str) -> list[str]:
    """RSS/Atom feed links, if the source publishes them. Same-host only."""
    found: list[str] = []
    for tag in soup.find_all("link", href=True):
        rel = tag.get("rel", [])
        rel_text = " ".join(rel) if isinstance(rel, list) else str(rel)
        typ = norm(tag.get("type", "")).lower()
        href = tag.get("href", "").strip()
        if "alternate" in rel_text.lower() and any(x in typ for x in ("rss", "atom", "xml")):
            full = urljoin(base_url, href)
            if _same_host(full, base_url) and full not in found:
                found.append(full)
    for tag in soup.find_all("a", href=True):
        href = tag.get("href", "").strip()
        if re.search(r"(?:^|/)(?:feed|rss|atom)(?:[./?]|$)", href, re.I):
            full = urljoin(base_url, href)
            if _same_host(full, base_url) and full not in found:
                found.append(full)
    return found[:5]


def parse_feed_entries(xml_text: str, base_url: str) -> list[dict[str, str]]:
    """Extract RSS 2.0 / Atom entry title and detail URL; never use feed publication date as event date."""
    try:
        root = ET.fromstring(xml_text.lstrip("\ufeff"))
    except ET.ParseError:
        return []
    out: list[dict[str, str]] = []
    for node in root.iter():
        local = node.tag.rsplit("}", 1)[-1].lower() if isinstance(node.tag, str) else ""
        if local not in ("item", "entry"):
            continue
        title = ""
        link = ""
        description = ""
        for child in list(node):
            child_name = child.tag.rsplit("}", 1)[-1].lower() if isinstance(child.tag, str) else ""
            if child_name == "title" and child.text:
                title = norm(child.text)
            elif child_name == "link":
                link = child.attrib.get("href", "") or (child.text or "")
            elif child_name in ("description", "summary", "content", "encoded") and child.text:
                description = norm(BeautifulSoup(child.text, "html.parser").get_text(" ", strip=True))
        full = urljoin(base_url, link.strip()) if link else ""
        if title and full and _same_host(full, base_url):
            out.append({"title": title, "url": full, "context": description[:1200], "time_text": ""})
    return out

def parse_listing_source(cfg: dict[str, Any]) -> tuple[list[Event], dict[str, Any]]:
    name, url = cfg["name"], cfg["url"]
    html, meta = fetch_text(url, timeout=cfg.get("timeout", 16))
    status = {"name": name, **meta, "status": "failed", "total_links": 0,
              "same_site_links": 0, "candidate_links": 0,
              "detail_success": 0, "accepted": 0, "rejected": {}}
    if not html:
        status["error"] = meta.get("error")
        return [], status
    soup = BeautifulSoup(html, "html.parser")
    for el in soup.find_all(["nav", "footer", "header", "aside", "script", "style"]):
        el.decompose()
    candidates: dict[str, dict[str, Any]] = {}
    feed_urls = discover_feed_urls(soup, url)
    status["feed_urls"] = feed_urls
    status["feed_candidates"] = 0
    for feed_url in feed_urls:
        feed_raw, feed_meta = fetch_text(feed_url, timeout=12)
        status.setdefault("feed_checks", []).append({"url": feed_url, **feed_meta})
        if not feed_raw:
            continue
        for entry in parse_feed_entries(feed_raw, url):
            if entry["url"] not in candidates and (cfg.get("accept_all_detail_links") or
                    any(w in (entry["title"] + " " + entry["context"]) for w in EVENT_WORDS)):
                candidates[entry["url"]] = entry
                status["feed_candidates"] += 1
    all_anchors = soup.find_all("a", href=True)
    status["total_links"] = len(all_anchors)
    for a in all_anchors:
        href = a.get("href", "").strip()
        title = clean_title(a.get_text(" ", strip=True))
        if not href or href.startswith(("#", "mailto:")) or not title or len(title) < 3:
            continue
        if any(k in title for k in EXCLUDED_TEXT):
            continue
        full = urljoin(url, href)
        if not _same_host(full, url) or full.rstrip("/") == url.rstrip("/"):
            continue
        path = urlparse(full).path.lower()
        if path.endswith((".pdf", ".jpg", ".png", ".jpeg", ".zip")):
            continue
        parent = a.find_parent(["article", "li", "tr", "section", "div"]) or a.parent
        context = norm(parent.get_text(" ", strip=True)) if parent else title
        time_el = parent.find("time") if parent else None
        time_text = norm(time_el.get("datetime", "") + " " + time_el.get_text(" ", strip=True)) if time_el else ""
        if not any(w in (title + " " + context) for w in EVENT_WORDS) and not cfg.get("accept_all_detail_links"):
            continue
        candidates.setdefault(full, {"url": full, "title": title, "context": context[:1200], "time_text": time_text})
    status["candidate_links"] = len(candidates)
    selected = list(candidates.values())[:cfg.get("max_details", 60)]
    status["truncated_candidates"] = max(0, len(candidates) - len(selected))
    jobs = [{"url": x["url"], "title": x["title"], "default_venue": cfg.get("default_venue")} for x in selected]
    details: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(fetch_detail_job, it) for it in jobs]
        for fut in as_completed(futures):
            item, data = fut.result()
            details[item["url"]] = data
    events: list[Event] = []
    for rec in selected:
        d = details.get(rec["url"], {})
        if d.get("http_status"):
            status["detail_success"] += 1
        title = d.get("title") or rec["title"]
        # 一覧カードの本文には掲載日が混在するため、詳細ページの開催日を優先。
        # 詳細に開催日がない場合は、<time datetime> が明示されたときだけ補完。
        spans = d.get("spans") or parse_date_spans(rec.get("time_text", ""))
        if not spans:
            status["rejected"]["no_event_date"] = status["rejected"].get("no_event_date", 0) + 1
            continue
        if cfg.get("require_public_keyword") and not any(k in f"{title} {rec['context']} {d.get('text', '')}" for k in cfg["require_public_keyword"]):
            status["rejected"]["not_public_event_keyword"] = status["rejected"].get("not_public_event_keyword", 0) + 1
            continue
        venue, address = d.get("venue") or "", d.get("address") or ""
        if not venue:
            r = lookup_venue(f"{title} {rec['context']}")
            if r:
                venue = r.get("name", "")
        ok, _, reason = verify_edogawa_location(address, venue, default_venue=cfg.get("default_venue"))
        if not ok:
            status["rejected"][reason] = status["rejected"].get(reason, 0) + 1
            continue
        for span in spans:
            ev = make_event(title, span, venue, address, rec["url"], name,
                            category=cfg.get("category", "地域イベント"), default_venue=cfg.get("default_venue"),
                            start_time=d.get("start_time"), end_time=d.get("end_time"),
                            image_url=d.get("image_url"), priority=cfg.get("priority", 4))
            if ev:
                events.append(ev); status["accepted"] += 1
            else:
                status["rejected"]["outside_or_unverified_location"] = status["rejected"].get("outside_or_unverified_location", 0) + 1
    if events:
        status["status"] = "ok"
    elif not candidates:
        status["status"] = "no_event_candidates"
    elif not status["detail_success"]:
        status["status"] = "detail_fetch_failed"
    else:
        status["status"] = "zero_valid_events"
    return events, status


LISTING_SOURCES = [
    {"name": "葛西海浜公園", "url": "https://kasaikaihinpark.com/event/", "default_venue": "葛西海浜公園", "category": "区内公共・公園", "priority": 2, "max_details": 80},
    {"name": "葛西臨海公園", "url": "https://www.tokyo-park.or.jp/park/kasairinkai/news/index.html", "default_venue": "葛西臨海公園", "category": "区内公共・公園", "priority": 2, "max_details": 80},
    {"name": "篠崎公園", "url": "https://www.tokyo-park.or.jp/park/shinozaki/news/index.html", "default_venue": "篠崎公園", "category": "区内公共・公園", "priority": 2, "max_details": 70},
    {"name": "大島小松川公園", "url": "https://www.tokyo-park.or.jp/park/ojima-komatsugawa/news/index.html", "default_venue": "大島小松川公園", "category": "区内公共・公園", "priority": 2, "max_details": 70},
    {"name": "魔法の文学館", "url": "https://kikismuseum.jp/category/event/", "default_venue": "魔法の文学館", "category": "区内公共・文化", "priority": 2, "max_details": 50},
    {"name": "タワーホール船堀", "url": "https://www.towerhall.jp/events/list/", "default_venue": "タワーホール船堀", "category": "区内公共・文化", "priority": 2, "max_details": 80},
    {"name": "総合文化センター・公演予定", "url": "https://edogawa-bunkacenter.jp/calendar-host-fiscal/", "default_venue": "総合文化センター", "category": "区内公共・文化", "priority": 2, "max_details": 100},
    {"name": "総合文化センター・月間カレンダー", "url": "https://edogawa-bunkacenter.jp/calendar/", "default_venue": "総合文化センター", "category": "区内公共・文化", "priority": 2, "max_details": 100},
    {"name": "葛西臨海水族園", "url": "https://www.tokyo-zoo.net/zoo/kasai/", "default_venue": "葛西臨海水族園", "category": "区内公共・文化", "priority": 2, "max_details": 80},
    {"name": "江戸川レクリエーション公園", "url": "https://edogawa-recreation.com/event/", "default_venue": "総合レクリエーション公園サービスセンター", "category": "区内公共・公園", "priority": 2, "max_details": 100},
    {"name": "新川さくら館", "url": "https://sakurakan-edogawa.jp/topics/", "default_venue": "新川さくら館", "category": "区内公共・文化", "priority": 2, "max_details": 70},
    {"name": "えどがわエコセンター", "url": "https://edogawa-ecocenter.jp/", "default_venue": "えどがわエコセンター", "category": "区内公共・環境", "priority": 2, "max_details": 80},
    {"name": "江戸川区立図書館", "url": "https://www.library.city.edogawa.tokyo.jp/toshow/event/", "default_venue": None, "category": "区内公共・図書館", "priority": 2, "max_details": 80},
    {"name": "江戸川まいぷれ", "url": "https://edogawa.mypl.net/event/", "default_venue": None, "category": "地域・まいぷれ", "priority": 4, "max_details": 120},
    {"name": "アリオ葛西", "url": "https://kasai.ario.jp/event/", "default_venue": "アリオ葛西", "category": "商業施設", "priority": 8, "max_details": 80},
    {"name": "TRXフリーマーケット", "url": "https://trx.jp/", "default_venue": None, "category": "地域・フリーマーケット", "priority": 4, "max_details": 100},
    {"name": "江戸川陸上競技場", "url": "https://www.kubota-spears.com/lp_edogawa-athletics/schedule/", "default_venue": "江戸川陸上競技場", "category": "スポーツ", "priority": 3, "max_details": 80},
    {"name": "江戸川球場", "url": "https://www.edogawa-3field.jp/baseball/taikaiguid/", "default_venue": "江戸川球場", "category": "スポーツ", "priority": 3, "max_details": 80},
]

SCHOOL_SOURCES = [
    ("都立江戸川高校", "https://www.metro.ed.jp/edogawa-h/news/index.html"),
    ("都立小松川高校", "https://www.metro.ed.jp/komatsugawa-h/news/index.html"),
    ("都立小岩高校", "https://www.metro.ed.jp/koiwa-h/news/index.html"),
    ("都立葛西南高校", "https://www.metro.ed.jp/kasaiminami-h/news/index.html"),
    ("都立紅葉川高校", "https://www.metro.ed.jp/momijigawa-h/news/index.html"),
    ("都立篠崎高校", "https://www.metro.ed.jp/shinozaki-h/news/index.html"),
]


def parse_tsuruoka_source() -> tuple[list[Event], dict[str, Any]]:
    """鶴岡市ページから、江戸川区内の駅前会場が明記された物産展だけを抽出。"""
    url = "https://www.city.tsuruoka.lg.jp/kanko/kankou-sonota/tokyo01.html"
    html, meta = fetch_text(url, timeout=18)
    status = {"name": "鶴岡市・江戸川区内物産展", **meta, "status": "failed",
              "candidate_blocks": 0, "accepted": 0, "rejected": {}}
    if not html:
        return [], status
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    base_title = norm(h1.get_text(" ", strip=True)) if h1 else "鶴岡市観光物産展"
    images = soup.find_all("meta", property="og:image")
    image_url = urljoin(url, images[0].get("content", "")) if images and images[0].get("content") else None
    out: list[Event] = []
    seen: set[tuple[str, str]] = set()
    for block in soup.find_all(["tr", "li", "p", "div", "section"]):
        text = norm(block.get_text(" ", strip=True))
        if not text or len(text) > 400:
            continue
        has_nishikasai = "西葛西" in text
        has_funabori = "船堀" in text
        if has_nishikasai == has_funabori:
            continue
        venue = "西葛西駅 南口広場" if has_nishikasai else "船堀駅 北口広場"
        spans = parse_date_spans(text)
        if not spans:
            continue
        status["candidate_blocks"] += 1
        reg = lookup_venue(venue)
        if not reg:
            status["rejected"]["venue_not_registered"] = status["rejected"].get("venue_not_registered", 0) + 1
            continue
        for span in spans:
            fingerprint = (venue, f"{span[0]}_{span[1]}")
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            title = f"{base_title}（{venue}）"
            ev = make_event(title, span, venue, reg.get("address", ""), url,
                            "鶴岡市公式・江戸川区内会場", category="友好都市・物産展",
                            image_url=image_url, priority=4)
            if ev:
                out.append(ev); status["accepted"] += 1
    status["status"] = "ok" if out else ("no_event_candidates" if not status["candidate_blocks"] else "zero_valid_events")
    return out, status


def parse_kviewer_api() -> tuple[list[Event], dict[str, Any]]:
    api_url = os.environ.get("KVIEWER_API_URL", "").strip()
    status = {"name": "江戸川区スポーツKintone/KViewer", "status": "not_configured",
              "viewer_url": KVIEWER_PUBLIC_URL, "accepted": 0,
              "note": "KViewerの外部公開API URLが未設定のため未取得"}
    if not api_url:
        return [], status
    raw, meta = fetch_text(api_url, timeout=25)
    status.update(meta)
    if not raw:
        status["status"] = "failed"
        return [], status
    try:
        obj = json.loads(raw)
    except Exception as e:
        status["status"] = "invalid_json"; status["error"] = str(e)
        return [], status
    records = obj.get("records", obj.get("data", [])) if isinstance(obj, dict) else obj
    if isinstance(records, dict):
        records = records.get("records", [])
    if not isinstance(records, list):
        status["status"] = "schema_unrecognized"
        status["error"] = "records/data が配列ではありません"
        status["response_keys"] = sorted(obj.keys()) if isinstance(obj, dict) else []
        return [], status
    status["record_count"] = len(records)
    status["record_fields"] = sorted(records[0].keys()) if records and isinstance(records[0], dict) else []
    if records:
        field_names = {norm(k).casefold() for k in status["record_fields"]}
        title_keys = {norm(k).casefold() for k in ("イベント名", "title", "event_name", "名称")}
        date_keys = {norm(k).casefold() for k in ("開催日", "開催日時", "開始日", "date", "start_date")}
        if not field_names.intersection(title_keys) or not field_names.intersection(date_keys):
            status["status"] = "schema_unrecognized"
            status["error"] = "イベント名/開催日フィールドを特定できません"
            return [], status
    events: list[Event] = []
    for row in records:
        if not isinstance(row, dict):
            continue
        def pick(keys):
            for k in keys:
                v = row.get(k)
                if isinstance(v, dict): v = v.get("value")
                if v not in (None, ""): return norm(v)
            return ""
        title = pick(("イベント名", "title", "event_name", "名称"))
        dtext = pick(("開催日", "開催日時", "開始日", "date", "start_date"))
        venue = pick(("会場", "場所", "施設名", "venue"))
        address = pick(("住所", "所在地", "address"))
        link = pick(("URL", "詳細URL", "url")) or api_url
        for span in parse_date_spans(dtext):
            ev = make_event(title, span, venue, address, link, "Kintone公開API",
                            category="区内スポーツ", priority=3)
            if ev: events.append(ev)
    status["status"] = "ok" if events else ("zero_records" if not records else "zero_valid_events")
    status["accepted"] = len(events)
    return events, status


def scrape_all(months: int = 12, include_supplements: bool = True) -> tuple[list[Event], dict[str, Any]]:
    all_events: list[Event] = []
    previous_reports: dict[str, Any] = {}
    try:
        previous_obj = json.loads(OUTPUT_STATUS.read_text(encoding="utf-8"))
        if previous_obj.get("mode") == "live":
            previous_reports = previous_obj.get("sources", {})
        elif previous_obj.get("sources"):
            previous_reports = previous_obj.get("sources", {})
    except Exception:
        pass
    reports: dict[str, Any] = {"mode": "live", "coverage_complete": False,
                               "generated_at": datetime.now(JST).isoformat(),
                               "timezone": "Asia/Tokyo", "months_requested": months,
                               "scope": "江戸川区内の開催場所を確認できたイベントのみ",
                               "sources": {}, "warnings": [],
                               "limitations": ["取得元の全ページを網羅できたかはサイト構造・公開範囲に依存します。",
                                               "RSS/Atomは発見された場合のみ詳細URLの発見経路として利用します。",
                                               "Kintone/KViewerは外部公開APIが別途設定された場合のみ取得します。"]}
    # 会場マスタを先にロードし、イベントCSVの場所名照合に利用する。
    for name, url in (
        ("東京都OpenData公共施設一覧CSV", TOKYO_FACILITY_CSV),
        ("東京都OpenData地域イベント会場CSV", TOKYO_EVENTSPACE_CSV),
    ):
        reports["sources"][name] = parse_venue_catalog(url, name)
    # 区公式の地図CSVを後から適用し、位置精度・更新日が明確な区の座標を優先する。
    for name, url in CITY_MAP_CSV_SOURCES:
        reports["sources"][name] = parse_venue_catalog(url, name)
    try:
        VENUES_FILE.write_text(json.dumps(VENUES, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        reports.setdefault("warnings", []).append(f"会場マスタ保存失敗: {e}")

    for name, url, prio in (
        ("江戸川区イベントカレンダーCSV", CITY_EVENT_CSV, 1),
        ("東京都OpenData江戸川区イベント一覧CSV", TOKYO_EVENT_CSV, 1),
    ):
        events, status = parse_csv_source(url, name, priority=prio)
        all_events.extend(events); reports["sources"][name] = status

    official_events, official_status = parse_city_calendar(months=months)
    all_events.extend(official_events)
    reports["sources"]["江戸川区公式イベントカレンダー"] = official_status

    if include_supplements:
        for cfg in LISTING_SOURCES:
            events, status = parse_listing_source(cfg)
            all_events.extend(events); reports["sources"][cfg["name"]] = status
        public_terms = ("文化祭", "学園祭", "公開", "説明会", "体験", "公演", "ライブ",
                        "コンサート", "展示", "フェスタ", "まつり", "バザー", "見学会", "教室")
        for label, url in SCHOOL_SOURCES:
            cfg = {"name": label, "url": url, "default_venue": label,
                   "category": "学校公開イベント", "priority": 5, "max_details": 50,
                   "require_public_keyword": public_terms}
            events, status = parse_listing_source(cfg)
            all_events.extend(events); reports["sources"][label] = status
        tsuruoka_events, tsuruoka_status = parse_tsuruoka_source()
        all_events.extend(tsuruoka_events); reports["sources"]["鶴岡市・江戸川区内物産展"] = tsuruoka_status
        kv_events, kv_status = parse_kviewer_api()
        all_events.extend(kv_events); reports["sources"]["Kintone/KViewer"] = kv_status

    merged = merge_events(all_events)
    reports["totals"] = {
        "raw_records": len(all_events), "unique_events": len(merged),
        "with_coordinates": sum(1 for e in merged if e.latitude is not None and e.longitude is not None),
        "without_coordinates": sum(1 for e in merged if e.latitude is None or e.longitude is None),
        "strict_edogawa_only": True,
    }
    # 前回は件数があった取得元が今回0件なら、静かに正常扱いせず警告として可視化。
    for source_name, source_report in reports["sources"].items():
        previous = previous_reports.get(source_name, {})
        old_count = int(previous.get("accepted", 0) or 0)
        now_count = int(source_report.get("accepted", 0) or 0)
        source_report["previous_accepted"] = old_count
        if old_count > 0 and now_count == 0 and source_report.get("status") not in ("not_configured",):
            source_report["regression_warning"] = True
            reports["warnings"].append(f"{source_name}: 前回{old_count}件→今回0件。HTML変更/掲載なし/日付条件を確認")
    for source_name, source_report in reports["sources"].items():
        print(f"[SOURCE] {source_name}: status={source_report.get('status')} "
              f"accepted={source_report.get('accepted', 0)} "
              f"candidates={source_report.get('candidate_rows', source_report.get('candidate_links', source_report.get('candidate_urls', '-')))}")
    city_status = reports["sources"].get("江戸川区公式イベントカレンダー", {})
    if city_status.get("day_detail_page_failures", 0):
        reports["warnings"].append(
            f"公式カレンダーの日別type=3取得に失敗: {city_status['day_detail_page_failures']} / "
            f"{city_status.get('day_detail_pages_requested', 0)} pages"
        )
    for warning in reports["warnings"]:
        print(f"[WARNING] {warning}")
    return merged, reports


def escape_ics(text: str) -> str:
    # iCalendar TEXT escaping: retain intended line breaks as \n rather than flattening them.
    t = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    t = t.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    return t.replace("\n", "\\n")


def fold_ics(line: str) -> str:
    chunks: list[str] = []
    current = ""
    for ch in line:
        if len((current + ch).encode("utf-8")) > 73:
            chunks.append(current); current = " " + ch
        else:
            current += ch
    if current: chunks.append(current)
    return "\r\n".join(chunks)


def make_uid(ev: Event) -> str:
    # タイトルや終了日が修正されても、同じ詳細ページ・同一開催日のイベントは同じUIDを維持。
    seed = "|".join((ev.url or "", norm(ev.venue).casefold(), ev.start_date, ev.start_time or ""))
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32] + "@edogawa-events.local"


def build_ics(events: list[Event]) -> str:
    now_utc = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Edogawa Ward Events//JP v2.0//EN",
             "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "X-WR-CALNAME:江戸川区内イベントカレンダー",
             "X-WR-TIMEZONE:Asia/Tokyo",
             "X-WR-CALDESC:開催場所が江戸川区内と確認できたイベントのみを収録した非公式カレンダー"]
    for ev in events:
        sd, ed = date.fromisoformat(ev.start_date), date.fromisoformat(ev.end_date)
        lines.extend(["BEGIN:VEVENT", f"UID:{make_uid(ev)}", f"DTSTAMP:{now_utc}", "SEQUENCE:0", "TRANSP:OPAQUE"])
        if ev.start_time and ev.end_time:
            sh, sm = map(int, ev.start_time.split(":")); eh, em = map(int, ev.end_time.split(":"))
            st = datetime.combine(sd, dtime(sh, sm), tzinfo=JST).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
            wraps_midnight = ed == sd and (eh == 24 or (eh, em) <= (sh, sm))
            edate = ed + timedelta(days=1) if wraps_midnight else ed
            et = datetime.combine(edate, dtime(eh % 24, em), tzinfo=JST).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
            lines.extend([f"DTSTART:{st}", f"DTEND:{et}"])
        else:
            lines.extend([f"DTSTART;VALUE=DATE:{sd:%Y%m%d}", f"DTEND;VALUE=DATE:{(ed + timedelta(days=1)):%Y%m%d}"])
        summary = f"[{ev.category}] {ev.title}" if ev.category else ev.title
        lines.append(fold_ics("SUMMARY:" + escape_ics(summary)))
        desc = [f"詳細: {ev.url}", f"会場: {ev.venue}", f"住所: {ev.address}", f"情報元: {ev.source}"]
        if ev.image_url: desc.append(f"画像: {ev.image_url}")
        if ev.description: desc.append(ev.description)
        if ev.source_urls: desc.append("参照元: " + " / ".join(ev.source_urls))
        lines.append(fold_ics("DESCRIPTION:" + escape_ics("\n".join(x for x in desc if x))))
        if ev.url: lines.append(fold_ics("URL:" + ev.url))
        if ev.venue or ev.address:
            lines.append(fold_ics("LOCATION:" + escape_ics(" ".join(x for x in (ev.venue, ev.address) if x))))
        if ev.latitude is not None and ev.longitude is not None:
            lines.append(f"GEO:{ev.latitude:.6f};{ev.longitude:.6f}")
        lines.extend([f"PRIORITY:{max(1, min(9, ev.priority))}",
                      fold_ics("CATEGORIES:" + escape_ics(ev.category.upper().replace("・", "-")))])
        if ev.image_url:
            lines.append(fold_ics("IMAGE;VALUE=URI;DISPLAY=BADGE:" + ev.image_url))
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def to_map_json(events: list[Event], reports: dict[str, Any], mode: str = "live") -> dict[str, Any]:
    items = []
    for ev in events:
        items.append({"id": make_uid(ev), "title": ev.title, "category": ev.category,
                      "start_date": ev.start_date, "end_date": ev.end_date,
                      "start_time": ev.start_time, "end_time": ev.end_time,
                      "all_day": ev.all_day, "timezone": "Asia/Tokyo",
                      "venue": ev.venue, "address": ev.address,
                      "latitude": ev.latitude, "longitude": ev.longitude,
                      "map_ready": ev.latitude is not None and ev.longitude is not None,
                      "coordinate_quality": ev.coordinate_quality,
                      "image_url": ev.image_url, "details_url": ev.url,
                      "source": ev.source, "source_urls": ev.source_urls,
                      "priority": ev.priority, "description": ev.description})
    return {"schema_version": "1.0", "generated_at": datetime.now(JST).isoformat(),
            "timezone": "Asia/Tokyo", "scope": "東京都江戸川区内のみ",
            "mode": mode, "strict_location_filter": True,
            "events": items,
            "map_summary": {"total_events": len(items),
                            "events_with_coordinates": sum(1 for x in items if x["map_ready"]),
                            "events_without_coordinates": sum(1 for x in items if not x["map_ready"])},
            "source_status": reports}


def atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8", newline="")
    os.replace(tmp, path)


def validate_outputs(ics_text: str, events: list[Event]) -> None:
    if "BEGIN:VCALENDAR" not in ics_text or "END:VCALENDAR" not in ics_text:
        raise RuntimeError("iCal structure validation failed")
    if ics_text.count("BEGIN:VEVENT") != ics_text.count("END:VEVENT"):
        raise RuntimeError("VEVENT count mismatch")
    if not events:
        raise RuntimeError("No verified Edogawa events. Previous outputs are preserved.")
    for e in events:
        location_ok, _, reason = verify_edogawa_location(e.address, e.venue)
        if not e.verified_location or not location_ok:
            raise RuntimeError(f"Non-verified/out-of-ward location found ({reason}): {e.title} / {e.venue} / {e.address}")
        if (e.latitude is not None or e.longitude is not None) and (
            e.latitude is None or e.longitude is None
            or not (35.5 <= e.latitude <= 35.9 and 139.7 <= e.longitude <= 140.0)
        ):
            raise RuntimeError(f"Out-of-area coordinates found: {e.title} / {e.latitude}, {e.longitude}")


def bootstrap_events() -> list[Event]:
    """調査した個別詳細ページで開催日・住所が確認できたイベントのみ。網羅版ではない。"""
    rows = [
        ("ペット防災セミナー", "2026-12-12", "2026-12-12", "臨海町2丁目地先", "東京都江戸川区臨海町2丁目地先", "https://www.city.edogawa.tokyo.jp/event/20261212petbosai.html", "14:00", "15:00"),
        ("介護の担い手研修", "2026-12-12", "2026-12-12", "葛西区民館 集会室第1", "東京都江戸川区中葛西3丁目10番1号", "https://www.city.edogawa.tokyo.jp/event/261212kaigo.html", "09:30", "14:35"),
        ("介護の担い手研修", "2026-12-13", "2026-12-13", "葛西区民館 集会室第1", "東京都江戸川区中葛西3丁目10番1号", "https://www.city.edogawa.tokyo.jp/event/261212kaigo.html", "09:30", "15:40"),
        ("人形劇『ねずみのすもう』", "2026-12-12", "2026-12-12", "上一色コミュニティセンター", "東京都江戸川区上一色2丁目6番10号", "https://www.city.edogawa.tokyo.jp/event/261212nezumi-2.html", "14:00", "14:45"),
        ("冬の親子ポニー飼育体験", "2026-12-12", "2026-12-12", "なぎさポニーランド", "東京都江戸川区南葛西7丁目3番", "https://www.city.edogawa.tokyo.jp/event/261206pony.html", "10:00", "11:45"),
        ("サウンド・オブ・サイエンス", "2026-12-12", "2026-12-12", "子ども未来館", "東京都江戸川区篠崎町3丁目12番10号", "https://www.city.edogawa.tokyo.jp/event/2612sound_of_science.html", "14:00", "16:00"),
        ("はじめてロボット教室", "2026-12-12", "2026-12-12", "子ども未来館", "東京都江戸川区篠崎町3丁目12番10号", "https://www.city.edogawa.tokyo.jp/event/2612robot.html", "14:00", "16:00"),
        ("江戸川区郷土資料室企画展「利根川と江戸川区」", "2026-12-12", "2027-03-14", "江戸川区郷土資料室", "東京都江戸川区篠崎町7丁目20番19号 篠崎ツインプレイス3階", "https://www.city.edogawa.tokyo.jp/event/2026kikaku.html", None, None),
        ("一之江抹香亭 江戸園芸植物展示「春の七草展」", "2026-12-12", "2026-12-28", "一之江抹香亭", "東京都江戸川区一之江5丁目13番16号", "https://www.city.edogawa.tokyo.jp/event/makko1212.html", "09:00", "17:00"),
        ("一之江抹香亭 江戸園芸植物展示「春の七草展」", "2027-01-04", "2027-01-07", "一之江抹香亭", "東京都江戸川区一之江5丁目13番16号", "https://www.city.edogawa.tokyo.jp/event/makko1212.html", "09:00", "17:00"),
        ("西洋美術史への扉", "2026-12-12", "2026-12-12", "しのざき文化プラザ", "東京都江戸川区篠崎町7丁目20番9号", "https://www.city.edogawa.tokyo.jp/event/26art.html", "14:00", "15:15"),
        ("西洋美術史への扉", "2027-01-09", "2027-01-09", "しのざき文化プラザ", "東京都江戸川区篠崎町7丁目20番9号", "https://www.city.edogawa.tokyo.jp/event/26art.html", "17:00", "18:15"),
        ("西洋美術史への扉", "2027-01-23", "2027-01-23", "しのざき文化プラザ", "東京都江戸川区篠崎町7丁目20番9号", "https://www.city.edogawa.tokyo.jp/event/26art.html", "14:00", "15:15"),
        ("西洋美術史への扉", "2027-02-13", "2027-02-13", "しのざき文化プラザ", "東京都江戸川区篠崎町7丁目20番9号", "https://www.city.edogawa.tokyo.jp/event/26art.html", "14:00", "15:15"),
        ("西洋美術史への扉", "2027-03-06", "2027-03-06", "しのざき文化プラザ", "東京都江戸川区篠崎町7丁目20番9号", "https://www.city.edogawa.tokyo.jp/event/26art.html", "14:00", "15:15"),
        ("認知症サポーター養成講座", "2026-12-12", "2026-12-12", "うらら一之江", "東京都江戸川区一之江7丁目50番6号", "https://www.city.edogawa.tokyo.jp/event/ninsapo261212.html", "13:30", "15:00"),
        ("第12回 新川みどりとごちそう市＆ミニミニ動物園", "2026-12-06", "2026-12-06", "新川さくら館", "東京都江戸川区船堀7丁目15番12号", "https://www.city.edogawa.tokyo.jp/event/2612_gochiso.html", "10:00", "15:00"),
    ]
    events = []
    for title, sd, ed, venue, address, url, st, et in rows:
        image = "https://www.city.edogawa.tokyo.jp/images/75173/market_zoo1.jpg" if "ごちそう市" in title else None
        ev = make_event(title, (date.fromisoformat(sd), date.fromisoformat(ed)), venue, address, url,
                        "公式ページ確認済み・プレビュー", category="区公式", start_time=st,
                        end_time=et, image_url=image, priority=1)
        if ev: events.append(ev)
    return merge_events(events)


def main() -> int:
    parser = argparse.ArgumentParser(description="江戸川区内イベントiCal/JSON生成")
    parser.add_argument("--months", type=int, default=12, help="公式カレンダーの取得対象月数")
    parser.add_argument("--bootstrap-preview", action="store_true", help="調査済みイベントだけのプレビュー生成")
    parser.add_argument("--skip-supplements", action="store_true", help="補完サイトの取得を省略")
    args = parser.parse_args()
    if args.bootstrap_preview:
        events = bootstrap_events()
        reports = {"mode": "bootstrap-preview", "complete": False,
                   "warning": "2026-11-26時点で個別詳細ページを確認できたイベントのみ。全件収集ではありません。",
                   "sources": {}, "totals": {"unique_events": len(events), "strict_edogawa_only": True}}
        mode = "bootstrap-preview"
        ics = build_ics(events).replace(
            "X-WR-CALNAME:江戸川区内イベントカレンダー",
            "X-WR-CALNAME:江戸川区内イベントカレンダー（確認済みプレビュー・未完成）"
        )
        payload = to_map_json(events, reports, mode=mode)
        validate_outputs(ics, events)
        atomic_write(OUTPUT_PREVIEW_ICS, ics)
        atomic_write(OUTPUT_PREVIEW_JSON, json.dumps(payload, ensure_ascii=False, indent=2))
        atomic_write(OUTPUT_PREVIEW_STATUS, json.dumps(reports, ensure_ascii=False, indent=2))
        print(f"Generated preview: {OUTPUT_PREVIEW_ICS.name} ({len(events)} events)")
        print(f"Generated preview: {OUTPUT_PREVIEW_JSON.name}")
        print(f"Mode: {mode}; complete: false; strict Edogawa-only filter: ON")
        return 0
    else:
        events, reports = scrape_all(months=args.months, include_supplements=not args.skip_supplements)
        mode = "live"
        official = reports["sources"].get("江戸川区公式イベントカレンダー", {})
        recognized_csv = any(
            "CSV" in n and s.get("status") in ("ok", "zero_valid_rows") and s.get("headers")
            and int(s.get("candidate_rows", 0) or 0) > 0
            for n, s in reports["sources"].items()
        )
        primary_event_success = any(
            int(reports["sources"].get(n, {}).get("accepted", 0) or 0) > 0
            for n in ("江戸川区イベントカレンダーCSV", "東京都OpenData江戸川区イベント一覧CSV",
                      "江戸川区公式イベントカレンダー")
        )
        calendar_success = int(official.get("calendar_months_successful", 0) or 0)
        calendar_requested = int(official.get("calendar_months_requested", args.months) or args.months)
        calendar_healthy = calendar_success >= max(1, int(calendar_requested * 0.6 + 0.999))
        reports["primary_source_health"] = {
            "recognized_event_csv": recognized_csv,
            "calendar_months_successful": calendar_success,
            "calendar_months_requested": calendar_requested,
            "calendar_healthy": calendar_healthy,
            "healthy": recognized_csv or calendar_healthy,
            "primary_event_success": primary_event_success,
        }
        if not events or not reports["primary_source_health"]["healthy"] or not primary_event_success:
            atomic_write(OUTPUT_STATUS, json.dumps(reports, ensure_ascii=False, indent=2))
            raise RuntimeError("イベントが0件、一次取得元の稼働確認失敗、または一次情報から有効イベントを確認できません。既存ICS/JSONは保持しました。")
        if not recognized_csv:
            reports["warnings"].append("CSVの認識済み取得がありません。公式カレンダーHTMLを一次情報として使用しています。")
        if official.get("status") != "ok" and not recognized_csv:
            reports["warnings"].append("公式カレンダーで有効イベントを確認できません。source_status.jsonの月別・詳細ページ状態を確認してください。")
        if any("CSV" in n and s.get("status") == "schema_unrecognized" for n, s in reports["sources"].items()):
            reports.setdefault("warnings", []).append("CSV列構成が未認識。CSVは採用せず、公式HTMLカレンダー等で補完しました。")
    ics = build_ics(events)
    payload = to_map_json(events, reports, mode=mode)
    validate_outputs(ics, events)
    atomic_write(OUTPUT_ICS, ics)
    atomic_write(OUTPUT_JSON, json.dumps(payload, ensure_ascii=False, indent=2))
    atomic_write(OUTPUT_STATUS, json.dumps(reports, ensure_ascii=False, indent=2))
    print(f"Generated: {OUTPUT_ICS.name} ({len(events)} events)")
    print(f"Generated: {OUTPUT_JSON.name}")
    print(f"Generated: {OUTPUT_STATUS.name}")
    print(f"With coordinates: {sum(1 for e in events if e.latitude is not None and e.longitude is not None)}")
    print(f"Mode: {mode}; strict Edogawa-only filter: ON")
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        raise
