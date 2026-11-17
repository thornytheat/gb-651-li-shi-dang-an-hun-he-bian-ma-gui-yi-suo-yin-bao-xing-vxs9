from fastapi import APIRouter, Query, Request
from fastapi.responses import Response

from .service import batch_evidence, execute
router = APIRouter(tags=["M6-批次导出"])

@router.get("/runs")
def page(request: Request):
    data = execute({"store": request.app.state.store})
    return request.app.state.templates.TemplateResponse(request, "m6_runs.html", {"title": "批次导出", "data": data})

@router.get("/api/runs")
def runs(request: Request):
    execute({"store": request.app.state.store})
    return {"runs": request.app.state.store.all()}

@router.get("/api/runs/batches")
def batch_runs(request: Request):
    return {"batches": request.app.state.store.list_batches()}


@router.get("/api/export/evidence.csv")
def export(request: Request, batch_no: str | None = Query(default=None)):
    if batch_no:
        data = batch_evidence(request.app.state.store, batch_no)
        filename = f"evidence-{batch_no}.csv"
    else:
        data = execute({"store": request.app.state.store})
        filename = "evidence.csv"
    return Response(
        data["csv"],
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
