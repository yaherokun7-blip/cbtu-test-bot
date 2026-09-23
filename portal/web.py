import asyncio
import hashlib
import hmac
import logging
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode, urlparse
from uuid import UUID

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import delete, select

from portal.config import ROOT, Settings
from portal.importer import MAX_BYTES, ImportErrorDetail, csv_bytes, parse_upload
from portal.store import Registry, RegistryError, oauth_states, sessions
from portal.classes import ClassRegistry


def token_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def create_session(registry, user_id, display_name):
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    with registry.transaction() as connection:
        connection.execute(delete(sessions).where(sessions.c.expires_at < int(time.time())))
        connection.execute(sessions.insert().values(token_hash=token_hash(token), user_id=user_id,
            display_name=display_name[:100], csrf=csrf, expires_at=int(time.time()) + 8 * 3600))
    return token


class CommitRequest(BaseModel):
    batch_id: UUID


class ClassRequest(BaseModel):
    course: str
    role_name: str
    capacity: int
    code: str = ""


def create_app(settings=None, registry=None):
    settings = settings or Settings()
    settings.validate()
    (ROOT / "data").mkdir(exist_ok=True)
    class_mode = settings.verification_mode == "classes"
    registry = registry or (ClassRegistry if class_mode else Registry)(settings.database_url)

    @asynccontextmanager
    async def lifespan(app):
        await asyncio.to_thread(registry.initialize)
        yield
        registry.engine.dispose()

    app = FastAPI(title="CSPACE · Learner Admin", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.registry = registry
    app.state.settings = settings
    templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
    secure = urlparse(settings.public_url).scheme == "https"

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        response.headers["Cache-Control"] = "no-store"
        if secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    def optional_user(request):
        token = request.cookies.get("cspace_session", "")
        if not token:
            return None
        with registry.engine.connect() as connection:
            row = connection.execute(select(sessions).where(sessions.c.token_hash == token_hash(token),
                sessions.c.expires_at > int(time.time()))).mappings().first()
        return dict(row) if row and row["user_id"] in settings.admin_ids else None

    def require_user(request: Request):
        user = optional_user(request)
        if not user:
            raise HTTPException(401, "กรุณาเข้าสู่ระบบด้วยบัญชีแอดมิน")
        if request.method not in {"GET", "HEAD"}:
            origin = request.headers.get("origin")
            if origin != settings.public_url or not hmac.compare_digest(request.headers.get("x-csrf-token", ""), user["csrf"]):
                raise HTTPException(403, "คำขอไม่ถูกต้อง กรุณารีเฟรชหน้าเว็บ")
        return user

    @app.exception_handler(RegistryError)
    async def registry_error(request, error):
        return JSONResponse({"detail": str(error)}, status_code=409)

    @app.exception_handler(ImportErrorDetail)
    async def import_error(request, error):
        return JSONResponse({"detail": str(error)}, status_code=400)

    @app.exception_handler(Exception)
    async def unexpected_error(request, error):
        logging.error("Portal request failed: %s", request.url.path, exc_info=error)
        return JSONResponse({"detail": "ระบบขัดข้องชั่วคราว ข้อมูลยังไม่ถูกยืนยัน กรุณาลองใหม่"}, status_code=503)

    @app.get("/health")
    def health():
        with registry.engine.connect() as connection:
            connection.execute(select(1))
        discord_bot = getattr(app.state, "discord_bot", None)
        if discord_bot is not None and not discord_bot.is_ready():
            raise HTTPException(503, "Discord is connecting")
        return {"status": "ok"}

    @app.get("/")
    def index(request: Request):
        user = optional_user(request)
        return templates.TemplateResponse(request=request, name="classes.html" if user and class_mode else "index.html", context={"user": user,
            "configured": bool(settings.client_id and settings.client_secret), "demo": settings.demo})

    @app.get("/auth/login")
    def login():
        if not settings.client_id or not settings.client_secret:
            raise HTTPException(503, "ยังไม่ได้ตั้งค่า Discord สำหรับเข้าสู่ระบบ กรุณาดูคู่มือติดตั้ง")
        state = secrets.token_urlsafe(32)
        with registry.transaction() as connection:
            connection.execute(delete(oauth_states).where(oauth_states.c.expires_at < int(time.time())))
            connection.execute(oauth_states.insert().values(token_hash=token_hash(state), expires_at=int(time.time()) + 600))
        response = RedirectResponse("https://discord.com/oauth2/authorize?" + urlencode({
            "client_id": settings.client_id, "redirect_uri": settings.redirect_uri,
            "response_type": "code", "scope": "identify", "state": state,
        }))
        response.set_cookie("cspace_oauth", state, httponly=True, secure=secure, samesite="lax", max_age=600, path="/auth")
        return response

    @app.get("/auth/callback")
    async def callback(request: Request, code: str = "", state: str = ""):
        cookie = request.cookies.get("cspace_oauth", "")
        if not state or not cookie or not code or not hmac.compare_digest(state, cookie):
            raise HTTPException(400, "การเข้าสู่ระบบไม่ถูกต้อง กรุณาเริ่มใหม่")
        def consume_state():
            with registry.transaction() as connection:
                return connection.execute(delete(oauth_states).where(oauth_states.c.token_hash == token_hash(state),
                    oauth_states.c.expires_at > int(time.time()))).rowcount == 1
        if not await asyncio.to_thread(consume_state):
            raise HTTPException(400, "คำขอเข้าสู่ระบบหมดอายุหรือถูกใช้แล้ว")
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                token_response = await client.post("https://discord.com/api/oauth2/token", data={
                    "grant_type": "authorization_code", "code": code, "redirect_uri": settings.redirect_uri,
                    "client_id": settings.client_id, "client_secret": settings.client_secret,
                })
                token_response.raise_for_status()
                profile = await client.get("https://discord.com/api/v10/users/@me",
                    headers={"Authorization": "Bearer " + token_response.json()["access_token"]})
                profile.raise_for_status()
                identity = profile.json()
        except (httpx.HTTPError, KeyError, ValueError):
            raise HTTPException(502, "ติดต่อ Discord ไม่สำเร็จ กรุณาเริ่มเข้าสู่ระบบใหม่")
        if identity.get("id") not in settings.admin_ids:
            raise HTTPException(403, "บัญชี Discord นี้ไม่มีสิทธิ์จัดการรายชื่อ")
        token = await asyncio.to_thread(create_session, registry, identity["id"], identity.get("global_name") or identity.get("username") or "Admin")
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie("cspace_oauth", path="/auth")
        response.set_cookie("cspace_session", token, httponly=True, secure=secure, samesite="lax", max_age=8 * 3600)
        return response

    @app.post("/auth/logout")
    def logout(request: Request, user=Depends(require_user)):
        with registry.transaction() as connection:
            connection.execute(delete(sessions).where(sessions.c.token_hash == user["token_hash"]))
        response = JSONResponse({"ok": True})
        response.delete_cookie("cspace_session")
        return response

    @app.get("/api/roster")
    def roster(q: str = "", status: str = "", course: str = "", page: int = 1, user=Depends(require_user)):
        if class_mode:
            raise HTTPException(404, "ใช้หน้าจัดการคลาส")
        return registry.dashboard(q[:100], status[:20], course[:100], max(1, min(page, 10000)))

    @app.get("/api/classes")
    def class_list(user=Depends(require_user)):
        if not class_mode:
            raise HTTPException(404)
        return {"classes": registry.class_list()}

    @app.post("/api/classes")
    def add_class(body: ClassRequest, user=Depends(require_user)):
        if not class_mode:
            raise HTTPException(404)
        return registry.create_class(body.course, body.role_name, body.capacity, body.code)

    @app.get("/api/classes/{class_id}/members")
    def class_members(class_id: UUID, user=Depends(require_user)):
        if not class_mode:
            raise HTTPException(404)
        return {"members": registry.class_members(str(class_id))}

    @app.get("/api/template.csv")
    def template(user=Depends(require_user)):
        return Response(csv_bytes(["รหัสผู้เรียน", "ชื่อ-นามสกุล", "หลักสูตร", "ยศ"],
            [["ST001", "ชื่อ นามสกุลตัวอย่าง", "AI รุ่น 1", "ผู้เรียน AI รุ่น 1"]]), media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="cspace-roster-template.csv"'})

    @app.get("/api/export.csv")
    def export(user=Depends(require_user)):
        if class_mode:
            return Response(csv_bytes(["คลาส", "ยศ", "โค้ดร่วม", "จำนวนคน", "รับยศแล้ว", "กำลังยืนยัน", "ว่าง"],
                [[r[key] for key in ("course", "role_name", "access_code", "capacity", "used", "processing", "remaining")]
                 for r in registry.class_list()]), media_type="text/csv",
                headers={"Content-Disposition": 'attachment; filename="cspace-class-codes.csv"'})
        return Response(csv_bytes(["รหัสผู้เรียน", "ชื่อ-นามสกุล", "หลักสูตร", "ยศ", "รหัสยืนยันรายบุคคล", "สถานะ", "Discord ID"],
            [[value if value is not None else "" for value in row] for row in registry.export_rows()]), media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="cspace-access-codes.csv"'})

    @app.post("/api/import/preview")
    async def preview(request: Request, filename: str = "", user=Depends(require_user)):
        payload = bytearray()
        async for chunk in request.stream():
            payload.extend(chunk)
            if len(payload) > MAX_BYTES:
                raise HTTPException(413, "ไฟล์ต้องมีขนาดไม่เกิน 2 MB")
        rows, errors = await asyncio.to_thread(parse_upload, filename, bytes(payload))
        return await asyncio.to_thread(registry.stage_import, user["user_id"], Path(filename).name, rows, errors)

    @app.post("/api/import/commit")
    def commit(body: CommitRequest, user=Depends(require_user)):
        return registry.commit_import(user["user_id"], str(body.batch_id))

    return app
