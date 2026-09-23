import asyncio
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import discord
from fastapi.testclient import TestClient
from sqlalchemy import select

from portal.classes import ClassRegistry
from portal.config import Settings
from portal.discord_roster import DiscordRoster
from portal.importer import parse_upload
from portal.store import RegistryError, sessions
from portal.web import create_app, create_session, token_hash


def allow(registry, course, role, names):
    rows = [dict(student_id=str(i), full_name=name, course=course, role_name=role, row=i+2) for i,name in enumerate(names)]
    staged = registry.stage_import("admin", "names.csv", rows, [])
    registry.commit_import("admin", staged["batch_id"])


class ClassTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.registry = ClassRegistry(f"sqlite:///{(Path(self.directory.name) / 'test.db').as_posix()}")
        self.registry.initialize()
        self.addCleanup(self.registry.engine.dispose)

    def test_40_simultaneous_requests_reserve_exactly_30_places(self):
        self.registry.create_class("Class A", "Students A", 30, "A30")
        allow(self.registry, "Class A", "Students A", [f"User {i}" for i in range(40)])
        def claim(index):
            try:
                return self.registry.reserve("A30", str(index), "900", f"User {index}")
            except RegistryError:
                return None
        with ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(claim, range(40)))
        self.assertEqual(sum(row is not None for row in results), 30)
        for row in filter(None, results):
            self.registry.finish_claim(row["id"], row["claim_id"])
        course = self.registry.class_list()[0]
        self.assertEqual((course["used"], course["remaining"]), (30, 0))

    def test_repeat_member_does_not_consume_more_and_failure_frees_slot(self):
        self.registry.create_class("A", "Students", 1, "AA")
        allow(self.registry, "A", "Students", ["Student One", "Student Two"])
        claim = self.registry.reserve("AA", "1", "900", "Student One")
        with self.assertRaises(RegistryError):
            self.registry.reserve("AA", "1", "900", "Student One")
        self.registry.finish_claim(claim["id"], claim["claim_id"], "Forbidden")
        self.assertEqual(self.registry.class_list()[0]["remaining"], 1)
        retry = self.registry.reserve("AA", "1", "900", "Student One")
        self.registry.finish_claim(retry["id"], retry["claim_id"])
        self.assertTrue(self.registry.reserve("AA", "1", "900", "Student One")["already_verified"])
        self.assertEqual(self.registry.class_list()[0]["used"], 1)
        with self.assertRaises(RegistryError):
            self.registry.reserve("AA", "2", "900", "Student Two")

    def test_departure_frees_slot_only_for_affected_role_and_class(self):
        for code, role in [("AA", "900"), ("BB", "901")]:
            self.registry.create_class(code, role, 1, code)
            allow(self.registry, code, role, ["Student One"])
            claim = self.registry.reserve(code, "1", role, "Student One")
            self.registry.finish_claim(claim["id"], claim["claim_id"])
        self.registry.mark_departed("1", ["900"])
        courses = {row["course"]:row for row in self.registry.class_list()}
        self.assertEqual(courses["AA"]["remaining"], 1)
        self.assertEqual(courses["BB"]["remaining"], 0)
        self.registry.mark_departed("1")
        self.assertTrue(all(row["remaining"] == 1 for row in self.registry.class_list()))

    def test_shared_code_requires_allowed_name_and_prevents_name_reuse(self):
        self.registry.create_class("A", "Students", 30, "AA")
        with self.assertRaises(RegistryError):
            self.registry.reserve("AA", "1", "900", "Student One")
        allow(self.registry, "A", "Students", ["Student One", "Student Two"])
        with self.assertRaises(RegistryError):
            self.registry.reserve("AA", "1", "900", "Outsider")
        claim = self.registry.reserve("AA", "1", "900", "  STUDENT   One ")
        with self.assertRaises(RegistryError):
            self.registry.reserve("AA", "2", "900", "Student One")
        with self.assertRaises(RegistryError):
            self.registry.reserve("AA", "1", "900", "Student Two")
        self.registry.finish_claim(claim["id"], claim["claim_id"])
        self.registry.mark_departed("1")
        with self.assertRaises(RegistryError):
            self.registry.reserve("AA", "2", "900", "Student One")
        self.assertEqual(self.registry.class_list()[0]["remaining"], 30)

    def test_duplicate_names_are_rejected_before_import(self):
        rows = [dict(student_id=str(i),full_name=name,course="A",role_name="Students",row=i+2)
                for i,name in enumerate(["Student One", "student  one"])]
        result = self.registry.stage_import("admin", "names.csv", rows, [])
        self.assertIsNone(result["batch_id"])
        self.assertEqual(result["error_count"], 1)

    def test_roster_import_generates_one_code_per_class_and_no_personal_codes(self):
        csv = b"student_id,full_name,course\n1,A,Class A\n2,B,Class A\n3,C,Class B\n"
        rows, errors = parse_upload("roster.csv", csv)
        staged = self.registry.stage_import("admin", "roster.csv", rows, errors)
        self.assertEqual(len(staged["rows"]), 2)
        with self.assertRaises(RegistryError):
            self.registry.commit_import("other", staged["batch_id"])
        self.assertEqual(self.registry.commit_import("admin", staged["batch_id"])["added"], 2)
        courses = self.registry.class_list()
        self.assertEqual(sorted(row["capacity"] for row in courses), [1, 2])
        self.assertEqual(len(self.registry.export_rows()), 0)
        with self.assertRaises(RegistryError):
            self.registry.commit_import("admin", staged["batch_id"])
        staged2 = self.registry.stage_import("admin", "roster.csv", rows, errors)
        self.assertEqual(self.registry.commit_import("admin", staged2["batch_id"])["skipped"], 2)
        self.assertEqual([r["access_code"] for r in self.registry.class_list()], [r["access_code"] for r in courses])

    def test_admin_web_create_export_and_member_status(self):
        settings = Settings(database_url=str(self.registry.engine.url), public_url="http://testserver",
                            production=False, verification_mode="classes", admin_ids=("1",))
        with TestClient(create_app(settings, self.registry)) as client:
            self.assertEqual(client.get("/api/classes").status_code, 401)
            token = create_session(self.registry, "1", "Admin")
            client.cookies.set("cspace_session", token)
            with self.registry.engine.connect() as conn:
                csrf = conn.scalar(select(sessions.c.csrf).where(sessions.c.token_hash == token_hash(token)))
            body = dict(course="A", role_name="Students", capacity=30, code="A30")
            self.assertEqual(client.post("/api/classes", json=body).status_code, 403)
            headers = {"origin":"http://testserver", "x-csrf-token":csrf}
            response = client.post("/api/classes", json=body, headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(client.post("/api/classes", json=body, headers=headers).status_code, 409)
            allow(self.registry, "A", "Students", ["Student"])
            claim = self.registry.reserve("A30", "2", "900", "Student")
            self.registry.finish_claim(claim["id"], claim["claim_id"])
            data = client.get('/api/classes/'+response.json()["id"]+'/members').json()
            self.assertEqual(data["members"][0]["status"], "verified")
            self.assertIn("A30", client.get("/api/export.csv").text)
            self.assertEqual(client.get("/api/roster").status_code, 404)
            self.assertIn("classes.js", client.get("/").text)


class ClassDiscordTests(unittest.IsolatedAsyncioTestCase):
    async def test_role_failure_releases_quota_and_success_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = ClassRegistry(f"sqlite:///{(Path(directory) / 'test.db').as_posix()}")
            registry.initialize()
            registry.create_class("A", "Students", 1, "A30")
            allow(registry, "A", "Students", ["Student"])
            class Role:
                id = 900
                name = "Students"
                mention = "<@&900>"
                managed = False
                permissions = discord.Permissions.none()
                def is_default(self): return False
                def __ge__(self, other): return False
            role = Role()
            cog = DiscordRoster.__new__(DiscordRoster)
            cog.registry = registry
            cog.settings = NS(guild_id="1")
            cog.class_mode = True
            from collections import defaultdict, deque
            cog.attempts = defaultdict(deque)
            user = NS(id=2, display_name="Student", add_roles=AsyncMock(side_effect=RuntimeError("Forbidden")))
            interaction = NS(guild=NS(id=1, roles=[role], me=NS(top_role=object())), user=user,
                             followup=NS(send=AsyncMock()))
            await cog.verify(interaction, "A30", "Student")
            self.assertEqual(registry.class_list()[0]["remaining"], 1)
            user.add_roles.side_effect = None
            await cog.verify(interaction, "A30", "Student")
            self.assertEqual(registry.class_list()[0]["used"], 1)
            await cog.verify(interaction, "A30", "Student")
            self.assertEqual(user.add_roles.await_count, 2)
            self.assertEqual(registry.class_list()[0]["used"], 1)
            registry.engine.dispose()
