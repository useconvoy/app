from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DEFAULT_URL = "postgresql+psycopg2://convoy:convoy@localhost:5432/convoy"


class Base(DeclarativeBase):
    pass


def database_url() -> str:
    return os.environ.get("CONVOY_DATABASE_URL", DEFAULT_URL)


def make_engine(url: str = ""):
    return create_engine(url or database_url(), future=True)


def make_session_factory(engine):
    return sessionmaker(bind=engine, future=True, expire_on_commit=False)
