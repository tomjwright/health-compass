"""Open the trial database in DuckDB's notebook UI, in your browser, on any OS.

    uv run --env-file .env python -m health_compass.explore

It uses the project's own DuckDB, so there's nothing else to install and the version always
matches the file. The database is opened read-only, so the API and a build can run at the
same time. It keeps showing the file it opened: restart it after a build to see the new data.
"""

import duckdb

from health_compass.transform import warehouse_path


def main() -> None:
    path = warehouse_path()
    # The UI saves its notebooks in a database of its own, which it can't create if the whole
    # connection is read-only. So DuckDB runs in memory, with only our file attached read-only.
    with duckdb.connect() as db:
        db.execute("ATTACH ? AS health_compass (READ_ONLY)", [str(path)])
        db.sql("USE health_compass")
        db.sql("CALL start_ui()")
        input(f"{path} is open at http://localhost:4213. Press Enter to stop. ")
        db.sql("CALL stop_ui_server()")


if __name__ == "__main__":
    main()
