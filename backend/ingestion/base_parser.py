from abc import ABC, abstractmethod
from typing import AsyncGenerator, Union
from uuid import UUID
import re

from ..schemas.post import PostCreate


class BaseParser(ABC):

    @abstractmethod
    async def parse(self, source: Union[str, bytes], user_id: UUID) -> AsyncGenerator[PostCreate, None]:
        """Yield PostCreate objects one by one for memory efficiency."""
        pass

    def normalize_text(self, text: str) -> str:
        """
        Clean raw text before storing:
        - Strip HTML tags
        - Remove URLs
        - Collapse excessive whitespace
        - Remove Reddit/Twitter artifacts ([removed], [deleted])
        - Strip leading/trailing whitespace
        """
        if not text:
            return ""

        # Remove HTML tags
        text = re.sub(r"<[^>]+>", " ", text)

        # Remove URLs (http/https/www)
        text = re.sub(r"http\S+|www\.\S+", "", text)

        # Remove Reddit artifacts
        text = re.sub(r"\[removed\]|\[deleted\]", "", text, flags=re.IGNORECASE)

        # Remove excessive punctuation repetition (e.g. "!!!!!!" → "!")
        text = re.sub(r"([!?.]){3,}", r"\1", text)

        # Collapse multiple newlines into max two
        text = re.sub(r"\n{3,}", "\n\n", text)

        # Collapse multiple spaces/tabs into one
        text = re.sub(r"[ \t]{2,}", " ", text)

        # Strip
        text = text.strip()

        return text

    def generate_hash(self, content: str) -> str:
        import hashlib
        return hashlib.sha256(content.encode()).hexdigest()

    def is_meaningful(self, text: str, min_words: int = 5) -> bool:
        """Return False for very short or empty content not worth analyzing."""
        return bool(text) and len(text.split()) >= min_words
