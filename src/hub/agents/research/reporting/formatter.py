from ..state import Article, ResearchState


def format_report(state: ResearchState, articles: list[Article], mode: str) -> str:
    lines = [f"## Research: {state.query}",
             f"Laya mode: `{mode}` | Search rounds: {state.search_rounds} | Pages read: {state.fetched_count}",
             "", "### Top results"]
    if not articles:
        lines.append("Không thu thập được bài viết phù hợp.")
    for index, article in enumerate(articles[:10], 1):
        lines += [
            f"**{index}. {article.title}** — {article.final_score:.1f}/100",
            f"- Source: {article.source or 'unknown'}",
            f"- Published: {article.published_at or 'not available'}",
            f"- URL: {article.url}",
            f"- Cross-source matches: {len(set(article.corroborating_sources))}",
        ]
        excerpt = (article.content or article.snippet).replace("\n", " ").strip()[:450]
        if excerpt:
            lines.append(f"- Summary extract: {excerpt}")
        lines.append("")
    lines += ["### Research notes",
              "- Điểm xếp hạng là heuristic + Laya (nếu active), không phải thước đo độ viral.",
              "- Ngày đăng có thể thiếu nếu website không cung cấp metadata."]
    if state.warnings:
        lines += ["", "### Warnings"] + [f"- {warning}" for warning in state.warnings[:8]]
    if state.actions:
        lines += ["", "### Decision log"] + [f"- {action}" for action in state.actions[-8:]]
    return "\n".join(lines)
