# CLAUDE.md

# Enterprise RAG Platform

## Project Overview

This repository contains a production-grade Enterprise Retrieval-Augmented Generation (RAG) platform.

This is NOT a tutorial project.

Every design decision should resemble what would be implemented in a real enterprise software product.

The primary objective is to build a modular, scalable, testable and production-ready RAG platform that demonstrates software engineering excellence.

The platform will later become the foundation for additional repositories including:

- Agentic Insurance Assistant
- LLMOps & Evaluation Platform

Therefore, maintainability and extensibility are more important than rapid feature development.

---

# Project Goals

The project must demonstrate:

- Enterprise software architecture
- Production-ready FastAPI backend
- Hybrid Retrieval
- PageIndex-inspired retrieval
- FAISS vector search
- BM25 retrieval
- Local LLM inference using Ollama
- Modular RAG pipeline
- Authentication
- Evaluation
- Observability
- Docker deployment
- CI/CD
- Comprehensive testing

---

# Development Environment

Operating System

- Windows (Primary)
- Linux compatibility should be maintained whenever possible.

Package Manager

- uv

Python

- Python 3.12

IDE

- Claude Code

LLM

- Ollama

Primary Development Model

- Qwen3

Version Control

- Git

---

# Mandatory Development Rules

Always use:

- uv add
- uv remove
- uv sync
- uv run

Never use:

- pip install
- pip uninstall

Never assume globally installed Python packages.

All dependencies must be managed through uv.

---

# Engineering Principles

Always prefer

- readability
- maintainability
- simplicity
- modularity
- testability

Avoid

- unnecessary abstractions
- premature optimization
- duplicated code
- deeply nested logic
- large functions

Every function should ideally have one responsibility.

---

# Architecture Principles

Follow feature-oriented architecture.

Separate responsibilities into independent modules.

Business logic must never exist inside API routes.

API routes should only:

- validate request
- call service layer
- return response

Use dependency injection whenever appropriate.

Prefer composition over inheritance.

---

# Coding Standards

Use

- Python type hints
- Pydantic v2
- Google-style docstrings

Functions should remain small.

Variable names should be descriptive.

Avoid single-character variable names except for loops.

Never suppress warnings unless absolutely necessary.

---

# Error Handling

Never silently ignore exceptions.

Raise meaningful exceptions.

Provide actionable error messages.

Log unexpected failures.

---

# Logging

Use structured logging.

Never use print() inside application code.

print() is acceptable only for local debugging and should be removed before commit.

---

# Testing

Every business logic module should have unit tests.

Prefer pytest.

Mock external dependencies.

Tests should be deterministic.

Avoid flaky tests.

---

# Security

Never hardcode

- API keys
- passwords
- secrets
- tokens

Always load configuration from environment variables.

Never commit secrets to Git.

---

# Dependencies

Before introducing a new dependency:

1. Check whether Python standard library is sufficient.
2. Check whether an existing project dependency can solve the problem.
3. Introduce a new dependency only if it provides significant value.

Avoid dependency bloat.

---

# Performance

Prefer efficient algorithms.

Avoid unnecessary database queries.

Avoid unnecessary LLM calls.

Avoid repeated embedding generation.

Cache expensive operations whenever appropriate.

---

# Documentation

Every major module must contain documentation.

Update README whenever setup changes.

Keep architecture documentation inside:

docs/

Do not place long technical explanations inside CLAUDE.md.

---

# Git Workflow

Commit frequently.

Each commit should represent one logical change.

Use Conventional Commits.

Examples

feat:
fix:
docs:
refactor:
test:
chore:

Never commit broken code.

---

# AI Assistant Behaviour

When generating code:

Always

- produce production-quality code
- explain architectural decisions
- use modern Python
- use type hints
- keep functions focused
- minimize dependencies
- write maintainable code

Never

- generate tutorial-style code
- introduce unnecessary complexity
- create placeholder implementations
- create unused files
- duplicate logic
- invent APIs or library behavior

If requirements are ambiguous,

ask questions before implementing.

---

# Project Philosophy

We are building software as if it will be deployed in production.

Every implementation should prioritize

- correctness
- maintainability
- extensibility
- readability
- engineering quality

over speed of implementation.

When multiple approaches are available,

recommend the one that would be selected by an experienced backend AI engineer building an enterprise product.

---

# Current Technology Stack

Backend

- FastAPI

Language

- Python 3.12

Package Manager

- uv

LLM

- Ollama

Model

- Qwen3

Embeddings

- Nomic Embed

Vector Database

- FAISS

Testing

- Pytest

Validation

- Pydantic

Containerization

- Docker

Version Control

- Git

---

# Future Features

The platform will eventually support

- PDF ingestion
- DOCX ingestion
- PPTX ingestion
- OCR
- Hybrid Retrieval
- PageIndex Retrieval
- Semantic Search
- BM25
- Reranking
- Multi-document Retrieval
- Authentication
- Conversation Memory
- Evaluation
- Observability
- Streaming Responses
- Docker Deployment
- CI/CD

These features should influence architectural decisions even before they are implemented.

---

End of file.