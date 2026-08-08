"""FastAPI application entrypoint."""

from fastapi import FastAPI

from app.ingestion.router import router as ingestion_router
from app.retrieval.router import router as retrieval_router

app = FastAPI(title="Enterprise RAG Platform")
app.include_router(ingestion_router)
app.include_router(retrieval_router)
