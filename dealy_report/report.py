from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import urlsplit


IMAGE_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
ACTION_TYPES = {"立即试用", "深入阅读", "继续观察"}


class ReportValidationError(ValueError):
    pass


@dataclass(frozen=True)
class Source:
    label: str
    url: str


@dataclass(frozen=True)
class ReportImage:
    key: str
    url: str
    source_url: str
    caption: str
    alt: str


@dataclass(frozen=True)
class MainStory:
    title: str
    paragraphs: tuple[str, ...]
    sources: tuple[Source, ...]


@dataclass(frozen=True)
class AgentCase:
    title: str
    project: str
    scenario: str
    paragraphs: tuple[str, ...]
    evidence: str
    reusable_insight: str
    image_key: str
    sources: tuple[Source, ...]


@dataclass(frozen=True)
class RadarItem:
    category: str
    title: str
    summary: str
    impact: str
    source: Source


@dataclass(frozen=True)
class ActionItem:
    type: str
    title: str
    detail: str


@dataclass(frozen=True)
class Report:
    date: str
    title: str
    lead: str
    main_story: MainStory
    images: tuple[ReportImage, ...]
    agent_cases: tuple[AgentCase, ...]
    radar: tuple[RadarItem, ...]
    actions: tuple[ActionItem, ...]


def _object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReportValidationError(f"{field} must be an object")
    return value


def _exact_keys(value: dict[str, Any], expected: set[str], field: str) -> None:
    unexpected = set(value) - expected
    if unexpected:
        names = ", ".join(sorted(unexpected))
        raise ReportValidationError(f"{field} contains unexpected fields: {names}")


def _list(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise ReportValidationError(f"{field} must be an array")
    return value


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReportValidationError(f"{field} must be a non-empty string")
    return value.strip()


def _https(value: Any, field: str) -> str:
    url = _text(value, field)
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ReportValidationError(f"{field} must be an HTTPS URL")
    return url


def _source(value: Any, field: str) -> Source:
    item = _object(value, field)
    _exact_keys(item, {"label", "url"}, field)
    return Source(label=_text(item.get("label"), f"{field}.label"), url=_https(item.get("url"), f"{field}.url"))


def _sources(value: Any, field: str) -> tuple[Source, ...]:
    items = _list(value, field)
    if not items:
        raise ReportValidationError(f"{field} must contain at least one source")
    return tuple(_source(item, f"{field}[{index}]") for index, item in enumerate(items))


def _paragraphs(value: Any, field: str, minimum: int, maximum: int) -> tuple[str, ...]:
    items = _list(value, field)
    if not minimum <= len(items) <= maximum:
        raise ReportValidationError(f"{field} must contain {minimum} to {maximum} paragraphs")
    return tuple(_text(item, f"{field}[{index}]") for index, item in enumerate(items))


def validate_report(payload: Any) -> Report:
    root = _object(payload, "report")
    _exact_keys(root, {"date", "title", "lead", "main_story", "images", "agent_cases", "radar", "actions"}, "report")
    report_date = _text(root.get("date"), "date")
    try:
        date.fromisoformat(report_date)
    except ValueError as error:
        raise ReportValidationError("date must use YYYY-MM-DD") from error

    story_data = _object(root.get("main_story"), "main_story")
    _exact_keys(story_data, {"title", "paragraphs", "sources"}, "main_story")
    main_story = MainStory(
        title=_text(story_data.get("title"), "main_story.title"),
        paragraphs=_paragraphs(story_data.get("paragraphs"), "main_story.paragraphs", 3, 5),
        sources=_sources(story_data.get("sources"), "main_story.sources"),
    )

    image_data = _list(root.get("images"), "images")
    if not 3 <= len(image_data) <= 4:
        raise ReportValidationError("report must contain 3 to 4 images")
    images: list[ReportImage] = []
    image_keys: set[str] = set()
    for index, raw_image in enumerate(image_data):
        item = _object(raw_image, f"images[{index}]")
        _exact_keys(item, {"key", "url", "source_url", "caption", "alt"}, f"images[{index}]")
        key = _text(item.get("key"), f"images[{index}].key")
        if not IMAGE_KEY_PATTERN.fullmatch(key):
            raise ReportValidationError(f"images[{index}].key is invalid")
        if key in image_keys:
            raise ReportValidationError(f"duplicate image key: {key}")
        image_keys.add(key)
        images.append(
            ReportImage(
                key=key,
                url=_https(item.get("url"), f"images[{index}].url"),
                source_url=_https(item.get("source_url"), f"images[{index}].source_url"),
                caption=_text(item.get("caption"), f"images[{index}].caption"),
                alt=_text(item.get("alt"), f"images[{index}].alt"),
            )
        )
    if "hero" not in image_keys:
        raise ReportValidationError("images must include the hero image")

    case_data = _list(root.get("agent_cases"), "agent_cases")
    if not 2 <= len(case_data) <= 3:
        raise ReportValidationError("report must contain 2 to 3 agent cases")
    cases: list[AgentCase] = []
    for index, raw_case in enumerate(case_data):
        item = _object(raw_case, f"agent_cases[{index}]")
        _exact_keys(
            item,
            {"title", "project", "scenario", "paragraphs", "evidence", "reusable_insight", "image_key", "sources"},
            f"agent_cases[{index}]",
        )
        image_key = _text(item.get("image_key"), f"agent_cases[{index}].image_key")
        if image_key not in image_keys:
            raise ReportValidationError(f"agent_cases[{index}] references an unknown image")
        cases.append(
            AgentCase(
                title=_text(item.get("title"), f"agent_cases[{index}].title"),
                project=_text(item.get("project"), f"agent_cases[{index}].project"),
                scenario=_text(item.get("scenario"), f"agent_cases[{index}].scenario"),
                paragraphs=_paragraphs(item.get("paragraphs"), f"agent_cases[{index}].paragraphs", 2, 2),
                evidence=_text(item.get("evidence"), f"agent_cases[{index}].evidence"),
                reusable_insight=_text(item.get("reusable_insight"), f"agent_cases[{index}].reusable_insight"),
                image_key=image_key,
                sources=_sources(item.get("sources"), f"agent_cases[{index}].sources"),
            )
        )

    radar_data = _list(root.get("radar"), "radar")
    if not radar_data:
        raise ReportValidationError("radar must contain at least one item")
    radar: list[RadarItem] = []
    for index, raw_radar in enumerate(radar_data):
        item = _object(raw_radar, f"radar[{index}]")
        _exact_keys(item, {"category", "title", "summary", "impact", "source"}, f"radar[{index}]")
        radar.append(
            RadarItem(
                category=_text(item.get("category"), f"radar[{index}].category"),
                title=_text(item.get("title"), f"radar[{index}].title"),
                summary=_text(item.get("summary"), f"radar[{index}].summary"),
                impact=_text(item.get("impact"), f"radar[{index}].impact"),
                source=_source(item.get("source"), f"radar[{index}].source"),
            )
        )

    action_data = _list(root.get("actions"), "actions")
    if len(action_data) != 3:
        raise ReportValidationError("actions must contain exactly three items")
    actions: list[ActionItem] = []
    for index, raw_action in enumerate(action_data):
        item = _object(raw_action, f"actions[{index}]")
        _exact_keys(item, {"type", "title", "detail"}, f"actions[{index}]")
        action_type = _text(item.get("type"), f"actions[{index}].type")
        if action_type not in ACTION_TYPES:
            raise ReportValidationError(f"actions[{index}].type is invalid")
        actions.append(
            ActionItem(
                type=action_type,
                title=_text(item.get("title"), f"actions[{index}].title"),
                detail=_text(item.get("detail"), f"actions[{index}].detail"),
            )
        )
    if {item.type for item in actions} != ACTION_TYPES:
        raise ReportValidationError("actions must include immediate, reading, and watch items")

    return Report(
        date=report_date,
        title=_text(root.get("title"), "title"),
        lead=_text(root.get("lead"), "lead"),
        main_story=main_story,
        images=tuple(images),
        agent_cases=tuple(cases),
        radar=tuple(radar),
        actions=tuple(actions),
    )


def report_json_schema() -> dict[str, Any]:
    source = {
        "type": "object",
        "properties": {"label": {"type": "string"}, "url": {"type": "string"}},
        "required": ["label", "url"],
        "additionalProperties": False,
    }
    image = {
        "type": "object",
        "properties": {
            "key": {"type": "string"},
            "url": {"type": "string"},
            "source_url": {"type": "string"},
            "caption": {"type": "string"},
            "alt": {"type": "string"},
        },
        "required": ["key", "url", "source_url", "caption", "alt"],
        "additionalProperties": False,
    }
    main_story = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "paragraphs": {"type": "array", "minItems": 3, "maxItems": 5, "items": {"type": "string"}},
            "sources": {"type": "array", "minItems": 1, "items": source},
        },
        "required": ["title", "paragraphs", "sources"],
        "additionalProperties": False,
    }
    agent_case = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "project": {"type": "string"},
            "scenario": {"type": "string"},
            "paragraphs": {"type": "array", "minItems": 2, "maxItems": 2, "items": {"type": "string"}},
            "evidence": {"type": "string"},
            "reusable_insight": {"type": "string"},
            "image_key": {"type": "string"},
            "sources": {"type": "array", "minItems": 1, "items": source},
        },
        "required": ["title", "project", "scenario", "paragraphs", "evidence", "reusable_insight", "image_key", "sources"],
        "additionalProperties": False,
    }
    radar = {
        "type": "object",
        "properties": {
            "category": {"type": "string"},
            "title": {"type": "string"},
            "summary": {"type": "string"},
            "impact": {"type": "string"},
            "source": source,
        },
        "required": ["category", "title", "summary", "impact", "source"],
        "additionalProperties": False,
    }
    action = {
        "type": "object",
        "properties": {"type": {"type": "string"}, "title": {"type": "string"}, "detail": {"type": "string"}},
        "required": ["type", "title", "detail"],
        "additionalProperties": False,
    }
    properties = {
        "date": {"type": "string"},
        "title": {"type": "string"},
        "lead": {"type": "string"},
        "main_story": main_story,
        "images": {"type": "array", "minItems": 3, "maxItems": 4, "items": image},
        "agent_cases": {"type": "array", "minItems": 2, "maxItems": 3, "items": agent_case},
        "radar": {"type": "array", "minItems": 1, "items": radar},
        "actions": {"type": "array", "minItems": 3, "maxItems": 3, "items": action},
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }
