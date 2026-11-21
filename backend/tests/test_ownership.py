from app.fetch import GuardedFetcher
from app.models import Page
from app.ownership import make_token, verify_ownership
from tests.conftest import FakeFetcher, page, public_resolver

TOKEN = make_token("x.test", "secret")


def test_token_is_stable_and_domain_specific():
    assert make_token("x.test", "secret") == TOKEN
    assert make_token("y.test", "secret") != TOKEN
    assert len(TOKEN) == 32


def test_dns_txt_verifies_without_touching_the_site():
    f = FakeFetcher({})
    seen = []
    def txt(name):
        seen.append(name)
        return [f"agentready-verify={TOKEN}"]
    r = verify_ownership("x.test", TOKEN, f, txt)
    assert (r.verified, r.method) == (True, "dns")
    assert seen == ["_agentready.x.test"] and f.calls == []


def test_meta_tag_verifies_with_one_root_request():
    html = f'<html><head><meta name="agentready-verification" content="{TOKEN}"></head></html>'
    f = FakeFetcher({"https://x.test/": page("https://x.test/", html)})
    r = verify_ownership("x.test", TOKEN, f, lambda name: [])
    assert (r.verified, r.method) == (True, "meta")
    assert f.calls == [("https://x.test/", False)]


def test_wrong_token_is_rejected():
    html = '<meta name="agentready-verification" content="nope">'
    f = FakeFetcher({"https://x.test/": page("https://x.test/", html)})
    r = verify_ownership("x.test", TOKEN, f, lambda name: ["agentready-verify=nope"])
    assert r.verified is False and r.method == ""


def test_unreachable_site_is_not_verified():
    f = FakeFetcher({"https://x.test/": page("https://x.test/", status=0)})
    assert verify_ownership("x.test", TOKEN, f, lambda name: []).verified is False


class Scripted:
    def __init__(self, pages):
        self.pages, self.calls, self.addresses = pages, [], []

    def get(self, url, *, rendered=False, address=None):
        self.calls.append(url)
        self.addresses.append(address)
        return self.pages.get(url) or Page(url, 404, {}, "")


def test_probe_does_not_follow_a_redirect_to_another_domain():
    inner = Scripted({"https://x.test/": Page("https://x.test/", 301, {"location": "https://evil.test/"}, "")})
    g = GuardedFetcher(inner, "x.test", sleep=lambda s: None, resolver=public_resolver)
    assert verify_ownership("x.test", TOKEN, g, lambda name: []).verified is False
    assert inner.calls == ["https://x.test/"]          # the redirect target was never requested


def test_probe_validates_an_on_domain_redirect_before_following_it():
    meta = f'<meta name="agentready-verification" content="{TOKEN}">'
    inner = Scripted({
        "https://x.test/": Page("https://x.test/", 301, {"location": "/home"}, ""),
        "https://x.test/home": Page("https://x.test/home", 200, {}, meta),
    })
    g = GuardedFetcher(inner, "x.test", sleep=lambda s: None, resolver=public_resolver)
    assert verify_ownership("x.test", TOKEN, g, lambda name: []).method == "meta"
    assert inner.calls == ["https://x.test/", "https://x.test/home"]


def test_probe_to_a_host_with_a_non_public_address_sends_no_request():
    inner = Scripted({})
    g = GuardedFetcher(inner, "x.test", sleep=lambda s: None, resolver=lambda h: ["10.0.0.5"])
    assert verify_ownership("x.test", TOKEN, g, lambda name: []).verified is False
    assert inner.calls == []
