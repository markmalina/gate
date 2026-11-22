"""Persisted processing milestones shared by Gate's audio sources."""

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column


class AudioProcessingMilestones:
    transcription_queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recognition_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    text_saved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    transcription_stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    transcription_stop_reason: Mapped[str | None] = mapped_column(String(128))

    def reset_processing_attempt(self, *, queued_at: datetime | None) -> None:
        self.transcription_queued_at = queued_at
        self.recognition_started_at = None
        self.text_saved_at = None
        self.transcription_stopped_at = None
        self.transcription_stop_reason = None

    def record_processing_result(self, at: datetime, *, reason: str | None) -> None:
        self.text_saved_at = at if reason is None else None
        self.transcription_stopped_at = at if reason is not None else None
        self.transcription_stop_reason = reason
