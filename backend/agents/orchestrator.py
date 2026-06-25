"""
High-level coordinator for Layer 3 agents.
Tutorial hints:
1) Keep orchestration simple and deterministic
2) Inject agents for testing/mocking
3) Persist outputs via db/models.py ProfileSnapshot
"""

from dataclasses import dataclass
from typing import Any, Sequence

from langchain_core.runnables import RunnablePassthrough

from .personality_agent import PersonalityAgent
from .profile_builder import ProfileBuilder
from .sentiment_agent import SentimentAgent
from .topics_agent import TopicsAgent


@dataclass(slots=True)
class AgentOutputs:
    sentiment: dict
    topics: dict
    personality: dict


class Orchestrator:
    def __init__(
        self,
        sentiment_agent: SentimentAgent | None = None,
        topics_agent: TopicsAgent | None = None,
        personality_agent: PersonalityAgent | None = None,
        profile_builder: ProfileBuilder | None = None,
    ) -> None:
        self.sentiment_agent = sentiment_agent or SentimentAgent()
        self.topics_agent = topics_agent or TopicsAgent()
        self.personality_agent = personality_agent or PersonalityAgent()
        self.profile_builder = profile_builder or ProfileBuilder()
        self._analysis_chain = (
            RunnablePassthrough.assign(
                sentiment=self.sentiment_agent.chain(),
                topics=self.topics_agent.chain(),
                personality=self.personality_agent.chain(),
            ).with_config(run_name="AnalyzePosts")
        )
        self._profile_chain = (
            self._analysis_chain
            | self.profile_builder.chain()
        ).with_config(run_name="BuildProfile")

    async def analyze_posts(
        self,
        posts: Sequence[str],
        user_id: Any,
    ) -> AgentOutputs:
        result = await self._analysis_chain.ainvoke(
            {"posts": list(posts), "user_id": user_id}
        )
        return AgentOutputs(
            sentiment=result["sentiment"],
            topics=result["topics"],
            personality=result["personality"],
        )

    async def build_profile(
        self,
        posts: Sequence[str],
        user_id: Any,
    ) -> dict:
        return await self._profile_chain.ainvoke(
            {"posts": list(posts), "user_id": user_id}
        )
