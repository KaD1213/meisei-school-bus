#!/usr/bin/env python3
"""
藤枝明誠公式サイトの最新スクールバス変更案内を確認し、
data/schedule.json の月別変更情報を更新する。

設計:
- 通常ダイヤ(base)は既存JSONを保持する。
- 最新の「スクールバス ○月変更案内」記事からPDFを発見する。
- PDF本文から確実に読める「運休」「A/B/C/D便」「変更あり」を抽出する。
- 変更時刻を確実に対応付けできない場合は勝手に補完せず、
  `status=changed` と公式PDF URLだけ保存する。
"""
from __future__ import annotations

import io
import calendar
import hashlib
import json
import re
import unicodedata
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urljoin, unquote

import pdfplumber
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "schedule.json"
BASE = "https://www.fgmeisei.ed.jp/"
UA = "MeiseiBusScheduleBot/2.0"

session = requests.Session()
session.headers.update({"User-Agent": UA})

def get(url: str, timeout=30) -> requests.Response:
    r = session.get(url, timeout=timeout)
    r.raise_for_status()
    return r

def discover_monthly_topics() -> list[tuple[str, str]]:
    """Return every monthly bus notice visible on the official home page."""
    soup = BeautifulSoup(get(BASE).text, "html.parser")
    candidates: dict[str, str] = {}
    for a in soup.select("a[href]"):
        href = urljoin(BASE, a.get("href"))
        if "/topics/" not in href or href in candidates:
            continue
        card = a.find_parent(class_="o-topics-list-item") or a.parent
        card_text = unicodedata.normalize("NFKC", " ".join(card.stripped_strings))
        title_match = re.search(r"(?:[【〖]\s*)?スクールバス\s*(?:[】〗]\s*)?(\d{1,2})\s*月\s*変更案内", card_text)
        if not title_match:
            continue
        candidates[href] = f"{datetime.now().year}年{title_match.group(1)}月変更案内"

    if not candidates:
        raise RuntimeError("月別スクールバス変更案内を発見できませんでした")

    def month_order(item):
        title = unicodedata.normalize("NFKC", item[1])
        year = re.search(r"(20\d{2})\s*年", title)
        month = re.search(r"(\d{1,2})\s*月\s*変更案内", title)
        return (int(year.group(1)) if year else datetime.now().year,
                int(month.group(1)) if month else 0)

    return sorted(((title, href) for href, title in candidates.items()), key=month_order, reverse=True)

def extract_pdf_links(topic_url: str) -> dict[str, str]:
    soup = BeautifulSoup(get(topic_url).text, "html.parser")
    found = {"hainan": ""}
    for a in soup.select("a[href$='.pdf'], a[href*='.pdf?']"):
        label = " ".join(a.stripped_strings)
        href = urljoin(topic_url, a.get("href"))
        hay = (label + " " + href).lower()
        if not found["hainan"] and ("御前崎" in unquote(hay) or "榛南" in unquote(hay)):
            found["hainan"] = href

    return found

def infer_year_month(text: str, topic_title: str) -> tuple[int, int]:
    title = unicodedata.normalize("NFKC", topic_title)
    # The title can begin with the article's publication date (e.g. 9月30日)
    # before the actual schedule month. Prefer the explicit announcement month.
    m = re.search(r"(\d{1,2})\s*月\s*変更案内", title)
    year_match = re.search(r"(20\d{2})\s*年", title)
    normalized_text = unicodedata.normalize("NFKC", text)
    text_year = re.search(r"(20\d{2})\s*年\s*\d{1,2}\s*月", normalized_text)
    year = int(text_year.group(1)) if text_year else int(year_match.group(1)) if year_match else datetime.now().year
    if m:
        return year, int(m.group(1))
    m = re.search(r"(20\d{2})\s*年\s*(\d{1,2})\s*月", normalized_text)
    if m:
        return int(m.group(1)), int(m.group(2))
    return year, datetime.now().month

def normalize(text):
    return unicodedata.normalize('NFKC', text or '').replace(' ', '')


def parse_calendar_pdf(content, year, month, source_url):
    result = {}
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            # Excel-exported PDFs can contain a ~1e-7 skew. pdfminer marks
            # these horizontal characters as vertical and hides the calendar.
            for char in page.chars:
                a, b, c, d, _, _ = char['matrix']
                if a > 0 and d > 0 and abs(b) < 1e-4 and abs(c) < 1e-4:
                    char['upright'] = True
            words = page.extract_words()
            headers = [w for w in words if normalize(w['text']) in
                       ['月曜日', '火曜日', '水曜日', '木曜日', '金曜日', '土曜日', '日曜日']]
            if len(headers) != 7:
                continue
            headers.sort(key=lambda w: w['x0'])
            if [normalize(w['text']) for w in headers] != ['月曜日', '火曜日', '水曜日', '木曜日', '金曜日', '土曜日', '日曜日']:
                raise ValueError('カレンダーの曜日列を確認できません')
            left = headers[0]['x0'] - 10
            spacing = (headers[-1]['x0'] - headers[0]['x0']) / 6
            edges = [left] + [(headers[i-1]['x0'] + headers[i]['x0']) / 2 + spacing / 2 - 10 for i in range(1, 7)] + [headers[-1]['x0'] + spacing - 10]
            top = max(w['bottom'] for w in headers)
            # Stop before the separate explanatory tables below the calendar.
            note_top = min((w['top'] for w in words if '運行について' in normalize(w['text']) and w['top'] > top), default=page.height)
            numbers = [w for w in words if top <= w['top'] < note_top and left <= w['x0'] < edges[-1]
                       and re.fullmatch(r'\d{1,2}', normalize(w['text']))]
            rows = []
            for w in sorted(numbers, key=lambda w: w['top']):
                if not rows or abs(rows[-1][0]['top'] - w['top']) > 3:
                    rows.append([w])
                else:
                    rows[-1].append(w)
            expected = calendar.Calendar(firstweekday=0).monthdayscalendar(year, month)
            if len(rows) < len(expected):
                raise ValueError('カレンダーの日付行が不足しています')
            gaps = [rows[i+1][0]['top'] - rows[i][0]['top'] for i in range(len(expected)-1)]
            gap = sorted(gaps)[len(gaps)//2]
            for row_index, week in enumerate(expected):
                row = rows[row_index]
                y0 = min(w['top'] for w in row) - 1
                y1 = rows[row_index+1][0]['top'] - 1 if row_index+1 < len(rows) else y0 + gap
                for col, day in enumerate(week):
                    if not day:
                        continue
                    dates = [w for w in row if edges[col] <= w['x0'] < edges[col+1]]
                    if len(dates) != 1 or int(normalize(dates[0]['text'])) != day:
                        raise ValueError(f'{month}/{day}: 日付の位置が一致しません')
                    text = normalize(page.crop((edges[col], y0, edges[col+1], y1)).extract_text())
                    key = f'{year:04d}-{month:02d}-{day:02d}'
                    if '運行なし' in text:
                        item = {'status': 'no_service', 'source_url': source_url}
                    else:
                        modes = re.findall(r'([ABCD])便', text)
                        if len(modes) != 1:
                            raise ValueError(f'{key}: 便を確実に判定できません')
                        changed = '下校' in text and '変更' in text
                        item = {'status': 'changed' if changed else 'special', 'mode': modes[0], 'source_url': source_url}
                    result[key] = item

        if len(result) != calendar.monthrange(year, month)[1]:
            raise ValueError('月間カレンダーを確実に取得できません')

        # Each table is isolated geometrically, so neighboring Saturday notes
        # cannot contaminate a weekday's changed departure times.
        for page in pdf.pages:
            for table in page.find_tables():
                rows = table.extract()
                heading = normalize(''.join(c or '' for c in rows[0]))
                if '運行について' not in heading or '下校' not in heading or '変更' not in heading:
                    continue
                dates = re.findall(r'(\d{1,2})/(\d{1,2})', heading)
                if not dates:
                    continue
                school = next((r for r in rows[1:] if normalize(r[0]) == '明誠高校'), None)
                if school is None:
                    continue
                departures, labels = [], []
                for index, value in enumerate(school[1:], start=1):
                    value = normalize(value)
                    if re.fullmatch(r'(?:[01]?\d|2[0-3]):[0-5]\d', value):
                        hour, minute = value.split(':')
                        departures.append(f'{int(hour):02d}:{minute}')
                        labels.append(f'{index}便')
                    elif value != '通過':
                        raise ValueError('変更便の時刻セルを判定できません')
                for mo, day in dates:
                    if int(mo) != month:
                        continue
                    key = f'{year:04d}-{month:02d}-{int(day):02d}'
                    if key not in result or result[key]['status'] != 'changed':
                        raise ValueError(f'{key}: カレンダーと変更表が一致しません')
                    if 'departures' in result[key] and result[key]['departures'] != departures:
                        raise ValueError(f'{key}: 変更表が重複しています')
                    result[key].update(departures=departures, departure_labels=labels)
        for key, item in result.items():
            if item['status'] == 'changed' and 'departures' not in item:
                item['note'] = '変更後の時刻は公式PDFを確認してください。'
    return result


def main():
    old = json.loads(DATA.read_text(encoding="utf-8"))
    topics = discover_monthly_topics()
    new = old
    any_success = False
    errors = []
    latest_success = (-1, -1)
    latest_title = ""
    latest_topic = ""

    for title, topic in topics:
        try:
            links = extract_pdf_links(topic)
        except Exception as error:
            errors.append(f"{topic}: {error}")
            continue
        for route_key in ("hainan",):
            url = links.get(route_key)
            if not url:
                errors.append(f"{topic} {route_key}: PDF not found")
                continue
            try:
                content = get(url).content
                with pdfplumber.open(io.BytesIO(content)) as pdf:
                    text = "\n".join(page.extract_text() or "" for page in pdf.pages)
                year, month = infer_year_month(text, title)
                month_id = f"{year:04d}-{month:02d}"
                now = datetime.now(timezone(timedelta(hours=9)))
                current_id = f"{now.year:04d}-{now.month:02d}"
                next_date = date(now.year + (now.month == 12), now.month % 12 + 1, 1)
                next_id = f"{next_date.year:04d}-{next_date.month:02d}"
                # Keep only this month and the next month. Older announcements
                # are deliberately ignored, even if they remain on the school site.
                if not (current_id <= month_id <= next_id):
                    continue
                route = new["routes"][route_key]
                digest = hashlib.sha256(content).hexdigest()
                overrides = parse_calendar_pdf(content, year, month, url)
                parser_status = "coordinate_calendar"
                monthly = {
                    "coverage_month": month_id,
                    "source_url": url,
                    "topic_url": topic,
                    "overrides": overrides,
                    "parser_status": parser_status,
                    "source_sha256": digest,
                }
                route.setdefault("months", {})[month_id] = monthly

                if (year, month) >= latest_success:
                    route["coverage_month"] = month_id
                    route["source_url"] = url
                    route["topic_url"] = topic
                    route["overrides"] = overrides
                    route["parser_status"] = monthly["parser_status"]
                    latest_success = (year, month)
                    latest_title, latest_topic = title, topic
                any_success = True
            except Exception as e:
                errors.append(f"{topic} {route_key}: {e}")

    if any_success:
        now = datetime.now(timezone(timedelta(hours=9)))
        current_id = f"{now.year:04d}-{now.month:02d}"
        next_date = date(now.year + (now.month == 12), now.month % 12 + 1, 1)
        next_id = f"{next_date.year:04d}-{next_date.month:02d}"
        for route in new.get("routes", {}).values():
            months = route.get("months", {})
            route["months"] = {
                month_id: monthly for month_id, monthly in months.items()
                if current_id <= month_id <= next_id
            }
        new["generated_at"] = datetime.now(timezone.utc).isoformat()
        new["last_topic_title"] = latest_title
        new["last_topic_url"] = latest_topic
        new["update_errors"] = errors
        temporary = DATA.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(DATA)
        print(f"updated current and next month through {latest_title}")
        if errors:
            print("warnings:", "; ".join(errors))
    else:
        # Important: do not destroy last known-good data.
        raise RuntimeError("No route was updated: " + "; ".join(errors))

if __name__ == "__main__":
    main()