import pytest


@pytest.fixture(scope="session", autouse=True)
def isolated_server_cache(tmp_path_factory):
    """Importing the real server during tests must not touch the user's cache."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("SZLCSC_CACHE_PATH", str(tmp_path_factory.mktemp("server-cache") / "cache.sqlite3"))
        yield
