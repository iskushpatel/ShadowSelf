from datetime import datetime, timezone
from typing import AsyncGenerator, List
from uuid import UUID

from langchain_text_splitters import RecursiveCharacterTextSplitter

from .base_parser import BaseParser
from ..schemas.post import PostCreate


class ManualParser(BaseParser):
    """User-provided content parser for manual ingestion."""

    def __init__(self, chunk_size: int = 1000, chunk_overlap: int = 200):
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            length_function=len,
            separators=["\n\n\n", "\n\n", "\n", ".", "!", "?", " ", ""],
            keep_separator=True,
        )

    async def parse(
        self,
        text: str,
        user_id: UUID,
        source_name: str = "manual_paste",
    ) -> AsyncGenerator[PostCreate, None]:
        """Parse user-provided text into PostCreate objects."""
        chunks = self.text_splitter.split_text(text.strip())
        total = len(chunks)

        for i, chunk in enumerate(chunks):
            if not chunk.strip():
                continue

            normalized = self.normalize_text(chunk)
            if not self.is_meaningful(normalized):
                continue

            post = PostCreate(
                user_id=user_id,
                source="manual",
                content=normalized,
                # posted_at not created_at — matches PostCreate schema
                posted_at=datetime.now(timezone.utc),
                metadata={
                    "platform": "manual",
                    "source_name": source_name,
                    "chunk_index": i,
                    "total_chunks": total,
                    "is_pasted": True,
                    "original_length": len(chunk),
                },
            )
            post.content_hash = self.generate_hash(post.content)
            yield post

    async def parse_batch(
        self,
        texts: List[str],
        user_id: UUID,
    ) -> AsyncGenerator[PostCreate, None]:
        """Parse multiple text blocks sequentially."""
        for text in texts:
            async for post in self.parse(text, user_id):
                yield post
