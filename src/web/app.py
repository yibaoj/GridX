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


STATIC_ROOT = Path(__file__).with_name("static")


class WebApplication:
    """Small HTTP application with static assets and read-only data routes."""

    def __init__(self, config_path: str | Path = "config/web.toml") -> None:
        self.data = WebDataService(config_path)

    def handler(self) -> type[BaseHTTPRequestHandler]:
        application = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                try:
                    application._get(self)
                except KeyError as error:
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

            def log_message(self, format: str, *args: object) -> None:
                print(f"web: {format % args}")

        return Handler

    def _get(self, request: BaseHTTPRequestHandler) -> None:
        path = unquote(urlparse(request.path).path)
        if path == "/api/bootstrap":
            request._json(self.data.bootstrap())
            return
        if path == "/api/boundary":
            request._json(self.data.boundary())
            return
        if path.startswith("/api/layers/"):
            request._json(self.data.layer(path.removeprefix("/api/layers/")))
            return
        self._static(request, path)

    @staticmethod
    def _static(request: BaseHTTPRequestHandler, path: str) -> None:
        relative = "index.html" if path in {"", "/"} else path.lstrip("/")
        target = (STATIC_ROOT / relative).resolve()
        if STATIC_ROOT.resolve() not in target.parents or not target.is_file():
            request.send_error(HTTPStatus.NOT_FOUND)
            return
        body = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        request.send_response(HTTPStatus.OK)
        request.send_header("Content-Type", f"{content_type}; charset=utf-8")
        request.send_header("Content-Length", str(len(body)))
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
