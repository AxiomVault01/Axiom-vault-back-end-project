def pytest_report_header(config):
    from django.conf import settings

    engine = settings.DATABASES["default"]["ENGINE"].rsplit(".", 1)[-1]
    if engine == "sqlite3":
        return "test database: SQLite (no local Postgres reachable)"
    return f"test database: Postgres ({engine})"
