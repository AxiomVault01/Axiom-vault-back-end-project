def pytest_report_header(config):
    from django.conf import settings

    engine = settings.DATABASES["default"]["ENGINE"].rsplit(".", 1)[-1]
    if engine == "sqlite3":
        return "test database: SQLite (no local Postgres reachable)"
    return f"test database: Postgres ({engine})"


import pytest


class FakeStorage:
    """Stands in for Cloudinary in every test, so no test ever reaches the network."""

    def __init__(self):
        self.files = {}
        self.deleted = []

    def store_private(self, data, public_id):
        self.files[public_id] = bytes(data)
        return {"public_id": public_id, "bytes": len(data)}

    def delete(self, public_id, image=False):
        self.deleted.append(public_id)
        self.files.pop(public_id, None)

    def store_public_image(self, data, public_id):
        self.files[public_id] = bytes(data)
        return {"public_id": public_id, "url": f"https://images.test/{public_id}.png"}

    def download_url(self, public_id, file_name, expires_in=3600):
        return f"https://storage.test/{public_id}?name={file_name}", 1_900_000_000


@pytest.fixture(autouse=True)
def fake_storage(monkeypatch):
    from config import file_storage

    fake = FakeStorage()
    monkeypatch.setattr(file_storage, "store_private", fake.store_private)
    monkeypatch.setattr(file_storage, "delete", fake.delete)
    monkeypatch.setattr(file_storage, "download_url", fake.download_url)
    monkeypatch.setattr(file_storage, "store_public_image", fake.store_public_image)
    return fake
