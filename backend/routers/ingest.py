import traceback
from uuid import UUID as _UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.ingestion.connected_clients import ConnectedSourceFetchError
from backend.ingestion.github_client import GithubFetchError
from backend.schemas.api import (
    IngestGithubRequest,
    IngestManualRequest,
    IngestRedditLiveRequest,
    IngestRedditZipRequest,
    IngestTokenSourceRequest,
    IngestResult,
)
from backend.services import (
    EmptyContentError,
    ingest_facebook_upload,
    ingest_github,
    ingest_gmail,
    ingest_instagram_upload,
    ingest_linkedin,
    ingest_linkedin_upload,
    ingest_manual,
    ingest_meta,
    ingest_reddit_live,
    ingest_reddit_zip,
)
from db.session import get_db

router = APIRouter(prefix="/ingest", tags=["ingest"])


def _handle_exc(exc: Exception) -> HTTPException:
    traceback.print_exc()
    if isinstance(exc, (EmptyContentError, FileNotFoundError, ValueError)):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, (ConnectedSourceFetchError, GithubFetchError)):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}")


# ── Manual ────────────────────────────────────────────────────────────────────
@router.post("/manual", response_model=IngestResult, status_code=status.HTTP_201_CREATED)
async def ingest_manual_route(payload: IngestManualRequest, db: AsyncSession = Depends(get_db)) -> IngestResult:
    try:
        user, ic, dc = await ingest_manual(db, user_id=payload.user_id, email=payload.email, username=payload.username, text=payload.text, source_name=payload.source_name)
    except Exception as exc:
        raise _handle_exc(exc) from exc
    return IngestResult(user_id=user.id, source="manual", ingested_count=ic, deduped_count=dc)


# ── Reddit live ───────────────────────────────────────────────────────────────
@router.post("/reddit-live", response_model=IngestResult, status_code=status.HTTP_201_CREATED)
async def ingest_reddit_live_route(payload: IngestRedditLiveRequest, db: AsyncSession = Depends(get_db)) -> IngestResult:
    try:
        user, ic, dc = await ingest_reddit_live(db, user_id=payload.user_id, email=payload.email, username=payload.username, reddit_username=payload.reddit_username)
    except Exception as exc:
        raise _handle_exc(exc) from exc
    return IngestResult(user_id=user.id, source="reddit_live", ingested_count=ic, deduped_count=dc)


# ── Reddit ZIP (server path) ──────────────────────────────────────────────────
@router.post("/reddit-zip", response_model=IngestResult, status_code=status.HTTP_201_CREATED)
async def ingest_reddit_zip_route(payload: IngestRedditZipRequest, db: AsyncSession = Depends(get_db)) -> IngestResult:
    try:
        user, ic, dc = await ingest_reddit_zip(db, user_id=payload.user_id, email=payload.email, username=payload.username, zip_path=payload.zip_path)
    except Exception as exc:
        raise _handle_exc(exc) from exc
    return IngestResult(user_id=user.id, source="reddit_zip", ingested_count=ic, deduped_count=dc)


# ── GitHub ────────────────────────────────────────────────────────────────────
@router.post("/github", response_model=IngestResult, status_code=status.HTTP_201_CREATED)
async def ingest_github_route(payload: IngestGithubRequest, db: AsyncSession = Depends(get_db)) -> IngestResult:
    try:
        user, ic, dc = await ingest_github(db, user_id=payload.user_id, email=None, username=payload.username or payload.github_username, github_username=payload.github_username)
    except Exception as exc:
        raise _handle_exc(exc) from exc
    return IngestResult(user_id=user.id, source="github", ingested_count=ic, deduped_count=dc)


# ── Gmail ─────────────────────────────────────────────────────────────────────
@router.post("/gmail", response_model=IngestResult, status_code=status.HTTP_201_CREATED)
async def ingest_gmail_route(payload: IngestTokenSourceRequest, db: AsyncSession = Depends(get_db)) -> IngestResult:
    try:
        user, ic, dc = await ingest_gmail(db, user_id=payload.user_id, email=payload.email, username=payload.username or payload.email, access_token=payload.access_token, max_results=payload.max_results)
    except Exception as exc:
        raise _handle_exc(exc) from exc
    return IngestResult(user_id=user.id, source="gmail", ingested_count=ic, deduped_count=dc)


# ── LinkedIn — OAuth JSON OR file upload ──────────────────────────────────────
@router.post("/linkedin", response_model=IngestResult, status_code=status.HTTP_201_CREATED)
async def ingest_linkedin_route(request: Request, db: AsyncSession = Depends(get_db)) -> IngestResult:
    try:
        ctype = request.headers.get("content-type", "")
        if "multipart/form-data" in ctype:
            form = await request.form()
            f = form.get("file")
            if not f:
                raise ValueError("No file uploaded")
            file_bytes = await f.read()
            uid = _UUID(form.get("user_id")) if form.get("user_id") else None
            user, ic, dc = await ingest_linkedin_upload(db, user_id=uid, email=None, username=form.get("username"), zip_bytes=file_bytes)
        else:
            p = await request.json()
            user, ic, dc = await ingest_linkedin(db, user_id=p.get("user_id"), email=p.get("email"), username=p.get("username"), access_token=p.get("access_token"), max_results=p.get("max_results", 1))
    except Exception as exc:
        raise _handle_exc(exc) from exc
    return IngestResult(user_id=user.id, source="linkedin", ingested_count=ic, deduped_count=dc)


# ── LinkedIn ZIP (browser file upload) ───────────────────────────────────────
@router.post("/linkedin-zip", response_model=IngestResult, status_code=status.HTTP_201_CREATED)
async def ingest_linkedin_zip_route(request: Request, db: AsyncSession = Depends(get_db)) -> IngestResult:
    try:
        form = await request.form()
        f = form.get("file")
        if not f:
            raise ValueError("No file uploaded")
        file_bytes = await f.read()
        uid = _UUID(form.get("user_id")) if form.get("user_id") else None
        user, ic, dc = await ingest_linkedin_upload(db, user_id=uid, email=None, username=form.get("username"), zip_bytes=file_bytes)
    except Exception as exc:
        raise _handle_exc(exc) from exc
    return IngestResult(user_id=user.id, source="linkedin", ingested_count=ic, deduped_count=dc)


# ── Meta — OAuth JSON OR file upload ─────────────────────────────────────────
@router.post("/meta", response_model=IngestResult, status_code=status.HTTP_201_CREATED)
async def ingest_meta_route(request: Request, db: AsyncSession = Depends(get_db)) -> IngestResult:
    try:
        ctype = request.headers.get("content-type", "")
        if "multipart/form-data" in ctype:
            form = await request.form()
            f = form.get("file")
            if not f:
                raise ValueError("No file uploaded")
            file_bytes = await f.read()
            uid = _UUID(form.get("user_id")) if form.get("user_id") else None
            user, ic, dc = await ingest_facebook_upload(db, user_id=uid, email=None, username=form.get("username"), zip_bytes=file_bytes)
        else:
            p = await request.json()
            user, ic, dc = await ingest_meta(db, user_id=p.get("user_id"), email=p.get("email"), username=p.get("username"), access_token=p.get("access_token"), max_results=p.get("max_results", 25))
    except Exception as exc:
        raise _handle_exc(exc) from exc
    return IngestResult(user_id=user.id, source="meta", ingested_count=ic, deduped_count=dc)


# ── Facebook ZIP (browser file upload) ───────────────────────────────────────
@router.post("/facebook-zip", response_model=IngestResult, status_code=status.HTTP_201_CREATED)
async def ingest_facebook_zip_route(request: Request, db: AsyncSession = Depends(get_db)) -> IngestResult:
    try:
        form = await request.form()
        f = form.get("file")
        if not f:
            raise ValueError("No file uploaded")
        file_bytes = await f.read()
        uid = _UUID(form.get("user_id")) if form.get("user_id") else None
        user, ic, dc = await ingest_facebook_upload(db, user_id=uid, email=None, username=form.get("username"), zip_bytes=file_bytes)
    except Exception as exc:
        raise _handle_exc(exc) from exc
    return IngestResult(user_id=user.id, source="meta", ingested_count=ic, deduped_count=dc)


# ── Instagram ZIP (browser file upload) ──────────────────────────────────────
@router.post("/instagram-zip", response_model=IngestResult, status_code=status.HTTP_201_CREATED)
async def ingest_instagram_zip_route(request: Request, db: AsyncSession = Depends(get_db)) -> IngestResult:
    try:
        form = await request.form()
        f = form.get("file")
        if not f:
            raise ValueError("No file uploaded")
        file_bytes = await f.read()
        uid = _UUID(form.get("user_id")) if form.get("user_id") else None
        user, ic, dc = await ingest_instagram_upload(db, user_id=uid, email=None, username=form.get("username"), zip_bytes=file_bytes)
    except Exception as exc:
        raise _handle_exc(exc) from exc
    return IngestResult(user_id=user.id, source="meta", ingested_count=ic, deduped_count=dc)
