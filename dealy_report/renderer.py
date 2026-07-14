from __future__ import annotations

from typing import Any

from .report import Report, Source


def _links(sources: tuple[Source, ...]) -> str:
    return " · ".join(f"[{source.label}]({source.url})" for source in sources)


def _markdown_div(content: str) -> dict[str, Any]:
    return {"tag": "div", "text": {"tag": "lark_md", "content": content}}


def _image_element(report: Report, image_key: str) -> list[dict[str, Any]]:
    image = next(item for item in report.images if item.key == image_key)
    return [
        {"tag": "img", "img_key": f"{{{{image:{image.key}}}}}", "alt": {"tag": "plain_text", "content": image.alt}},
        _markdown_div(f"{image.caption} · [查看图源]({image.source_url})"),
    ]


def _card(title: str, elements: list[dict[str, Any]], template: str = "blue") -> dict[str, Any]:
    return {
        "config": {"wide_screen_mode": True, "enable_forward": True},
        "header": {"template": template, "title": {"tag": "plain_text", "content": title}},
        "elements": elements,
    }


def render_feishu_manifest(report: Report, max_cards: int = 3) -> dict[str, Any]:
    if max_cards < 3:
        raise ValueError("article layout requires three cards")

    first: list[dict[str, Any]] = [_markdown_div(report.lead)]
    first.extend(_image_element(report, "hero"))
    first.append({"tag": "hr"})
    first.append(_markdown_div(f"**{report.main_story.title}**\n\n" + "\n\n".join(report.main_story.paragraphs)))
    first.append(_markdown_div(f"**来源：** {_links(report.main_story.sources)}"))

    second: list[dict[str, Any]] = []
    for index, case in enumerate(report.agent_cases):
        if index:
            second.append({"tag": "hr"})
        second.append(_markdown_div(f"**{case.title}｜{case.project}**\n\n{case.scenario}"))
        second.extend(_image_element(report, case.image_key))
        second.append(_markdown_div("\n\n".join(case.paragraphs)))
        second.append(_markdown_div(f"**效果证据：** {case.evidence}\n\n**可复用启发：** {case.reusable_insight}"))
        second.append(_markdown_div(f"**来源：** {_links(case.sources)}"))

    radar_lines = [
        f"- **{item.category}｜{item.title}**：{item.summary} {item.impact} [来源]({item.source.url})"
        for item in report.radar
    ]
    action_lines = [f"- **{item.type}｜{item.title}**：{item.detail}" for item in report.actions]
    third = [
        _markdown_div("**技术雷达**\n\n" + "\n".join(radar_lines)),
        {"tag": "hr"},
        _markdown_div("**今日行动**\n\n" + "\n".join(action_lines)),
        {"tag": "note", "elements": [{"tag": "plain_text", "content": f"每日 AI 图文情报 · {report.date}"}]},
    ]

    return {
        "images": {image.key: image.url for image in report.images},
        "cards": [
            _card(report.title, first, "blue"),
            _card("Agent 真实项目应用", second, "green"),
            _card("技术雷达与今日行动", third, "turquoise"),
        ],
    }


def render_markdown(report: Report) -> str:
    lines = [f"# {report.title}", "", report.lead, "", f"## {report.main_story.title}", ""]
    for paragraph in report.main_story.paragraphs:
        lines.extend([paragraph, ""])
    lines.extend([f"来源：{_links(report.main_story.sources)}", "", "## Agent 真实项目应用", ""])
    for case in report.agent_cases:
        lines.extend([f"### {case.title}｜{case.project}", "", case.scenario, ""])
        for paragraph in case.paragraphs:
            lines.extend([paragraph, ""])
        lines.extend(
            [
                f"效果证据：{case.evidence}",
                "",
                f"可复用启发：{case.reusable_insight}",
                "",
                f"来源：{_links(case.sources)}",
                "",
            ]
        )
    lines.extend(["## 技术雷达", ""])
    for item in report.radar:
        lines.append(f"- **{item.category}｜{item.title}**：{item.summary} {item.impact} [{item.source.label}]({item.source.url})")
    lines.extend(["", "## 今日行动", ""])
    for item in report.actions:
        lines.append(f"- **{item.type}｜{item.title}**：{item.detail}")
    return "\n".join(lines).rstrip() + "\n"

