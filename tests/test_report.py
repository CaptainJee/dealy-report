import unittest

from dealy_report.report import ReportValidationError, report_json_schema, validate_report


def valid_payload():
    return {
        "date": "2026-07-14",
        "title": "Agent 工程进入可验证交付阶段",
        "lead": "今天的主线是 Agent 从演示走向有证据的生产应用。",
        "main_story": {
            "title": "主稿标题",
            "paragraphs": ["事实段。", "背景段。", "影响段。"],
            "sources": [{"label": "官方公告", "url": "https://example.com/story"}],
        },
        "images": [
            {
                "key": "hero",
                "url": "https://cdn.example.com/hero.png",
                "source_url": "https://example.com/story",
                "caption": "主稿产品图",
                "alt": "主稿产品界面",
            },
            {
                "key": "agent1",
                "url": "https://cdn.example.com/agent1.png",
                "source_url": "https://example.com/agent-one",
                "caption": "案例一架构图",
                "alt": "案例一架构",
            },
            {
                "key": "agent2",
                "url": "https://cdn.example.com/agent2.png",
                "source_url": "https://example.com/agent-two",
                "caption": "案例二产品图",
                "alt": "案例二产品",
            },
        ],
        "agent_cases": [
            {
                "title": "案例一",
                "project": "Project One",
                "scenario": "生产场景",
                "paragraphs": ["项目、用户和实际场景。", "技术栈、工作流和效果证据。"],
                "evidence": "官方复盘提供上线数据。",
                "reusable_insight": "先约束工具权限，再扩大自动化范围。",
                "image_key": "agent1",
                "sources": [{"label": "项目复盘", "url": "https://example.com/agent-one"}],
            },
            {
                "title": "案例二",
                "project": "Project Two",
                "scenario": "企业场景",
                "paragraphs": ["项目、用户和实际场景。", "技术栈、工作流和效果证据。"],
                "evidence": "仓库与发布记录可核验。",
                "reusable_insight": "评测结果进入发布门禁。",
                "image_key": "agent2",
                "sources": [{"label": "项目仓库", "url": "https://example.com/agent-two"}],
            },
        ],
        "radar": [
            {
                "category": "模型/API",
                "title": "工具调用更新",
                "summary": "能力变化。",
                "impact": "开发者影响。",
                "source": {"label": "更新日志", "url": "https://example.com/radar"},
            }
        ],
        "actions": [
            {"type": "立即试用", "title": "运行样例", "detail": "验证工具权限。"},
            {"type": "深入阅读", "title": "阅读复盘", "detail": "关注评测设计。"},
            {"type": "继续观察", "title": "观察成本", "detail": "记录一周变化。"},
        ],
    }


class ReportValidationTests(unittest.TestCase):
    def test_accepts_article_with_two_agent_cases_and_three_images(self):
        report = validate_report(valid_payload())

        self.assertEqual(report.title, "Agent 工程进入可验证交付阶段")
        self.assertEqual(len(report.agent_cases), 2)
        self.assertEqual(report.images[0].key, "hero")

    def test_rejects_missing_agent_case(self):
        payload = valid_payload()
        payload["agent_cases"] = payload["agent_cases"][:1]

        with self.assertRaisesRegex(ReportValidationError, "2 to 3 agent cases"):
            validate_report(payload)

    def test_rejects_non_https_source_or_image(self):
        payload = valid_payload()
        payload["images"][0]["url"] = "http://127.0.0.1/private.png"

        with self.assertRaisesRegex(ReportValidationError, "HTTPS"):
            validate_report(payload)

    def test_rejects_unknown_agent_image_key(self):
        payload = valid_payload()
        payload["agent_cases"][0]["image_key"] = "missing"

        with self.assertRaisesRegex(ReportValidationError, "unknown image"):
            validate_report(payload)

    def test_schema_is_strict_and_requires_all_top_level_fields(self):
        schema = report_json_schema()

        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), set(schema["properties"]))
        self.assertEqual(schema["properties"]["agent_cases"]["minItems"], 2)
        self.assertEqual(schema["properties"]["images"]["minItems"], 3)


if __name__ == "__main__":
    unittest.main()

