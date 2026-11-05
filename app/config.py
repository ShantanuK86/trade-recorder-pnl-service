"""SQLite is the zero-setup default; PostgreSQL uses the included Psycopg driver."""

import os

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./trade_recorder.db")
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
if not DATABASE_URL.startswith(("sqlite:", "postgresql+psycopg:")):
    raise ValueError("Use sqlite:///... or postgresql+psycopg://...")
# Responses always expose both average-cost and FIFO fields.
