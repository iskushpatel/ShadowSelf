from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from langchain_core.runnables import RunnableLambda, RunnablePassthrough

from .llm_json import LLMJsonClient

if TYPE_CHECKING:
    from .orchestrator import AgentOutputs

_PROMPT_PATH = Path(__file__).parent / "prompts" / "profile_merge.txt"


class ProfileBuilder:
    def __init__(self, client: LLMJsonClient | None = None) -> None:
        self.client = client or LLMJsonClient()
        self._prompt = _PROMPT_PATH.read_text(encoding="utf-8")

    def chain(self):
        profile_chain = self.client.build_json_chain(
            run_name="ProfileBuilder",
            prompt_builder=lambda inputs: f"""{self._prompt}

Return ONLY valid JSON. No markdown. No preamble.

USER ID: {inputs["user_id"]}

SENTIMENT AGENT OUTPUT:
{inputs["sentiment"]}

TOPICS AGENT OUTPUT:
{inputs["topics"]}

PERSONALITY AGENT OUTPUT:
{inputs["personality"]}
""",
        )
        return (
            RunnablePassthrough.assign(profile=profile_chain)
            | RunnableLambda(self._finalize_profile)
        ).with_config(run_name="ProfileBuilder")

    async def build_profile(self, outputs: AgentOutputs, user_id: Any) -> dict:
        payload = {
            "user_id": user_id,
            "sentiment": outputs.sentiment,
            "topics": outputs.topics,
            "personality": outputs.personality,
        }
        return await self.chain().ainvoke(payload)

    def _finalize_profile(self, inputs: dict) -> dict:
        profile = dict(inputs["profile"])
        profile["raw_output"] = {
            "sentiment": inputs["sentiment"],
            "topics": inputs["topics"],
            "personality": inputs["personality"],
        }
        return profile
