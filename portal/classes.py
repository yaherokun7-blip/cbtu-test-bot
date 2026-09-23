"""Shared class codes with capacity reserved atomically before Discord I/O."""
import json
import re
import secrets
import time
import uuid

from sqlalchemy import Column, ForeignKey, Integer, String, Table, UniqueConstraint, delete, func, select, update
from sqlalchemy.exc import IntegrityError

from portal.store import Registry, RegistryError, imports, metadata

classes = Table("class_codes", metadata,
    Column("id", String(36), primary_key=True),
    Column("course", String(100), unique=True, nullable=False),
    Column("role_name", String(100), nullable=False),
    Column("access_code", String(50), unique=True, nullable=False),
    Column("capacity", Integer, nullable=False),
    Column("created_at", Integer, nullable=False))
members = Table("class_members", metadata,
    Column("id", String(36), primary_key=True),
    Column("class_id", ForeignKey("class_codes.id"), nullable=False),
    Column("discord_id", String(24), nullable=False),
    Column("full_name", String(100), nullable=False),
    Column("role_id", String(24)), Column("status", String(20), nullable=False),
    Column("claim_id", String(36)), Column("last_error", String(300)),
    Column("updated_at", Integer, nullable=False), Column("verified_at", Integer),
    UniqueConstraint("class_id", "discord_id"))

allowed_learners = Table("class_allowed_learners", metadata,
    Column("id", String(36), primary_key=True),
    Column("class_id", ForeignKey("class_codes.id"), nullable=False),
    Column("student_id", String(64), nullable=False),
    Column("full_name", String(100), nullable=False),
    Column("normalized_name", String(100), nullable=False),
    Column("discord_id", String(24)),
    UniqueConstraint("class_id", "student_id"), UniqueConstraint("class_id", "normalized_name"),
    UniqueConstraint("class_id", "discord_id"))


def normalize_name(value):
    import unicodedata
    return " ".join(unicodedata.normalize("NFC", value).split()).casefold()


class ClassRegistry(Registry):
    def _create(self, connection, course, role_name, capacity, code=""):
        course, role_name = course.strip(), role_name.strip()
        code = code.strip().upper() or "CS-" + secrets.token_hex(5).upper()
        if not course or len(course) > 100 or not role_name or len(role_name) > 100:
            raise RegistryError("กรอกชื่อคลาสและยศ ความยาวไม่เกิน 100 ตัวอักษร")
        if not 1 <= capacity <= 100000:
            raise RegistryError("จำนวนคนต้องอยู่ระหว่าง 1 ถึง 100,000")
        if not re.fullmatch(r"[A-Z0-9_-]{2,50}", code):
            raise RegistryError("โค้ดต้องเป็นตัวอักษรอังกฤษ ตัวเลข _ หรือ - จำนวน 2–50 ตัว")
        value = dict(id=str(uuid.uuid4()), course=course, role_name=role_name, capacity=capacity,
                     access_code=code, created_at=int(time.time()))
        connection.execute(classes.insert().values(**value))
        return value

    def create_class(self, course, role_name, capacity, code=""):
        try:
            with self.transaction() as connection:
                return self._create(connection, course, role_name, capacity, code)
        except IntegrityError as exc:
            raise RegistryError("ชื่อคลาสหรือโค้ดนี้มีอยู่แล้ว กรุณาใช้ชื่อใหม่") from exc

    def class_list(self):
        with self.engine.connect() as connection:
            rows = connection.execute(select(classes).order_by(classes.c.created_at, classes.c.course)).mappings().all()
            counts = connection.execute(select(members.c.class_id, members.c.status, func.count())
                .group_by(members.c.class_id, members.c.status)).all()
            allowed_counts = dict(connection.execute(select(allowed_learners.c.class_id, func.count())
                .group_by(allowed_learners.c.class_id)).all())
        totals = {}
        for class_id, status, count in counts:
            totals.setdefault(class_id, {})[status] = count
        result = []
        for row in rows:
            statuses = totals.get(row["id"], {})
            used = statuses.get("verified", 0)
            processing = statuses.get("processing", 0)
            result.append({**dict(row), "used": used, "processing": processing,
                           "remaining": max(0, row["capacity"] - used - processing),
                           "allowed_count": allowed_counts.get(row["id"], 0)})
        return result

    def class_members(self, class_id):
        with self.engine.connect() as connection:
            return [dict(row) for row in connection.execute(select(members).where(members.c.class_id == class_id)
                .order_by(members.c.updated_at.desc())).mappings()]

    def lookup_code(self, code):
        with self.engine.connect() as connection:
            row = connection.execute(select(classes).where(classes.c.access_code == code)).mappings().first()
            if not row:
                raise RegistryError("ไม่พบโค้ดคลาส กรุณาตรวจสอบโค้ดที่ได้รับ")
            return {**dict(row), "role_id": None}

    def reserve(self, code, discord_id, role_id, full_name=""):
        with self.transaction() as connection:
            # All admissions to a class serialize on this row in PostgreSQL.
            course = connection.execute(select(classes).where(classes.c.access_code == code)
                .with_for_update()).mappings().first()
            if not course:
                raise RegistryError("ไม่พบโค้ดคลาส")
            allowed = connection.execute(select(allowed_learners).where(allowed_learners.c.class_id == course["id"],
                allowed_learners.c.normalized_name == normalize_name(full_name))).mappings().first()
            if not allowed:
                raise RegistryError("ชื่อไม่ตรงกับรายชื่อของคลาส กรุณากรอกชื่อ–นามสกุลตามที่ลงทะเบียน หรือติดต่อผู้ดูแล")
            if allowed["discord_id"] and allowed["discord_id"] != discord_id:
                raise RegistryError("รายชื่อนี้ผูกกับบัญชี Discord อื่นแล้ว กรุณาติดต่อผู้ดูแล")
            bound = connection.execute(select(allowed_learners.c.id).where(allowed_learners.c.class_id == course["id"],
                allowed_learners.c.discord_id == discord_id, allowed_learners.c.id != allowed["id"])).first()
            if bound:
                raise RegistryError("บัญชีนี้ผูกกับชื่ออื่นในคลาสแล้ว กรุณาติดต่อผู้ดูแล")
            member = connection.execute(select(members).where(members.c.class_id == course["id"],
                members.c.discord_id == discord_id)).mappings().first()
            if member and member["status"] == "verified":
                return {**dict(member), "course": course["course"], "already_verified": True}
            if member and member["status"] == "processing":
                raise RegistryError("กำลังมอบยศให้บัญชีนี้ กรุณารอสักครู่")
            occupied = connection.scalar(select(func.count()).select_from(members).where(
                members.c.class_id == course["id"], members.c.status.in_(["verified", "processing"])))
            if occupied >= course["capacity"]:
                raise RegistryError("คลาสนี้ครบจำนวนแล้ว กรุณาติดต่อผู้ดูแล")
            connection.execute(update(allowed_learners).where(allowed_learners.c.id == allowed["id"]).values(discord_id=discord_id))
            claim = dict(class_id=course["id"], discord_id=discord_id, full_name=allowed["full_name"],
                         role_id=role_id, status="processing", claim_id=str(uuid.uuid4()),
                         last_error=None, verified_at=None, updated_at=int(time.time()))
            entry_id = member["id"] if member else str(uuid.uuid4())
            if member:
                connection.execute(update(members).where(members.c.id == entry_id).values(**claim))
            else:
                connection.execute(members.insert().values(id=entry_id, **claim))
            return {**claim, "id": entry_id, "course": course["course"]}

    def finish_claim(self, entry_id, claim_id, error=None):
        with self.transaction() as connection:
            result = connection.execute(update(members).where(members.c.id == entry_id,
                members.c.claim_id == claim_id, members.c.status == "processing").values(
                status="error" if error else "verified", last_error=error, claim_id=None,
                updated_at=int(time.time()), verified_at=None if error else int(time.time())))
            return result.rowcount == 1

    def stale_claims(self):
        with self.engine.connect() as connection:
            return [dict(row) for row in connection.execute(select(members).where(
                members.c.status == "processing", members.c.updated_at < int(time.time()) - 120)).mappings()]

    def mark_departed(self, discord_id, removed_role_ids=None):
        conditions = [members.c.discord_id == discord_id, members.c.status == "verified"]
        if removed_role_ids is not None:
            conditions.append(members.c.role_id.in_(removed_role_ids))
        with self.transaction() as connection:
            connection.execute(update(members).where(*conditions).values(status="departed", updated_at=int(time.time())))

    def stage_import(self, owner, filename, rows, errors):
        # Each roster provides the allowed names and a default capacity for new classes.
        groups = {}
        errors = list(errors)
        for row in rows:
            group = groups.setdefault(row["course"], dict(course=row["course"], role_name=row["role_name"], capacity=0, learners=[]))
            group["capacity"] += 1
            normalized = normalize_name(row["full_name"])
            if any(person["normalized_name"] == normalized for person in group["learners"]):
                errors.append({"row": row["row"], "message": "ชื่อซ้ำในคลาสเดียวกัน กรุณาให้ชื่อที่แยกบุคคลได้ก่อนนำเข้า"})
            group["learners"].append(dict(student_id=row["student_id"], full_name=row["full_name"], normalized_name=normalized))
            if group["role_name"] != row["role_name"]:
                errors.append({"row": row["row"], "message": "คลาสเดียวกันต้องใช้ยศเดียวกัน"})
        values = list(groups.values())
        batch_id = str(uuid.uuid4()) if values and not errors else None
        with self.transaction() as connection:
            connection.execute(delete(imports).where(imports.c.committed_at.is_(None),
                imports.c.created_at < int(time.time()) - 1800))
            if batch_id:
                connection.execute(imports.insert().values(id=batch_id, owner_id=owner, filename=filename[:255],
                    payload=json.dumps({"classes": values}, ensure_ascii=False), created_at=int(time.time())))
        return dict(batch_id=batch_id, filename=filename, rows=values, count=len(rows),
                    errors=errors[:100], error_count=len(errors))

    def commit_import(self, owner, batch_id):
        try:
            with self.transaction() as connection:
                batch = connection.execute(select(imports).where(imports.c.id == batch_id).with_for_update()).mappings().first()
                if not batch or batch["owner_id"] != owner or batch["created_at"] < int(time.time()) - 1800 or batch["committed_at"]:
                    raise RegistryError("รายการหมดอายุหรือบันทึกแล้ว กรุณาอัปโหลดใหม่")
                payload = json.loads(batch["payload"])
                if not isinstance(payload, dict) or "classes" not in payload:
                    raise RegistryError("รูปแบบรายการเปลี่ยนแล้ว กรุณาอัปโหลดใหม่")
                added, skipped = 0, 0
                for row in payload["classes"]:
                    old = connection.execute(select(classes).where(classes.c.course == row["course"])).mappings().first()
                    if old:
                        if old["role_name"] != row["role_name"]:
                            raise RegistryError("คลาสเดิมใช้ยศต่างกัน ระบบจะไม่ทับค่าเดิม")
                        skipped += 1
                        course = old
                    else:
                        course = self._create(connection, row["course"], row["role_name"], row["capacity"])
                        added += 1
                    for person in row["learners"]:
                        existing = connection.execute(select(allowed_learners).where(
                            allowed_learners.c.class_id == course["id"], allowed_learners.c.student_id == person["student_id"])).mappings().first()
                        if existing:
                            if existing["normalized_name"] != person["normalized_name"]:
                                raise RegistryError("รหัสผู้เรียนเดิมมีชื่อเปลี่ยนไป กรุณาตรวจข้อมูล ไม่สามารถทับชื่อที่ผูกบัญชีแล้ว")
                            continue
                        connection.execute(allowed_learners.insert().values(id=str(uuid.uuid4()), class_id=course["id"], **person))
                connection.execute(update(imports).where(imports.c.id == batch_id).values(committed_at=int(time.time()), payload="{}"))
                return {"added": added, "skipped": skipped}
        except IntegrityError as exc:
            raise RegistryError("คลาสมีการเปลี่ยนแปลงระหว่างบันทึก กรุณาอัปโหลดใหม่") from exc
