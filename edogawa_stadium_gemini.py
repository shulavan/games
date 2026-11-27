#!/usr/bin/env python3
"""Extract Edogawa Baseball Stadium monthly schedule with Gemini URL Context.

This is an isolated pilot. It does not modify the main Edogawa iCal generator.
All Gemini requests use the Gemini Developer API free-tier model configured below.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

INDEX_URL = "https://www.edogawa-3field.jp/baseball/taikaiguid/"
VENUE = "江戸川区球場"
TIMEZONE = ZoneInfo("Asia/Tokyo")
DEFAULT_MODEL = "gemini-3.5-flash-lite"
USER_AGENT = "EdogawaStadiumSchedulePilot/1.0 (+https://shulavan.github.io/games/)"

JSON_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "venue": {"type": "STRING"},
        "year": {"type": "INTEGER"},
        "month": {"type": "INTEGER"},
        "events": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "date": {"type": "STRING", "description": "YYYY-MM-DD"},
                    "weekday": {"type": "STRING", "enum": ["月", "火", "水", "木", "金", "土", "日"]},
                    "title": {"type": "STRING"},
                    "event_type": {"type": "STRING", "enum": ["大会", "試合", "教室", "施設利用", "予備日", "休場", "その他"]},
                    "status": {"type": "STRING", "enum": ["開催予定", "予備日", "休場", "未定"]},
                    "start_time": {"type": "STRING", "nullable": True, "description": "HH:MM; unknown or not printed must be null"},
                    "end_time": {"type": "STRING", "nullable": True, "description": "HH:MM; unknown or not printed must be null"},
                    "notes": {"type": "STRING"},
                    "confidence": {"type": "NUMBER", "minimum": 0, "maximum": 1}
                },
                "required": ["date", "weekday", "title", "event_type", "status", "start_time", "end_time", "notes", "confidence"]
            }
        },
        "warnings": {"type": "ARRAY", "items": {"type": "STRING"}}
    },
    "required": ["venue", "year", "month", "events", "warnings"]
}

PROMPT = """あなたは日本語の野球場月間予定表を読み取るデータ抽出エンジンです。
指定された江戸川区球場の公式ページと、そのページに掲載された月間予定表画像/PDFだけを根拠に、対象月の予定を全件抽出してください。

厳守事項:
1. 画像/PDFの表の行・列・結合セルを視覚的に読み取り、日付と内容の対応を正確に復元する。
2. 「2(月)・9(月)」のように複数日が同じ行にある場合、日付ごとに別イベントを作り、weekdayも各日付に対応させる。
3. 時刻が印字されていない予定に時刻を推測して付けない。start_time/end_time は null。
4. 空欄の日をイベントとして作らない。予備日・休場日と明記された場合はstatusで区別する。
5. 年月は画像見出しを優先し、指定年月と異なる古い画像や別月の情報を混ぜない。
6. 同じ予定が複数画像に重複掲載されている場合は1件に統合する。
7. 文字が読めない場合は推測せずwarningsに記載し、confidenceを下げる。
8. 試合開始時刻と施設利用時間を混同しない。画像にない終了時刻はnull。
9. ページの掲載日、過去記事、別記事の大会説明を予定表の予定として追加しない。
10. titleは予定表の「内容」欄の名称を簡潔に保持する。

対象: 江戸川区球場、{year}年{month}月。
出力は指定JSONスキーマに従う。説明文やMarkdownは出力しない。
"""


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def fetch_html(url: str, timeout: int = 30) -> str:
    response = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    response.encoding = response.apparent_encoding or response.encoding
    return response.text


def find_month_article(index_html: str, year: int, month: int, base_url: str = INDEX_URL) -> str:
    soup = BeautifulSoup(index_html, "html.parser")
    patterns = [
        re.compile(rf"{year}\s*\(令和\s*\d+\)\s*年\s*{month}\s*月\s*大会等予定表"),
        re.compile(rf"{year}\s*年\s*{month}\s*月\s*大会等予定表"),
    ]
    for anchor in soup.find_all("a", href=True):
        label = normalize_text(anchor.get_text(" ", strip=True))
        if any(p.search(label) for p in patterns):
            return urljoin(base_url, anchor["href"])
    raise ValueError(f"{year}年{month}月の大会等予定表リンクが見つかりません")


def discover_media_urls(article_html: str, article_url: str) -> list[str]:
    soup = BeautifulSoup(article_html, "html.parser")
    found: list[str] = []
    allowed = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".pdf"}

    def add(raw: str | None) -> None:
        if not raw:
            return
        raw = raw.strip()
        if not raw or raw.startswith(("data:", "javascript:", "#")):
            return
        absolute = urljoin(article_url, raw)
        path = urlparse(absolute).path.lower()
        if any(path.endswith(ext) for ext in allowed) and absolute not in found:
            found.append(absolute)

    for tag in soup.find_all(["img", "a"]):
        if tag.name == "img":
            for attr in ("src", "data-src", "data-lazy-src", "data-original"):
                add(tag.get(attr))
            srcset = tag.get("srcset") or tag.get("data-srcset")
            if srcset:
                for candidate in srcset.split(","):
                    add(candidate.strip().split(" ")[0])
        else:
            add(tag.get("href"))
    return found


def collect_source_urls(year: int, month: int, fetcher=fetch_html) -> dict[str, Any]:
    index_html = fetcher(INDEX_URL)
    article_url = find_month_article(index_html, year, month)
    article_html = fetcher(article_url)
    media_urls = discover_media_urls(article_html, article_url)
    if not media_urls:
        raise ValueError("対象月の記事ページから画像/PDFが見つかりません")
    # URL Context supports up to 20 URLs per request. Include the index and article
    # as context, then every schedule image/PDF discovered from that article.
    urls = [INDEX_URL, article_url, *media_urls]
    if len(urls) > 20:
        raise ValueError(f"対象URLが20件を超えています: {len(urls)}")
    return {
        "venue": VENUE,
        "year": year,
        "month": month,
        "index_url": INDEX_URL,
        "article_url": article_url,
        "media_urls": media_urls,
        "gemini_urls": urls,
    }


def validate_result(data: dict[str, Any], year: int, month: int) -> list[str]:
    errors: list[str] = []
    if data.get("venue") != VENUE:
        errors.append("venueが江戸川区球場ではありません")
    if data.get("year") != year or data.get("month") != month:
        errors.append("出力年月が対象年月と一致しません")
    seen: set[tuple[str, str, str | None, str | None]] = set()
    for i, event in enumerate(data.get("events", [])):
        try:
            d = date.fromisoformat(event.get("date", ""))
            if d.year != year or d.month != month:
                errors.append(f"events[{i}]の日付が対象月外です: {event.get('date')}")
        except (TypeError, ValueError):
            errors.append(f"events[{i}]の日付形式が不正です")
        weekday_map = {"月": 0, "火": 1, "水": 2, "木": 3, "金": 4, "土": 5, "日": 6}
        weekday = event.get("weekday")
        if weekday not in weekday_map or ("date" in event and isinstance(event.get("date"), str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", event["date"]) and date.fromisoformat(event["date"]).weekday() != weekday_map.get(weekday, -1)):
            errors.append(f"events[{i}]の曜日が日付と一致しません")
        title = normalize_text(event.get("title", ""))
        if not title:
            errors.append(f"events[{i}]のタイトルが空です")
        for key in ("start_time", "end_time"):
            value = event.get(key)
            if value is not None and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", str(value)):
                errors.append(f"events[{i}].{key}の時刻形式が不正です: {value}")
        start, end = event.get("start_time"), event.get("end_time")
        if start and end and end <= start:
            errors.append(f"events[{i}]の終了時刻が開始時刻以前です")
        if not isinstance(event.get("confidence"), (int, float)) or not 0 <= event["confidence"] <= 1:
            errors.append(f"events[{i}]のconfidenceが0〜1の範囲外です")
        elif event["confidence"] < 0.75:
            errors.append(f"events[{i}]のconfidenceが低いため人手確認が必要です")
        key = (event.get("date", ""), title, start, end)
        if key in seen:
            errors.append(f"重複イベント: {key}")
        seen.add(key)
    return errors


def call_gemini(source: dict[str, Any], api_key: str, model: str = DEFAULT_MODEL) -> dict[str, Any]:
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError("google-genaiが未インストールです。pip install -r requirements-gemini.txt") from exc

    client = genai.Client(api_key=api_key)
    prompt = PROMPT.format(year=source["year"], month=source["month"])
    prompt += "\n\n解析対象URL一覧:\n" + "\n".join(source["gemini_urls"])
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            tools=[types.Tool(url_context=types.UrlContext())],
            response_mime_type="application/json",
            response_schema=JSON_SCHEMA,
            temperature=0,
        ),
    )
    if not response.text:
        raise RuntimeError("GeminiからJSON応答がありません")
    data = json.loads(response.text)
    # Keep provenance outside the model-authored schema.
    data["source"] = {
        "index_url": source["index_url"],
        "article_url": source["article_url"],
        "media_urls": source["media_urls"],
        "model": model,
        "retrieved_at": datetime.now(TIMEZONE).isoformat(),
    }
    metadata = getattr(response.candidates[0], "url_context_metadata", None) if response.candidates else None
    if metadata:
        data["url_context_metadata"] = str(metadata)
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="江戸川区球場の月間大会予定表をGeminiでJSON化")
    parser.add_argument("--year", type=int, help="対象年（省略時は今月）")
    parser.add_argument("--month", type=int, help="対象月（省略時は今月）")
    parser.add_argument("--model", default=os.getenv("GEMINI_MODEL", DEFAULT_MODEL))
    parser.add_argument("--discover-only", action="store_true", help="URL一覧だけ取得し、Gemini APIを呼ばない")
    parser.add_argument("--output", default="edogawa_stadium_events.json")
    args = parser.parse_args()
    now = datetime.now(TIMEZONE)
    year, month = args.year or now.year, args.month or now.month
    if not 1 <= month <= 12:
        parser.error("--monthは1〜12で指定してください")

    source = collect_source_urls(year, month)
    if args.discover_only:
        result: dict[str, Any] = {"source_discovery": source}
    else:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEYが設定されていません")
        result = call_gemini(source, api_key, args.model)
        errors = validate_result(result, year, month)
        result["validation"] = {"ok": not errors, "errors": errors}
        if errors:
            print("検証エラーがあるため、正常データとして採用しないでください:", file=sys.stderr)
            for error in errors:
                print(f"- {error}", file=sys.stderr)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"出力: {args.output}")
    if not args.discover_only and result.get("validation", {}).get("errors"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
