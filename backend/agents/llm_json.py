from __future__ import annotations

import json
import os
from collections.abc import Callable, Sequence

from dotenv import load_dotenv
from langchain_core.runnables import RunnableLambda
from langchain_groq import ChatGroq

load_dotenv()


class MissingLLMConfigError(RuntimeError):
    """Raised when LLM analysis is requested without provider config."""


class LLMJsonClient:
    def __init__(self, model: str | None = None) -> None:
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise MissingLLMConfigError(
                "GROQ_API_KEY is required for LLM agent analysis."
            )

        self.model = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")
        self.llm = ChatGroq(
            model=self.model,
            api_key=api_key,
            temperature=0.2,
            model_kwargs={"response_format": {"type": "json_object"}},
        )

    async def ainvoke_json(self, prompt: str) -> dict:
        response = await self.llm.ainvoke(prompt)
        return self._parse_json_response(str(response.content))

    def build_json_chain(
        self,
        *,
        prompt_builder: Callable[[dict], str],
        run_name: str,
    ):
        return (
            RunnableLambda(prompt_builder)
            | self.llm
            | RunnableLambda(lambda message: self._parse_json_response(str(message.content)))
        ).with_config(run_name=run_name)

    def format_posts(self, posts: Sequence[str], limit: int = 40) -> str:
        return "\n\n".join(
            f"POST {index + 1}:\n{post[:1500]}" for index, post in enumerate(posts[:limit])
        )

    def _strip_json_fences(self, content: str) -> str:
        content = content.strip()
        if content.startswith("```json"):
            content = content.removeprefix("```json").strip()
        if content.startswith("```"):
            content = content.removeprefix("```").strip()
        if content.endswith("```"):
            content = content.removesuffix("```").strip()
        return content

    def _parse_json_response(self, content: str) -> dict:
        content = self._strip_json_fences(content)
        decoder = json.JSONDecoder(strict=False)

        try:
            parsed, _ = decoder.raw_decode(content)
        except json.JSONDecodeError:
            start_candidates = [
                index for index in (content.find("{"), content.find("[")) if index != -1
            ]
            if not start_candidates:
                raise
            parsed, _ = decoder.raw_decode(content[min(start_candidates):])

        if isinstance(parsed, list):
            return {"items": parsed}
        if not isinstance(parsed, dict):
            raise ValueError("LLM response JSON must be an object or array.")
        return parsed
