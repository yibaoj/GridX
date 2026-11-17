"""Development server for the spatial data website."""

from __future__ import annotations

import argparse
import gzip
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import mimetypes
from pathlib import Path
from urllib.parse import unquote, urlparse

from .data import WebDataService, encode_json
from .ui_data import prepare_dashboard


STATIC_ROOT = Path(__file__).with_name("static")
UI_ROOT = Path(__file__).resolve().parents[2] / "outputs/web-ui"


def static_file(path: str) -> Path | None:
    legacy = path == "/legacy" or path.startswith("/legacy/")
    root = STATIC_ROOT if legacy or path in {"/app.js", "/styles.css"} or not (UI_ROOT / "index.html").is_file() else UI_ROOT
    relative = path.removeprefix("/legacy") if legacy else path
    relative = "index.html" if relative in {"", "/"} else relative.lstrip("/")
    target = (root / relative).resolve()
    return target if root.resolve() in target.parents and target.is_file() else None


class WebApplication:
    """Small HTTP application with static assets and read-only data routes."""

    def __init__(self, config_path: str | Path = "config/web.toml") -> None:
        self.data = WebDataService(config_path)
        self.dashboard_error = ""
        try:
            self.dashboard = prepare_dashboard(self.data.output_root)
        except (OSError, ValueError, KeyError) as error:
            self.dashboard = {}
            self.dashboard_error = f"本地网页导出数据不完整或格式不兼容：{error}"

    def handler(self) -> type[BaseHTTPRequestHandler]:
        application = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                try:
                    application._get(self)
                except (KeyError, FileNotFoundError) as error:
                    self._json({"error": str(error)}, HTTPStatus.NOT_FOUND)
                except Exception as error:
                    self._json(
                        {"error": f"{type(error).__name__}: {error}"},
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                    )

            def _json(
                self,
                payload: dict[str, object],
                status: HTTPStatus = HTTPStatus.OK,
            ) -> None:
                body = gzip.compress(encode_json(payload), compresslevel=5)
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _prebuilt(self, path: Path) -> None:
                body = path.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                print(f"web: {format % args}")

        return Handler

    def _get(self, request: BaseHTTPRequestHandler) -> None:
        path = unquote(urlparse(request.path).path)
        if path == "/api/bootstrap":
            payload = self.data.bootstrap()
            if self.dashboard_error:
                payload["data_status"] = {"available": False, "message": self.dashboard_error}
            request._json(payload)
            return
        if path in {"/api/overview", "/api/regional-overview"}:
            name = path.removeprefix("/api/")
            if name not in self.dashboard:
                raise FileNotFoundError("尚未生成本地网页数据。")
            request._prebuilt(self.dashboard[name])
            return
        if path.startswith("/api/boundary/"):
            request._prebuilt(self.data.boundary_path(
                path.removeprefix("/api/boundary/")
            ))
            return
        if path.startswith("/api/layers/"):
            parts = path.removeprefix("/api/layers/").split("/")
            if len(parts) not in {2, 3}:
                raise KeyError("Layer route requires stage and layer IDs.")
            request._prebuilt(self.data.layer_path(*parts))
            return
        if path == "/api/application/uc":
            request._prebuilt(self.data.application_path())
            return
        self._static(request, path)

    @staticmethod
    def _static(request: BaseHTTPRequestHandler, path: str) -> None:
        target = static_file(path)
        if target is None:
            request.send_error(HTTPStatus.NOT_FOUND)
            return
        body = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        request.send_response(HTTPStatus.OK)
        request.send_header("Content-Type", f"{content_type}; charset=utf-8")
        request.send_header("Content-Length", str(len(body)))
        if target.suffix == ".html":
            request.send_header("Content-Security-Policy", "connect-src 'self'; object-src 'none'; base-uri 'self'")
        request.end_headers()
        request.wfile.write(body)


def serve(
    config_path: str | Path = "config/web.toml",
    host: str | None = None,
    port: int | None = None,
) -> None:
    """Run the local web application until interrupted."""

    application = WebApplication(config_path)
    server_options = application.data.config["server"]
    address = (host or server_options["host"], port or server_options["port"])
    server = ThreadingHTTPServer(address, application.handler())
    print(f"Spatial data web: http://{address[0]}:{address[1]}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/web.toml")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    options = parser.parse_args()
    serve(options.config, options.host, options.port)


if __name__ == "__main__":
    main()
