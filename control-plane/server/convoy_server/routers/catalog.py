from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from ..auth import Principal, assert_live_principal, audit, require_role
from ..compat import matrix
from ..config import get_settings
from ..db import get_db, write_txn
from ..hardware import PROFILES, profile_dict
from ..ids import utcnow
from ..models import BuildRecipe, Device, EvalSet, FixtureModel, Plan, Release, RuntimeArtifact
from ..schemas_catalog import ArtifactIn, EvalSetIn, ModelIn, PlanIn, RecipeIn, ReleaseIn
from ..services import catalog as cat

router = APIRouter(prefix="/api/v1", tags=["catalog"])


def _err(e: cat.CatalogError) -> HTTPException:
    return HTTPException(e.status, str(e))


# ---- eval sets ----


@router.get("/eval-sets")
def list_eval_sets(p: Principal = Depends(require_role("viewer")), db: DbSession = Depends(get_db)):
    return [
        cat.eval_set_out(e, brief=True)
        for e in db.scalars(select(EvalSet).order_by(EvalSet.created_at.desc()))
    ]


@router.get("/eval-sets/{es_id}")
def get_eval_set(es_id: str, p: Principal = Depends(require_role("viewer")), db: DbSession = Depends(get_db)):
    e = db.get(EvalSet, es_id)
    if not e:
        raise HTTPException(404, "eval set not found")
    return cat.eval_set_out(e)


@router.post("/eval-sets", status_code=201)
def create_eval_set(
    body: EvalSetIn, p: Principal = Depends(require_role("operator")), db: DbSession = Depends(get_db)
):
    try:
        cases = body.cases if body.cases is not None else cat.parse_jsonl(body.cases_jsonl or "")
        with write_txn(db):
            row = cat.create_eval_set(
                db,
                name=body.name,
                version=body.version,
                cases=cases,
                scorer=body.scorer,
                description=body.description,
                created_by=p.user.id,
            )
            audit(db, p, "evalset.create", row.id, name=body.name, version=body.version)
    except cat.CatalogError as e:
        raise _err(e) from e
    return cat.eval_set_out(row)


# ---- recipes ----


@router.get("/recipes")
def list_recipes(p: Principal = Depends(require_role("viewer")), db: DbSession = Depends(get_db)):
    return [
        cat.recipe_out(r) for r in db.scalars(select(BuildRecipe).order_by(BuildRecipe.created_at.desc()))
    ]


@router.post("/recipes", status_code=201)
def create_recipe(
    body: RecipeIn, p: Principal = Depends(require_role("operator")), db: DbSession = Depends(get_db)
):
    if body.backend == "simulated" and not get_settings().simulator:
        raise HTTPException(409, "simulated recipes require simulator mode")
    cmake = body.cmake_flags or list(cat.DEFAULT_CMAKE)
    if body.track and body.target:
        raise HTTPException(422, "give either track or target, not both")
    if body.track and body.backend != "cuda":
        raise HTTPException(422, "track applies to cuda recipes only")
    try:
        target = body.target or cat.cuda_target(body.track)
        with write_txn(db):
            row = cat.create_recipe(
                db,
                name=body.name,
                commit=body.commit,
                tag=body.tag,
                cmake_flags=cmake,
                target=target,
                backend=body.backend,
                created_by=p.user.id,
            )
            audit(db, p, "recipe.create", row.id)
    except cat.CatalogError as e:
        raise _err(e) from e
    return cat.recipe_out(row)


# ---- runtime artifacts ----


@router.get("/runtime-artifacts")
def list_artifacts(p: Principal = Depends(require_role("viewer")), db: DbSession = Depends(get_db)):
    return [
        cat.artifact_out(a)
        for a in db.scalars(select(RuntimeArtifact).order_by(RuntimeArtifact.created_at.desc()))
    ]


@router.post("/runtime-artifacts", status_code=201)
def register_artifact(
    body: ArtifactIn, p: Principal = Depends(require_role("operator")), db: DbSession = Depends(get_db)
):
    recipe = db.get(BuildRecipe, body.recipe_id)
    if not recipe:
        raise HTTPException(404, "recipe not found")
    try:
        with write_txn(db):
            row = cat.register_artifact(
                db,
                get_settings(),
                recipe=recipe,
                receipt=body.receipt,
                scope=body.scope,
                storage=body.storage,
                created_by=p.user.email,
            )
            audit(db, p, "artifact.register", row.id, scope=body.scope, storage=body.storage)
    except cat.CatalogError as e:
        raise _err(e) from e
    return cat.artifact_out(row)


_UPLOAD_SEM = __import__("asyncio").Semaphore(1)


@router.post("/runtime-artifacts/upload", status_code=201)
async def upload_archive(
    request: Request, p: Principal = Depends(require_role("operator")), db: DbSession = Depends(get_db)
):
    """Authenticated bounded streaming upload of a fleet archive (tar.gz). Chunks are hashed and written to
    a temp file incrementally (never buffered in memory, never in SQLite), inspected, then published by
    rename with fsync. One upload at a time; the DB write lock is never held during the upload."""
    s = get_settings()
    if request.headers.get("content-type", "").split(";")[0] not in (
        "application/gzip",
        "application/x-gtar",
        "application/octet-stream",
        "application/x-tar",
    ):
        raise HTTPException(415, "send the archive as application/gzip")
    cl = request.headers.get("content-length")
    if cl and int(cl) > s.max_upload_bytes:
        raise HTTPException(413, "archive too large")
    if _UPLOAD_SEM.locked():
        raise HTTPException(429, "another upload is in progress")
    async with _UPLOAD_SEM:
        sink = cat.UploadSink(s, s.max_upload_bytes, s.artifact_quota_bytes)
        try:
            async for chunk in request.stream():
                sink.write(chunk)
            res = sink.finish_and_inspect()
        except cat.CatalogError as e:
            sink.abort()
            raise HTTPException(e.status, str(e)) from e
        except Exception as e:
            sink.abort()
            raise HTTPException(422, f"archive rejected: {type(e).__name__}") from e
    with write_txn(db):
        audit(db, p, "artifact.upload", res["archive_sha256"], size=res["archive_size"])
    return res


# ---- releases ----


@router.get("/releases")
def list_releases(p: Principal = Depends(require_role("viewer")), db: DbSession = Depends(get_db)):
    return [
        cat.release_out(r, brief=True)
        for r in db.scalars(select(Release).order_by(Release.created_at.desc()))
    ]


@router.get("/releases/{rel_id}")
def get_release(rel_id: str, p: Principal = Depends(require_role("viewer")), db: DbSession = Depends(get_db)):
    r = db.get(Release, rel_id)
    if not r:
        raise HTTPException(404, "release not found")
    return cat.release_out(r)


@router.post("/releases", status_code=201)
def create_release(
    body: ReleaseIn, p: Principal = Depends(require_role("operator")), db: DbSession = Depends(get_db)
):
    # model resolution and the remote GGUF header inspection run BEFORE the write transaction: a stalled
    # model host must never hold the database writer. The transaction re-validates every reference.
    try:
        prepared = cat.prepare_release(db, get_settings(), body.model_dump(exclude_none=True))
        with write_txn(db):
            # the preparation above may have waited on a model host: the principal that started it may
            # have been disabled or revoked meanwhile, so the operator authority is re-read here (R13)
            assert_live_principal(db, p, "operator")
            row = cat.persist_release(db, get_settings(), prepared, p.user.id)
            audit(
                db,
                p,
                "release.create",
                row.id,
                name=body.name,
                version=body.version,
                build_status=row.build_status,
            )
    except cat.CatalogError as e:
        raise _err(e) from e
    return cat.release_out(row)


@router.post("/releases/{rel_id}/retire")
def retire_release(
    rel_id: str, p: Principal = Depends(require_role("admin")), db: DbSession = Depends(get_db)
):
    r = db.get(Release, rel_id)
    if not r:
        raise HTTPException(404, "release not found")
    with write_txn(db):
        r.retired_at = utcnow()
        audit(db, p, "release.retire", r.id)
    return cat.release_out(r)


@router.get("/releases/{rel_id}/budget/{device_id}")
def release_budget(
    rel_id: str,
    device_id: str,
    p: Principal = Depends(require_role("viewer")),
    db: DbSession = Depends(get_db),
):
    r = db.get(Release, rel_id)
    d = db.get(Device, device_id)
    if not r or not d:
        raise HTTPException(404, "release or device not found")
    return cat.plan_for_device(r, d)


@router.post("/releases/resolve")
def resolve_model(
    body: ModelIn, p: Principal = Depends(require_role("operator")), db: DbSession = Depends(get_db)
):
    # read-only preview: never inside a write transaction (the header fetch may stall on the model host)
    try:
        res = cat.resolve_model(db, get_settings(), body.model_dump())
        # the header inspection the release would record (advisory for hf/supplied; from bytes for fixtures)
        gguf_meta, gguf_prov = cat.derive_gguf_metadata(get_settings(), res, None)
    except (cat.CatalogError, cat.ModelSourceError) as e:
        raise HTTPException(getattr(e, "status", 422), str(e)) from e
    return {
        "source": res.source,
        "repo": res.repo,
        "commit": res.commit,
        "verified": res.verified,
        "files": [f.__dict__ for f in res.files],
        "metadata": res.metadata,
        "gguf": gguf_meta,
        "gguf_provenance": gguf_prov,
    }


@router.get("/baseline")
def baseline(p: Principal = Depends(require_role("viewer"))):
    return {
        "qwen": cat.QWEN_BASELINE,
        "runtime": {
            "tag": cat.LLAMA_CPP_TAG,
            "commit": cat.LLAMA_CPP_COMMIT,
            "verified": "git tag peeled via ls-remote 2026-09-12",
        },
        "cmake": cat.DEFAULT_CMAKE,
        "target": cat.CUDA_TARGET,
        "tracks": cat.CUDA_TRACKS,
        "default_track": cat.DEFAULT_TRACK,
    }


# ---- plans ----


@router.get("/plans")
def list_plans(
    release_id: str | None = None,
    p: Principal = Depends(require_role("viewer")),
    db: DbSession = Depends(get_db),
):
    q = select(Plan).order_by(Plan.created_at.desc())
    if release_id:
        q = q.where(Plan.release_id == release_id)
    return [cat.plan_out(x) for x in db.scalars(q)]


@router.get("/plans/{plan_id}")
def get_plan(plan_id: str, p: Principal = Depends(require_role("viewer")), db: DbSession = Depends(get_db)):
    x = db.get(Plan, plan_id)
    if not x:
        raise HTTPException(404, "plan not found")
    return cat.plan_out(x)


@router.post("/plans", status_code=201)
def create_plan(
    body: PlanIn, p: Principal = Depends(require_role("operator")), db: DbSession = Depends(get_db)
):
    try:
        with write_txn(db):
            row = cat.create_plan(
                db,
                name=body.name,
                release_id=body.release_id,
                baseline_release_id=body.baseline_release_id,
                eval_set_id=body.eval_set_id,
                gates=body.gates,
                workload=body.workload,
                sample_policy=body.sample_policy,
                created_by=p.user.id,
            )
            audit(db, p, "plan.create", row.id)
    except cat.CatalogError as e:
        raise _err(e) from e
    return cat.plan_out(row)


# ---- reference data ----
@router.get("/hardware/profiles")
def hardware_profiles(p: Principal = Depends(require_role("viewer"))):
    return [profile_dict(k) for k in PROFILES]


@router.get("/compat/matrix")
def compat_matrix(p: Principal = Depends(require_role("viewer"))):
    return matrix()


@router.get("/evaluator")
def evaluator_info(p: Principal = Depends(require_role("viewer"))):
    from convoy_agent.evaluator import EVALUATOR_VERSION, METRICS
    from convoy_agent.scoring import MATCH_KINDS, SCORER_VERSION

    return {
        "evaluator_version": EVALUATOR_VERSION,
        "scorer_version": SCORER_VERSION,
        "metrics": {k: {"unit": v[0], "evidence": v[1]} for k, v in METRICS.items()},
        "match_kinds": list(MATCH_KINDS),
        "default_gates": cat.DEFAULT_GATES,
    }


@router.get("/fixtures/models")
def fixture_models(p: Principal = Depends(require_role("viewer")), db: DbSession = Depends(get_db)):
    if not get_settings().simulator:
        return []
    return [
        {"repo": f.repo, "revision": f.revision, "files": f.files, "metadata": f.metadata_}
        for f in db.scalars(select(FixtureModel))
    ]


@router.get("/artifacts/{artifact_id}/manifest")
def artifact_manifest(
    artifact_id: str, p: Principal = Depends(require_role("viewer")), db: DbSession = Depends(get_db)
):
    a = db.get(RuntimeArtifact, artifact_id)
    if not a:
        raise HTTPException(404, "artifact not found")
    return cat.artifact_out(a)


_ = Query
