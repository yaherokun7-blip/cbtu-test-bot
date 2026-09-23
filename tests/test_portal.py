import asyncio
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import select

from portal.config import Settings
from portal.importer import parse_upload, ImportErrorDetail
from portal.store import Registry, RegistryError, sessions
from portal.web import create_app, create_session, token_hash
from serve import supervise

ADMIN = "474113669295505409"
CSV = "รหัสผู้เรียน,ชื่อ-นามสกุล,หลักสูตร,ยศ\n001,ผู้เรียน ทดสอบ,AI,นักเรียน AI\n".encode("utf-8-sig")


class PortalTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.registry = Registry(f"sqlite:///{(Path(self.directory.name) / 'test.db').as_posix()}")
        self.registry.initialize()
        self.addCleanup(self.registry.engine.dispose)
        self.settings = Settings(database_url=str(self.registry.engine.url), public_url="http://testserver",
            production=False, admin_ids=(ADMIN,), client_id="", client_secret="", guild_id="1", verification_mode="roster")
        self.app = create_app(self.settings, self.registry)
        self.client = self.enterContext(TestClient(self.app))

    def login(self):
        token = create_session(self.registry, ADMIN, "Admin")
        self.client.cookies.set("cspace_session", token)
        with self.registry.engine.connect() as connection:
            csrf = connection.scalar(select(sessions.c.csrf).where(sessions.c.token_hash == token_hash(token)))
        return {"origin": "http://testserver", "x-csrf-token": csrf}

    def seed(self):
        rows, errors = parse_upload("test.csv", CSV)
        batch = self.registry.stage_import(ADMIN, "test.csv", rows, errors)
        self.registry.commit_import(ADMIN, batch["batch_id"])
        return self.registry.export_rows()[0][4]

    def test_csv_preview_commit_replay_export_and_logout(self):
        self.assertEqual(self.client.get("/api/roster").status_code, 401)
        headers = self.login()
        self.assertEqual(self.client.post("/api/import/preview?filename=a.csv", content=CSV).status_code, 403)
        preview = self.client.post("/api/import/preview?filename=a.csv", content=CSV, headers=headers)
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.json()["rows"][0]["student_id"], "001")
        body = {"batch_id": preview.json()["batch_id"]}
        commit = self.client.post("/api/import/commit", json=body, headers=headers)
        self.assertEqual(commit.json()["added"], 1)
        self.assertEqual(self.client.post("/api/import/commit", json=body, headers=headers).status_code, 409)
        self.assertEqual(self.client.get("/api/roster").json()["total"], 1)
        self.assertIn("CS-", self.client.get("/api/export.csv").text)
        self.assertEqual(self.client.post("/auth/logout", headers=headers).status_code, 200)
        self.assertEqual(self.client.get("/api/export.csv").status_code, 401)

    def test_xlsx_upload_and_formula_rejection(self):
        book = Workbook()
        sheet = book.active
        sheet.append(["student_id", "full_name", "course"])
        sheet.append(["002", "Example Student", "Excel"])
        buffer = io.BytesIO()
        book.save(buffer)
        preview = self.client.post("/api/import/preview?filename=test.xlsx", content=buffer.getvalue(), headers=self.login())
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.json()["new_count"], 1)
        sheet["B2"] = '=HYPERLINK("https://example.com")'
        buffer = io.BytesIO()
        book.save(buffer)
        with self.assertRaises(ImportErrorDetail):
            parse_upload("test.xlsx", buffer.getvalue())
        book.close()

    def test_claim_cannot_be_stolen_and_failed_claim_can_retry(self):
        code = self.seed()
        claim = self.registry.reserve(code, "111", "999")
        with self.assertRaises(RegistryError):
            self.registry.reserve(code, "222", "999")
        with self.assertRaises(RegistryError):
            self.registry.reserve(code, "111", "999")
        self.assertFalse(self.registry.finish_claim(claim["id"], "wrong"))
        self.assertTrue(self.registry.finish_claim(claim["id"], claim["claim_id"], "Missing permission"))
        retry = self.registry.reserve(code, "111", "999")
        self.assertTrue(self.registry.finish_claim(retry["id"], retry["claim_id"]))
        self.assertEqual(self.registry.dashboard()["counts"], {"verified": 1})

    def test_duplicate_upload_preserves_code_and_status(self):
        code = self.seed()
        claim = self.registry.reserve(code, "111", "999")
        self.registry.finish_claim(claim["id"], claim["claim_id"])
        rows, errors = parse_upload("again.csv", CSV)
        batch = self.registry.stage_import(ADMIN, "again.csv", rows, errors)
        with self.assertRaises(RegistryError):
            self.registry.commit_import("other-admin", batch["batch_id"])
        self.assertEqual(self.registry.commit_import(ADMIN, batch["batch_id"]), {"added": 0, "skipped": 1})
        exported = self.registry.export_rows()[0]
        self.assertEqual(exported[4:6], (code, "verified"))

    def test_oauth_denies_forged_state_and_revoked_admin(self):
        self.assertEqual(self.client.get("/auth/callback?code=x&state=y").status_code, 400)
        self.login()
        self.settings.admin_ids = ()
        self.assertEqual(self.client.get("/api/roster").status_code, 401)

    def test_health_requires_bot_connection_when_running_combined(self):
        self.app.state.discord_bot = SimpleNamespace(is_ready=lambda: False)
        self.assertEqual(self.client.get("/health").status_code, 503)
        self.app.state.discord_bot = SimpleNamespace(is_ready=lambda: True)
        self.assertEqual(self.client.get("/health").status_code, 200)

    def test_production_rejects_ephemeral_database(self):
        self.settings.production = True
        self.settings.public_url = "https://example.com"
        self.settings.client_id = "1"
        self.settings.client_secret = "example"
        with self.assertRaisesRegex(ValueError, "PostgreSQL"):
            self.settings.validate()


class SupervisionTests(unittest.IsolatedAsyncioTestCase):
    async def test_bot_failure_stops_web_and_propagates(self):
        server = SimpleNamespace(should_exit=False)
        async def web():
            while not server.should_exit:
                await asyncio.sleep(0.001)
        async def failed_bot():
            raise RuntimeError("Login failed")
        server.serve = web
        bot = SimpleNamespace(close=AsyncMock())
        with self.assertRaisesRegex(RuntimeError, "Login failed"):
            await supervise(server, failed_bot, bot)
        self.assertTrue(server.should_exit)
        bot.close.assert_awaited_once()

    async def test_web_shutdown_closes_bot(self):
        server = SimpleNamespace(should_exit=False, serve=AsyncMock())
        async def running_bot():
            await asyncio.Event().wait()
        bot = SimpleNamespace(close=AsyncMock())
        await supervise(server, running_bot, bot)
        bot.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
