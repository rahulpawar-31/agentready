import os

os.environ.setdefault("AGENTREADY_DEV", "1")     # main.py fails closed without a secret

import pytest

from app.models import Page


def page(url, body="", status=200, headers=None):
    return Page(url=url, status=status, headers=headers or {}, body=body)


def public_resolver(host):
    """A resolver for tests: every host resolves to one public address (example.com's)."""
    return ["93.184.216.34"]


class FakeFetcher:
    """Maps exact URLs to Pages; anything else is a 404."""

    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, url, *, rendered=False, address=None):
        self.calls.append((url, rendered))
        return self.pages.get(url) or Page(url, 404, {}, "")


@pytest.fixture(autouse=True)
def _clear_api_overrides():
    """Dependency overrides are global state on the FastAPI app: never leak them between tests."""
    yield
    from app import main
    main.app.dependency_overrides.clear()
