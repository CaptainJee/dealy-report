import unittest

from dealy_report.renderer import render_feishu_manifest, render_markdown
from dealy_report.report import validate_report
from tests.test_report import valid_payload


class RendererTests(unittest.TestCase):
    def setUp(self):
        self.report = validate_report(valid_payload())

    def test_renders_three_article_cards_with_real_image_placeholders(self):
        manifest = render_feishu_manifest(self.report, max_cards=3)

        self.assertEqual(len(manifest["cards"]), 3)
        self.assertEqual(manifest["images"]["hero"], "https://cdn.example.com/hero.png")
        serialized = str(manifest["cards"])
        self.assertIn("{{image:hero}}", serialized)
        self.assertIn("{{image:agent1}}", serialized)
        self.assertIn("Agent 真实项目应用", serialized)
        self.assertIn("https://example.com/agent-one", serialized)

    def test_rejects_card_limit_below_required_layout(self):
        with self.assertRaisesRegex(ValueError, "three cards"):
            render_feishu_manifest(self.report, max_cards=2)

    def test_markdown_preserves_full_report_and_sources(self):
        markdown = render_markdown(self.report)

        self.assertIn("# Agent 工程进入可验证交付阶段", markdown)
        self.assertIn("## Agent 真实项目应用", markdown)
        self.assertIn("[项目复盘](https://example.com/agent-one)", markdown)
        self.assertIn("## 今日行动", markdown)

    def test_fourth_image_is_rendered_in_cards_and_markdown(self):
        payload = valid_payload()
        payload["images"].append(
            {
                "key": "architecture",
                "url": "https://cdn.example.com/architecture.png",
                "source_url": "https://example.com/architecture",
                "caption": "补充架构图",
                "alt": "系统架构",
            }
        )
        report = validate_report(payload)

        manifest = render_feishu_manifest(report)
        markdown = render_markdown(report)

        self.assertIn("{{image:architecture}}", str(manifest["cards"]))
        self.assertIn("![系统架构](https://cdn.example.com/architecture.png)", markdown)
        self.assertIn("[查看图源](https://example.com/architecture)", markdown)


if __name__ == "__main__":
    unittest.main()
