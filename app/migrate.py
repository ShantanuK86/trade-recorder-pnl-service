"""Explicit, repeatable widening migration for the original PostgreSQL schema.

Stop API writers and back up the database first. This cannot recover precision or
inventory already lost by previous application versions; reconcile historical data.
"""

from sqlalchemy import CheckConstraint, inspect
from sqlalchemy.schema import AddConstraint

from app.database import Base, engine
from app.finance import STORAGE_PRECISION, STORAGE_SCALE
from app.models import DecimalField


def migrate(bind=engine):
    if bind.dialect.name not in ("sqlite", "postgresql"):
        raise RuntimeError("Only SQLite and PostgreSQL are supported")
    with bind.begin() as connection:
        if bind.dialect.name == "sqlite":
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            Base.metadata.create_all(connection)
            return
        connection.exec_driver_sql("SELECT pg_advisory_xact_lock(74219051)")
        Base.metadata.create_all(connection)
        for table in Base.metadata.sorted_tables:
            columns = {c["name"]: c for c in inspect(connection).get_columns(table.name)}
            for column in table.columns:
                if isinstance(column.type, DecimalField):
                    datatype = columns[column.name]["type"]
                    if (datatype.precision, datatype.scale) != (
                        STORAGE_PRECISION,
                        STORAGE_SCALE,
                    ):
                        # Identifiers are fixed model definitions, never request data.
                        connection.exec_driver_sql(
                            f'ALTER TABLE "{table.name}" ALTER COLUMN "{column.name}" '
                            f"TYPE NUMERIC({STORAGE_PRECISION}, {STORAGE_SCALE})"
                        )
            existing = {c["name"] for c in inspect(connection).get_check_constraints(table.name)}
            for constraint in table.constraints:
                if isinstance(constraint, CheckConstraint) and constraint.name not in existing:
                    connection.execute(AddConstraint(constraint))


if __name__ == "__main__":
    migrate()
    print("Schema migration completed. Reconcile historical records before resuming writers.")
