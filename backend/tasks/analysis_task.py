"""
Background analysis task boilerplate.
Tutorial hints:
1) Use celery to call the async orchestrator (asyncio.run or any loop adapter)
2) Save outputs to DB after build_profile()
3) Return a minimal payload (snapshot id) to avoid big results
"""

from typing import Sequence

from ..agents.orchestrator import Orchestrator
from .celery_app import create_celery_app


celery_app = create_celery_app()


@celery_app.task(name="shadowself.analyze_posts")
def analyze_posts_task(user_id: str, posts: Sequence[str]) -> dict:
    orchestrator = Orchestrator()
    # TODO: run async orchestrator in your preferred event loop setup.
    raise NotImplementedError("Wire async orchestration for background task.")

