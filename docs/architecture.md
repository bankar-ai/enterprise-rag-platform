# Architecture

## Project Overview

This repository contains a production-grade Enterprise Retrieval-Augmented Generation (RAG) platform.

This is NOT a tutorial project.

Every design decision should resemble what would be implemented in a real enterprise software product.

The primary objective is to build a modular, scalable, testable and production-ready RAG platform that demonstrates software engineering excellence.

The platform will later become the foundation for additional repositories including:

- Agentic Insurance Assistant
- LLMOps & Evaluation Platform

Therefore, maintainability and extensibility are more important than rapid feature development.

## Project Goals

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

## Project Philosophy

We are building software as if it will be deployed in production.

Every implementation should prioritize

- correctness
- maintainability
- extensibility
- readability
- engineering quality

over speed of implementation.

When multiple approaches are available, recommend the one that would be selected by an experienced backend AI engineer building an enterprise product.

## Architecture Principles

Follow feature-oriented architecture.

Separate responsibilities into independent modules.

Business logic must never exist inside API routes.

API routes should only:

- validate request
- call service layer
- return response

Use dependency injection whenever appropriate.

Prefer composition over inheritance.

All dependencies must be open-source and free to use. Paid or proprietary APIs/services (hosted LLMs, managed vector DBs, paid embedding APIs, etc.) are out of scope unless explicitly approved as an exception.

## Technology Stack

Operating System

- Windows (primary), Linux compatibility maintained whenever possible

Language

- Python 3.12

Package Manager

- uv

Backend

- FastAPI

Validation

- Pydantic v2

LLM

- Ollama, primary development model Qwen3

Embeddings

- Nomic Embed

Vector Database

- FAISS

Testing

- Pytest

Containerization

- Docker

Version Control

- Git

IDE

- Claude Code

## Architecture Diagram

The full target-state architecture — API layer, service layer, data layer, core/infra, cross-cutting concerns, and deployment — is diagrammed in `docs/diagrams/architecture.drawio` (source, editable in [draw.io](https://app.diagrams.net/)) and `docs/diagrams/architecture.png` (exported image — pending, see ERP-009). Components not yet built are shown dashed/grey; components that exist today are shown solid/green. See `docs/superpowers/specs/2026-07-26-system-architecture-design.md` for the design rationale.
