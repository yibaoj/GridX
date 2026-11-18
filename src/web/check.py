"""Verify a running local web service against its locally generated payloads."""

import argparse
import gzip
import hashlib
import json
from urllib.parse import urlparse
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from .data import WebDataService


def check(address: str, config: str) -> dict:
    if urlparse(address).hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Use the local service address.")
    service = WebDataService(config)

    def request(route):
        with urlopen(address.rstrip("/") + route, timeout=30) as response:
            body = response.read()
            if response.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            return json.loads(body)

    bootstrap = request("/api/bootstrap")
    assert bootstrap["data_status"]["available"], bootstrap["data_status"]["message"]
    manifest = bootstrap["manifest"]
    routes = {"/api/application/uc": service.application_path()}
    for stage in manifest["stages"]:
        if stage == "application":
            continue
        routes[f"/api/boundary/{stage}"] = service.boundary_path(stage)
        for layer in manifest["layers"]:
            if layer == "resource":
                for resource in manifest["resource_classes"]:
                    routes[f"/api/layers/{stage}/{layer}/{resource['id']}"] = service.layer_path(stage, layer, resource["id"])
            else:
                routes[f"/api/layers/{stage}/{layer}"] = service.layer_path(stage, layer)
    for route, path in routes.items():
        expected = json.loads(gzip.decompress(path.read_bytes()))
        assert request(route) == expected, route
    overview = request("/api/overview")
    regional = request("/api/regional-overview")
    for summary in (overview, regional):
        assert summary["schema_version"] == 1
        assert summary["year"] == manifest["year"]
        for source in summary["sources"]:
            actual = hashlib.sha256((service.output_root / source["path"]).read_bytes()).hexdigest()
            assert actual == source["sha256"], source["path"]
    assert overview["annual_energy_twh"] == sum(province["value"] for province in overview["provinces"])
    assert regional["national"]["peak_load_mw"] is None
    assert set(regional["provinces"]) == {province["id"] for province in overview["provinces"]}
    return {"routes_checked": len(routes) + 3, "year": manifest["year"],
            "provinces": len(overview["provinces"]), "sources": len(overview["sources"]),
            "result": "passed"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", default="http://127.0.0.1:8765")
    parser.add_argument("--config", default="config/web.toml")
    options = parser.parse_args()
    try:
        result = check(options.address, options.config)
    except HTTPError as error:
        parser.exit(1, f"Web check failed: HTTP {error.code} at {error.url}. "
                    "Rebuild the data with python -m src.web.build, "
                    "then restart python -m src.web.app.\n")
    except URLError as error:
        parser.exit(1, f"Cannot connect to {options.address}: {error.reason}. "
                    "Start the service in another terminal with "
                    "conda run --no-capture-output -n env-py313 python -m src.web.app, "
                    "then run this check again. "
                    "To build data, use python -m src.web.build.\n")
    except (AssertionError, FileNotFoundError, ValueError) as error:
        parser.exit(1, f"Web data check failed: {error}. "
                    "Check config/web.toml, rebuild the data and restart the service.\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
