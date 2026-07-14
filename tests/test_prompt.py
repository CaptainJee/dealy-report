import unittest
from types import SimpleNamespace

from dealy_report.prompt import build_daily_prompt


class PromptTests(unittest.TestCase):
    def test_prompt_contains_profile_preferences_and_nonnegotiable_agent_column(self):
        profile = SimpleNamespace(
            language="zh-CN",
            audience="开发者和技术负责人",
            topics=("代码 Agent", "企业落地"),
            sections=("model-platform", "benchmarks-evaluation"),
            source_balance="balanced",
            max_cards=3,
        )

        prompt = build_daily_prompt(profile, "2026-07-14")

        self.assertIn("2026-07-14", prompt)
        self.assertIn("开发者和技术负责人", prompt)
        self.assertIn("代码 Agent、企业落地", prompt)
        self.assertIn("Agent 真实项目应用", prompt)
        self.assertIn("模型与平台更新", prompt)
        self.assertIn("Benchmark 与评测信号", prompt)
        self.assertNotIn("开发者工具与开源、Agent 工程实践", prompt)
        self.assertIn("2-3", prompt)
        self.assertIn("3-4", prompt)
        self.assertIn("HTTPS", prompt)
        self.assertIn("JSON", prompt)


if __name__ == "__main__":
    unittest.main()
