from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from .llm_json import LLMJsonClient


class SentimentAgent:
    def __init__(self, client: LLMJsonClient | None = None) -> None:
        self.client = client or LLMJsonClient()
        self._prompt_path = Path(__file__).parent / "prompts" / "sentiment.txt"

    def chain(self):
        prompt = self._prompt_path.read_text(encoding="utf-8")
        return self.client.build_json_chain(
            run_name="SentimentAgent",
            prompt_builder=lambda inputs: f"""{prompt}

Return ONLY valid JSON.
Schema: {{"dominant": "positive|negative|neutral|mixed", "valence": 0.0, "arc": ["labels"], "rationale": "brief evidence"}}
Valence must be from -1 to 1.

USER ID: {inputs["user_id"]}

DATA:
{self.client.format_posts(inputs["posts"])}
""",
        )

    async def analyze(self, posts: Sequence[str], user_id: Any) -> dict:
        return await self.chain().ainvoke({"posts": posts, "user_id": user_id})
