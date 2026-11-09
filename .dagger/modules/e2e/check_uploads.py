"""Run a CLI command and reject uploads of the ignored cache fixture."""

import gzip
import http.server
import os
import subprocess
import sys
import threading


def fields(data):
    """Read protobuf wire fields without depending on an OTLP package."""
    offset = 0

    def varint():
        nonlocal offset
        value = shift = 0
        while True:
            byte = data[offset]
            offset += 1
            value |= (byte & 127) << shift
            if byte < 128:
                return value
            shift += 7

    while offset < len(data):
        tag = varint()
        wire = tag & 7
        if wire == 0:
            value = varint()
        elif wire in (1, 5):
            size = 8 if wire == 1 else 4
            value = data[offset : offset + size]
            offset += size
        elif wire == 2:
            size = varint()
            value = data[offset : offset + size]
            offset += size
        else:
            raise ValueError(f"unsupported protobuf wire type {wire}")
        yield tag >> 3, value


def span_names(data):
    # ExportTraceServiceRequest.resource_spans -> scope_spans -> spans -> name.
    for field, resource in fields(data):
        if field != 1:
            continue
        for field, scope in fields(resource):
            if field != 2:
                continue
            for field, span in fields(scope):
                if field == 2:
                    for field, name in fields(span):
                        if field == 5:
                            yield name.decode("utf-8")


names = []


class Receiver(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        if self.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        names.extend(span_names(body))
        self.send_response(200)
        self.send_header("Content-Type", "application/x-protobuf")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *_):
        pass


with http.server.HTTPServer(("127.0.0.1", 0), Receiver) as server:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}/v1/traces"
    result = subprocess.run(
        sys.argv[1:],
        env={
            **os.environ,
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": endpoint,
            "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": "http/protobuf",
        },
        check=False,
    )
    server.shutdown()
    thread.join()

if result.returncode:
    sys.exit(result.returncode)
assert any(name.startswith("uploading /repo") for name in names), "no workspace upload spans captured"
assert "gitignore-upload-poison" not in names, "generation uploaded the ignored cache"
