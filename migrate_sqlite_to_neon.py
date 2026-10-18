from pathlib import Path
import sqlite3

from sqlalchemy import (
    Integer, create_engine, func, inspect, select, text
)

from app import app, db


def main():
    database_path = Path(__file__).resolve().parent / "connecthub.db"
    if not database_path.is_file():
        raise RuntimeError("Local SQLite database was not found.")

    # Open the source database in read-only mode.
    source_url = (
        "sqlite:///" + database_path.as_posix()
    )
    source_engine = create_engine(
        source_url,
        connect_args={"check_same_thread": False}
    )

    try:
        with app.app_context():
            target_engine = db.engine

            try:
                with source_engine.connect() as source:
                    integrity = source.exec_driver_sql(
                        "PRAGMA integrity_check"
                    ).scalar_one()

                    if integrity != "ok":
                        raise RuntimeError(
                            "SQLite integrity check failed. Migration stopped."
                        )

                    source_metadata = db.metadata.__class__()
                    source_metadata.reflect(bind=source_engine)

                    source_tables = set(source_metadata.tables)
                    model_tables = set(db.metadata.tables)

                    if source_tables != model_tables:
                        raise RuntimeError(
                            "Source/model table mismatch. Migration stopped."
                        )

                    expected = {}
                    for name in sorted(source_tables):
                        table = source_metadata.tables[name]
                        expected[name] = source.execute(
                            select(func.count()).select_from(table)
                        ).scalar_one()

                    print("Source integrity:", integrity)
                    print("Source records:", sum(expected.values()))
                    for name, count in sorted(expected.items()):
                        print(f"  {name}: {count}")

                # One PostgreSQL transaction: schema and data either
                # commit together or roll back together.
                with target_engine.begin() as target:
                    existing = set(
                        inspect(target).get_table_names(schema="public")
                    )

                    if existing:
                        raise RuntimeError(
                            "Neon already contains tables. Nothing was copied."
                        )

                    db.metadata.create_all(target)

                    with source_engine.connect() as source:
                        for table in db.metadata.sorted_tables:
                            source_table = source_metadata.tables[table.name]
                            rows = [
                                dict(row)
                                for row in source.execute(
                                    select(source_table)
                                ).mappings()
                            ]

                            if rows:
                                target.execute(table.insert(), rows)

                    # Reset PostgreSQL-generated integer ID sequences
                    # so new records continue after migrated IDs.
                    for table in db.metadata.sorted_tables:
                        primary_keys = list(table.primary_key.columns)
                        if len(primary_keys) != 1:
                            continue

                        pk = primary_keys[0]
                        if not isinstance(pk.type, Integer):
                            continue

                        maximum = target.execute(
                            select(func.max(pk)).select_from(table)
                        ).scalar_one()

                        sequence = target.execute(
                            text("""
                                SELECT pg_get_serial_sequence(
                                    :table_name, :column_name
                                )
                            """),
                            {
                                "table_name": f'public."{table.name}"',
                                "column_name": pk.name,
                            },
                        ).scalar_one()

                        if sequence:
                            target.execute(
                                text("""
                                    SELECT setval(
                                        CAST(:sequence AS regclass),
                                        :last_value,
                                        :is_called
                                    )
                                """),
                                {
                                    "sequence": sequence,
                                    "last_value": maximum if maximum is not None else 1,
                                    "is_called": maximum is not None,
                                },
                            )

                    # Verify counts before the transaction can commit.
                    actual = {}
                    for table in db.metadata.sorted_tables:
                        actual[table.name] = target.execute(
                            select(func.count()).select_from(table)
                        ).scalar_one()

                    if actual != expected:
                        raise RuntimeError(
                            f"Count verification failed. Expected {expected}; "
                            f"received {actual}."
                        )

                print("\nSUCCESS: Migration committed and counts verified.")
                print("Migrated records:", sum(actual.values()))
                for name, count in sorted(actual.items()):
                    print(f"  {name}: {count}")

            finally:
                target_engine.dispose()

    finally:
        source_engine.dispose()


if __name__ == "__main__":
    main()
