from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from .llm_json import LLMJsonClient


class PersonalityAgent:
    def __init__(self, client: LLMJsonClient | None = None) -> None:
        self.client = client or LLMJsonClient()
        self._prompt_path = Path(__file__).parent / "prompts" / "personality.txt"

    def chain(self):
        prompt = self._prompt_path.read_text(encoding="utf-8")
        return self.client.build_json_chain(
            run_name="PersonalityAgent",
            prompt_builder=lambda inputs: f"""{prompt}

Return ONLY valid JSON.
Schema: {{"ocean": {{"openness": 0.0, "conscientiousness": 0.0, "extraversion": 0.0, "agreeableness": 0.0, "neuroticism": 0.0}}, "confidence": {{"openness": 0.0, "conscientiousness": 0.0, "extraversion": 0.0, "agreeableness": 0.0, "neuroticism": 0.0}}, "rationale": "brief evidence"}}
Scores and confidence must be from 0 to 1. Do not diagnose mental health.

USER ID: {inputs["user_id"]}

DATA:
{self.client.format_posts(inputs["posts"])}
""",
        )

    async def analyze(self, posts: Sequence[str], user_id: Any) -> dict:
        return await self.chain().ainvoke({"posts": posts, "user_id": user_id})
