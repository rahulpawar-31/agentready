import json

from ..models import Category, Confidence, Status
from .base import Site, clip, finding

CAT = Category.IDENTITY
SPEC_PATHS = ("/openapi.json", "/swagger.json", "/.well-known/openapi.json")


def _spec(pages):
    for pg in pages:
        if pg.status != 200:
            continue
        try:
            data = json.loads(pg.body)
        except (ValueError, RecursionError):
            continue
        if isinstance(data, dict) and ("openapi" in data or "swagger" in data):
            return pg, data
    return None, None


def _scheme_types(spec: dict) -> list[str]:
    components = spec.get("components")
    schemes = components.get("securitySchemes") if isinstance(components, dict) else None
    if not schemes:
        schemes = spec.get("securityDefinitions")
    if not isinstance(schemes, dict):
        return []
    return sorted({clip(s["type"]) for s in schemes.values() if isinstance(s, dict) and s.get("type")})


def run(site: Site):
    pages = [site.get(p) for p in SPEC_PATHS]
    pg, spec = _spec(pages)
    check = "GET " + ", ".join(SPEC_PATHS) + " and look for a JSON OpenAPI or Swagger document"
    if spec is None:
        # One failed candidate leaves this combined observation incomplete, wherever it appears in the list.
        # finding() turns that response into an unreachable, unscored finding with the failing URL.
        observed = next((p for p in pages if p.status == 0 or p.status >= 500), pages[0])
        ev = "No OpenAPI document at the 3 standard paths. Specs published elsewhere are not checked"
        return [
            finding("openapi_public", CAT, observed, passed=False, weight=3, check=check,
                    evidence=ev, status=Status.NOT_DETECTED, confidence=Confidence.MEDIUM),
            finding("auth_declared", CAT, observed, passed=False, weight=2,
                    check="Read securitySchemes from the OpenAPI document",
                    evidence="No OpenAPI document was found to read",
                    status=Status.NOT_DETECTED, confidence=Confidence.LOW),
        ]
    types = _scheme_types(spec)
    return [
        finding("openapi_public", CAT, pg, passed=True, weight=3, check=check,
                evidence=f"OpenAPI document served at {pg.url}",
                status=Status.DETECTED, confidence=Confidence.HIGH),
        finding("auth_declared", CAT, pg, passed=bool(types), weight=2,
                check="Read securitySchemes (or securityDefinitions) from the OpenAPI document",
                evidence=("Auth types declared: " + ", ".join(types)) if types else
                "The document declares no security scheme",
                status=Status.DETECTED if types else Status.NOT_DETECTED,
                confidence=Confidence.HIGH if types else Confidence.MEDIUM),
    ]
