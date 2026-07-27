import os
from flask import Flask, jsonify, Response
from prometheus_client import Counter, generate_latest, CONTENT_TYPE_LATEST

app = Flask(__name__)

REQUEST_COUNTER = Counter(
    "backend_requests_total", "Celkovy pocet requestov na backend", ["endpoint"]
)


@app.route("/api/health")
def health():
    REQUEST_COUNTER.labels(endpoint="health").inc()
    return jsonify(status="ok")


@app.route("/api/hello")
def hello():
    REQUEST_COUNTER.labels(endpoint="hello").inc()
    message = os.environ.get("APP_MESSAGE", "Hello from backend")
    secret_present = bool(os.environ.get("APP_SECRET_TOKEN"))
    return jsonify(message=message, secret_present=secret_present)


@app.route("/metrics")
def metrics():
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
