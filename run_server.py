"""
Robust server entrypoint (gevent) for multi-client / HTTPS use.

WHY THIS EXISTS
---------------
The built-in Flask/werkzeug development server (what ``python app.py`` uses) is
not designed for many concurrent, long-lived connections, and its SSL handling
is fragile — its own startup banner says "Do not use it in a production
deployment". This system streams video over MJPEG: every open ``/video_feed``
holds one connection for its entire lifetime. Add HTTPS plus several clients
(especially the STREAM source, where each browser both pushes frames AND views
the annotated stream) and the dev server's accept loop stalls — the page "works
for a moment, then times out" (ERR_TIMED_OUT).

gevent's cooperative ``WSGIServer`` handles thousands of long-lived connections
in a single process and terminates TLS robustly, which fixes that hang.

HOW TO RUN
----------
Use this INSTEAD of ``python app.py`` for the station / multi-client / HTTPS:

    python run_server.py

``python app.py`` still works unchanged for quick local/dev single-user runs.

IMPORTANT — thread=False
------------------------
We monkey-patch everything EXCEPT threads. The camera capture and YOLO inference
run on real OS threads (app.py), and OpenCV's blocking ``read()`` must not stall
the gevent hub. Keeping ``thread=False`` leaves those as true OS threads, so the
hub stays responsive while the camera blocks.
"""

# Monkey-patch BEFORE importing anything that touches socket/ssl (Flask, app, …).
from gevent import monkey
monkey.patch_all(thread=False)

import os

import config
from logger import setup_logger
import app as appmod  # runs app.py module-level setup, NOT its __main__ block

logger = setup_logger(__name__)


def main():
    # Reuse the exact same initialization as app.py (detector + DB).
    try:
        appmod.init_system()
    except Exception as e:
        logger.error(f"Error during system initialization: {e}")
        logger.warning("Server will start but some features may be unavailable.")

    from gevent.pywsgi import WSGIServer

    # HTTPS is opt-in (config.USE_HTTPS) and requires the cert/key. Same rules as
    # app.py: if enabled but the files are missing, fall back to plain HTTP.
    ssl_args = {}
    scheme = "http"
    if getattr(config, "USE_HTTPS", False):
        if os.path.exists(config.SSL_CERT_FILE) and os.path.exists(config.SSL_KEY_FILE):
            ssl_args = {"certfile": config.SSL_CERT_FILE, "keyfile": config.SSL_KEY_FILE}
            scheme = "https"
        else:
            logger.warning(
                "USE_HTTPS=True but cert/key not found "
                f"({config.SSL_CERT_FILE}, {config.SSL_KEY_FILE}). "
                "Run `python generate_cert.py` first. Falling back to HTTP."
            )

    print("=" * 64)
    print(f"  CONFIG_VERSION      : {config.CONFIG_VERSION}")
    print(f"  SERVER              : gevent WSGIServer")
    print(f"  URL                 : {scheme}://{config.FLASK_HOST}:{config.FLASK_PORT}")
    print("=" * 64)
    logger.info(f"Starting gevent server at {scheme}://{config.FLASK_HOST}:{config.FLASK_PORT}")

    server = WSGIServer((config.FLASK_HOST, config.FLASK_PORT), appmod.app, **ssl_args)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        appmod.cleanup()


if __name__ == "__main__":
    main()
