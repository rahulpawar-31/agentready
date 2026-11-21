import httpx
from app.fetch import CachedFetcher, GuardedFetcher, HttpxFetcher, is_public
from app.models import Page
from tests.conftest import public_resolver


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


class Scripted:
    def __init__(self, pages):
        self.pages, self.calls, self.addresses = pages, [], []

    def get(self, url, *, rendered=False, address=None):
        self.calls.append(url)
        self.addresses.append(address)
        return self.pages.get(url) or Page(url, 404, {}, "")


class Clock:
    def __init__(self):
        self.t, self.slept = 0.0, []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def guard(pages, **kw):
    inner = Scripted(pages)
    resolver = kw.pop("resolver", public_resolver)
    return GuardedFetcher(inner, "x.test", sleep=lambda s: None, resolver=resolver, **kw), inner


# --- HttpxFetcher -----------------------------------------------------------
def test_httpx_fetcher_returns_page_with_lowercased_headers():
    def handler(req):
        return httpx.Response(200, text="hello", headers={"X-Test": "1"})
    p = HttpxFetcher(_client(handler)).get("https://x.test/")
    assert (p.status, p.body, p.headers["x-test"]) == (200, "hello", "1")


def test_httpx_fetcher_does_not_follow_redirects():
    def handler(req):
        return httpx.Response(301, headers={"Location": "https://other.test/"})
    p = HttpxFetcher(_client(handler)).get("https://x.test/")
    assert p.status == 301 and p.headers["location"] == "https://other.test/"
    assert HttpxFetcher()._client.follow_redirects is False


def test_httpx_fetcher_maps_network_error_to_status_0_with_error():
    def handler(req):
        raise httpx.ConnectError("boom")
    p = HttpxFetcher(_client(handler)).get("https://x.test/")
    assert p.status == 0 and p.body == "" and "ConnectError" in p.error


def test_httpx_fetcher_connects_to_the_pinned_address_and_keeps_host_and_sni():
    seen = {}
    def handler(req):
        seen.update(host=req.url.host, header=req.headers["host"], sni=req.extensions.get("sni_hostname"))
        return httpx.Response(200, text="ok")
    p = HttpxFetcher(_client(handler)).get("https://x.test/a?q=1", address="93.184.216.34")
    assert p.body == "ok" and p.url == "https://x.test/a?q=1"
    assert seen == {"host": "93.184.216.34", "header": "x.test", "sni": "x.test"}


# --- GuardedFetcher ---------------------------------------------------------
def test_other_host_is_blocked_without_sending_a_request():
    g, inner = guard({})
    p = g.get("https://evil.test/")
    assert p.status == 0 and "outside" in p.error and inner.calls == []


def test_non_http_scheme_is_blocked():
    g, inner = guard({})
    assert g.get("ftp://x.test/").status == 0 and inner.calls == []


def test_same_host_redirect_is_followed():
    g, inner = guard({
        "https://x.test/a": Page("https://x.test/a", 301, {"location": "/b"}, ""),
        "https://x.test/b": Page("https://x.test/b", 200, {}, "ok"),
    })
    p = g.get("https://x.test/a")
    assert (p.status, p.body, p.url) == (200, "ok", "https://x.test/b")


def test_redirect_off_the_verified_host_is_never_requested():
    g, inner = guard({"https://x.test/a": Page("https://x.test/a", 302, {"location": "https://evil.test/x"}, "")})
    p = g.get("https://x.test/a")
    assert p.status == 0 and "outside" in p.error
    assert inner.calls == ["https://x.test/a"]


def test_redirect_loop_is_stopped():
    g, _ = guard({"https://x.test/a": Page("https://x.test/a", 301, {"location": "/a"}, "")}, max_redirects=3)
    p = g.get("https://x.test/a")
    assert p.status == 0 and "redirects" in p.error


def test_requests_are_rate_limited():
    c = Clock()
    g = GuardedFetcher(Scripted({}), "x.test", resolver=public_resolver, min_interval=1.0,
                       clock=c.now, sleep=c.sleep)
    g.get("https://x.test/a")
    g.get("https://x.test/b")
    assert c.slept == [1.0]


def test_non_public_addresses_are_blocked_before_any_request():
    for addr in ["10.0.0.5", "127.0.0.1", "169.254.169.254", "192.168.1.10", "172.16.0.1",
                 "100.64.0.1", "0.0.0.0", "224.0.0.1", "240.0.0.1", "::1", "fe80::1", "fc00::1"]:
        g, inner = guard({}, resolver=lambda h, a=addr: [a])
        p = g.get("https://x.test/")
        assert p.status == 0 and "non-public" in p.error and inner.calls == [], addr


def test_one_private_answer_among_public_ones_blocks_the_request():
    g, inner = guard({}, resolver=lambda h: ["93.184.216.34", "10.0.0.5"])
    assert g.get("https://x.test/").status == 0 and inner.calls == []


def test_unresolvable_host_is_blocked():
    g, inner = guard({}, resolver=lambda h: [])
    p = g.get("https://x.test/")
    assert p.status == 0 and "resolve" in p.error and inner.calls == []


def test_dns_rebinding_on_a_redirect_is_blocked():
    answers = iter([["93.184.216.34"], ["127.0.0.1"]])   # public first, then loopback
    g, inner = guard({"https://x.test/a": Page("https://x.test/a", 301, {"location": "/b"}, "")},
                     resolver=lambda h: next(answers))
    p = g.get("https://x.test/a")
    assert p.status == 0 and "non-public" in p.error
    assert inner.calls == ["https://x.test/a"]          # the second request was never sent


def test_the_checked_address_is_the_one_pinned_for_the_request():
    g, inner = guard({"https://x.test/": Page("https://x.test/", 200, {}, "ok")},
                     resolver=lambda h: ["93.184.216.35", "93.184.216.34"])
    g.get("https://x.test/")
    assert inner.addresses == ["93.184.216.34"]          # lowest address (IPv4 first, then numeric)


def test_pinned_address_order_is_ipv4_first_then_numeric_not_alphabetical():
    g, inner = guard({"https://x.test/": Page("https://x.test/", 200, {}, "ok")},
                     resolver=lambda h: ["2606:2800:220:1:248:1893:25c8:1946", "93.184.216.100", "93.184.216.9"])
    g.get("https://x.test/")
    assert inner.addresses == ["93.184.216.9"]           # a string sort would pick .100


def test_cache_hits_consume_no_budget_and_redirect_hops_do(tmp_path):
    inner = Scripted({
        "https://x.test/a": Page("https://x.test/a", 301, {"location": "/b"}, ""),
        "https://x.test/b": Page("https://x.test/b", 200, {}, "ok"),
    })
    g = GuardedFetcher(inner, "x.test", resolver=public_resolver, sleep=lambda s: None, max_requests=3)
    cf = CachedFetcher(g, tmp_path)
    for _ in range(5):
        assert cf.get("https://x.test/a").body == "ok"
    assert len(inner.calls) == 2 and g.requests_used == 2   # one request plus one hop; four cache hits cost nothing


def test_urls_with_user_information_are_blocked():
    g, inner = guard({})
    assert g.get("https://user:pw@x.test/").status == 0
    assert g.get("https://x.test@evil.test/").status == 0
    assert inner.calls == []


def test_is_public_accepts_global_unicast_only():
    assert is_public("93.184.216.34") and is_public("2606:2800:220:1:248:1893:25c8:1946")
    assert not is_public("::ffff:127.0.0.1") and not is_public("not-an-ip")


def test_request_budget_is_enforced():
    g, inner = guard({}, max_requests=2)
    g.get("https://x.test/a")
    g.get("https://x.test/b")
    p = g.get("https://x.test/c")
    assert p.status == 0 and "budget" in p.error and len(inner.calls) == 2


def test_guard_refuses_a_fetcher_that_follows_redirects():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)), follow_redirects=True)
    assert HttpxFetcher(client).follows_redirects is True
    try:
        GuardedFetcher(HttpxFetcher(client), "x.test")
        assert False, "expected ValueError"
    except ValueError:
        pass


# --- CachedFetcher ----------------------------------------------------------
def test_cached_fetcher_only_calls_inner_once(tmp_path):
    inner = Scripted({"https://x.test/": Page("https://x.test/", 200, {}, "once")})
    cf = CachedFetcher(inner, tmp_path)
    assert cf.get("https://x.test/").body == "once"
    assert cf.get("https://x.test/").body == "once"
    assert len(inner.calls) == 1 and cf.misses == 1


def test_cache_key_separates_rendered_and_plain(tmp_path):
    class Inner:
        def get(self, url, *, rendered=False):
            return Page(url, 200, {}, "R" if rendered else "P")
    cf = CachedFetcher(Inner(), tmp_path)
    assert cf.get("https://x.test/").body == "P"
    assert cf.get("https://x.test/", rendered=True).body == "R"


def test_env_and_git_probes_are_never_written_to_disk(tmp_path):
    inner = Scripted({"https://x.test/.env": Page("https://x.test/.env", 200, {}, "SECRET=1")})
    cf = CachedFetcher(inner, tmp_path)
    cf.get("https://x.test/.env")
    cf.get("https://x.test/.env")
    cf.get("https://x.test/.git/HEAD")
    assert list(tmp_path.iterdir()) == [] and len(inner.calls) == 3


# --- Phase 1 review fixes: cache -------------------------------------------
import os

import pytest


def _cache_over_guard(pages, tmp_path):
    g, inner = guard(pages)
    return CachedFetcher(g, tmp_path), inner


def test_a_redirect_into_env_is_never_cached(tmp_path):
    pages = {
        "https://x.test/robots.txt": Page("https://x.test/robots.txt", 301, {"location": "/.env"}, ""),
        "https://x.test/.env": Page("https://x.test/.env", 200, {}, "DB_PASSWORD=hunter2\n"),
    }
    cf, inner = _cache_over_guard(pages, tmp_path)
    assert cf.get("https://x.test/robots.txt").status == 200
    cf.get("https://x.test/robots.txt")
    assert list(tmp_path.glob("*")) == []          # nothing written, so nothing can leak
    assert inner.calls.count("https://x.test/.env") == 2


@pytest.mark.parametrize("path", ["/.ENV", "/%2eenv", "/./.env", "/a/../.env", "/.Git/HEAD",
                                  "/.git/config", "//.env", "/%2E%67it/HEAD"])
def test_secret_paths_are_never_cached_whatever_the_spelling(tmp_path, path):
    url = f"https://x.test{path}"
    inner = Scripted({url: Page(url, 200, {}, "K=v")})
    cf = CachedFetcher(inner, tmp_path)
    cf.get(url)
    cf.get(url)
    assert list(tmp_path.glob("*")) == [] and len(inner.calls) == 2


@pytest.mark.parametrize("status", [0, 429, 500, 503])
def test_error_pages_are_not_cached(tmp_path, status):
    url = "https://x.test/robots.txt"
    inner = Scripted({url: Page(url, status, {}, "", error="boom" if status == 0 else "")})
    cf = CachedFetcher(inner, tmp_path)
    cf.get(url)
    cf.get(url)
    assert len(inner.calls) == 2 and list(tmp_path.glob("*")) == []


def test_404_and_200_pages_are_cached(tmp_path):
    inner = Scripted({"https://x.test/ok": Page("https://x.test/ok", 200, {}, "ok")})
    cf = CachedFetcher(inner, tmp_path)
    for _ in range(2):
        cf.get("https://x.test/ok")
        cf.get("https://x.test/missing")
    assert len(inner.calls) == 2


@pytest.mark.parametrize("garbage", ["", "{\"url\": \"x\"", "[]", "{\"foo\": 1}", "not json"])
def test_a_corrupt_cache_file_is_a_miss_and_is_repaired(tmp_path, garbage):
    url = "https://x.test/"
    inner = Scripted({url: Page(url, 200, {}, "fresh")})
    cf = CachedFetcher(inner, tmp_path)
    cf._path(url, False).parent.mkdir(parents=True, exist_ok=True)
    cf._path(url, False).write_text(garbage)
    assert cf.get(url).body == "fresh"
    assert cf.get(url).body == "fresh" and len(inner.calls) == 1


def test_cache_writes_are_atomic_and_a_failed_write_does_not_break_the_fetch(tmp_path, monkeypatch):
    url = "https://x.test/"
    inner = Scripted({url: Page(url, 200, {}, "ok")})
    cf = CachedFetcher(inner, tmp_path)
    assert cf.get(url).body == "ok"
    assert [p.suffix for p in tmp_path.iterdir()] == [".json"]            # no temp files left behind
    other = "https://x.test/other"
    inner.pages[other] = Page(other, 200, {}, "two")
    def boom(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr("app.fetch.os.replace", boom)
    assert cf.get(other).body == "two"
    assert [p.suffix for p in tmp_path.iterdir()] == [".json"]            # no partial or temp file


def test_the_cache_directory_is_only_created_when_something_is_written(tmp_path):
    d = tmp_path / "later"
    cf = CachedFetcher(Scripted({"https://x.test/": Page("https://x.test/", 200, {}, "ok")}), d)
    assert not d.exists()
    cf.get("https://x.test/")
    assert d.exists()


# --- Phase 1 review fixes: is_public, HttpxFetcher, guard -------------------
@pytest.mark.parametrize("addr", ["64:ff9b::7f00:1", "64:ff9b::a00:1", "fec0::1", "::10.0.0.1",
                                  "192.0.0.9", "192.88.99.1", "::1", "::", "2002:7f00:1::",
                                  "64:ff9b:1::1", "fd00::1"])
def test_is_public_rejects_special_ranges(addr):
    assert not is_public(addr)


def test_is_public_still_accepts_real_public_addresses():
    assert is_public("93.184.216.34") and is_public("2606:2800:220:1:248:1893:25c8:1946")
    assert is_public("64:ff9b::808:808")        # NAT64 to a public IPv4 is re-tested as that IPv4


def test_httpx_fetcher_turns_any_exception_into_a_status_0_page():
    def handler(req):
        raise RuntimeError("weird")
    p = HttpxFetcher(_client(handler)).get("https://x.test/")
    assert p.status == 0 and p.error == "request error: RuntimeError"


def test_unparsable_port_and_idn_host_do_not_raise():
    ok = lambda req: httpx.Response(200, text="x")
    f = HttpxFetcher(_client(ok))
    assert f.get("https://x.test:abc/", address="93.184.216.34").status == 0
    assert f.get("https://x.test:99999/", address="93.184.216.34").status == 0
    assert f.get("http://bücher.test/", address="93.184.216.34").status == 0


def test_response_bodies_are_capped_at_two_million_bytes():
    def handler(req):
        return httpx.Response(200, content=b"a" * 3_000_000)
    p = HttpxFetcher(_client(handler)).get("https://x.test/")
    assert p.status == 200 and len(p.body) == 2_000_000


def test_response_charset_is_honoured_and_bad_bytes_do_not_raise():
    def handler(req):
        return httpx.Response(200, content="café".encode("latin-1"),
                              headers={"content-type": "text/plain; charset=latin-1"})
    assert HttpxFetcher(_client(handler)).get("https://x.test/").body == "café"
    def handler2(req):
        return httpx.Response(200, content=b"\xff\xfe\x00abc")
    assert "abc" in HttpxFetcher(_client(handler2)).get("https://x.test/").body


def test_default_client_ignores_proxy_environment(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.local:3128")
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.local:3128")
    assert httpx.Client()._mounts                 # control: a default httpx client would use the proxy
    c = HttpxFetcher()._client
    assert c.trust_env is False and not c._mounts


def test_https_to_http_downgrade_redirect_is_blocked():
    g, inner = guard({"https://x.test/a": Page("https://x.test/a", 301, {"location": "http://x.test/b"}, "")})
    p = g.get("https://x.test/a")
    assert p.status == 0 and "downgrade" in p.error and inner.calls == ["https://x.test/a"]


def test_default_guard_allows_exactly_six_requests_for_one_url_then_blocks():
    g, inner = guard({"https://x.test/a": Page("https://x.test/a", 301, {"location": "/a"}, "")})
    p = g.get("https://x.test/a")
    assert len(inner.calls) == 6 and p.status == 0 and "more than 5 redirects" in p.error
