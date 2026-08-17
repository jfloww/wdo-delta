"""The walking skeleton's HTTP surface.

One calculated figure, its full derivation, and the health endpoints a hosted
service needs. Deliberately small: this exists to prove the deployment path and
the money-serialisation boundary while both are still trivial to debug.

The real API arrives in milestone 5.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Final

from fastapi import FastAPI
from fastapi.responses import FileResponse

from offerdelta.api.presenters import present_comparison
from offerdelta.api.schemas import (
    ComparisonSchema,
    DerivationNodeSchema,
    HealthSchema,
    VersionSchema,
)
from offerdelta.application.queries.get_demo_comparison import get_demo_comparison
from offerdelta.application.queries.get_demo_derivation import get_demo_derivation

#: Bumped whenever a calculation rule changes. Every result will reference it
#: once results are persisted, so a stored figure stays reproducible.
ENGINE_VERSION: Final = "0.1.0-skeleton"

_STATIC = Path(__file__).parent / "static"

app = FastAPI(
    title="OfferDelta",
    summary="Personalized job offer and relocation decision engine",
    docs_url="/docs",
)


def _package_version() -> str:
    try:
        return version("offerdelta")
    except PackageNotFoundError:  # pragma: no cover - only when running from source
        return "unknown"


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(_STATIC / "index.html")


@app.get("/v1/health/live", response_model=HealthSchema)
def live() -> HealthSchema:
    """The process is running."""
    return HealthSchema(status="live")


@app.get("/v1/health/ready", response_model=HealthSchema)
def ready() -> HealthSchema:
    """The service can serve traffic.

    Once PostgreSQL arrives in milestone 5 this checks the connection; for now
    readiness and liveness are the same thing.
    """
    return HealthSchema(status="ready")


@app.get("/v1/version", response_model=VersionSchema)
def service_version() -> VersionSchema:
    return VersionSchema(
        service="offerdelta",
        version=_package_version(),
        engine=ENGINE_VERSION,
    )


@app.get("/v1/demo/derivation", response_model=DerivationNodeSchema)
def demo_derivation() -> DerivationNodeSchema:
    """Monthly disposable cash for the demo profile, with its full derivation.

    All amounts are decimal strings. Do not parse them as JavaScript numbers.
    """
    return DerivationNodeSchema.of(get_demo_derivation())


@app.get("/v1/demo/comparison", response_model=ComparisonSchema)
def demo_comparison() -> ComparisonSchema:
    """The full Auburn-to-New-Jersey comparison.

    Component deltas, both derivation trees, the cumulative series, and all
    three solvers. Every amount is a decimal string.

    `reconciled` reports whether every projected month balanced on both sides.
    The engine refuses to return an unbalanced result, so it is always true —
    it is surfaced so a reader can see the guarantee rather than trust it.
    """
    return present_comparison(get_demo_comparison())
