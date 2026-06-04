import os
from fastapi.staticfiles import StaticFiles
from main import app, BASE_DIR


def _find_frontend_dist() -> str:
    candidates = [
        os.environ.get("AI_DCP_FRONTEND_DIST"),
        os.path.join(os.path.dirname(BASE_DIR), "frontend", "dist"),
        os.path.join(BASE_DIR, "frontend", "dist"),
        os.path.join(BASE_DIR, "dist"),
        os.path.join(BASE_DIR, "site"),
    ]
    for p in candidates:
        if not p:
            continue
        try:
            if os.path.isdir(p) and os.path.exists(os.path.join(p, "index.html")):
                return p
        except Exception:
            continue
    return ""


dist_dir = _find_frontend_dist()
if dist_dir:
    app.mount("/ui", StaticFiles(directory=dist_dir, html=True), name="frontend")

