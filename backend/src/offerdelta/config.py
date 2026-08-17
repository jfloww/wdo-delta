"""Runtime configuration.

Read from the environment, with a local `.env` for development. The connection
string is a secret: it is never logged, never echoed, and never included in an
error message. `Settings.redacted_dsn` exists so diagnostics can say which host
they reached without leaking the credentials to reach it.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Everything the service needs from its environment."""

    model_config = SettingsConfigDict(
        env_file=_BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    #: PostgreSQL DSN. Absent in CI, which is why every database-backed test
    #: skips rather than fails when it is missing — a green suite must not
    #: depend on a secret that only exists on one machine.
    connection_string: str | None = Field(default=None, alias="CONNECTION_STRING")

    @property
    def database_available(self) -> bool:
        return bool(self.connection_string)

    @property
    def sqlalchemy_dsn(self) -> str:
        """The DSN with a driver SQLAlchemy 2 understands.

        Neon hands out `postgresql://…`; SQLAlchemy needs the driver named
        explicitly or it reaches for psycopg2, which is not installed.
        """
        if not self.connection_string:
            raise RuntimeError(
                "CONNECTION_STRING is not set; add it to backend/.env or the environment"
            )
        dsn = self.connection_string
        if dsn.startswith("postgresql://"):
            return dsn.replace("postgresql://", "postgresql+psycopg://", 1)
        if dsn.startswith("postgres://"):
            return dsn.replace("postgres://", "postgresql+psycopg://", 1)
        return dsn

    @property
    def redacted_dsn(self) -> str:
        """Host and database only — safe to log, print, or put in an error."""
        if not self.connection_string:
            return "<unset>"
        parts = urlsplit(self.connection_string)
        host = parts.hostname or "?"
        return urlunsplit((parts.scheme, host, parts.path, "", ""))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
