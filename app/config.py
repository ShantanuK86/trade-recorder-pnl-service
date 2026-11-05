"""
Application configuration.

Uses environment variables with sensible defaults for local development.
Switch DATABASE_URL to a PostgreSQL connection string for production.
"""

import os


DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    "sqlite:///./trade_recorder.db",
)

# Cost-basis method: "average" or "fifo"
# Both are always computed internally; this controls the default response.
COST_BASIS_METHOD: str = os.getenv("COST_BASIS_METHOD", "average")

