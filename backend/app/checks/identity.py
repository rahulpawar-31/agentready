import json

from ..models import Category, Confidence, Status
from .base import Site, finding

CAT = Category.IDENTITY
SPEC_PATHS = ("/openapi.json", "/swagger.json", "/.well-known/openapi.json")


def _spec(pages):
    for pg in pages:
        if pg.status != 200:
            continue
        try:
            data = json.loads(pg.body)
        except ValueError:
            continue
        if isinstance(data, dict) and ("openapi" in data or "swagger" in data):
            return pg, data
    return None, None


def run(site: Site):
    pages = [site.get(p) for p in SPEC_PATHS]
    pg, spec = _spec(pages)
    check = "GET " + ", ".join(SPEC_PATHS) + " and look for a JSON OpenAPI or Swagger document"
    if spec is None:
        return [
            finding("openapi_public", CAT, pages[0], passed=False, weight=3, check=check,
                    evidence="No OpenAPI document at the 3 standard paths. Specs published elsewhere are not checked",
                    status=Status.NOT_DETECTED, confidence=Confidence.MEDIUM),
            finding("auth_declared", CAT, pages[0], passed=False, weight=2,
                    check="Read securitySchemes from the OpenAPI document",
                    evidence="No OpenAPI document was found to read",
                    status=Status.NOT_DETECTED, confidence=Confidence.LOW),
        ]
    schemes = spec.get("components", {}).get("securitySchemes") or spec.get("securityDefinitions") or {}
    types = sorted({str(s.get("type")) for s in schemes.values()}) if schemes else []
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
