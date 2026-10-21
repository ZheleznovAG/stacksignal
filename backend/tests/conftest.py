import sys
import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Tests never use keys from the real developer session, and never touch the network to fetch
# the local embedding model either.
os.environ["AI_ENABLED"] = "0"
os.environ["STACKSIGNAL_PRIVATE_DIR"] = ""  # Never use an external personal installation in tests.
os.environ["EMBEDDINGS_ENABLED"] = "0"
os.environ["GITHUB_LOGIN"] = ""  # your stars are synced only in tests that set a login
# FastAPI's TestClient sends "Host: testserver" (app/security.py refuses unknown hosts).
os.environ["ALLOWED_HOSTS"] = "localhost,127.0.0.1,::1,testserver"
from app.db import Base  # noqa: E402
from app import models  # noqa: E402,F401  (registers models on Base.metadata)


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _no_search_pause(monkeypatch):
    """GitHub searches are spaced out in real runs; tests must not sleep."""
    from app import config
    monkeypatch.setattr(config, "GITHUB_SEARCH_PAUSE", 0.0)
    monkeypatch.setattr(config, "ARXIV_PAUSE", 0.0)


@pytest.fixture(autouse=True)
def _no_private_profiles(monkeypatch, tmp_path):
    """Tests must not depend on the developer's local, untracked private profiles."""
    from app import seed
    monkeypatch.setattr(seed, "PRIVATE_PROFILES_PATH", tmp_path / "absent.json")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Tests use fake transports (tests/fakes.py). A test that forgets one would quietly search
    GitHub for real -- slow, flaky and dependent on the machine -- so any outside connection fails."""
    import socket
    import urllib.request

    # A system proxy on localhost (e.g. a VPN client) would otherwise carry any request out.
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {})  # on Windows it also reads the registry
    real_connect = socket.socket.connect

    def guarded(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise RuntimeError(f"tests must not use the network (tried {address!r})")
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)
