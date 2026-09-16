from types import SimpleNamespace

from fastapi import FastAPI

import alliance_release_stability_v1 as stability


def _handler(module, name):
    def endpoint():
        return {"ok": True}
    endpoint.__module__ = module
    endpoint.__name__ = name
    return endpoint


def _add(app, path, method, module, name):
    app.add_api_route(path, _handler(module, name), methods=[method])


def test_owner_check_rejects_shadowing():
    app = FastAPI()
    _add(app, "/login", "GET", "wrong", "wrong")
    _add(app, "/login", "GET", "app", "login_page")
    result = stability._check_owner(app, stability.CANONICAL_ROUTES[0])
    assert result["passed"] is False
    assert result["active"] == "wrong.wrong"


def test_owner_check_accepts_canonical_first():
    app = FastAPI()
    _add(app, "/login", "GET", "app", "login_page")
    _add(app, "/login", "GET", "legacy", "login")
    result = stability._check_owner(app, stability.CANONICAL_ROUTES[0])
    assert result["passed"] is True
    assert result["registered_count"] == 2
    assert result["shadowed_owners"] == ["legacy.login"]


def test_source_views_are_fixed_and_complete():
    assert stability.SOURCE_VIEWS == (
        "manual",
        "whatsapp",
        "newspaper",
        "magazine",
    )
    expression = stability._source_case("evidence")
    for source in ("WHATSAPP", "NEWSPAPER", "MAGAZINE", "MANUAL"):
        assert source in expression


def test_route_presence_checks_method():
    app = FastAPI()
    _add(app, "/api/v60/deal-match", "GET", "matcher", "match")
    assert stability._route_present(app, "/api/v60/deal-match") is True
    assert stability._route_present(app, "/api/v60/deal-match", "POST") is False


if __name__ == "__main__":
    test_owner_check_rejects_shadowing()
    test_owner_check_accepts_canonical_first()
    test_source_views_are_fixed_and_complete()
    test_route_presence_checks_method()
    print("ROUTE_SHADOWING_TEST=PASS")
    print("CANONICAL_OWNER_TEST=PASS")
    print("CANONICAL_SOURCE_VIEW_TEST=PASS")
