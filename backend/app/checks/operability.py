from bs4 import BeautifulSoup

from ..models import Category, Confidence, Status
from .base import Site, clip, finding

CAT = Category.OPERABILITY
MIN_TEXT_CHARS = 200
CAPTCHA_MARKERS = ("g-recaptcha", "h-captcha", "cf-turnstile", "google.com/recaptcha", "hcaptcha.com",
                   "challenges.cloudflare.com/turnstile")
SKIP_TYPES = {"hidden", "submit", "button", "image", "reset"}


def _visible_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    return soup.get_text(" ", strip=True)


def _input_type(el) -> str:
    return str(el.get("type") or "text").strip().lower()


def _unlabelled(soup: BeautifulSoup) -> list[str]:
    bad = []
    label_for = {l.get("for") for l in soup.find_all("label") if l.get("for")}
    for el in soup.find_all(["input", "textarea", "select"]):
        if el.name == "input" and _input_type(el) in SKIP_TYPES:
            continue
        ok = (str(el.get("aria-label") or "").strip()
              or (el.get("id") and el.get("id") in label_for)
              or el.find_parent("label") is not None)
        if not ok:
            bad.append(clip(el.get("name") or el.get("id") or el.name))
    return bad


def _captcha_markers(soup: BeautifulSoup) -> list[str]:
    """Markers are looked for in tag attribute values only (class, id, src, data-*): never in
    comments, script bodies or visible prose."""
    found = set()
    for tag in soup.find_all(True):
        blob = " ".join(" ".join(v) if isinstance(v, list) else str(v) for v in tag.attrs.values()).lower()
        found.update(m for m in CAPTCHA_MARKERS if m in blob)
    return sorted(found)


def run(site: Site):
    home = site.get("/")
    soup = BeautifulSoup(home.body, "html.parser")
    text_len = len(_visible_text(home.body))
    bad = _unlabelled(soup)
    captcha = _captcha_markers(soup)
    has_password = any(_input_type(el) == "password" for el in soup.find_all("input"))
    N, D, H, M, L = Status.NOT_DETECTED, Status.DETECTED, Confidence.HIGH, Confidence.MEDIUM, Confidence.LOW

    out = []
    has_text = text_len >= MIN_TEXT_CHARS
    out.append(finding("no_js_content", CAT, home, passed=has_text, weight=3,
                       check="GET / and count the text characters in the static HTML (scripts and styles removed)",
                       evidence=(f"{text_len} characters of text in the static HTML. Whether interactive features need JavaScript was not assessed"
                                 if has_text else
                                 f"Only {text_len} characters of text in the static HTML (need {MIN_TEXT_CHARS}). The page may depend on JavaScript to render its content, or may simply be short"),
                       status=D if has_text else N, confidence=M))
    out.append(finding("labelled_forms", CAT, home, passed=not bad, weight=2,
                       check="GET / and check each form field has a label, aria-label or wrapping label",
                       evidence=("Unlabelled fields in the static HTML: " + ", ".join(bad)) if bad else
                       "No unlabelled form fields in the home page's static HTML (labels added by script are not visible)",
                       status=D if bad else N, confidence=M))
    out.append(finding("no_captcha", CAT, home, passed=not captcha, weight=3,
                       check="GET / and search tag attributes in the static HTML for reCAPTCHA, hCaptcha or Turnstile markers",
                       evidence=("CAPTCHA markers found: " + ", ".join(captcha)) if captcha else
                       "CAPTCHA markers not detected in the static HTML attributes. Challenges loaded by script or shown only on submit would not be visible",
                       status=D if captcha else N, confidence=H if captcha else L))
    walled = home.status in (401, 403) or has_password
    if home.status in (401, 403):
        wall_ev, wall_conf = f"Home page returned {home.status}", H
    elif has_password:
        wall_ev, wall_conf = "Password field present on the home page", M
    else:
        wall_ev, wall_conf = (f"Home page returned {home.status} with no password field. Login walls elsewhere on the site were not checked"), M
    out.append(finding("no_login_wall", CAT, home, passed=not walled, weight=2,
                       check="GET / and check for a 401/403 status or a password field",
                       evidence=wall_ev, status=D if walled else N, confidence=wall_conf))
    return out
