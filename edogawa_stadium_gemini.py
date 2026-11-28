#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""江戸川区球場の大会等予定表をGemini API無料枠で読み取る専用処理。"""
from __future__ import annotations

import json
import os
import re
from datetime import date
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

INDEX_URL = "https://www.edogawa-3field.jp/baseball/taikaiguid/"
MODEL_NAME = "gemini-3.5-flash-lite"  # Google公式料金表でFree Tier対象を確認
VENUE = "江戸川区球場"
ADDRESS = "東京都江戸川区西葛西7丁目2番1号"
SOURCE_NAME = "江戸川区球場・Gemini大会予定表"
MAX_ASSET_BYTES = 12 * 1024 * 1024

EVENT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "events": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "date": {"type": "STRING", "description": "開催日 YYYY-MM-DD"},
                    "title": {"type": "STRING", "description": "大会・試合・利用予定の名称"},
                    "start_time": {"type": "STRING", "description": "開始時刻 HH:MM。不明なら空文字"},
                    "end_time": {"type": "STRING", "description": "終了時刻 HH:MM。不明なら空文字"},
                    "kind": {"type": "STRING", "description": "event / reserve / closure / maintenance / other"},
                    "notes": {"type": "STRING", "description": "対戦相手、予備日、備考など画像に明記された情報"},
                },
                "required": ["date", "title", "start_time", "end_time", "kind", "notes"],
            },
        }
    },
    "required": ["events"],
}



def find_month_article(index_html: str, year: int, month: int, base_url: str = INDEX_URL) -> str | None:
    """公式一覧HTMLから指定年月の『大会等予定表』ページURLを返す。

    旧パイロット版のテスト・呼び出し元との互換性を保つ公開ヘルパー。
    年月が明示されたリンクだけを対象にし、別年の同月ページを誤選択しない。
    """
    soup = BeautifulSoup(index_html, "html.parser")
    for anchor in soup.find_all("a", href=True):
        label = " ".join([anchor.get_text(" ", strip=True), str(anchor.get("title", ""))])
        parent_text = anchor.parent.get_text(" ", strip=True) if anchor.parent else ""
        combined = f"{label} {parent_text}"
        normalized = re.sub(r"\s+", "", combined)
        era = re.search(r"令和(\d{1,2})年", normalized)
        match = re.search(r"(20\d{2}).{0,12}?年.{0,12}?(\d{1,2})月", normalized)
        if match:
            found_year, found_month = int(match.group(1)), int(match.group(2))
        elif era:
            found_year = 2018 + int(era.group(1))
            m = re.search(r"年[^0-9]{0,12}(\d{1,2})月", normalized)
            if not m:
                continue
            found_month = int(m.group(1))
        else:
            continue
        if (found_year, found_month) == (int(year), int(month)) and "大会等予定表" in combined:
            target = urljoin(base_url, anchor["href"].strip())
            if urlparse(target).scheme in ("http", "https"):
                return target
    return None


def discover_media_urls(article_html: str, article_url: str) -> list[str]:
    """予定表ページ内の画像/PDF URLを文書順に重複なく抽出する。"""
    soup = BeautifulSoup(article_html, "html.parser")
    found: list[str] = []
    seen: set[str] = set()
    for node in soup.find_all("img"):
        for attr in ("src", "data-src", "data-original", "data-lazy-src"):
            raw = node.get(attr)
            if not raw:
                continue
            full = urljoin(article_url, raw.strip())
            if urlparse(full).scheme in ("http", "https") and full not in seen:
                seen.add(full)
                found.append(full)
            break
    for anchor in soup.find_all("a", href=True):
        raw = anchor["href"].strip()
        full = urljoin(article_url, raw)
        path = urlparse(full).path.lower()
        if path.endswith((".pdf", ".png", ".jpg", ".jpeg", ".webp")) and urlparse(full).scheme in ("http", "https") and full not in seen:
            seen.add(full)
            found.append(full)
    return found


def validate_result(data: dict[str, Any], year: int, month: int) -> list[str]:
    """Geminiの抽出結果を対象年月・曜日・時刻・重複の観点で検証する。"""
    errors: list[str] = []
    if not isinstance(data, dict) or not isinstance(data.get("events"), list):
        return ["events配列がありません"]
    seen: set[tuple[str, str, str, str]] = set()
    weekdays = "月火水木金土日"
    for index, row in enumerate(data["events"], 1):
        if not isinstance(row, dict):
            errors.append(f"{index}件目: イベント形式が不正")
            continue
        try:
            event_day = date.fromisoformat(str(row.get("date", "")))
        except (TypeError, ValueError):
            errors.append(f"{index}件目: 日付形式が不正")
            continue
        if (event_day.year, event_day.month) != (int(year), int(month)):
            errors.append(f"{index}件目: 対象月外の日付です")
        weekday = row.get("weekday")
        if weekday and str(weekday).strip() != weekdays[event_day.weekday()]:
            errors.append(f"{index}件目: 曜日が日付と一致しません")
        title = str(row.get("title", "")).strip()
        if not title:
            errors.append(f"{index}件目: イベント名が空です")
        confidence = row.get("confidence")
        if confidence is not None:
            try:
                if float(confidence) < 0.8:
                    errors.append(f"{index}件目: 信頼度が低いため人手確認が必要です")
            except (TypeError, ValueError):
                errors.append(f"{index}件目: 信頼度の形式が不正です")
        start, end = row.get("start_time"), row.get("end_time")
        for value in (start, end):
            if value not in (None, "") and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", str(value)):
                errors.append(f"{index}件目: 時刻形式が不正です")
                break
        if start and end and re.fullmatch(r"\d{2}:\d{2}", str(start)) and re.fullmatch(r"\d{2}:\d{2}", str(end)) and str(end) < str(start):
            errors.append(f"{index}件目: 終了時刻が開始時刻より前です")
        key = (event_day.isoformat(), title, str(start or ""), str(end or ""))
        if key in seen:
            errors.append(f"{index}件目: 重複イベントです")
        seen.add(key)
    return errors

def _get(url: str, timeout: int = 25) -> requests.Response:
    response = requests.get(url, timeout=timeout, headers={
        "User-Agent": "Mozilla/5.0 (compatible; EdogawaEventCalendar/3.2.19)",
        "Accept-Language": "ja,en-US;q=0.8,en;q=0.6",
    })
    response.raise_for_status()
    return response


def _month_from_title(title: str, app: Any) -> tuple[int, int] | None:
    text = app.era_to_western(title)
    text = re.sub(r"[（(]令和\s*\d{1,2}[）)]", "", text)
    match = re.search(r"(20\d{2})\s*年[^0-9]{0,12}(\d{1,2})\s*月", text)
    if not match:
        match = re.search(r"(20\d{2})\s*年\s*(\d{1,2})\s*月", text)
    if not match:
        return None
    year, month = int(match.group(1)), int(match.group(2))
    return (year, month) if 1 <= month <= 12 else None


def _discover_month_pages(html: str, app: Any) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    pages: dict[str, dict[str, str]] = {}
    # 年月の見出しを含むリンクを優先。月ページのURLは公式サイトのリンクからのみ取得する。
    for anchor in soup.find_all("a", href=True):
        title = app.norm(anchor.get_text(" ", strip=True) or anchor.get("title", ""))
        href = urljoin(INDEX_URL, anchor["href"].strip())
        if "大会等予定表" not in title and "大会等予定表" not in app.norm(anchor.parent.get_text(" ", strip=True) if anchor.parent else ""):
            continue
        month = _month_from_title(title + " " + app.norm(anchor.parent.get_text(" ", strip=True) if anchor.parent else ""), app)
        if not month or urlparse(href).netloc != urlparse(INDEX_URL).netloc:
            continue
        pages[href] = {"url": href, "title": title or f"{month[0]}年{month[1]}月大会等予定表",
                       "year": str(month[0]), "month": str(month[1])}
    # 詳細ページURLをリンクから取れないCMS変更時は、トップページ内の年月見出し直下の画像を使う。
    if not pages:
        month = _month_from_title(soup.get_text(" ", strip=True), app)
        if month:
            pages[INDEX_URL] = {"url": INDEX_URL, "title": f"{month[0]}年{month[1]}月大会等予定表",
                                "year": str(month[0]), "month": str(month[1])}
    # 取得対象は当月から先12か月。過去の年度アーカイブをGeminiに送らず、
    # 無料枠の消費と過去予定の再登録を防ぐ。
    current = (app.TODAY.year, app.TODAY.month)
    upper_index = current[0] * 12 + current[1] + 12
    selected = []
    for page in pages.values():
        ym_index = int(page["year"]) * 12 + int(page["month"])
        if current[0] * 12 + current[1] <= ym_index <= upper_index:
            selected.append(page)
    return sorted(selected, key=lambda x: (int(x["year"]), int(x["month"])))


def _assets_for_page(html: str, page_url: str) -> list[tuple[str, bytes, str]]:
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    for node in soup.find_all("img"):
        src = node.get("src") or node.get("data-src") or node.get("data-original")
        if src:
            full = urljoin(page_url, src.strip())
            if urlparse(full).scheme in ("http", "https"):
                urls.append(full)
    for anchor in soup.find_all("a", href=True):
        href = urljoin(page_url, anchor["href"].strip())
        if urlparse(href).path.lower().endswith((".pdf", ".png", ".jpg", ".jpeg", ".webp")):
            urls.append(href)
    out: list[tuple[str, bytes, str]] = []
    seen: set[str] = set()
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        path = urlparse(url).path.lower()
        if re.search(r"(?:logo|icon|banner|sns|facebook|instagram|noimage)", path):
            continue
        try:
            response = _get(url, timeout=25)
            data = response.content
            if not data or len(data) > MAX_ASSET_BYTES:
                continue
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
            if path.endswith(".pdf") or content_type == "application/pdf":
                mime = "application/pdf"
            elif path.endswith(".png") or content_type == "image/png":
                mime = "image/png"
            elif path.endswith(".webp") or content_type == "image/webp":
                mime = "image/webp"
            else:
                mime = "image/jpeg"
            if mime == "application/pdf" or mime.startswith("image/"):
                out.append((url, data, mime))
        except Exception:
            continue
    return out


def _gemini_extract(client: Any, page: dict[str, str], assets: list[tuple[str, bytes, str]], page_text: str) -> dict[str, Any]:
    from google.genai import types

    year, month = int(page["year"]), int(page["month"])
    prompt = f"""あなたは日本の野球場の月間予定表を読み取るデータ抽出器です。
対象施設はオーエンススタジアム江戸川（江戸川区球場）、対象月は{year}年{month}月です。
添付の公式予定表画像/PDFとページ本文を読み、表の行・列・曜日・日付の対応を保って全予定を抽出してください。

重要なルール:
- 表の見出しにある年・月を基準にし、dateは必ず{year}-{month:02d}-DD形式にする。
- 1日に複数の大会・試合がある場合は、イベントを分けて複数件出す。
- 1つの行の「2日・9日」のように同じ大会が複数日に記載されている場合は、日付ごとに1件ずつ出す。
- 大会名、試合名、対戦カード、開始時刻・終了時刻は書かれている範囲で正確に抽出する。
- 予備日・予備日程はkind=reserve、休場・休館はclosure、整備・点検等はmaintenance。
- 通常の大会・試合はkind=event。空欄、単なる曜日、欄外の凡例はイベント化しない。
- 予定がない日を補完しない。表にある予定だけを抽出する。
- 年月が対象月と違う日付、読み取れない日付、推測した時刻は出力しない。
- 備考には対戦相手、予備日条件、雨天時対応など、表に明記された短い情報だけを入れる。
- 同じセルを重複して読み取らない。ただし同じ日に別試合がある場合は両方残す。
- 出力は指定JSONスキーマに従う。

ページ本文:
{page_text[:9000]}

資料URL:
{page['url']}
"""
    contents: list[Any] = [prompt]
    for _url, data, mime in assets:
        contents.append(types.Part.from_bytes(data=data, mime_type=mime))
    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=contents,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=EVENT_SCHEMA,
            temperature=0,
        ),
    )
    text = getattr(response, "text", None)
    if not text:
        raise RuntimeError("Gemini returned an empty response")
    parsed = json.loads(text)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("events"), list):
        raise ValueError("Gemini JSON schema mismatch: events array missing")
    return parsed


def collect_stadium_events(app: Any) -> tuple[list[Any], dict[str, Any]]:
    """公式の月別画像/PDFをGeminiで解釈し、検証済みEventだけを返す。"""
    status: dict[str, Any] = {"name": SOURCE_NAME, "url": INDEX_URL, "status": "not_configured",
                              "model": MODEL_NAME, "tier": "free_only", "accepted": 0,
                              "months": [], "rejected": {}}
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        status["note"] = "GEMINI_API_KEY未設定のためGemini処理をスキップ"
        return [], status
    try:
        from google import genai
    except Exception as exc:
        status.update({"status": "dependency_missing", "error": str(exc)})
        return [], status
    try:
        index_response = _get(INDEX_URL)
        index_html = index_response.text
        pages = _discover_month_pages(index_html, app)
        status["discovered_month_pages"] = len(pages)
        if not pages:
            status.update({"status": "no_month_pages", "error": "公式ページから年月付き予定表リンクを発見できません"})
            return [], status
        client = genai.Client(api_key=api_key)
        events: list[Any] = []
        seen: set[tuple[str, str, str]] = set()
        for page in pages:
            month_status = {"title": page["title"], "url": page["url"], "assets": 0,
                            "accepted": 0, "rejected": {}}
            try:
                page_response = _get(page["url"])
                page_soup = BeautifulSoup(page_response.text, "html.parser")
                page_text = app.norm(page_soup.get_text(" ", strip=True))
                assets = _assets_for_page(page_response.text, page["url"])
                month_status["assets"] = len(assets)
                if not assets:
                    month_status["status"] = "no_schedule_assets"
                    status["months"].append(month_status)
                    continue
                parsed = _gemini_extract(client, page, assets, page_text)
                for row in parsed["events"]:
                    try:
                        event_date = date.fromisoformat(str(row.get("date", "")))
                        if event_date.year != int(page["year"]) or event_date.month != int(page["month"]):
                            raise ValueError("date_outside_target_month")
                        title = app.norm(row.get("title", ""))
                        if not title or title in {"-", "―", "なし", "予定なし"}:
                            raise ValueError("empty_title")
                        start_time = app.norm(row.get("start_time", "")) or None
                        end_time = app.norm(row.get("end_time", "")) or None
                        for value in (start_time, end_time):
                            if value and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
                                raise ValueError("invalid_time")
                        kind = app.norm(row.get("kind", "event")).lower()
                        prefix = {"reserve": "【予備日】", "closure": "【休場】",
                                  "maintenance": "【整備】"}.get(kind, "")
                        if prefix and not title.startswith(prefix):
                            title = prefix + title
                        notes = app.norm(row.get("notes", ""))
                        ev = app.make_event(
                            title, (event_date, event_date), VENUE, ADDRESS, page["url"], SOURCE_NAME,
                            category="区内スポーツ", start_time=start_time, end_time=end_time,
                            description=notes, priority=2,
                        )
                        if not ev:
                            raise ValueError("event_validation_or_geocoding_failed")
                        key = (ev.start_date, app._title_fingerprint(ev.title), ev.start_time or "")
                        if key in seen:
                            raise ValueError("duplicate")
                        seen.add(key)
                        ev.source_urls = sorted(set(ev.source_urls + [INDEX_URL] + [a[0] for a in assets]))
                        events.append(ev)
                        month_status["accepted"] += 1
                    except Exception as exc:
                        reason = str(exc) or type(exc).__name__
                        month_status["rejected"][reason] = month_status["rejected"].get(reason, 0) + 1
                month_status["status"] = "ok" if month_status["accepted"] else "zero_valid_events"
            except Exception as exc:
                month_status["status"] = "failed"
                month_status["error"] = f"{type(exc).__name__}: {exc}"
            status["months"].append(month_status)
        status["accepted"] = len(events)
        status["status"] = "ok" if events else "zero_valid_events"
        return events, status
    except Exception as exc:
        status.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
        return [], status
