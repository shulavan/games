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
import difflib
import io
import json
import os
import re
import ssl
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
from requests.adapters import HTTPAdapter
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
JST = ZoneInfo("Asia/Tokyo")
UTC = timezone.utc
TODAY = datetime.now(JST).date()
APP_VERSION = "3.2.8"

OUTPUT_ICS = ROOT / "edogawa_events.ics"
OUTPUT_JSON = ROOT / "edogawa_events.json"
OUTPUT_STATUS = ROOT / "source_status.json"
OUTPUT_FAILED_STATUS = ROOT / "source_status_failed.json"
OUTPUT_PREVIEW_ICS = ROOT / "edogawa_events_preview.ics"
OUTPUT_PREVIEW_JSON = ROOT / "edogawa_events_preview.json"
OUTPUT_PREVIEW_STATUS = ROOT / "source_status_preview.json"
VENUES_FILE = ROOT / "edogawa_venues.json"
GEOCODE_CACHE_FILE = ROOT / "geocoding_cache.json"

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
    "Mozilla/5.0 (compatible; EdogawaEventCalendar/3.0; "
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
# ページ自体がイベントではないことが明らかなタイトル。完全一致・見出し語の双方で除外する。
NON_EVENT_PAGE_TITLES = {
    "くらし手続き", "よくある問い合わせqa", "よくある質問", "faq", "サイトマップ",
    "スポーツ文化", "健康医療福祉", "組織図と組織一覧", "組織図", "組織一覧",
    "区政情報", "区の紹介", "施設案内", "アクセス", "交通アクセス", "お問い合わせ",
    "お知らせ", "ニュース", "イベント情報", "イベント一覧", "新着情報", "トップページ",
    "フロア案内", "フロアガイド", "館内案内", "施設概要", "外国の方向け生活情報",
    "区内の主なイベント", "子育て教育", "まちづくり環境", "施設情報", "people",
    "このページに関するお問い合わせ", "このページに関するお問い合わせ先",
    "このページに関するお問合せ", "このページに関するお問合せ先",
}
NON_EVENT_TITLE_PREFIXES = (
    "サイトマップ", "よくある質問", "よくある問い合わせ", "組織図", "組織一覧",
    "施設案内", "カテゴリ一覧", "カテゴリー一覧", "ジャンル一覧", "お知らせ一覧",
    "ニュース一覧", "イベント一覧", "くらし・手続き", "健康・医療・福祉",
    "フロア案内", "フロアガイド", "館内案内", "外国の方向け生活情報",
    "このページに関するお問い合わせ", "このページに関するお問合せ",
    "区内の主なイベント", "子育て・教育", "まちづくり・環境", "施設情報",
)
MAX_EVENT_LOOKAHEAD_DAYS = 550
MAX_EVENT_DURATION_DAYS = 366

# 江戸川区外の代表的な施設名。会場名だけでなく詳細本文にも適用する。
# 特に施設運営者のニュース一覧に、別施設で開催する催事が掲載されるケースを防ぐ。
OUTSIDE_FACILITY_TERMS = (
    "恩賜上野動物園", "東京都恩賜上野動物園", "上野動物園", "上野公園",
    "上野恩賜公園", "東京国立博物館", "国立科学博物館", "東京都美術館",
    "東京文化会館", "浅草文化観光センター", "東京ビッグサイト",
    "日本科学未来館", "葛飾区郷土と天文の博物館", "すみだ水族館",
    "千葉市動物公園", "市川市動植物園", "浦安市文化会館",
)


def outside_facility_mention(text: str) -> str | None:
    """区外施設名の記載を検出。表記揺れを吸収して最初に一致した施設名を返す。"""
    value = norm(text)
    for term in sorted(OUTSIDE_FACILITY_TERMS, key=len, reverse=True):
        if term in value:
            return term
    return None


APPLICATION_CLOSED_TERMS = ("申込は終了しました", "申し込みは終了しました", "募集終了", "受付終了")
FOOTER_CUTOFF_TERMS = (
    "イベント情報をiPhone・iPad端末", "イベント情報をiPhone/iPad端末",
    "このページに関するお問い合わせ", "このページに関するお問合せ",
    "このページに関するお問い合わせ先", "このページに関するお問合せ先",
)


def normalize_event_url(value: str) -> str:
    """比較用のイベントURL正規形。追跡パラメータとフラグメントのみ除去する。"""
    if not value:
        return ""
    parsed = urlparse(value.strip())
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        return ""
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    query = []
    for key, vals in parse_qs(parsed.query, keep_blank_values=True).items():
        lk = key.casefold()
        if lk.startswith("utm_") or lk in {"fbclid", "gclid", "yclid", "_ga", "mc_cid", "mc_eid", "ref"}:
            continue
        for val in vals:
            query.append((key, val))
    query.sort()
    return f"https://{host}{path}" + ("?" + urlencode(query, doseq=True) if query else "")


def normalize_category(value: str) -> str:
    """利用者向けにカテゴリ名を短く統一する。"""
    category = norm(value).strip("[]［］")
    aliases = {
        "区公式オープンデータ": "区公式",
        "区内公共・公園": "区内公園",
        "区内公共・文化": "区内文化",
        "学校公開イベント": "学校公開",
        "地域・まいぷれ": "まいぷれ",
    }
    return aliases.get(category, category)


def clean_source_boilerplate_lines(lines: list[str]) -> list[str]:
    """区公式・公園・学校公開ページの共通ナビゲーションを除去する。"""
    school_nav = {
        "ニュース", "お知らせ", "学校生活・行事等", "総合的な探究の時間",
        "youtube", "受検生の方", "卒業生の方", "経営企画室より", "同窓会(輝篠会)より",
    }
    cleaned: list[str] = []
    in_park_year_nav = False
    for raw in lines:
        line = norm(raw)
        if not line:
            continue
        if line == "年別":
            in_park_year_nav = True
            continue
        if in_park_year_nav and re.fullmatch(r"20(?:26|25|24|23|22|21)年", line):
            continue
        if in_park_year_nav:
            in_park_year_nav = False
        # 江戸川区公式ページの定型導入文
        line = re.sub(r"^ページID\s*[：:]?\s*\d+\s*ここから本文です。?\s*", "", line)
        line = re.sub(r"^ページID\s*[：:]?\s*\d+\s*", "", line)
        line = line.replace("ここから本文です。", "").strip()
        if not line:
            continue
        # 公園サイトの年別アーカイブナビゲーション
        if re.search(r"年別.*2026年.*2025年.*2024年.*2023年.*2022年.*2021年", line):
            continue
        if re.fullmatch(r"年別(?:\s*20(?:26|25|24|23|22|21)年)+", line):
            continue
        # 学校サイト共通ナビ。単独行だけを対象にし、本文中の語句は残す。
        if line.casefold() in {x.casefold() for x in school_nav}:
            continue
        cleaned.append(line)
    return cleaned


def clean_location_text(value: str) -> str:
    """会場欄のナビ・交通案内・重複を除去する。"""
    text = norm(value)
    text = re.split(r"\s*ホームページ\s*", text, maxsplit=1)[0]
    text = re.split(r"(?:【(?:電車|バス|車|徒歩|公共交通機関)|(?:電車|バス|車|公共交通機関)をご利用の場合|アクセス方法|交通アクセス|公共交通機関)", text, maxsplit=1)[0]
    return text.strip(" ・|｜")


def combine_location(venue: str, address: str) -> str:
    """会場名と住所を重複なく連結する。"""
    venue, address = clean_location_text(venue), clean_location_text(address)
    if not venue:
        return address
    if not address:
        return venue
    compact_v = re.sub(r"[\s　]", "", venue)
    compact_a = re.sub(r"[\s　]", "", address)
    venue_core = compact_v.replace("駅前", "")
    if compact_v and (compact_v in compact_a or (venue_core and venue_core in compact_a)):
        return address
    if compact_a and compact_a in compact_v:
        return venue
    return venue + " " + address


def clean_description_text(value: str) -> str:
    """イベント本文からカレンダー取込案内・問い合わせ等のフッターを除去。"""
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    cut = len(text)
    for marker in FOOTER_CUTOFF_TERMS:
        pos = text.find(marker)
        if pos >= 0:
            cut = min(cut, pos)
    text = text[:cut]
    lines = [norm(line) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def is_application_closed(title: str) -> bool:
    return any(term in norm(title) for term in APPLICATION_CLOSED_TERMS)


def is_annual_overview_page(title: str, url: str = "") -> bool:
    t = re.sub(r"[\s　・･/／｜|:：()（）]+", "", norm(title)).casefold()
    if t in {"イベントカレンダー", "年間イベント", "年間行事", "区内の主なイベント"}:
        return True
    path = urlparse(url).path.casefold() if url else ""
    return path.endswith("/kankomidokoro/event/index.html") or path.endswith("/kankomidokoro/event/")


def is_non_event_page(title: str, text: str = "", url: str = "") -> bool:
    """案内・カテゴリ・FAQ等をイベントとして誤登録しないための共通ゲート。"""
    if is_annual_overview_page(title, url) or is_application_closed(title):
        return True
    path_for_exclusion = urlparse(url).path.casefold() if url else ""
    if path_for_exclusion.rstrip("/").endswith("/e027/kosodate/kenzenikusei/mirai/index.html"):
        return True
    t = re.sub(r"[\s　・･/／｜|:：()（）]+", "", norm(title)).casefold()
    if not t or t in {re.sub(r"[\s　・･/／｜|:：()（）]+", "", x).casefold()
                    for x in NON_EVENT_PAGE_TITLES}:
        return True
    normalized_title = re.sub(r"[\s　・･/／｜|:：()（）]+", "", norm(title))
    normalized_prefixes = [re.sub(r"[\s　・･/／｜|:：()（）]+", "", x) for x in NON_EVENT_TITLE_PREFIXES]
    if any(normalized_title.startswith(x) for x in normalized_prefixes):
        return True
    # 施設紹介ページはタイトル語で判定する。本文中の一般的な「施設紹介」は
    # イベントの説明にも登場するため、本文だけを根拠に除外しない。
    # URLがカテゴリ/サイト案内で、タイトルもイベント名らしくないものを除外。
    path = urlparse(url).path.lower() if url else ""
    if any(x in path for x in ("/sitemap", "/faq", "/organization", "/about/", "/category/")):
        if not any(w in norm(title) for w in EVENT_WORDS):
            return True
    return False


def valid_event_span(span: tuple[date, date], today: date | None = None) -> tuple[bool, str]:
    """過去イベント・遠すぎる日付・異常な長期開催を安全側に除外。"""
    today = today or TODAY
    start, end = span
    if end < start:
        return False, "invalid_date_range"
    if end < today:
        return False, "past_event"
    if start > today + timedelta(days=MAX_EVENT_LOOKAHEAD_DAYS):
        return False, "date_too_far"
    if (end - start).days > MAX_EVENT_DURATION_DAYS:
        return False, "abnormally_long_event"
    return True, "ok"
APPLICATION_DATE_TERMS = (
    "申込期間", "申し込み期間", "申込開始", "申込締切", "申込期限", "受付期間",
    "受付開始", "受付締切", "応募期間", "応募開始", "応募締切", "販売期間",
    "発売日", "更新日", "掲載日", "公開日", "投稿日", "募集期間",
)
EVENT_DATE_HEADINGS = ("開催日時", "開催日", "開催期間", "開催日程", "開催スケジュール", "実施日", "実施日時", "実施期間", "日時", "日程")
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
    "物産展", "フリーマーケット", "フリマ", "おはなし会", "上映会", "読書会",
    "人形劇", "工作会", "相談会", "説明会", "学校見学会", "授業公開", "部活動体験",
    "ボードゲーム", "科学あそび", "演芸", "落語会", "スポーツ教室", "試合", "大会等予定表",
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
    "start": ("開催日", "開催日時", "開催開始日", "開催期間", "開始日", "開始日時", "日付", "start_date", "start", "event_date", "日程"),
    "end": ("終了日", "終了日時", "開催終了日", "end_date", "end"),
    "venue": ("場所名称（おでかけスポット）", "場所名称", "場所・名称", "会場", "開催場所", "場所", "施設名", "施設名称", "会場名", "venue", "place"),
    "address": ("所在地_連結表記", "町名番地", "住所", "会場住所", "開催場所住所", "所在地", "address", "住所詳細"),
    "city": ("市区町村名", "市区町村", "所在地_市区町村", "市町村名", "自治体名", "city_name", "city"),
    "city_code": ("市区町村コード", "全国地方公共団体コード", "localgov_code", "city_code"),
    "pref": ("都道府県名", "都道府県", "prefecture", "pref"),
    "url": ("ホームページURL", "コンテンツURL", "詳細URL", "関連URL", "リンク", "URL", "url", "web"),
    "image": ("イベント画像のURL", "画像URL", "写真URL", "サムネイル", "画像", "image_url", "image"),
    "description": ("PR文", "紹介PR文", "概要", "説明", "内容", "イベント内容", "description", "summary"),
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


def parse_date_spans(raw: str, base_year: int | None = None, include_past: bool = False) -> list[tuple[date, date]]:
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
    # 「12月1日～5日」「12/1～5」のように月を省略した終了日も補完する。
    shorthand: list[tuple[date, int, int]] = []
    for d1, _a, e1 in list(found):
        tail = text[e1:]
        m_short = re.match(r"\s*(?:[（(][月火水木金土日](?:曜日)?[）)])?\s*(?:から|～|〜|~|−|―|ー|-)\s*(?!20\d{2}\s*年)(\d{1,2})\s*日?", tail)
        if m_short:
            day2 = int(m_short.group(1))
            year2, month2 = d1.year, d1.month
            try:
                d2 = date(year2, month2, day2)
                if d2 < d1:
                    d2 = date(year2 + 1, month2, day2)
                shorthand.append((d2, e1 + m_short.start(1), e1 + m_short.end(1)))
            except ValueError:
                pass
    found.extend(shorthand)
    # 重複 token (和暦変換後とISOなど) を整理
    unique = []
    seen = set()
    for d, a, b in sorted(found, key=lambda x: x[1]):
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
    return [(a, b) for a, b in spans if (include_past or b >= TODAY) and a <= TODAY + timedelta(days=550)]


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
    if outside_facility_mention(a):
        return False, None, "outside_facility_address"
    if outside_facility_mention(v):
        return False, None, "outside_facility_venue"
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
    for el in soup.find_all(["nav", "footer", "header", "aside", "script", "style", "form"]):
        el.decompose()
    # MyPlue等、main/articleタグを使わずコンテンツ用クラスを持つページを優先。
    # まいぷれはページによってmain/articleを使わず、イベント詳細を独自カラムに格納する。
    # 詳細カラム候補を先に探し、ページ全体の共通ナビ・カテゴリ一覧を本文へ混ぜない。
    detail_pattern = re.compile(r"(?:event[-_]?detail|detail[-_]?event|event[-_]?contents?|event[-_]?body|main[-_]?contents?|contents[-_]?main|article[-_]?body|eventDetail|mainContents)", re.I)
    detail_candidates = soup.find_all(["div", "section"], attrs={"class": detail_pattern})
    detail_candidates += soup.find_all(["div", "section"], id=detail_pattern)
    detail_candidates = [x for x in detail_candidates if getattr(x, "attrs", None) is not None]
    # 最小の詳細コンテナを選ぶ。外側のラッパーを選ぶとサイドバーまで含むため。
    detail_candidates.sort(key=lambda x: len(x.find_all(True)))
    is_mypl = "mypl.net" in urlparse(page_url).netloc.lower()
    main = ((detail_candidates[0] if detail_candidates else None) if is_mypl else None) or (
            soup.find("main") or soup.find("article") or
            (detail_candidates[0] if detail_candidates else None) or soup.body or soup)
    for el in main.find_all(["nav", "footer", "header", "aside", "form"]):
        el.decompose()
    # ResultSetは走査開始時点の子孫一覧を保持するため、親要素をdecompose()
    # した後も、その子孫が一覧に残る。分解済みTagはattrs=Noneになるので、
    # そのままget()すると AttributeError で全収集処理が停止してしまう。
    # attrsを確認し、クラス属性が文字列の場合も安全に正規化する。
    for el in list(main.find_all(True)):
        attrs = getattr(el, "attrs", None)
        if attrs is None:
            continue
        el_id = attrs.get("id", "")
        classes = attrs.get("class", [])
        if isinstance(classes, str):
            classes = classes.split()
        marker = " ".join([str(el_id), " ".join(str(c) for c in classes)]).casefold()
        if re.search(r"(?:global[-_ ]?nav|site[-_ ]?nav|gnav|breadcrumb|side[-_ ]?menu|footer[-_ ]?menu|header[-_ ]?menu)", marker):
            el.decompose()
    title_el = main.find("h1") or soup.find("h1") or main.find(["h2", "h3"])
    title = norm(title_el.get_text(" ", strip=True)) if title_el else ""
    # 図書館など「イベント情報」がH1、個別イベント名がH3/H4のCMSに対応。
    if is_non_event_page(title, url=page_url):
        for heading in reversed(main.find_all(["h2", "h3", "h4"])):
            candidate_title = norm(heading.get_text(" ", strip=True))
            if candidate_title and not is_non_event_page(candidate_title, url=page_url):
                title_el, title = heading, candidate_title
                break
    lines = [norm(x) for x in main.get_text("\n", strip=True).splitlines() if norm(x)]
    # 共通メニューがdiv/spanの連続テキストとして埋め込まれた場合の保険。
    # イベント本文や見出しは残し、ジャンル・店舗カテゴリのナビ行だけを落とす。
    mypl_nav_markers = ("地元暮らしをちょっぴり楽しく", "グルメ", "ショッピング", "美容・健康", "暮らし・相談", "全て", "すべて")
    if is_mypl:
        lines = [line for line in lines if not any(marker in line for marker in mypl_nav_markers)]
        # MyPlueの一部ページでは共通ナビがmain内に混在する。
        # イベント詳細のH1より前はサイト共通領域として本文から除外する。
        if title:
            title_line_index = next((i for i, line in enumerate(lines) if line == title or title in line), None)
            if title_line_index is not None:
                lines = lines[title_line_index:]
    lines = clean_source_boilerplate_lines(lines)
    # 開催日見出しの配下だけを開催日根拠にする。記事公開日・更新日・掲載日を
    # ページ全体から拾う旧方式は、ニュース記事を「今日開催」と誤登録するため廃止。
    date_lines = section_lines_after_heading(lines, EVENT_DATE_HEADINGS, stop=STOP_HEADINGS)
    if not date_lines:
        # CMSによっては見出しを使わず「開催日：」形式で記載するため、明示ラベルを許可。
        date_lines = [x.split("：", 1)[-1].split(":", 1)[-1].strip()
                      for x in lines
                      if re.match(r"^(?:開催日|開催日時|開催期間|実施日|実施日時|会期)\s*[：:]", x)
                      and not any(k in x for k in APPLICATION_DATE_TERMS)]
    # ニュース形式の告知は開催日見出しがない場合がある。
    # 「12月10日に開催」「12月10日に実施します」等、同じ文中に開催意図が明記された行だけ補う。
    publication_terms = ("公開日", "掲載日", "投稿日", "更新日", "配信日", "発信日", "お知らせ日", "更新", "掲載", "公開")
    event_cues = ("開催します", "開催いたします", "開催されます", "開催予定", "開催する予定",
                  "実施します", "実施いたします", "行います", "行ないます", "開きます", "会期")
    for line in lines:
        if any(term in line for term in publication_terms) or is_contingency_date_line(line):
            continue
        if any(cue in line for cue in event_cues) and parse_date_spans(line):
            if line not in date_lines:
                date_lines.append(line)
    date_text = " ".join(x for x in date_lines if not is_contingency_date_line(x))
    spans = parse_date_spans(date_text)
    # 時刻は開催日時欄だけから取る。申込期間・開館時間を誤って開催時刻にしない。
    time_start, time_end = parse_time_range(date_text)
    venue_text = section_after_heading(lines, VENUE_HEADINGS, stop=ADDRESS_HEADINGS + STOP_HEADINGS)
    address_text = section_after_heading(lines, ADDRESS_HEADINGS, stop=("内容", "対象", "定員", "費用", "料金", "問合せ", "主催", "備考"))
    address_text = clean_location_text(address_text)
    # アクセス案内が住所欄に連結されたサイトでは、住所の先頭部分だけを残す。
    address_text = re.split(r"(?:【(?:電車|バス|車)|(?:電車|バス|車)をご利用の場合|アクセス方法|交通アクセス)", address_text, maxsplit=1)[0].strip()
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
    full_text = "\n".join(lines)
    official_url = None
    for a in main.find_all("a", href=True):
        href = urljoin(page_url, a.get("href", "").strip())
        label = norm(a.get_text(" ", strip=True) + " " + a.get("title", ""))
        if ("city.edogawa.tokyo.jp" in urlparse(href).netloc.lower() and
                (any(k in label for k in ("公式", "詳細", "イベントページ", "ホームページ", "開催案内")) or
                 "/event/" in urlparse(href).path.lower() or urlparse(href).path.lower().endswith(".html"))):
            official_url = href
            break
    non_event = is_non_event_page(title, full_text, page_url)
    outside_facility = outside_facility_mention(full_text)
    return {"title": title, "date_text": date_text, "spans": spans,
            "start_time": time_start, "end_time": time_end, "venue": venue,
            "address": address, "image_url": image_url, "text": full_text,
            "official_url": official_url,
            "venue_registry": venue_reg, "is_non_event_page": non_event,
            "outside_facility_mention": outside_facility}


class TLS12FallbackAdapter(HTTPAdapter):
    """一部の自治体サイト向けにTLS1.2/低セキュリティレベルを限定利用する接続アダプター。"""
    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.maximum_version = ssl.TLSVersion.TLSv1_2
        context.set_ciphers("DEFAULT:@SECLEVEL=1")
        pool_kwargs["ssl_context"] = context
        return super().init_poolmanager(connections, maxsize, block=block, **pool_kwargs)

def fetch_library_tls12(url: str, params: dict[str, Any] | None = None, timeout: int = 20):
    """図書館サイトのTLSハンドシェイク失敗時のみTLS1.2で再取得する。"""
    session = requests.Session()
    session.headers.update(SESSION.headers)
    session.mount("https://", TLS12FallbackAdapter())
    response = session.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return response.text, len(response.content), response.status_code

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
            if "library.city.edogawa.tokyo.jp" in (urlparse(url).hostname or "") and isinstance(e, requests.exceptions.SSLError):
                try:
                    body, size, code = fetch_library_tls12(url, params=params, timeout=timeout)
                    meta.update({"http_status": code, "bytes": size, "error": None, "tls_fallback": "TLS1.2"})
                    with _FETCH_CACHE_LOCK:
                        _FETCH_CACHE[cache_key] = (body, dict(meta))
                    return body, meta
                except Exception as tls_error:
                    meta["tls_fallback_error"] = f"{type(tls_error).__name__}: {tls_error}"
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


# 会場座標の補完。住所は国土地理院住所検索API、施設名のみの場合はNominatimを利用。
_GEOCODE_CACHE: dict[str, dict[str, Any]] = {}
_GEOCODE_CACHE_LOADED = False
_GEOCODE_LAST_NOMINATIM = 0.0
GEOCODE_STATS = {"cache_hits": 0, "gsi_queries": 0, "nominatim_queries": 0,
                 "success": 0, "failed": 0, "outside": 0}

def _load_geocode_cache() -> None:
    global _GEOCODE_CACHE_LOADED
    if _GEOCODE_CACHE_LOADED:
        return
    _GEOCODE_CACHE_LOADED = True
    try:
        data = json.loads(GEOCODE_CACHE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            _GEOCODE_CACHE.update(data)
    except Exception:
        pass

def _inside_edogawa_bounds(lat: float, lon: float) -> bool:
    # 区の外周を囲む安全側の検索範囲。行政区名も別途照合する。
    return 35.58 <= lat <= 35.80 and 139.80 <= lon <= 139.95

def geocode_edogawa_location(address: str, venue: str = "") -> tuple[float | None, float | None, str | None]:
    """江戸川区内と行政区名まで確認できた座標だけ返す。失敗時はNone。"""
    global _GEOCODE_LAST_NOMINATIM
    _load_geocode_cache()
    raw_address = norm(address)
    raw_venue = norm(venue)
    # 区のイベント詳細欄に混入する「ホームページ」「アクセス」以降のリンク文字列を除去。
    clean_address = re.split(r"(?:ホームページ|アクセス方法|関連リンク|地図)", raw_address, maxsplit=1)[0].strip(" ・,、")
    clean_venue = re.split(r"(?:講師|出演者|主催|ホームページ|アクセス)", raw_venue, maxsplit=1)[0].strip(" ・,、")
    query = clean_address or (f"東京都江戸川区{clean_venue}" if clean_venue else "")
    if not query or contains_outside_region(query):
        return None, None, None
    key = _compact_address(query)
    cached = _GEOCODE_CACHE.get(key)
    if cached:
        GEOCODE_STATS["cache_hits"] += 1
        if cached.get("municipality") == "江戸川区":
            return float(cached["lat"]), float(cached["lon"]), cached.get("provider", "geocoding_cache")
        return None, None, None
    # GSI住所検索は日本国内住所の無償API。返却住所に江戸川区を含む候補だけ採用。
    if "江戸川区" in query:
        try:
            GEOCODE_STATS["gsi_queries"] += 1
            r = SESSION.get("https://msearch.gsi.go.jp/address-search/AddressSearch",
                            params={"q": query}, timeout=12)
            r.raise_for_status()
            candidates = r.json()
            for item in candidates if isinstance(candidates, list) else []:
                title = norm(item.get("properties", {}).get("title", ""))
                coords = item.get("geometry", {}).get("coordinates", [])
                # 「東京都江戸川区」だけの行政区域代表点は会場座標として採用しない。
                if "江戸川区" not in title or len(coords) < 2 or not re.search(r"\d|丁目", title):
                    continue
                lon, lat = float(coords[0]), float(coords[1])
                if _inside_edogawa_bounds(lat, lon):
                    value = {"lat": lat, "lon": lon, "municipality": "江戸川区", "provider": "GSI"}
                    _GEOCODE_CACHE[key] = value
                    GEOCODE_STATS["success"] += 1
                    return lat, lon, "GSI"
                GEOCODE_STATS["outside"] += 1
        except Exception:
            pass
    # 施設名のみの会場はNominatimで検索。公開APIの利用規約に従い毎秒1件以下に制限。
    if raw_venue:
        try:
            wait = 1.1 - (time.monotonic() - _GEOCODE_LAST_NOMINATIM)
            if wait > 0:
                time.sleep(wait)
            _GEOCODE_LAST_NOMINATIM = time.monotonic()
            GEOCODE_STATS["nominatim_queries"] += 1
            r = SESSION.get("https://nominatim.openstreetmap.org/search",
                            params={"q": f"{clean_venue}, 江戸川区, 東京都, 日本", "format": "jsonv2",
                                    "addressdetails": 1, "limit": 5, "countrycodes": "jp"}, timeout=15,
                            headers={"User-Agent": "EdogawaEventCalendar/3.2.1 (https://github.com/shulavan/games)"})
            r.raise_for_status()
            for item in (r.json() if isinstance(r.json(), list) else []):
                ad = item.get("address", {})
                municipality = " ".join(str(ad.get(k, "")) for k in
                                         ("city", "borough", "city_district", "town", "municipality"))
                lat, lon = float(item["lat"]), float(item["lon"])
                if "江戸川区" in municipality and _inside_edogawa_bounds(lat, lon):
                    value = {"lat": lat, "lon": lon, "municipality": "江戸川区", "provider": "Nominatim"}
                    _GEOCODE_CACHE[key] = value
                    GEOCODE_STATS["success"] += 1
                    return lat, lon, "Nominatim"
                GEOCODE_STATS["outside"] += 1
        except Exception:
            pass
    GEOCODE_STATS["failed"] += 1
    return None, None, None

def save_geocode_cache() -> None:
    if _GEOCODE_CACHE:
        try:
            GEOCODE_CACHE_FILE.write_text(json.dumps(_GEOCODE_CACHE, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

def expand_recurring_spans(text: str, spans: list[tuple[date, date]]) -> list[tuple[date, date]]:
    """開催可能日を1日単位に展開。定期開催・既知の休館日/休講日も反映する。

    全N回は開始日から数えた総開催回数として扱い、過去分を数えた後に
    今日以降だけを返す。期間イベントは日ごとのVEVENTに分割する。
    """
    t = str(text or "")
    normalized = norm(t)
    if not spans:
        return []
    spans = [span for span in spans if valid_event_span(span)[0]]
    if not spans:
        return []
    weekdays = {"月": 0, "火": 1, "水": 2, "木": 3, "金": 4, "土": 5, "日": 6}
    excluded_weekdays: set[int] = set()
    # 「月曜日定休」「休館日は月曜日」「毎週月曜休館」など明記された休みだけ除外。
    for m in re.finditer(r"([月火水木金土日])曜(?:日)?(?:は|が|を)?(?:定休|休館|休業|休講|休み)", normalized):
        excluded_weekdays.add(weekdays[m.group(1)])
    for m in re.finditer(r"(?:定休日|休館日|休業日|休講日)\s*(?:は|：|:)?\s*([月火水木金土日])曜(?:日)?", normalized):
        excluded_weekdays.add(weekdays[m.group(1)])

    # 「休館日：2026年12月28日～2027年1月4日」など明示された日付休止期間。
    excluded_dates: set[date] = set()
    for line in re.split(r"[\n\r。;；]", t):
        marker = re.search(r"休館日|休業日|定休日|休講日|休暇期間|開催しない|開催なし|お休み|休み", line)
        if not marker:
            continue
        # 休みを示す語より後ろの日付だけを休止日として読む。
        # 「開催期間12/1～5、休館日12/3」の開催期間全体を誤って消さない。
        date_text = line[marker.end():].lstrip(" ：:はが")
        closed_spans = parse_date_spans(date_text)
        if not closed_spans:
            # 「2026年12月3日は休館日」のように日付が休み語の直前にある表記。
            before = line[:marker.start()]
            m_before = re.search(r"((?:20\d{2}\s*年\s*)?\d{1,2}\s*月\s*\d{1,2}\s*日?)\s*(?:は|が)?\s*$", before)
            if m_before:
                closed_spans = parse_date_spans(m_before.group(1))
        for a, b in closed_spans:
            if (b - a).days > 62:
                continue
            d = a
            while d <= b:
                excluded_dates.add(d)
                d += timedelta(days=1)

    start, end = spans[0]
    count_m = re.search(r"全\s*(\d+)\s*回", normalized)
    total_limit = int(count_m.group(1)) if count_m else None
    # 「休暇期間を除く」とだけ記載され、休暇の日付が公開されていない場合、
    # 全N回を開始日から機械的に数えると開催期間の後半を誤って消してしまう。
    # その場合は終了日まで曜日を展開し、公式に特定できた休止日だけ除外する。
    unspecified_breaks = bool(re.search(r"(?:夏季|冬季|春季)?休暇期間(?:中)?は除く|休業期間(?:中)?は除く", normalized))
    if unspecified_breaks:
        total_limit = None
    recurrence_dates: list[date] | None = None

    weekly = re.search(r"毎週\s*([月火水木金土日])曜", normalized)
    nth_monthly = re.search(r"(?:毎月\s*)?第([1-5])\s*([月火水木金土日])曜", normalized)
    if weekly:
        target = weekdays[weekly.group(1)]
        first = start + timedelta(days=(target - start.weekday()) % 7)
        recurrence_dates = []
        d = first
        while d <= end and (total_limit is None or len(recurrence_dates) < total_limit):
            if d.weekday() not in excluded_weekdays and d not in excluded_dates:
                recurrence_dates.append(d)
            d += timedelta(days=7)
    elif nth_monthly:
        nth, target = int(nth_monthly.group(1)), weekdays[nth_monthly.group(2)]
        recurrence_dates = []
        y, mo = start.year, start.month
        while date(y, mo, 1) <= end and (total_limit is None or len(recurrence_dates) < total_limit):
            first = date(y, mo, 1)
            d = first + timedelta(days=(target - first.weekday()) % 7 + 7 * (nth - 1))
            if d.month == mo and start <= d <= end and d.weekday() not in excluded_weekdays and d not in excluded_dates:
                recurrence_dates.append(d)
            mo += 1
            if mo == 13:
                y, mo = y + 1, 1

    if recurrence_dates is not None:
        return [(d, d) for d in recurrence_dates if d >= TODAY]

    # 通常の複数日開催は、毎日を独立VEVENTとして保持する。
    expanded: list[tuple[date, date]] = []
    for a, b in spans:
        d = a
        while d <= b:
            if d >= TODAY and d.weekday() not in excluded_weekdays and d not in excluded_dates:
                expanded.append((d, d))
            d += timedelta(days=1)
    # 複数の開催日が同じ入力にある場合も重複を排除して日付順にする。
    return sorted(set(expanded), key=lambda x: x[0])

def make_event(title: str, span: tuple[date, date], venue: str, address: str, url: str,
               source: str, category: str = "地域イベント", default_venue: str | None = None,
               start_time: str | None = None, end_time: str | None = None,
               image_url: str | None = None, description: str = "", priority: int = 4,
               lat: float | None = None, lon: float | None = None) -> Event | None:
    if is_application_closed(title) or is_annual_overview_page(title, url) or is_non_event_page(title, description, url):
        return None
    venue = clean_location_text(venue)
    address = clean_location_text(address)
    description = clean_description_text(description)
    if outside_facility_mention(description) or outside_facility_mention(venue) or outside_facility_mention(address):
        return None
    valid_span, _span_reason = valid_event_span(span)
    if not valid_span:
        return None
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
    # 座標がない会場は住所ジオコーディングで補完。解決できなければイベント自体を掲載しない。
    if lat is None or lon is None:
        lat, lon, geocode_provider = geocode_edogawa_location(address, venue)
        if lat is None or lon is None:
            return None
        quality = f"geocoded_{geocode_provider}"
    # 行政区内の住所でも、外れた座標・片側だけの座標は安全側に除外。
    if lat is None or lon is None or not _inside_edogawa_bounds(float(lat), float(lon)):
        return None
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


def _title_fingerprint(value: str) -> str:
    t = norm(value).casefold()
    t = re.sub(r"[（(【\[]?(?:令和|平成|昭和)?[0-9０-９]{1,4}年[0-9０-９]{1,2}月[0-9０-９]{1,2}日?(?:[^）)】\]]*)[）)】\]]?", "", t)
    t = re.sub(r"[【】\[\]（）()「」『』・･,，。:：!！?？\s　]+", "", t)
    t = re.sub(r"(?:を開催します|開催いたします|開催されます|開催のお知らせ|イベント情報|イベント|開催)", "", t)
    return t


def _source_rank(ev: Event) -> int:
    source = norm(ev.source + " " + ev.category)
    if "江戸川区公式イベントカレンダー" in source or ("区公式" in source and "オープンデータ" not in source):
        return 0
    if "オープンデータ" in source or "OpenData" in source:
        return 1
    if "まいぷれ" in source or "地域" in source:
        return 2
    return 3


def _is_generic_image(url: str | None) -> bool:
    if not url:
        return True
    return bool(re.search(r"(?:^|[/_-])(?:logo|icon|dummy|noimage|default)(?:[._/-]|$)", url, re.I))


def _is_city_official_url(value: str) -> bool:
    host = urlparse(value or "").netloc.lower()
    return host.endswith("city.edogawa.tokyo.jp")


def _official_url_basename(value: str) -> str:
    if not _is_city_official_url(value):
        return ""
    return urlparse(value).path.rstrip("/").rsplit("/", 1)[-1].casefold()


def _official_source(ev: Event) -> bool:
    label = norm(ev.source + " " + ev.category)
    return "区公式" in label and "オープンデータ" not in label


def _events_are_same(a: Event, b: Event) -> bool:
    # 同一の正規化イベントURLかつ同じ開催日なら情報源横断で統合。
    # 同日複数回開催で双方の時刻が異なる場合は別イベントとして保持する。
    urls_a = {normalize_event_url(x) for x in (a.source_urls + ([a.url] if a.url else []))}
    urls_b = {normalize_event_url(x) for x in (b.source_urls + ([b.url] if b.url else []))}
    urls_a.discard(""); urls_b.discard("")
    if urls_a.intersection(urls_b) and a.start_date == b.start_date:
        if a.start_time and b.start_time and a.start_time != b.start_time:
            return False
        if a.end_time and b.end_time and a.end_time != b.end_time:
            return False
        return True
    if a.start_date != b.start_date:
        return False
    # 区公式URLのフルパス/ショートパス差を吸収。末尾ファイル名だけで確定せず、
    # 同日・タイトル一致も必須にして別ページの誤統合を防ぐ。
    official_names_a = {_official_url_basename(x) for x in (a.source_urls + ([a.url] if a.url else []))}
    official_names_b = {_official_url_basename(x) for x in (b.source_urls + ([b.url] if b.url else []))}
    official_names_a.discard(""); official_names_b.discard("")
    ta, tb = _title_fingerprint(a.title), _title_fingerprint(b.title)
    title_ratio = difflib.SequenceMatcher(None, ta, tb).ratio() if ta and tb else 0.0
    same_official_file = bool(official_names_a.intersection(official_names_b))
    if same_official_file and title_ratio >= 0.78:
        if a.start_time and b.start_time and a.start_time != b.start_time:
            return False
        if a.end_time and b.end_time and a.end_time != b.end_time:
            return False
        return True
    # 区公式ページ同士で同日・同タイトル、住所が軽微な表記差だけの場合も統合。
    # 会場名が明確に異なるケースは除外する。
    if _official_source(a) and _official_source(b) and title_ratio >= 0.94:
        va, vb = norm(a.venue).casefold(), norm(b.venue).casefold()
        if va and vb and va != vb and difflib.SequenceMatcher(None, va, vb).ratio() < 0.90:
            return False
        aa, ab = _compact_address(a.address), _compact_address(b.address)
        if aa and ab and difflib.SequenceMatcher(None, aa, ab).ratio() >= 0.90:
            if a.start_time and b.start_time and a.start_time != b.start_time:
                return False
            if a.end_time and b.end_time and a.end_time != b.end_time:
                return False
            return True
    if _compact_address(a.address) and _compact_address(b.address):
        if _compact_address(a.address) != _compact_address(b.address):
            return False
    elif norm(a.venue).casefold() != norm(b.venue).casefold():
        return False
    if a.start_time and b.start_time and a.start_time != b.start_time:
        return False
    ta, tb = _title_fingerprint(a.title), _title_fingerprint(b.title)
    if not ta or not tb:
        return False
    return ta == tb or difflib.SequenceMatcher(None, ta, tb).ratio() >= 0.88


def merge_events(events: Iterable[Event]) -> list[Event]:
    """URL・開催日単位で統合。公式HTMLの開催情報とCSV個別画像を優先して良いとこ取り。"""
    merged: list[Event] = []
    for ev in sorted(events, key=lambda e: (_source_rank(e), e.priority, e.start_date, e.title)):
        ev.venue = clean_location_text(ev.venue)
        ev.address = clean_location_text(ev.address)
        ev.description = clean_description_text(ev.description)
        if is_application_closed(ev.title) or is_annual_overview_page(ev.title, ev.url):
            continue
        match = next((old for old in merged if _events_are_same(old, ev)), None)
        if match is None:
            merged.append(ev)
            continue
        match.source_urls = sorted(set(match.source_urls + ev.source_urls + ([match.url] if match.url else []) + ([ev.url] if ev.url else [])))
        # HTML公式を優先して日時・会場・本文を採用。公式HTMLがなければ既存の高品質情報を維持。
        if _source_rank(ev) < _source_rank(match):
            match.title = ev.title or match.title
            match.category = ev.category or match.category
            match.source = ev.source or match.source
            match.url = ev.url or match.url
            match.venue = ev.venue or match.venue
            match.address = ev.address or match.address
            match.start_time = ev.start_time or match.start_time
            match.end_time = ev.end_time or match.end_time
            match.description = ev.description or match.description
            if ev.latitude is not None and ev.longitude is not None:
                match.latitude, match.longitude = ev.latitude, ev.longitude
                match.coordinate_quality = ev.coordinate_quality
            match.priority = min(match.priority, ev.priority)
        else:
            if not match.start_time and ev.start_time: match.start_time = ev.start_time
            if not match.end_time and ev.end_time: match.end_time = ev.end_time
            if not match.venue and ev.venue: match.venue = ev.venue
            if not match.address and ev.address: match.address = ev.address
            if not match.description and ev.description: match.description = ev.description
            if (match.latitude is None or match.longitude is None) and ev.latitude is not None and ev.longitude is not None:
                match.latitude, match.longitude = ev.latitude, ev.longitude
                match.coordinate_quality = ev.coordinate_quality
            match.priority = min(match.priority, ev.priority)
        # 個別チラシ画像を優先し、logo.png等の汎用画像は個別画像で置換。
        if not _is_generic_image(ev.image_url) and (_is_generic_image(match.image_url) or _source_rank(ev) == 1):
            match.image_url = ev.image_url
        elif not match.image_url and ev.image_url:
            match.image_url = ev.image_url
        # 期間表記が異なる場合は開催日の独立性を守り、終了日をむやみに拡張しない。
        # v3.2.2以降は原則1日単位VEVENTなので、同日統合後も開催日を維持。
    return sorted(merged, key=lambda e: (e.start_date, e.start_time or "99:99", e.priority, e.title))


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
                # 列名の一部一致は誤割当てを生みやすい列では禁止し、固有名を優先する。
                if field_name == "address" and ("コード" in hn or "町字id" in hn):
                    continue
                if field_name == "url" and ("画像" in hn or "予約" in hn):
                    continue
                if field_name == "image" and "ホームページ" in hn:
                    continue
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
    starts = parse_date_spans(start_raw, include_past=True) if start_raw else []
    ends = parse_date_spans(end_raw, include_past=True) if end_raw else []
    if starts:
        start_date = starts[0][0]
        end_date = ends[-1][1] if ends else starts[-1][1]
        if end_date < start_date:
            return None
        if end_date <= TODAY + timedelta(days=550):
            return start_date, end_date
    combined = parse_date_spans(" ".join(x for x in (start_raw, end_raw) if x), include_past=True)
    return combined[0] if combined else None


def parse_csv_source(url: str, name: str, priority: int = 1,
                     suppress_urls: set[str] | None = None) -> tuple[list[Event], dict[str, Any]]:
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
    suppress = {normalize_event_url(x) for x in (suppress_urls or set())}
    suppress.discard("")
    status["suppressed_official_html_rows"] = 0
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
        if normalize_event_url(url_v) in suppress:
            status["suppressed_official_html_rows"] += 1
            status["rejected"]["official_html_has_same_detail_url"] = status["rejected"].get("official_html_has_same_detail_url", 0) + 1
            continue
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
            description = " ".join(x for x in (description, detail.get("text", "")[:2000]) if x)
        # CSV座標は緯度経度の両方が存在し、住所が区内と確認できるときだけ採用。
        try:
            lat = float(get("lat")) if get("lat") else None
            lon = float(get("lon")) if get("lon") else None
        except ValueError:
            lat = lon = None
        external_name = outside_facility_mention(" ".join((title, venue, address, description)))
        if external_name:
            key = "outside_facility_mention:" + external_name
            status["rejected"][key] = status["rejected"].get(key, 0) + 1
            continue
        if is_non_event_page(title, description, url_v):
            status["rejected"]["non_event_page"] = status["rejected"].get("non_event_page", 0) + 1
            continue
        time_s, time_e = get("start_time") or None, get("end_time") or None
        if not (time_s and time_e):
            time_s, time_e = parse_time_range(start_raw + " " + end_raw)
        expanded_spans = expand_recurring_spans(" ".join((description, start_raw, end_raw)), [span])
        for event_span in expanded_spans:
            ev = make_event(title, event_span, venue, address, url_v, name,
                            category="区公式オープンデータ", default_venue=None,
                            start_time=time_s, end_time=time_e, image_url=image,
                            description=description, priority=(9 if len(expanded_spans) > 1 or span[0] != span[1] else priority), lat=lat, lon=lon)
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
    c_addr = find_col(("所在地_連結表記", "所在地", "住所", "施設所在地", "会場住所", "所在地住所", "住所（所在地）", "address"))
    if c_addr and ("コード" in norm(c_addr) or "町字id" in norm(c_addr)):
        c_addr = next((h for h in headers if ("連結表記" in norm(h) or norm(h) in ("所在地", "住所"))), None)
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
                            if not title or len(title) < 3 or any(k in title for k in EXCLUDED_TEXT) or is_non_event_page(title, url=full):
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
                        if not title or len(title) < 3 or any(k in title for k in EXCLUDED_TEXT) or is_non_event_page(title, url=full):
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
                if not title or len(title) < 3 or any(k in title for k in EXCLUDED_TEXT) or is_non_event_page(title, url=full):
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
        if d.get("is_non_event_page") or is_non_event_page(title, d.get("text", ""), url):
            status["rejected"]["non_event_page"] = status["rejected"].get("non_event_page", 0) + 1
            continue
        external_name = d.get("outside_facility_mention") or outside_facility_mention(d.get("text", ""))
        if external_name:
            status["rejected"]["outside_facility_mention:" + external_name] = status["rejected"].get("outside_facility_mention:" + external_name, 0) + 1
            continue
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
        was_multi_day_or_multi_date = len(spans) > 1 or any(a != b for a, b in spans)
        spans = expand_recurring_spans(d.get("text", ""), spans)
        for span in spans:
            if span[1] < TODAY:
                continue
            ev = make_event(title, span, venue, address, url, "江戸川区公式イベントカレンダー",
                            category="区公式", start_time=event_start_time, end_time=event_end_time,
                            image_url=d.get("image_url") or item.get("image"), priority=(9 if len(spans) > 1 or was_multi_day_or_multi_date else 1),
                            description=d.get("text", "")[:2000])
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
            if entry["url"] not in candidates and not is_non_event_page(entry["title"], entry.get("context", ""), entry["url"]) and (cfg.get("accept_all_detail_links") or
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
        if any(k in title for k in EXCLUDED_TEXT) or is_non_event_page(title):
            continue
        full = urljoin(url, href)
        if is_non_event_page(title, url=full):
            continue
        if not _same_host(full, url) or full.rstrip("/") == url.rstrip("/"):
            continue
        path = urlparse(full).path.lower()
        if path.endswith((".pdf", ".jpg", ".png", ".jpeg", ".zip")):
            continue
        parent = a.find_parent(["article", "li", "tr", "section", "div"]) or a.parent
        context = norm(parent.get_text(" ", strip=True)) if parent else title
        if cfg.get("context_ancestors"):
            for ancestor in a.parents:
                if getattr(ancestor, "name", None) in ("body", "html"):
                    break
                candidate_context = norm(ancestor.get_text(" ", strip=True))
                if (candidate_context and len(candidate_context) <= 2500
                        and parse_date_spans(candidate_context) and lookup_venue(candidate_context)):
                    context = candidate_context
                    parent = ancestor
                    break
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
        title = rec["title"] if cfg.get("prefer_listing_title") else (d.get("title") or rec["title"])
        if ((d.get("is_non_event_page") and not cfg.get("prefer_listing_title"))
                or is_non_event_page(title, d.get("text", ""), rec["url"])):
            status["rejected"]["non_event_page"] = status["rejected"].get("non_event_page", 0) + 1
            continue
        external_name = d.get("outside_facility_mention") or outside_facility_mention(d.get("text", ""))
        if external_name:
            key = "outside_facility_mention:" + external_name
            status["rejected"][key] = status["rejected"].get(key, 0) + 1
            continue
        # 一覧カードの<time>は記事公開日・更新日である場合が多いため、開催日として流用しない。
        # 詳細ページに開催日が明示されていない場合は、安全側に倒して除外する。
        spans = d.get("spans") or []
        if not spans and cfg.get("date_from_context"):
            spans = parse_date_spans(rec.get("context", ""))
        if not spans:
            status["rejected"]["no_event_date"] = status["rejected"].get("no_event_date", 0) + 1
            continue
        was_multi_day_or_multi_date = len(spans) > 1 or any(a != b for a, b in spans)
        spans = expand_recurring_spans(d.get("text", "") + " " + rec.get("context", ""), spans)
        if cfg.get("require_public_keyword") and not any(k in f"{title} {rec['context']} {d.get('text', '')}" for k in cfg["require_public_keyword"]):
            status["rejected"]["not_public_event_keyword"] = status["rejected"].get("not_public_event_keyword", 0) + 1
            continue
        venue, address = d.get("venue") or "", d.get("address") or ""
        if not venue:
            r = lookup_venue(f"{title} {rec['context']}")
            if r:
                venue = r.get("name", "")
        if not venue and cfg.get("venue_from_context"):
            r = lookup_venue(rec.get("context", "")) or lookup_library_venue(rec.get("context", ""))
            if r:
                venue, address = r.get("name", ""), r.get("address", "")
        ok, _, reason = verify_edogawa_location(address, venue, default_venue=cfg.get("default_venue"))
        if not ok:
            status["rejected"][reason] = status["rejected"].get(reason, 0) + 1
            continue
        for span in spans:
            ev = make_event(title, span, venue, address, rec["url"], name,
                            category=cfg.get("category", "地域イベント"), default_venue=cfg.get("default_venue"),
                            start_time=d.get("start_time"), end_time=d.get("end_time"),
                            image_url=d.get("image_url"), priority=(9 if len(spans) > 1 or was_multi_day_or_multi_date else cfg.get("priority", 4)),
                            description=d.get("text", "")[:2000])
            if ev:
                if d.get("official_url"):
                    ev.source_urls = sorted(set(ev.source_urls + [d["official_url"]]))
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


LIBRARY_BRANCH_ALIASES = {
    "篠崎子ども": "篠崎子ども図書館", "清新町": "清新町コミュニティ図書館",
    "鹿骨": "鹿骨コミュニティ図書館", "西葛西": "西葛西図書館",
    "東葛西": "東葛西図書館", "小松川": "小松川図書館",
    "松江": "松江図書館", "東部": "東部図書館", "中央": "中央図書館",
    "葛西": "葛西図書館", "小岩": "小岩図書館", "篠崎": "篠崎図書館",
}

def lookup_library_venue(context: str) -> dict[str, Any] | None:
    value = norm(context)
    for branch, full_name in sorted(LIBRARY_BRANCH_ALIASES.items(), key=lambda x: len(x[0]), reverse=True):
        if branch in value:
            reg = lookup_venue(full_name)
            if reg:
                return reg
    return None



def _status_for_direct_source(name: str, url: str) -> dict[str, Any]:
    return {"name": name, "url": url, "status": "failed", "candidate_links": 0,
            "detail_success": 0, "accepted": 0, "rejected": {}}


def _record_rejection(status: dict[str, Any], reason: str) -> None:
    status["rejected"][reason] = status["rejected"].get(reason, 0) + 1


def _append_direct_event(events: list[Event], status: dict[str, Any], *, title: str,
                         spans: list[tuple[date, date]], venue: str, url: str,
                         source: str, start_time: str | None = None,
                         end_time: str | None = None, description: str = "",
                         image_url: str | None = None, priority: int = 3) -> None:
    title = clean_title(title)
    if not title or is_non_event_page(title, description, url):
        _record_rejection(status, "non_event_or_empty_title")
        return
    if not spans:
        _record_rejection(status, "no_event_date")
        return
    reg = lookup_venue(venue)
    actual_venue = reg.get("name", venue) if reg else venue
    address = reg.get("address", "") if reg else ""
    ok, _, reason = verify_edogawa_location(address, actual_venue)
    if not ok:
        _record_rejection(status, reason)
        return
    for span in spans:
        if span[1] < TODAY:
            continue
        ev = make_event(title, span, actual_venue, address, url, source,
                        category="区内公共・文化" if "文化センター" in source else
                                 ("区内公共・図書館" if "図書館" in source else
                                  ("スポーツ" if "陸上" in source else "区内公共・環境")),
                        default_venue=actual_venue, start_time=start_time,
                        end_time=end_time, image_url=image_url,
                        description=description[:2000], priority=priority)
        if ev:
            events.append(ev)
            status["accepted"] += 1
        else:
            _record_rejection(status, "outside_or_unverified_location")


def parse_cultural_center_calendar(months: int = 12) -> tuple[list[Event], dict[str, Any]]:
    """総合文化センターの月間カレンダー表を直接読む。記事リンクを総当たりしない。"""
    name = "総合文化センター・月間カレンダー"
    base = "https://edogawa-bunkacenter.jp/calendar/"
    status = _status_for_direct_source(name, base)
    events: list[Event] = []
    year, month = TODAY.year, TODAY.month
    for offset in range(max(1, months)):
        y, m = year + (month - 1 + offset) // 12, (month - 1 + offset) % 12 + 1
        page_url = f"{base}{y}/{m:02d}/"
        html, meta = fetch_text(page_url, timeout=18)
        status.setdefault("month_checks", []).append({"url": page_url, **meta})
        if not html:
            continue
        status["calendar_months_successful"] = status.get("calendar_months_successful", 0) + 1
        soup = BeautifulSoup(html, "html.parser")
        for row in soup.find_all("tr"):
            cells = row.find_all(["td", "th"], recursive=False)
            if len(cells) < 4:
                continue
            day_text = norm(cells[0].get_text(" ", strip=True))
            title_cell = cells[2]
            title = norm(title_cell.get_text(" ", strip=True))
            if not title or any(x in title for x in ("休館", "休演", "イベント掲載")):
                continue
            day_match = re.search(r"\d{1,2}", day_text)
            if not day_match:
                continue
            try:
                event_day = date(y, m, int(day_match.group()))
            except ValueError:
                continue
            time_text = norm(cells[3].get_text(" ", strip=True))
            start = None
            # 「14:00開演（13:30開場）」では開演時刻のみを採用。
            mt = re.search(r"(\d{1,2})[:：](\d{2})\s*開演", time_text)
            if mt:
                start = f"{int(mt.group(1)):02d}:{mt.group(2)}"
            else:
                mt = re.search(r"(\d{1,2})[:：](\d{2})", time_text)
                if mt:
                    start = f"{int(mt.group(1)):02d}:{mt.group(2)}"
            link = title_cell.find("a", href=True)
            detail_url = urljoin(page_url, link["href"]) if link else page_url
            image = None
            img = title_cell.find("img", src=True)
            if img:
                image = urljoin(page_url, img["src"])
            _append_direct_event(events, status, title=title, spans=[(event_day, event_day)],
                                 venue="江戸川区総合文化センター", url=detail_url,
                                 source=name, start_time=start, description=title, image_url=image)
    status["status"] = "ok" if events else ("calendar_fetch_failed" if not status.get("calendar_months_successful") else "zero_valid_events")
    return events, status


def parse_ecocenter_listing() -> tuple[list[Event], dict[str, Any]]:
    """エコセンターのイベント一覧。日付付き一覧タイトルを開催日根拠にする。"""
    name = "えどがわエコセンター"
    url = "https://edogawa-ecocenter.jp/event/all/"
    status = _status_for_direct_source(name, url)
    html, meta = fetch_text(url, timeout=20)
    status.update(meta)
    events: list[Event] = []
    if not html:
        status["error"] = meta.get("error")
        return events, status
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main") or soup.find(id=re.compile(r"main|content", re.I)) or soup
    for a in main.find_all("a", href=True):
        title = clean_title(a.get_text(" ", strip=True))
        if not title:
            continue
        context_node = a.find_parent(["li", "article", "div"]) or a.parent
        context = norm(context_node.get_text(" ", strip=True)) if context_node else title
        date_text = title + " " + context
        # 一覧の「YYYY年M月D日」表記だけを日付の根拠とする。
        spans = parse_date_spans(date_text)
        if not spans:
            continue
        status["candidate_links"] += 1
        title = re.sub(r"^(?:20\d{2}年)?\s*\d{1,2}月\d{1,2}日\s*", "", title)
        if not title:
            _record_rejection(status, "date_only_link")
            continue
        detail_url = urljoin(url, a["href"])
        start, end = parse_time_range(context)
        image = None
        img = a.find("img") or (context_node.find("img") if context_node else None)
        if img and img.get("src"):
            image = urljoin(url, img["src"])
        _append_direct_event(events, status, title=title, spans=spans,
                             venue="えどがわエコセンター", url=detail_url,
                             source=name, start_time=start, end_time=end,
                             description=context, image_url=image)
    status["status"] = "ok" if events else "zero_valid_events"
    return events, status


def parse_library_event_listing() -> tuple[list[Event], dict[str, Any]]:
    """図書館の一覧テーブル/カードから館名・開催日・時刻・タイトルを直接抽出。"""
    name = "江戸川区立図書館"
    url = "https://www.library.city.edogawa.tokyo.jp/toshow/event/"
    status = _status_for_direct_source(name, url)
    html, meta = fetch_text(url, timeout=20)
    status.update(meta)
    events: list[Event] = []
    if not html:
        status["error"] = meta.get("error")
        return events, status
    soup = BeautifulSoup(html, "html.parser")
    # 現行一覧は「館名/カテゴリ/日時/タイトル」の繰り返しブロック。
    for a in soup.find_all("a", href=True):
        title = clean_title(a.get_text(" ", strip=True))
        if not title or is_non_event_page(title, url=a["href"]):
            continue
        node = a.find_parent(["tr", "li", "article", "dl", "div"]) or a.parent
        best = node
        for ancestor in a.parents:
            if getattr(ancestor, "name", None) in ("body", "html"):
                break
            txt = norm(ancestor.get_text(" ", strip=True))
            if len(txt) > 1800:
                break
            if "館名" in txt and ("日時" in txt or re.search(r"20\d{2}年\s*\d{1,2}月\s*\d{1,2}日", txt)):
                best = ancestor
                break
        context = norm(best.get_text(" ", strip=True)) if best else title
        if not re.search(r"20\d{2}年\s*\d{1,2}月\s*\d{1,2}日", context):
            continue
        venue_reg = lookup_library_venue(context)
        if not venue_reg:
            _record_rejection(status, "unknown_library_branch")
            continue
        # 日付と時刻は一覧行に限って採用。詳細ページの掲載日を開催日と誤認しない。
        spans = parse_date_spans(context)
        if not spans:
            continue
        start, end = parse_time_range(context)
        detail_url = urljoin(url, a["href"])
        status["candidate_links"] += 1
        _append_direct_event(events, status, title=title, spans=spans,
                             venue=venue_reg.get("name", ""), url=detail_url,
                             source=name, start_time=start, end_time=end,
                             description=context)
    status["status"] = "ok" if events else "zero_valid_events"
    return events, status


def parse_athletics_news_listing(max_details: int = 100) -> tuple[list[Event], dict[str, Any]]:
    """陸上競技場のお知らせ記事を追跡。記事公開日は開催日として使わない。"""
    name = "江戸川陸上競技場"
    url = "https://www.edogawa-3field.jp/athletics/category/athletics-news/"
    status = _status_for_direct_source(name, url)
    html, meta = fetch_text(url, timeout=20)
    status.update(meta)
    events: list[Event] = []
    if not html:
        status["error"] = meta.get("error")
        return events, status
    soup = BeautifulSoup(html, "html.parser")
    candidates: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        title = clean_title(a.get_text(" ", strip=True))
        href = urljoin(url, a["href"])
        if not title or not _same_host(href, url) or "/athletics/" not in urlparse(href).path:
            continue
        if href.rstrip("/") == url.rstrip("/") or is_non_event_page(title, url=href):
            continue
        # 記事本文のURL形状のみ。カテゴリ・施設ページやKintone申込リンクは除外。
        if re.search(r"/(?:category|tag|page)/", urlparse(href).path):
            continue
        if href not in candidates:
            candidates[href] = title
    status["candidate_links"] = len(candidates)
    selected = list(candidates.items())[:max_details]
    status["truncated_candidates"] = max(0, len(candidates) - len(selected))
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(fetch_detail_job, {"url": u, "title": t, "default_venue": "スピアーズえどりくフィールド"}): (u, t)
                   for u, t in selected}
        for fut in as_completed(futures):
            u, listed_title = futures[fut]
            try:
                _, detail = fut.result()
            except Exception:
                detail = {}
            if detail.get("http_status"):
                status["detail_success"] += 1
            title = detail.get("title") or listed_title
            if detail.get("is_non_event_page") or is_non_event_page(title, detail.get("text", ""), u):
                _record_rejection(status, "non_event_page")
                continue
            spans = detail.get("spans") or []
            if not spans:
                _record_rejection(status, "no_event_date")
                continue
            # 公開日だけでなく「開催日」「実施日」等から抽出された開催日がある記事に限定。
            _append_direct_event(events, status, title=title, spans=spans,
                                 venue="江戸川陸上競技場", url=u,
                                 source=name, start_time=detail.get("start_time"),
                                 end_time=detail.get("end_time"),
                                 description=detail.get("text", ""),
                                 image_url=detail.get("image_url"), priority=4)
    status["status"] = "ok" if events else ("detail_fetch_failed" if not status["detail_success"] and selected else "zero_valid_events")
    return events, status


LISTING_SOURCES = [
    {"name": "葛西海浜公園", "url": "https://kasaikaihinpark.com/event/", "default_venue": "葛西海浜公園", "category": "区内公共・公園", "priority": 2, "max_details": 80},
    {"name": "葛西臨海公園", "url": "https://www.tokyo-park.or.jp/park/kasairinkai/news/index.html", "default_venue": "葛西臨海公園", "category": "区内公共・公園", "priority": 2, "max_details": 80},
    {"name": "篠崎公園", "url": "https://www.tokyo-park.or.jp/park/shinozaki/news/index.html", "default_venue": "篠崎公園", "category": "区内公共・公園", "priority": 2, "max_details": 70},
    {"name": "大島小松川公園", "url": "https://www.tokyo-park.or.jp/park/ojima-komatsugawa/news/index.html", "default_venue": "大島小松川公園", "category": "区内公共・公園", "priority": 2, "max_details": 70},
    {"name": "魔法の文学館", "url": "https://kikismuseum.jp/category/event/", "default_venue": "魔法の文学館", "category": "区内公共・文化", "priority": 2, "max_details": 50},
    {"name": "タワーホール船堀", "url": "https://www.towerhall.jp/events/list/", "default_venue": "タワーホール船堀", "category": "区内公共・文化", "priority": 2, "max_details": 80},
    {"name": "葛西臨海水族園", "url": "https://www.tokyo-zoo.net/zoo/kasai/", "default_venue": "葛西臨海水族園", "category": "区内公共・文化", "priority": 2, "max_details": 80},
    {"name": "江戸川レクリエーション公園", "url": "https://edogawa-recreation.com/event/", "default_venue": "総合レクリエーション公園サービスセンター", "category": "区内公共・公園", "priority": 2, "max_details": 100},
    {"name": "新川さくら館", "url": "https://sakurakan-edogawa.jp/topics/", "default_venue": "新川さくら館", "category": "区内公共・文化", "priority": 2, "max_details": 70},
    {"name": "江戸川まいぷれ", "url": "https://edogawa.mypl.net/event/", "default_venue": None, "category": "地域・まいぷれ", "priority": 4, "max_details": 120},
    {"name": "アリオ葛西", "url": "https://kasai.ario.jp/event/", "default_venue": "アリオ葛西", "category": "商業施設", "priority": 8, "max_details": 80},
    {"name": "TRXフリーマーケット", "url": "https://trx.jp/", "default_venue": None, "category": "地域・フリーマーケット", "priority": 4, "max_details": 100},
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
        was_multi_day_or_multi_date = len(spans) > 1 or any(a != b for a, b in spans)
        spans = expand_recurring_spans(text, spans)
        for span in spans:
            fingerprint = (venue, f"{span[0]}_{span[1]}")
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            title = f"{base_title}（{venue}）"
            ev = make_event(title, span, venue, reg.get("address", ""), url,
                            "鶴岡市公式・江戸川区内会場", category="友好都市・物産展",
                            image_url=image_url, priority=(9 if len(spans) > 1 or was_multi_day_or_multi_date else 4))
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
        # 公開ビューURLも確認し、未設定を単なる0件として隠さない。
        viewer_html, viewer_meta = fetch_text(KVIEWER_PUBLIC_URL, timeout=25)
        status["viewer_probe"] = viewer_meta
        if viewer_html:
            viewer_soup = BeautifulSoup(viewer_html, "html.parser")
            status["viewer_title"] = norm(viewer_soup.title.get_text(" ", strip=True)) if viewer_soup.title else ""
            status["viewer_table_count"] = len(viewer_soup.find_all("table"))
            status["viewer_link_count"] = len(viewer_soup.find_all("a", href=True))
            status["status"] = "public_view_accessible_api_not_configured"
        else:
            status["status"] = "public_view_unreachable_api_not_configured"
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
        original_spans = parse_date_spans(dtext)
        was_multi_day_or_multi_date = len(original_spans) > 1 or any(a != b for a, b in original_spans)
        spans = expand_recurring_spans(dtext, original_spans)
        for span in spans:
            ev = make_event(title, span, venue, address, link, "Kintone公開API",
                            category="区内スポーツ", priority=(9 if len(spans) > 1 or was_multi_day_or_multi_date else 3))
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
    reports: dict[str, Any] = {"app_version": APP_VERSION, "mode": "live", "coverage_complete": False,
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

    # 先に区公式HTMLを取得し、同じ詳細URLを持つOpenData行はCSV側から除外する。
    # HTMLが存在するイベントの期間CSV展開による「開催日でない日」の大量生成を防ぐ。
    official_events, official_status = parse_city_calendar(months=months)
    all_events.extend(official_events)
    reports["sources"]["江戸川区公式イベントカレンダー"] = official_status
    official_detail_urls = {normalize_event_url(x) for ev in official_events
                            for x in (ev.source_urls + ([ev.url] if ev.url else []))}
    official_detail_urls.discard("")
    for name, url, prio in (
        ("江戸川区イベントカレンダーCSV", CITY_EVENT_CSV, 1),
        ("東京都OpenData江戸川区イベント一覧CSV", TOKYO_EVENT_CSV, 1),
    ):
        events, status = parse_csv_source(url, name, priority=prio, suppress_urls=official_detail_urls)
        all_events.extend(events); reports["sources"][name] = status

    if include_supplements:
        # v3.2.8 第一段階: 4施設は汎用リンク巡回を使わず、一覧の構造に合わせて取得する。
        for parser in (parse_cultural_center_calendar, parse_ecocenter_listing,
                       parse_library_event_listing, parse_athletics_news_listing):
            events, status = parser()
            all_events.extend(events)
            reports["sources"][status.get("name", parser.__name__)] = status
        for cfg in LISTING_SOURCES:
            events, status = parse_listing_source(cfg)
            all_events.extend(events); reports["sources"][cfg["name"]] = status
        public_terms = ("文化祭", "学園祭", "公開", "説明会", "体験", "公演", "ライブ",
                        "コンサート", "展示", "フェスタ", "まつり", "バザー", "見学会", "教室")
        for label, url in SCHOOL_SOURCES:
            cfg = {"name": label, "url": url, "default_venue": label,
                   "category": "学校公開イベント", "priority": 5, "max_details": 250,
                   "require_public_keyword": public_terms}
            events, status = parse_listing_source(cfg)
            all_events.extend(events); reports["sources"][label] = status
        tsuruoka_events, tsuruoka_status = parse_tsuruoka_source()
        all_events.extend(tsuruoka_events); reports["sources"]["鶴岡市・江戸川区内物産展"] = tsuruoka_status
        # Kintone/KViewer は内容・取得仕様の確認が終わるまで収集対象外。

    merged = merge_events(all_events)
    save_geocode_cache()
    reports["geocoding"] = dict(GEOCODE_STATS)
    reports["totals"] = {
        "raw_records": len(all_events), "unique_events": len(merged),
        "deduplicated_records": len(all_events) - len(merged),
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
        has_candidates = (int(source_report.get("candidate_links", 0) or 0) > 0
                          or int(source_report.get("candidate_rows", 0) or 0) > 0)
        has_successful_details = int(source_report.get("detail_success", 0) or 0) > 0
        if now_count == 0 and has_candidates and (has_successful_details or "CSV" in source_name):
            source_report["unexpected_zero"] = True
            reports["warnings"].append(f"{source_name}: 候補データがあるのに0件。列対応/フィルター/日付/会場抽出の取りこぼしを要確認")
    for source_name, source_report in reports["sources"].items():
        print(f"[SOURCE] {source_name}: status={source_report.get('status')} "
              f"accepted={source_report.get('accepted', 0)} "
              f"candidates={source_report.get('candidate_rows', source_report.get('candidate_links', source_report.get('candidate_urls', '-')))}")
    if any(x.get("unexpected_zero") for x in reports["sources"].values()):
        reports["coverage_complete"] = False
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
    seed = "|".join((normalize_event_url(ev.url), ev.start_date, ev.start_time or ""))
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32] + "@edogawa-events.local"


def build_ics(events: list[Event], generated_at: str | None = None, execution_id: str | None = None) -> str:
    generated = datetime.fromisoformat(generated_at) if generated_at else datetime.now(JST)
    now_utc = generated.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Edogawa Ward Events//JP v3.2.7//EN",
             "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "X-WR-CALNAME:江戸川区内イベントカレンダー",
             "X-WR-TIMEZONE:Asia/Tokyo",
             "X-WR-CALDESC:開催場所が江戸川区内と確認できたイベントのみを収録した非公式カレンダー"]
    if execution_id:
        lines.append(f"X-EDOGAWA-EXECUTION-ID:{execution_id}")
    lines.append("X-EDOGAWA-GENERATED-AT:" + generated.isoformat())
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
        category_label = normalize_category(ev.category)
        summary = f"{ev.title} [{category_label}]" if category_label else ev.title
        lines.append(fold_ics("SUMMARY:" + escape_ics(summary)))
        desc = [f"詳細: {ev.url}", f"会場: {ev.venue}", f"住所: {ev.address}", f"情報元: {ev.source}"]
        if ev.image_url: desc.append(f"画像: {ev.image_url}")
        if ev.description: desc.append(ev.description)
        if ev.source_urls: desc.append("参照元: " + " / ".join(ev.source_urls))
        lines.append(fold_ics("DESCRIPTION:" + escape_ics("\n".join(x for x in desc if x))))
        if ev.url: lines.append(fold_ics("URL:" + ev.url))
        if ev.venue or ev.address:
            lines.append(fold_ics("LOCATION:" + escape_ics(combine_location(ev.venue, ev.address))))
        if ev.latitude is not None and ev.longitude is not None:
            lines.append(f"GEO:{ev.latitude:.6f};{ev.longitude:.6f}")
        lines.extend([f"PRIORITY:{max(1, min(9, ev.priority))}",
                      fold_ics("CATEGORIES:" + escape_ics(category_label.upper().replace("・", "-")))])
        if ev.image_url:
            lines.append(fold_ics("IMAGE;VALUE=URI;DISPLAY=BADGE:" + ev.image_url))
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def to_map_json(events: list[Event], reports: dict[str, Any], mode: str = "live") -> dict[str, Any]:
    items = []
    for ev in events:
        items.append({"id": make_uid(ev), "title": ev.title, "category": normalize_category(ev.category),
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
    return {"schema_version": "1.0", "app_version": APP_VERSION,
            "generated_at": reports.get("generated_at") or datetime.now(JST).isoformat(),
            "execution_id": reports.get("execution_id"),
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


def atomic_write_bundle(files: dict[Path, str]) -> None:
    """同一実行で作った複数成果物をすべて一時保存してから置換する。"""
    staged: list[tuple[Path, Path]] = []
    try:
        for path, content in files.items():
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(content, encoding="utf-8", newline="")
            staged.append((tmp, path))
        for tmp, path in staged:
            os.replace(tmp, path)
    finally:
        for tmp, _ in staged:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


def validate_outputs(ics_text: str, events: list[Event]) -> None:
    if "BEGIN:VCALENDAR" not in ics_text or "END:VCALENDAR" not in ics_text:
        raise RuntimeError("iCal structure validation failed")
    if ics_text.count("BEGIN:VEVENT") != ics_text.count("END:VEVENT"):
        raise RuntimeError("VEVENT count mismatch")
    if "PRODID:-//Edogawa Ward Events//JP v3.2.7//EN" not in ics_text:
        raise RuntimeError("iCal version marker mismatch")
    if not events:
        raise RuntimeError("No verified Edogawa events. Previous outputs are preserved.")
    for e in events:
        location_ok, _, reason = verify_edogawa_location(e.address, e.venue)
        if not e.verified_location or not location_ok:
            raise RuntimeError(f"Non-verified/out-of-ward location found ({reason}): {e.title} / {e.venue} / {e.address}")
        if e.latitude is None or e.longitude is None:
            raise RuntimeError(f"Missing geocoded coordinates: {e.title} / {e.venue} / {e.address}")
        if not _inside_edogawa_bounds(float(e.latitude), float(e.longitude)):
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
        reports = {"app_version": APP_VERSION, "mode": "bootstrap-preview", "complete": False,
                   "warning": "2026-11-26時点で個別詳細ページを確認できたイベントのみ。全件収集ではありません。",
                   "sources": {}, "totals": {"unique_events": len(events), "strict_edogawa_only": True}}
        mode = "bootstrap-preview"
        reports["generated_at"] = datetime.now(JST).isoformat()
        reports["execution_id"] = hashlib.sha256(reports["generated_at"].encode("utf-8")).hexdigest()[:16]
        ics = build_ics(events, reports["generated_at"], reports["execution_id"]).replace(
            "X-WR-CALNAME:江戸川区内イベントカレンダー",
            "X-WR-CALNAME:江戸川区内イベントカレンダー（確認済みプレビュー・未完成）"
        )
        payload = to_map_json(events, reports, mode=mode)
        validate_outputs(ics, events)
        atomic_write_bundle({OUTPUT_PREVIEW_ICS: ics,
                             OUTPUT_PREVIEW_JSON: json.dumps(payload, ensure_ascii=False, indent=2),
                             OUTPUT_PREVIEW_STATUS: json.dumps(reports, ensure_ascii=False, indent=2)})
        print(f"Generated preview: {OUTPUT_PREVIEW_ICS.name} ({len(events)} events)")
        print(f"Generated preview: {OUTPUT_PREVIEW_JSON.name}")
        print(f"Mode: {mode}; complete: false; strict Edogawa-only filter: ON")
        return 0
    else:
        events, reports = scrape_all(months=args.months, include_supplements=not args.skip_supplements)
        mode = "live"
        official = reports["sources"].get("江戸川区公式イベントカレンダー", {})
        csv_schema_recognized = any(
            "CSV" in n and s.get("status") in ("ok", "zero_valid_rows") and s.get("headers")
            and int(s.get("candidate_rows", 0) or 0) > 0
            for n, s in reports["sources"].items()
        )
        recognized_csv = any(
            "CSV" in n and s.get("status") == "ok" and int(s.get("accepted", 0) or 0) > 0
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
            "event_csv_schema_recognized": csv_schema_recognized,
            "recognized_event_csv": recognized_csv,
            "calendar_months_successful": calendar_success,
            "calendar_months_requested": calendar_requested,
            "calendar_healthy": calendar_healthy,
            "healthy": recognized_csv or calendar_healthy,
            "primary_event_success": primary_event_success,
        }
        if (not events or not reports["primary_source_health"]["healthy"] or not primary_event_success
            or any(e.latitude is None or e.longitude is None for e in events)):
            atomic_write(OUTPUT_FAILED_STATUS, json.dumps(reports, ensure_ascii=False, indent=2))
            raise RuntimeError("イベントが0件、一次取得元の稼働確認失敗、または一次情報から有効イベントを確認できません。公開中のiCal/JSON/ステータスはすべて保持し、失敗診断のみsource_status_failed.jsonへ出力しました。")
        if not recognized_csv:
            reports["warnings"].append("イベントCSVは列構成を認識できても有効イベント0件の可能性があります。各CSVのrejected内訳を確認してください。公式カレンダーHTMLで補完しています。")
        if official.get("status") != "ok" and not recognized_csv:
            reports["warnings"].append("公式カレンダーで有効イベントを確認できません。source_status.jsonの月別・詳細ページ状態を確認してください。")
        if any("CSV" in n and s.get("status") == "schema_unrecognized" for n, s in reports["sources"].items()):
            reports.setdefault("warnings", []).append("CSV列構成が未認識。CSVは採用せず、公式HTMLカレンダー等で補完しました。")
    reports["generated_at"] = datetime.now(JST).isoformat()
    reports["execution_id"] = hashlib.sha256(reports["generated_at"].encode("utf-8")).hexdigest()[:16]
    ics = build_ics(events, reports["generated_at"], reports["execution_id"])
    payload = to_map_json(events, reports, mode=mode)
    validate_outputs(ics, events)
    atomic_write_bundle({OUTPUT_ICS: ics,
                         OUTPUT_JSON: json.dumps(payload, ensure_ascii=False, indent=2),
                         OUTPUT_STATUS: json.dumps(reports, ensure_ascii=False, indent=2)})
    try:
        OUTPUT_FAILED_STATUS.unlink(missing_ok=True)
    except OSError:
        pass
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
