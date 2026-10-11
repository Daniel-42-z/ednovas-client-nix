"""Isolated HTTP fixture: reflect unique path tokens to prevent cached success."""
from http.server import BaseHTTPRequestHandler, HTTPServer


class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        payload = ("ednovas-fixture:" + self.path + "\n").encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


HTTPServer(("0.0.0.0", 18080), FixtureHandler).serve_forever()
