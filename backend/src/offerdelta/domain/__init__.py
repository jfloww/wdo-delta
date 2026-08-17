"""Domain layer — the LifeShock calculation engine.

This package depends on the Python standard library only. It must never import
FastAPI, Pydantic, SQLAlchemy, boto3, PySpark, or any LLM SDK. The boundary is
enforced by import-linter in CI, not by convention.
"""
