from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import web
from .config import Settings
from .errors import install
from .store import Store
from .modules.m1_intake import handler as m1_handler
from .modules.m2_encoding import handler as m2_handler
from .modules.m3_fields import handler as m3_handler
from .modules.m4_content import handler as m4_handler
from .modules.m5_reconcile import handler as m5_handler
from .modules.m6_runs import handler as m6_handler


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(title="历史档案混合编码归一与索引保形", version=settings.rule_version)
    app.state.settings = settings
    app.state.store = Store(settings.db_path, default_template_version=settings.rule_version)
    app.state.templates = web.templates
    app.mount("/static", StaticFiles(directory=str(web.BASE / "static")), name="static")
    for module in (m1_handler, m2_handler, m3_handler, m4_handler, m5_handler, m6_handler):
        app.include_router(module.router)
    @app.middleware("http")
    async def template_context(request, call_next):
        # Pages show which template release is actually in force; the value is
        # read per request so a publication is visible without a restart.
        if request.url.path.startswith(("/api", "/static")):
            return await call_next(request)
        try:
            request.state.active_template = app.state.store.active_state()
        except Exception:
            request.state.active_template = {"active_version": settings.rule_version, "revision": 0}
        return await call_next(request)

    install(app)

    @app.get("/api/health")
    def health():
        state = app.state.store.active_state()
        return {
            "status": "ok",
            "project": "archivecodec",
            "runs": app.state.store.count(),
            "batches": app.state.store.count_batches(),
            "active_template_version": state["active_version"],
            "template_revision": state["revision"],
        }

    @app.get("/")
    def home():
        from fastapi.responses import RedirectResponse

        return RedirectResponse("/intake")

    return app


app = create_app()
