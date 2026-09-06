"""SQLAlchemy ORM model for persisted evaluation run summaries."""

import uuid
from datetime import datetime

from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.ingestion.models import Base


class EvaluationRunRecord(Base):
    """One persisted evaluation run's aggregate metrics and per-query breakdown."""

    __tablename__ = "evaluation_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_at: Mapped[datetime] = mapped_column(server_default=func.now())
    top_k: Mapped[int]
    num_queries: Mapped[int]
    mean_precision_at_k: Mapped[float]
    mean_recall_at_k: Mapped[float]
    mrr: Mapped[float]
    details: Mapped[list[dict[str, object]]] = mapped_column(JSON)
