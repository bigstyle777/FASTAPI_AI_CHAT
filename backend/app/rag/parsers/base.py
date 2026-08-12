from abc import ABC, abstractmethod
from pathlib import Path


class DocumentParser(ABC):
    suffixes: set[str] = set()

    @abstractmethod
    def parse(self, path: str | Path) -> str:
        """Return normalized plain text extracted from a stored document."""
