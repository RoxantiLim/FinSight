from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from app.schemas import AdapterResult, SourceName


class BaseBillAdapter(ABC):
    source: SourceName

    @abstractmethod
    def parse(self, file_path: str | Path) -> AdapterResult:
        """Parse a source bill into normalized transaction inputs."""
