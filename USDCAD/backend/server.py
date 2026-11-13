"""Read-only API, independent of the frontend and calculation worker."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def handler(data_dir, cors_origin):
    class API(BaseHTTPRequestHandler):
        def do_GET(self):
            routes = {"/api/v1/dashboard": "dashboard.json", "/api/v1/status": "update-status.json"}
            name = routes.get(self.path.split("?")[0])
            if not name:
                self.send_error(404)
                return
            try:
                body = (data_dir / name).read_bytes()
            except FileNotFoundError:
                self.send_error(503, "Run the update worker to publish a snapshot first")
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            if cors_origin:
                self.send_header("Access-Control-Allow-Origin", cors_origin)
                self.send_header("Vary", "Origin")
            self.end_headers()
            self.wfile.write(body)
    return API


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--data-dir", type=Path, default=ROOT / "data")
    p.add_argument("--cors-origin", default=os.getenv("USDCAD_CORS_ORIGIN", ""))
    a = p.parse_args()
    server = ThreadingHTTPServer((a.host, a.port), handler(a.data_dir, a.cors_origin))
    print(f"USDCAD read-only API http://{a.host}:{a.port}/api/v1/dashboard", flush=True)
    server.serve_forever()
