"""Shared registry. Short SQL transactions reserve a claim before Discord I/O."""
import json
import secrets
import time
import uuid
from contextlib import contextmanager

from sqlalchemy import (Column, ForeignKey, Integer, MetaData, String, Table, Text,
                        UniqueConstraint, and_, create_engine, delete, func, or_, select, text, update)
from sqlalchemy.exc import IntegrityError

metadata = MetaData()
learners = Table("learners", metadata,
    Column("id", String(36), primary_key=True), Column("student_id", String(64), unique=True, nullable=False),
    Column("full_name", String(100), nullable=False), Column("discord_id", String(24), unique=True))
enrollments = Table("enrollments", metadata,
    Column("id", String(36), primary_key=True), Column("learner_id", ForeignKey("learners.id"), nullable=False),
    Column("course", String(100), nullable=False), Column("role_name", String(100), nullable=False),
    Column("role_id", String(24)), Column("access_code", String(32), unique=True, nullable=False),
    Column("status", String(20), nullable=False, default="pending"), Column("claim_id", String(36)),
    Column("last_error", String(300)), Column("created_at", Integer, nullable=False),
    Column("updated_at", Integer, nullable=False), Column("verified_at", Integer),
    UniqueConstraint("learner_id", "course"))
imports = Table("imports", metadata,
    Column("id", String(36), primary_key=True), Column("owner_id", String(24), nullable=False),
    Column("filename", String(255), nullable=False), Column("payload", Text, nullable=False),
    Column("created_at", Integer, nullable=False), Column("committed_at", Integer))
sessions = Table("sessions", metadata,
    Column("token_hash", String(64), primary_key=True), Column("user_id", String(24), nullable=False),
    Column("display_name", String(100), nullable=False), Column("csrf", String(64), nullable=False),
    Column("expires_at", Integer, nullable=False))
oauth_states = Table("oauth_states", metadata,
    Column("token_hash", String(64), primary_key=True), Column("expires_at", Integer, nullable=False))
service_status = Table("service_status", metadata,
    Column("name", String(50), primary_key=True), Column("updated_at", Integer, nullable=False),
    Column("detail", String(200)))


class RegistryError(ValueError):
    pass


class Registry:
    def __init__(self, url):
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+psycopg://", 1)
        elif url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        self.engine = create_engine(url, pool_pre_ping=True,
            connect_args={"check_same_thread": False, "timeout": 30} if url.startswith("sqlite:") else {})

    @contextmanager
    def transaction(self):
        with self.engine.connect() as connection:
            if self.engine.dialect.name == "sqlite":
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            else:
                connection.begin()
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def initialize(self):
        with self.transaction() as connection:
            if self.engine.dialect.name == "postgresql":
                connection.execute(text("SELECT pg_advisory_xact_lock(474113669)"))
            metadata.create_all(connection)

    def check_rows(self, connection, rows):
        names = {r.student_id: r for r in connection.execute(select(learners)).all()}
        existing = {(r.learner_id, r.course): r for r in connection.execute(select(enrollments)).all()}
        errors, skipped = [], 0
        for row in rows:
            learner = names.get(row["student_id"])
            if learner and learner.full_name != row["full_name"]:
                errors.append({"row": row["row"], "message": "รหัสผู้เรียนนี้มีชื่ออื่นอยู่ในระบบแล้ว กรุณาตรวจสอบ"})
            elif learner and (learner.id, row["course"]) in existing:
                enrollment = existing[(learner.id, row["course"])]
                if enrollment.role_name != row["role_name"]:
                    errors.append({"row": row["row"], "message": "รายการเดิมใช้ยศต่างกัน ระบบจะไม่เปลี่ยนสิทธิ์จากการอัปโหลด"})
                else:
                    skipped += 1
        return errors, skipped

    def stage_import(self, owner, filename, rows, errors):
        now = int(time.time())
        with self.transaction() as connection:
            extra, skipped = self.check_rows(connection, rows)
            errors = errors + extra
            batch_id = str(uuid.uuid4())
            connection.execute(delete(imports).where(imports.c.committed_at.is_(None), imports.c.created_at < now - 1800))
            if not errors and rows:
                connection.execute(imports.insert().values(id=batch_id, owner_id=owner, filename=filename[:255],
                    payload=json.dumps(rows, ensure_ascii=False), created_at=now))
            else:
                batch_id = None
        return {"batch_id": batch_id, "filename": filename, "rows": rows[:100], "count": len(rows),
                "new_count": len(rows) - skipped, "skipped": skipped, "errors": errors[:100], "error_count": len(errors)}

    def commit_import(self, owner, batch_id):
        now = int(time.time())
        try:
            with self.transaction() as connection:
                batch = connection.execute(select(imports).where(imports.c.id == batch_id).with_for_update()).mappings().first()
                if not batch or batch["owner_id"] != owner or batch["created_at"] < now - 1800:
                    raise RegistryError("รายการหมดอายุหรือไม่พบ กรุณาอัปโหลดใหม่")
                if batch["committed_at"]:
                    raise RegistryError("รายการนี้บันทึกแล้ว กรุณารีเฟรชหน้ารายชื่อ")
                rows = json.loads(batch["payload"])
                errors, skipped = self.check_rows(connection, rows)
                if errors:
                    raise RegistryError(errors[0]["message"])
                people = {r.student_id: r.id for r in connection.execute(select(learners.c.student_id, learners.c.id))}
                existing = {(r.learner_id, r.course) for r in connection.execute(select(enrollments.c.learner_id, enrollments.c.course))}
                added = 0
                for row in rows:
                    person_id = people.get(row["student_id"])
                    if person_id is None:
                        person_id = str(uuid.uuid4())
                        connection.execute(learners.insert().values(id=person_id, student_id=row["student_id"], full_name=row["full_name"]))
                        people[row["student_id"]] = person_id
                    if (person_id, row["course"]) in existing:
                        continue
                    connection.execute(enrollments.insert().values(id=str(uuid.uuid4()), learner_id=person_id,
                        course=row["course"], role_name=row["role_name"], access_code="CS-" + secrets.token_hex(8).upper(),
                        status="pending", created_at=now, updated_at=now))
                    existing.add((person_id, row["course"]))
                    added += 1
                # Keep audit metadata, not a second copy of every person's details.
                connection.execute(update(imports).where(imports.c.id == batch_id).values(committed_at=now, payload="[]"))
            return {"added": added, "skipped": skipped}
        except IntegrityError as exc:
            raise RegistryError("รายชื่อมีการเปลี่ยนแปลงระหว่างบันทึก กรุณาอัปโหลดเพื่อตรวจใหม่") from exc

    def dashboard(self, query="", status="", course="", page=1):
        joined = enrollments.join(learners)
        conditions = []
        if query:
            pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            conditions.append(or_(learners.c.full_name.ilike(pattern, escape="\\"), learners.c.student_id.ilike(pattern, escape="\\")))
        if status:
            conditions.append(enrollments.c.status == status)
        if course:
            conditions.append(enrollments.c.course == course)
        with self.engine.connect() as connection:
            counts = dict(connection.execute(select(enrollments.c.status, func.count()).group_by(enrollments.c.status)).all())
            total = connection.scalar(select(func.count()).select_from(joined).where(*conditions))
            result = connection.execute(select(enrollments.c.id, learners.c.student_id, learners.c.full_name,
                learners.c.discord_id, enrollments.c.course, enrollments.c.role_name, enrollments.c.status,
                enrollments.c.verified_at, enrollments.c.last_error).select_from(joined).where(*conditions)
                .order_by(enrollments.c.created_at.desc(), learners.c.student_id, enrollments.c.course)
                .offset((page - 1) * 50).limit(50)).mappings().all()
            heartbeat = connection.execute(select(service_status).where(service_status.c.name == "discord")).mappings().first()
            courses = connection.scalars(select(enrollments.c.course).distinct().order_by(enrollments.c.course)).all()
            people = connection.scalar(select(func.count()).select_from(learners))
        return {"rows": [dict(row) for row in result], "total": total, "page": page, "page_size": 50,
                "counts": counts, "learner_count": people, "courses": list(courses),
                "bot": {"online": bool(heartbeat and heartbeat["updated_at"] > time.time() - 120),
                        "last_seen": heartbeat["updated_at"] if heartbeat else None}}

    def export_rows(self):
        with self.engine.connect() as connection:
            return connection.execute(select(learners.c.student_id, learners.c.full_name, enrollments.c.course,
                enrollments.c.role_name, enrollments.c.access_code, enrollments.c.status, learners.c.discord_id)
                .select_from(enrollments.join(learners)).order_by(learners.c.student_id, enrollments.c.course)).all()

    def reserve(self, code, discord_id, role_id):
        now = int(time.time())
        try:
            with self.transaction() as connection:
                entry = connection.execute(select(enrollments).where(enrollments.c.access_code == code).with_for_update()).mappings().first()
                if not entry:
                    raise RegistryError("ไม่พบรหัสในรายชื่อผู้มีสิทธิ์ กรุณาตรวจสอบรหัสรายบุคคล")
                learner = connection.execute(select(learners).where(learners.c.id == entry["learner_id"]).with_for_update()).mappings().one()
                if learner["discord_id"] and learner["discord_id"] != discord_id:
                    raise RegistryError("สิทธิ์นี้ผูกกับบัญชี Discord อื่นแล้ว กรุณาติดต่อผู้ดูแล")
                bound = connection.execute(select(learners.c.id).where(learners.c.discord_id == discord_id, learners.c.id != learner["id"])).first()
                if bound:
                    raise RegistryError("บัญชี Discord นี้ผูกกับผู้เรียนคนอื่นแล้ว กรุณาติดต่อผู้ดูแล")
                if entry["status"] == "processing":
                    raise RegistryError("กำลังตรวจสอบคำขอก่อนหน้า กรุณารอสักครู่แล้วลองใหม่")
                claim_id = str(uuid.uuid4())
                connection.execute(update(learners).where(learners.c.id == learner["id"]).values(discord_id=discord_id))
                connection.execute(update(enrollments).where(enrollments.c.id == entry["id"]).values(
                    status="processing", claim_id=claim_id, role_id=role_id, updated_at=now, last_error=None))
                return {**dict(entry), "claim_id": claim_id, "full_name": learner["full_name"], "discord_id": discord_id}
        except IntegrityError as exc:
            raise RegistryError("บัญชีนี้กำลังผูกกับผู้เรียนอื่น กรุณาตรวจสอบและลองใหม่") from exc

    def lookup_code(self, code):
        with self.engine.connect() as connection:
            row = connection.execute(select(enrollments).where(enrollments.c.access_code == code)).mappings().first()
            if not row:
                raise RegistryError("ไม่พบรหัสในรายชื่อผู้มีสิทธิ์")
            return dict(row)

    def finish_claim(self, entry_id, claim_id, error=None):
        now = int(time.time())
        values = {"status": "error" if error else "verified", "last_error": error,
                  "updated_at": now, "claim_id": None, "verified_at": None if error else now}
        with self.transaction() as connection:
            changed = connection.execute(update(enrollments).where(enrollments.c.id == entry_id,
                enrollments.c.claim_id == claim_id, enrollments.c.status == "processing").values(**values))
            return changed.rowcount == 1

    def stale_claims(self):
        with self.engine.connect() as connection:
            return [dict(row) for row in connection.execute(select(enrollments, learners.c.discord_id)
                .select_from(enrollments.join(learners)).where(enrollments.c.status == "processing",
                    enrollments.c.updated_at < int(time.time()) - 120)).mappings()]

    def mark_departed(self, discord_id, removed_role_ids=None):
        with self.transaction() as connection:
            person = connection.scalar(select(learners.c.id).where(learners.c.discord_id == discord_id))
            if not person:
                return
            conditions = [enrollments.c.learner_id == person, enrollments.c.status == "verified"]
            if removed_role_ids is not None:
                conditions.append(enrollments.c.role_id.in_(removed_role_ids))
            connection.execute(update(enrollments).where(*conditions).values(status="departed", updated_at=int(time.time())))

    def heartbeat(self):
        with self.transaction() as connection:
            now = int(time.time())
            if not connection.execute(update(service_status).where(service_status.c.name == "discord").values(updated_at=now)).rowcount:
                connection.execute(service_status.insert().values(name="discord", updated_at=now))
