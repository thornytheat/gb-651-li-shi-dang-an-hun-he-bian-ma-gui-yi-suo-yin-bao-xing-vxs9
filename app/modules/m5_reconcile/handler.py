from fastapi import APIRouter, Body, Request

from .service import execute, reconcile_batch

router = APIRouter(tags=["M5-索引对账"])


@router.get("/reconcile")
def page(request: Request):
    data = execute()
    return request.app.state.templates.TemplateResponse(request, "m5_reconcile.html", {"title": "索引对账", "data": data})


@router.post("/api/reconcile/batch/{batch_no}")
def api_batch(batch_no: str, request: Request, payload: dict = Body(default={})):
    return reconcile_batch(request.app.state.store, batch_no, payload)


@router.post("/api/reconcile")
def api(payload: dict = Body(default={})):
    return execute(payload or None)
