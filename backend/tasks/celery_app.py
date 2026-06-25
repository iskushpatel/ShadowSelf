"""
Celery app factory (optional).
Wire broker/beat settings here if you use background tasks.
"""

import os


def create_celery_app():
    from celery import Celery

    broker_url = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
    backend_url = os.getenv("CELERY_BACKEND_URL", broker_url)

    app = Celery("shadowself", broker=broker_url, backend=backend_url)
    app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="UTC",
        enable_utc=True,
    )
    return app

