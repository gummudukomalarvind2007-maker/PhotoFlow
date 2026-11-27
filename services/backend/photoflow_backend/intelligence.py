from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence


@dataclass(frozen=True)
class IntelligencePolicy:
    cloud_processing_consented: bool = False
    face_grouping_enabled: bool = False


class ImageUnderstandingProvider(Protocol):
    def understand(self, image_path: Path) -> dict[str, Any]: ...


class SemanticPhotoSearch(Protocol):
    def search(self, query: str, limit: int) -> Sequence[int]: ...


class FaceGroupingProvider(Protocol):
    def group(self, image_paths: Sequence[Path]) -> Sequence[Sequence[Path]]: ...


class DuplicateRecommendationProvider(Protocol):
    def recommend(self, media_ids: Sequence[int]) -> Sequence[tuple[int, int]]: ...
