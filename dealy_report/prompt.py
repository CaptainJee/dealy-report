from __future__ import annotations

from typing import Any

from .config import SECTION_LABELS


SOURCE_BALANCE = {
    "balanced": "国内与海外一手来源保持平衡",
    "domestic": "优先国内一手来源，同时保留必要的海外信号",
    "global": "优先海外一手来源，同时保留必要的国内信号",
}


def build_daily_prompt(profile: Any, report_date: str) -> str:
    topics = "、".join(profile.topics)
    sections = "、".join(SECTION_LABELS[section] for section in profile.sections)
    source_preference = SOURCE_BALANCE.get(profile.source_balance, SOURCE_BALANCE["balanced"])
    return f"""生成 {report_date} 的 AI 研究员日报，使用 {profile.language} 写作，读者是{profile.audience}。

关注主题：{topics}。来源偏好：{source_preference}。技术雷达优先栏目：{sections}。“Agent 真实项目应用”始终是独立必选栏目。主要覆盖过去 24 小时，确有价值时可扩展至 72 小时并标明发布日期。

只使用可以打开并核验的一手来源：官方博客、产品文档、GitHub 仓库与 release、论文原文、公司公告。不得把传闻、未来日期或营销口号写成事实。每个判断都说明发生了什么、为什么重要、对开发者或项目落地意味着什么。

文章必须包含：
1. 80-150 字导语和一篇由 3-5 个短段落组成的今日主稿。
2. 独立的“Agent 真实项目应用”栏目，收录 2-3 个真实产品、开源项目、企业案例或工程复盘。每个案例严格写两个短段落，并提供场景、技术栈、工作流、效果证据和可复用启发。纯 demo 不收录。
3. 技术雷达与三项今日行动，行动类型分别是“立即试用”“深入阅读”“继续观察”。
4. 选择 3-4 张与正文直接相关的真实图片，包含 hero 主图和每个 Agent 案例配图。图片 URL 与图源页都必须是公开 HTTPS，格式为 PNG/JPEG/GIF/WebP，不得放占位图或只给图片链接。

最终响应只能是符合所给 JSON Schema 的 JSON。不要输出 Markdown 围栏、解释、Webhook、访问令牌、image_key 或任何凭证。卡片渲染和飞书发送由可信本地程序完成。"""
