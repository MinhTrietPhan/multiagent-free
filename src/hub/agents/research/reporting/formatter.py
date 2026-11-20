"""Render the final research report.

The report is intentionally compact: a plan line, an evidence line, at most five
clean items with a 2–3 sentence summary, and a short line about what was
filtered out. It never cuts a sentence in half and stays under the character
budget used by the Telegram client.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from ..processors.synthesize import SummaryItem
from ..state import ResearchState

DEFAULT_REPORT_CHARS = 3500
_DROP_LABELS = {
    "duplicate": "trùng lặp",
    "clickbait": "giật tít",
    "off-topic": "ngoài chủ đề",
    "thin": "quá ngắn",
    "unknown-url": "URL lạ",
    "filtered": "đã lọc",
}
_LEVEL_LABELS = {
    "insufficient": "chưa đủ",
    "single_source": "chưa xác nhận chéo",
    "corroborated": "có xác nhận chéo",
}


def _report_chars() -> int:
    try:
        return max(800, min(6000, int(os.getenv("HUB_RESEARCH_REPORT_CHARS",
                                               str(DEFAULT_REPORT_CHARS)))))
    except ValueError:
        return DEFAULT_REPORT_CHARS


def _when(published_at: str | None) -> str:
    if not published_at:
        return "không rõ ngày"
    lowered = published_at.lower().strip()
    if lowered in {"yesterday", "hôm qua"}:
        return "hôm qua"
    try:
        value = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        hours = max(0.0, (datetime.now(timezone.utc) - value).total_seconds() / 3600)
        if hours < 1:
            return "vừa xong"
        if hours < 24:
            return f"{int(hours)} giờ trước"
        if hours < 24 * 7:
            return f"{int(hours // 24)} ngày trước"
        return value.strftime("%d/%m/%Y")
    except (ValueError, TypeError, OverflowError):
        return published_at[:40]


def _confidence(item: SummaryItem) -> str:
    return {"high": "độ tin cậy cao", "low": "độ tin cậy thấp"}.get(
        item.confidence, "độ tin cậy vừa")


def _drop_summary(dropped: list[dict]) -> str:
    counts: dict[str, int] = {}
    for entry in dropped:
        reason = str(entry.get("reason") or "filtered")
        label = _DROP_LABELS.get(reason, reason[:24])
        counts[label] = counts.get(label, 0) + 1
    if not counts:
        return ""
    return " · ".join(f"{count} {label}" for label, count in counts.items())


def format_report(state: ResearchState, items: list[SummaryItem],
                  dropped: list[dict] | None = None) -> str:
    budget = _report_chars()
    domains = {article.source for article in state.articles if article.source}
    plan_line = (f"Kế hoạch: {len(state.plan.queries) if state.plan else 0} truy vấn"
                 f" · đọc {state.fetched_count}/{state.max_pages} trang"
                 f" · {len(domains)} domain")
    if state.stop_reason:
        plan_line += f" · dừng: {state.stop_reason}"
    corroborated = sum(1 for item in items if item.independent >= 2)
    quality_line = (f"Độ tin cậy: {_LEVEL_LABELS.get(state.evidence_level, state.evidence_level)}"
                    f" — {corroborated}/{len(items)} tin có xác nhận chéo")

    lines = [f"## Research: {state.query}", plan_line, quality_line, ""]
    if not items:
        lines.append("Không thu thập được bài viết phù hợp. Thử câu hỏi cụ thể hơn "
                     "hoặc bật thêm nguồn tìm kiếm.")
    for index, item in enumerate(items, 1):
        block = [f"**{index}. {item.headline}** · {item.domain or 'nguồn không rõ'}"
                 f" · {_when(item.published_at)}"]
        if item.summary:
            block.append(item.summary)
        meta = [f"{item.independent} nguồn"]
        if item.sources:
            meta.append("xác nhận: " + ", ".join(item.sources[:4]))
        meta.append(_confidence(item))
        block.append(" · ".join(meta))
        block.append(item.url)
        block.append("")
        if len("\n".join(lines + block)) > budget - 200:
            remaining = len(items) - index + 1
            lines.append(f"… còn {remaining} tin chưa hiển thị để giữ báo cáo gọn.")
            break
        lines += block

    filtered = _drop_summary(dropped or [])
    if filtered:
        lines.append(f"Đã lọc: {filtered}")
    if state.warnings:
        lines.append("")
        lines.append("Ghi chú:")
        lines += [f"- {warning[:200]}" for warning in state.warnings[:5]]
    text = "\n".join(lines).strip()
    if len(text) > budget:
        text = text[:budget - 1].rstrip() + "…"
    return text
