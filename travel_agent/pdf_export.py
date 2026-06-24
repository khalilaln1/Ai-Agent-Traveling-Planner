"""Dependency-free PDF export for travel plans."""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any


def export_plan_pdf(plan: dict[str, Any], output_path: str | Path) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = _plan_lines(plan)
    _write_simple_pdf(lines, output)
    return output


def _plan_lines(plan: dict[str, Any]) -> list[str]:
    summary = plan.get("trip_summary", {})
    budget = plan.get("budget", {})
    lines = [
        "Travel Plan",
        "",
        f"Destination: {summary.get('destination', 'Unknown')}",
        f"Dates: {(summary.get('dates') or {}).get('start')} to {(summary.get('dates') or {}).get('end')}",
        f"Travelers: {summary.get('travelers')}",
        f"Pace: {summary.get('pace')}",
        "",
        "Budget",
        f"Estimated total: {budget.get('estimated_total')} {budget.get('currency')}",
        f"Target budget: {budget.get('target_budget')} {budget.get('currency')}",
        f"Status: {budget.get('budget_status')}",
        "",
        "AI Response",
    ]
    lines.extend((plan.get("ai_response") or "No AI response was generated.").splitlines())
    lines.extend([
        "",
        "Itinerary",
    ])

    for day in plan.get("itinerary", []):
        lines.append("")
        lines.append(f"Day {day.get('day')} - {day.get('date')} - {day.get('theme')}")
        for slot_name in ["morning", "afternoon", "evening"]:
            activities = day.get(slot_name, [])
            if not activities:
                continue
            lines.append(f"{slot_name.title()}:")
            for activity in activities:
                lines.append(
                    f"- {activity.get('name')} ({activity.get('area')}, {activity.get('duration_hours')}h)"
                )
        for note in day.get("notes", []):
            lines.append(f"Note: {note}")

    if plan.get("booking_checklist"):
        lines.extend(["", "Booking Checklist"])
        lines.extend(f"- {item}" for item in plan["booking_checklist"])

    if plan.get("warnings"):
        lines.extend(["", "Warnings"])
        lines.extend(f"- {item}" for item in plan["warnings"])

    return lines


def _write_simple_pdf(lines: list[str], output: Path) -> None:
    pages = _paginate(lines)
    objects: list[bytes] = []
    catalog_id = 1
    pages_id = 2
    font_id = 3
    next_id = 4
    page_ids = []

    for page_lines in pages:
        content_id = next_id
        page_id = next_id + 1
        next_id += 2
        page_ids.append(page_id)
        objects.append(_content_object(content_id, page_lines))
        objects.append(_page_object(page_id, pages_id, content_id, font_id))

    objects.insert(0, _font_object(font_id))
    objects.insert(0, _pages_object(pages_id, page_ids))
    objects.insert(0, _catalog_object(catalog_id, pages_id))

    offsets = []
    body = b"%PDF-1.4\n"
    for obj in objects:
        offsets.append(len(body))
        body += obj
    xref_offset = len(body)
    body += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii")
    for offset in offsets:
        body += f"{offset:010d} 00000 n \n".encode("ascii")
    body += (
        f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_id} 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    ).encode("ascii")
    output.write_bytes(body)


def _paginate(lines: list[str]) -> list[list[str]]:
    wrapped = []
    for line in lines:
        if not line:
            wrapped.append("")
            continue
        wrapped.extend(textwrap.wrap(line, width=88) or [""])
    return [wrapped[index : index + 44] for index in range(0, len(wrapped), 44)] or [[]]


def _catalog_object(object_id: int, pages_id: int) -> bytes:
    return f"{object_id} 0 obj\n<< /Type /Catalog /Pages {pages_id} 0 R >>\nendobj\n".encode("ascii")


def _pages_object(object_id: int, page_ids: list[int]) -> bytes:
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    return f"{object_id} 0 obj\n<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>\nendobj\n".encode("ascii")


def _font_object(object_id: int) -> bytes:
    return f"{object_id} 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n".encode("ascii")


def _page_object(object_id: int, pages_id: int, content_id: int, font_id: int) -> bytes:
    return (
        f"{object_id} 0 obj\n"
        f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 612 792] "
        f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>\n"
        "endobj\n"
    ).encode("ascii")


def _content_object(object_id: int, lines: list[str]) -> bytes:
    commands = ["BT", "/F1 10 Tf", "50 750 Td", "14 TL"]
    for line in lines:
        commands.append(f"({_escape_pdf_text(line)}) Tj")
        commands.append("T*")
    commands.append("ET")
    stream = "\n".join(commands).encode("ascii", errors="replace")
    return (
        f"{object_id} 0 obj\n<< /Length {len(stream)} >>\nstream\n".encode("ascii")
        + stream
        + b"\nendstream\nendobj\n"
    )


def _escape_pdf_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
