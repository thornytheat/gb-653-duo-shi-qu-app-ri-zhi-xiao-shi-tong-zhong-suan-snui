from fastapi import APIRouter, Body, Request
from . import service

router = APIRouter(tags=["M4-小时分桶"])


@router.get("/buckets")
def page(request: Request):
    settings = request.app.state.settings
    data = service.page_data(request.app.state.store, settings.rule_version)
    return request.app.state.templates.TemplateResponse(
        request, "m4_buckets.html", {"title": "小时分桶", "data": data})


@router.post("/api/buckets/rebuild")
def api(request: Request, payload: dict = Body(default={})):
    settings = request.app.state.settings
    return service.rebuild(request.app.state.store, payload or {}, settings.rule_version)


@router.post("/api/buckets/correct")
def correct(request: Request, payload: dict = Body(default={})):
    settings = request.app.state.settings
    return service.correct(request.app.state.store, payload or {}, settings.rule_version)


@router.get("/api/buckets/batches")
def batches(request: Request):
    store = request.app.state.store
    current = store.current_batch()
    return {"current": current["run_id"] if current else None,
            "batches": [{"run_id": b["run_id"], "digest": b["digest"],
                         "created_at": b["created_at"], "result": b["result"]}
                        for b in store.batches()]}
