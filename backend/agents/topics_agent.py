from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from .llm_json import LLMJsonClient


class TopicsAgent:
    def __init__(self, client: LLMJsonClient | None = None) -> None:
        self.client = client or LLMJsonClient()
        self._prompt_path = Path(__file__).parent / "prompts" / "topics.txt"

    def chain(self):
        prompt = self._prompt_path.read_text(encoding="utf-8")
        return self.client.build_json_chain(
            run_name="TopicsAgent",
            prompt_builder=lambda inputs: f"""{prompt}

Return ONLY valid JSON.
Schema: {{"topics": [{{"theme": "topic", "weight": 0.0, "example_posts": ["short evidence"]}}]}}
Weights must be from 0 to 1 and should roughly sum to 1.

USER ID: {inputs["user_id"]}

DATA:
{self.client.format_posts(inputs["posts"])}
""",
        )

    async def analyze(self, posts: Sequence[str], user_id: Any) -> dict:
        return await self.chain().ainvoke({"posts": posts, "user_id": user_id})
