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
import json
import re
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import pdfplumber
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "schedule.json"
BASE = "https://www.fgmeisei.ed.jp/"
UA = "MeiseiBusScheduleBot/1.0 (+non-commercial student project)"

# Manually transcribed from the official October 2026 calendar image and
# timetable. PDF text extraction cannot reliably associate these calendar
# cells with their date, so this reviewed month must never be replaced by the
# generic parser's result.
VERIFIED_CALENDAR_MODES = {
    "2026-10": {
        "A": [1, 2, 5, 6, 7, 8, 14, 15, 16, 18, 19, 20, 21, 22, 23, 26, 27, 28, 29, 30],
        "C": [3, 17, 24, 31],
        "D": [10],
        "no_service": [4, 11, 12, 25],
        "changed": {
            9: ["13:15", "17:10", "19:20"],
            13: ["17:10", "19:20"],
        },
        "notes": {18: "しらうめ祭のためA便運行"},
    }
}

def verified_calendar_overrides(year: int, month: int, source_url: str) -> dict | None:
    """Build exact overrides for a month manually checked against its image."""
    spec = VERIFIED_CALENDAR_MODES.get(f"{year:04d}-{month:02d}")
    if spec is None:
        return None
    result = {}
    for mode in ("A", "B", "C", "D"):
        for day in spec.get(mode, []):
            result[f"{year:04d}-{month:02d}-{day:02d}"] = {
                "status": "special", "mode": mode, "source_url": source_url,
            }
    for day in spec.get("no_service", []):
        result[f"{year:04d}-{month:02d}-{day:02d}"] = {
            "status": "no_service", "source_url": source_url,
        }
    for day, departures in spec.get("changed", {}).items():
        result[f"{year:04d}-{month:02d}-{day:02d}"] = {
            "status": "changed", "mode": "A", "source_url": source_url,
            "departures": departures,
            "note": f"変更後の明誠高校出発は{len(departures)}便です。",
        }
    for day, note in spec.get("notes", {}).items():
        result[f"{year:04d}-{month:02d}-{day:02d}"]["note"] = note
    return result

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
        if not found["hainan"] and ("御前崎" in label or "榛南" in label):
            found["hainan"] = href

    # Fallback: article layout generally lists monthly Hainan then Yaizu PDFs
    if not found["hainan"]:
        pdfs = [urljoin(topic_url, a.get("href")) for a in soup.select("a[href]") if ".pdf" in (a.get("href") or "").lower()]
        monthly = [u for u in pdfs if "時刻表" not in u]
        if monthly:
            found["hainan"] = monthly[0]
    return found

def pdf_text(url: str) -> str:
    content = get(url).content
    out = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            text = page.extract_text(x_tolerance=2, y_tolerance=3) or ""
            out.append(text)
    return "\n".join(out)

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

def expand_date_expr(expr: str, year: int, default_month: int) -> list[str]:
    """Extract month/day tokens. Handles '9/21～9/23' by expanding same-month ranges."""
    expr = expr.replace("／", "/").replace("～", "~").replace("〜", "~")
    # Remove weekday parentheses to simplify.
    expr = re.sub(r"[（(][月火水木金土日][）)]", "", expr)

    range_m = re.search(r"(?:(\d{1,2})/)?(\d{1,2})\s*~\s*(?:(\d{1,2})/)?(\d{1,2})", expr)
    results = []
    if range_m:
        m1 = int(range_m.group(1) or default_month)
        d1 = int(range_m.group(2))
        m2 = int(range_m.group(3) or m1)
        d2 = int(range_m.group(4))
        if m1 == m2 and d2 >= d1 and d2 - d1 <= 14:
            for d in range(d1, d2 + 1):
                results.append(f"{year:04d}-{m1:02d}-{d:02d}")

    for m, d in re.findall(r"(?:(\d{1,2})/)?(\d{1,2})", expr):
        mo = int(m or default_month)
        day = int(d)
        if 1 <= mo <= 12 and 1 <= day <= 31:
            key = f"{year:04d}-{mo:02d}-{day:02d}"
            if key not in results:
                results.append(key)
    return results

def nearby_date_block(lines: list[str], i: int, year: int, month: int) -> list[str]:
    # Search a few lines backwards for the "☆ date ... の運行について" heading.
    for j in range(i, max(-1, i-5), -1):
        if "運行について" in lines[j] or lines[j].lstrip().startswith("☆"):
            dates = expand_date_expr(lines[j], year, month)
            if dates:
                return dates
    return []

def parse_overrides(text: str, year: int, month: int, source_url: str) -> dict:
    text = unicodedata.normalize("NFKC", text)
    # Match only explicit date headings. A general number scan also picks up
    # school-year numbers, table cells, and unrelated months in the PDF.
    heading = re.compile(
        r"(?:[☆※]\s*)?(?:R\s*\d+\s*年\s*)?(?:(?P<month>\d{1,2})\s*/\s*(?P<day>\d{1,2})|(?P<day_only>\d{1,2})(?=\s*[（(]))"
        r"(?P<tail>[^\n]*?の運行について)",
        re.IGNORECASE,
    )
    matches = list(heading.finditer(text))
    overrides: dict[str, dict] = {}
    for index, match in enumerate(matches):
        heading_text = match.group(0)
        event_month = int(match.group("month") or month)
        days = [int(match.group("day") or match.group("day_only"))]
        for continuation in re.finditer(r"[・、,~〜～-]\s*(?:(\d{1,2})\s*/\s*)?(\d{1,2})", match.group("tail")):
            continued_month = int(continuation.group(1) or event_month)
            if continued_month == event_month:
                days.append(int(continuation.group(2)))

        # The PDF's first page places 10/9 and the Saturday notes side by side.
        # Read the changed-table marker from that shared section, then use the
        # school row below to collect only actual departures.
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        if index + 1 < len(matches):
            next_match = matches[index + 1]
            same_line = text.count("\n", 0, match.start()) == text.count("\n", 0, next_match.start())
            if same_line:
                end = matches[index + 2].start() if index + 2 < len(matches) else len(text)
        context = text[match.end():min(end, match.end() + 2500)]
        status = None
        mode = None
        # A calendar month PDF can place a day's date and its A/B/C/D marker
        # beside a different date's timetable-change note (for example,
        # Friday 10/9 next to Saturday 10/10). Only attach the change label
        # when the date's own compact block explicitly carries the marker.
        own_day_context = context[:260]
        # Do not infer a change solely from nearby PDF text. The calendar
        # parser can flatten adjacent date cells into one text run. A changed
        # day is recognized automatically only when its own compact section
        # contains the note and the school's actual departure row.
        has_changed_timetable = (
            "下校便" in own_day_context
            and "変更" in own_day_context
            and bool(re.search(r"明誠高校.{0,80}(?:[01]?\d|2[0-3]):[0-5]\d", own_day_context))
        )
        if has_changed_timetable:
            status = "changed"
        elif "運行はありません" in context or "運行なし" in context:
            status = "no_service"
        else:
            mode_match = re.search(r"([BCD])\s*便", context[:260])
            if mode_match:
                mode = mode_match.group(1)
                status = "special"
            elif re.search(r"A\s*便.{0,30}(?:通常|です)", context[:260]):
                status, mode = "special", "A"

        table_context = context

        # Keep only dates in the announcement month and real calendar dates.
        for day in days:
            try:
                from datetime import date
                date(year, event_month, day)
            except ValueError:
                continue
            if event_month != month or status is None:
                continue
            key = f"{year:04d}-{event_month:02d}-{day:02d}"
            payload = {"status": status, "source_url": source_url}
            if mode:
                payload["mode"] = mode
            if status == "changed":
                payload["note"] = "下校時刻変更あり。時刻は公式PDFを確認してください。"
                departure_block = re.search(r"下校便[^\n]{0,30}変更", table_context)
                if departure_block:
                    rows = table_context[departure_block.end():].splitlines()
                    for row in rows[:14]:
                        if "明誠高校" not in row:
                            continue
                        # The first Meisei row is the school's departure;
                        # later rows are intermediate stops on the route.
                        school_row = row[row.index("明誠高校") + len("明誠高校"):]
                        departures = re.findall(r"(?<!\d)(?:[01]?\d|2[0-3]):[0-5]\d(?!\d)", school_row)
                        if departures:
                            payload["departures"] = departures
                        break
            overrides[key] = payload
    return overrides

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
        links = extract_pdf_links(topic)
        for route_key in ("hainan",):
            url = links.get(route_key)
            if not url:
                errors.append(f"{topic} {route_key}: PDF not found")
                continue
            try:
                text = pdf_text(url)
                year, month = infer_year_month(text, title)
                month_id = f"{year:04d}-{month:02d}"
                now = datetime.now()
                current_id = f"{now.year:04d}-{now.month:02d}"
                next_date = date(now.year + (now.month == 12), now.month % 12 + 1, 1)
                next_id = f"{next_date.year:04d}-{next_date.month:02d}"
                # Keep only this month and the next month. Older announcements
                # are deliberately ignored, even if they remain on the school site.
                if not (current_id <= month_id <= next_id):
                    continue
                overrides = parse_overrides(text, year, month, url)
                route = new["routes"][route_key]
                # Preserve verified departures when a PDF extraction only
                # identifies the changed date but cannot read its table row.
                previous_month = route.get("months", {}).get(month_id, {})
                previous_overrides = previous_month.get("overrides", route.get("overrides", {}))
                exact_calendar = verified_calendar_overrides(year, month, url)
                # A human-reviewed calendar image is authoritative for its
                # whole month. PDF text extraction flattens adjacent cells,
                # so even a full-looking parse can incorrectly mark every
                # Saturday as changed. Keep the reviewed month intact.
                reviewed_calendar = (exact_calendar is not None
                                     or previous_month.get("parser_status") == "verified_calendar_image")
                if exact_calendar is not None:
                    overrides = exact_calendar
                elif reviewed_calendar:
                    overrides = dict(previous_overrides)
                for day, item in overrides.items():
                    old_item = previous_overrides.get(day, {})
                    # A timetable note can be picked up from a neighboring
                    # date in the PDF text layer. If a reviewed calendar says
                    # this date is a specific A/B/C/D service and the parser
                    # found no replacement departure times, retain that
                    # reviewed mode rather than publishing a false change.
                    if (item.get("status") == "changed"
                            and not item.get("departures")
                            and old_item.get("status") == "special"
                            and old_item.get("mode") in {"A", "B", "C", "D"}):
                        overrides[day] = old_item
                        continue
                    if item.get("status") == "changed" and not item.get("departures") and old_item.get("departures"):
                        item["departures"] = old_item["departures"]
                        item["note"] = old_item.get("note", item.get("note", ""))
                monthly = {
                    "coverage_month": month_id,
                    "source_url": url,
                    "topic_url": topic,
                    "overrides": overrides,
                    "parser_status": "verified_calendar_image" if reviewed_calendar else "ok",
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
        now = datetime.now()
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
        DATA.write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"updated current and next month through {latest_title}")
        if errors:
            print("warnings:", "; ".join(errors))
    else:
        # Important: do not destroy last known-good data.
        raise RuntimeError("No route was updated: " + "; ".join(errors))

if __name__ == "__main__":
    main()
