import os
import threading
from flask import Flask

app = Flask(__name__)

# API interna para la web (apagada si falta BOT_API_SECRET). Ver utils/api.py.
from utils.api import bp as api_bp  # noqa: E402
app.register_blueprint(api_bp)


@app.route("/")
def index():
    return "OK", 200


def _run():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


def keep_alive():
    threading.Thread(target=_run, daemon=True).start()
