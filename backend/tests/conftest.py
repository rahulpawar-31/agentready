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
