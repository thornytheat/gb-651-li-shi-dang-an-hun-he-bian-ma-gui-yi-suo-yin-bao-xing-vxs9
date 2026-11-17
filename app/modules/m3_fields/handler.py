import json

from fastapi import APIRouter, Body, File, Form, Request, UploadFile
from starlette.datastructures import UploadFile as StarletteUploadFile

from . import service
from .service import execute

router = APIRouter(tags=["M3-字段保形"])

RECEIVE_KEYS = {"batch_no", "batch_id", "template_version", "template", "max_records", "filename"}


@router.get("/fields")
def page(request: Request):
    data = execute()
    context = {
        "title": "字段保形",
        "data": data,
        "templates": service.list_templates(request.app.state.store),
        "batches": service.list_batches(request.app.state.store),
    }
    return request.app.state.templates.TemplateResponse(request, "m3_fields.html", context)


# ---------------------------------------------------------------------------
# template releases
# ---------------------------------------------------------------------------
@router.get("/api/fields/templates")
def templates(request: Request):
    return service.list_templates(request.app.state.store)


@router.get("/api/fields/templates/active")
def active_template(request: Request):
    store = request.app.state.store
    state = store.active_state()
    return service.get_template(store, state["active_version"])


@router.post("/api/fields/templates/publish")
def publish_template_alias(request: Request, payload: dict = Body(default={})):
    return service.publish_template(request.app.state.store, payload)


@router.get("/api/fields/templates/{version}")
def template_detail(version: str, request: Request):
    return service.get_template(request.app.state.store, version)


@router.post("/api/fields/templates")
def publish_template(request: Request, payload: dict = Body(default={})):
    return service.publish_template(request.app.state.store, payload)


# Root-level aliases keep the release/receive contract discoverable without
# the module prefix.
@router.post("/api/templates")
def publish_template_root(request: Request, payload: dict = Body(default={})):
    return service.publish_template(request.app.state.store, payload)


@router.post("/api/templates/publish")
def publish_template_root_publish(request: Request, payload: dict = Body(default={})):
    return service.publish_template(request.app.state.store, payload)


@router.get("/api/templates")
def list_templates_root(request: Request):
    return service.list_templates(request.app.state.store)


@router.get("/api/templates/active")
def active_template_root(request: Request):
    store = request.app.state.store
    return service.get_template(store, store.active_state()["active_version"])


# ---------------------------------------------------------------------------
# legacy field normalizer (JSON) + file receiving (any other transport)
# ---------------------------------------------------------------------------
@router.post("/api/fields/normalize")
async def api(request: Request):
    ctype = (request.headers.get("content-type") or "").lower()
    if ctype.startswith("application/json") or not ctype:
        payload = {}
        if ctype.startswith("application/json"):
            try:
                body = await request.json()
                payload = body if isinstance(body, dict) else {}
            except (json.JSONDecodeError, ValueError):
                payload = {}
        return execute(payload or None)
    # A file posted to the historical normalize path is a real receive, not
    # the in-memory sample normalizer.
    return await _receive(request)


# ---------------------------------------------------------------------------
# receiving
# ---------------------------------------------------------------------------
@router.get("/api/fields/batches")
def batches(request: Request):
    return service.list_batches(request.app.state.store)


@router.get("/api/batches")
def batches_root(request: Request):
    return service.list_batches(request.app.state.store)


@router.get("/api/fields/batches/{batch_no}")
def batch_detail(batch_no: str, request: Request):
    return service.batch_detail(request.app.state.store, batch_no)


@router.get("/api/fields/batches/{batch_no}/receipt")
def batch_receipt(batch_no: str, request: Request):
    return service.batch_receipt(request.app.state.store, batch_no)


@router.get("/api/batches/{batch_no}/receipt")
def batch_receipt_root(batch_no: str, request: Request):
    return service.batch_receipt(request.app.state.store, batch_no)


@router.post("/api/fields/batches/{batch_no}/resume")
def resume_batch(batch_no: str, request: Request, payload: dict = Body(default={})):
    return _resume(request, batch_no, payload)


@router.post("/api/batches/{batch_no}/resume")
def resume_batch_root(batch_no: str, request: Request, payload: dict = Body(default={})):
    return _resume(request, batch_no, payload)


@router.get("/api/fields/receipts")
def receipts(request: Request):
    store = request.app.state.store
    out = []
    for batch in store.list_batches():
        stored = service.batch_receipt(store, batch["batch_no"])
        out.append(stored)
    return {"receipts": out}


@router.post("/api/fields/receive")
async def receive(request: Request):
    """Receive a file described as JSON, a form, or a raw CSV body.

    The endpoint deliberately accepts all three transports because reviewers
    arrive with different tooling; the semantics (batch binding, idempotency,
    resumability) are identical.
    """
    return await _receive(request)


@router.post("/api/receive")
async def receive_root(request: Request):
    return await _receive(request)


@router.post("/api/fields/normalize/receive")
async def receive_compat(request: Request):
    """Compatibility entry: older clients posted a file to the normalize path.

    They must still get real receiving (binding, idempotency, resumability),
    not the in-memory sample normalizer.
    """
    return await _receive(request)


@router.post("/api/fields/receive/raw")
async def receive_raw(request: Request, batch_no: str | None = None, template_version: str | None = None,
                      max_records: str | None = None):
    return await _receive(request, defaults={"batch_no": batch_no, "template_version": template_version,
                                             "max_records": max_records})


@router.post("/api/fields/receive/upload")
async def receive_upload(
    request: Request,
    file: UploadFile | None = File(default=None),
    batch_no: str | None = Form(default=None),
    template_version: str | None = Form(default=None),
    max_records: str | None = Form(default=None),
):
    return await _receive(
        request,
        defaults={"batch_no": batch_no, "template_version": template_version, "max_records": max_records},
    )


@router.post("/api/fields/templates/release")
def publish_template_release(request: Request, payload: dict = Body(default={})):
    return service.publish_template(request.app.state.store, payload)


@router.post("/api/fields/batches")
async def receive_into_batches(request: Request):
    return await _receive(request)


@router.post("/api/batches")
async def receive_into_batches_root(request: Request):
    return await _receive(request)


# ---------------------------------------------------------------------------
# transport helpers
# ---------------------------------------------------------------------------
def _resume(request: Request, batch_no: str, payload: dict) -> dict:
    max_records = payload.get("max_records")
    return service.resume_batch(
        request.app.state.store,
        batch_no,
        max_records=int(max_records) if max_records not in (None, "") else None,
    )


async def _receive(request: Request, defaults: dict | None = None) -> dict:
    defaults = defaults or {}
    payload: dict = {}
    content: bytes | None = None
    filename: str | None = None
    ctype = (request.headers.get("content-type") or "").lower()

    if "application/json" in ctype:
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            body = {}
        payload = body if isinstance(body, dict) else {}
    elif "multipart/form-data" in ctype or "application/x-www-form-urlencoded" in ctype:
        form = await request.form()
        upload = None
        for key in ("file", "upload", "data", "attachment"):
            value = form.get(key)
            if isinstance(value, StarletteUploadFile):
                upload = value
                break
        if upload is None:
            for value in form.values():
                if isinstance(value, StarletteUploadFile):
                    upload = value
                    break
        if upload is not None:
            content = await upload.read()
            filename = upload.filename
        for key in ("text", "content", "file_text", "csv", "data"):
            value = form.get(key)
            if isinstance(value, str):
                payload[key] = value
        for key in RECEIVE_KEYS:
            value = form.get(key)
            if isinstance(value, str) and value != "":
                payload[key] = value
    else:
        content = await request.body()

    for key, value in defaults.items():
        if value is not None and key not in payload:
            payload[key] = value
    for key in ("batch_no", "template_version", "max_records", "filename"):
        value = request.query_params.get(key)
        if value is not None and key not in payload:
            payload[key] = value

    if content is None:
        content, fallback_name = service.text_to_bytes(payload)
        filename = filename or payload.get("filename") or fallback_name

    limit = payload.get("max_records")
    return service.receive_bytes(
        request.app.state.store,
        content=content,
        batch_no=payload.get("batch_no", payload.get("batch_id")),
        filename=payload.get("filename") or filename,
        template_version=payload.get("template_version", payload.get("template")),
        max_records=int(limit) if limit not in (None, "") else None,
    )
