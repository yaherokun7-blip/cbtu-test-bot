import asyncio
import logging
import time
from collections import defaultdict, deque

import discord
from discord.ext import tasks

from portal.config import Settings
from portal.store import Registry, RegistryError
from portal.classes import ClassRegistry


class DiscordRoster:
    def __init__(self, bot):
        self.bot = bot
        self.settings = Settings()
        self.class_mode = self.settings.verification_mode == "classes"
        self.registry = (ClassRegistry if self.class_mode else Registry)(self.settings.database_url)
        self.attempts = defaultdict(deque)

    async def start(self):
        await asyncio.to_thread(self.registry.initialize)
        self.reconcile.start()

    def stop(self):
        self.reconcile.cancel()

    def correct_guild(self, guild):
        return guild is not None and str(guild.id) == self.settings.guild_id

    async def verify(self, interaction, code, full_name=""):
        if not self.correct_guild(interaction.guild):
            return await interaction.followup.send("❌ ยังไม่ได้กำหนดเซิร์ฟเวอร์สำหรับรายชื่อชุดนี้ กรุณาติดต่อผู้ดูแล", ephemeral=True)
        now = time.monotonic()
        recent = self.attempts[interaction.user.id]
        while recent and recent[0] < now - 60:
            recent.popleft()
        if len(recent) >= 5:
            return await interaction.followup.send("กรุณารอหนึ่งนาทีแล้วลองใหม่", ephemeral=True)
        recent.append(now)
        claim = None
        try:
            entry = await asyncio.to_thread(self.registry.lookup_code, code.strip().upper())
            guild = interaction.guild
            role = guild.get_role(int(entry["role_id"])) if entry["role_id"] else None
            role = role or discord.utils.get(guild.roles, name=entry["role_name"])
            if not role:
                role = await guild.create_role(name=entry["role_name"], permissions=discord.Permissions.none(), reason="Learner course role")
            dangerous = ["administrator", "manage_guild", "manage_roles", "manage_channels", "kick_members",
                         "ban_members", "moderate_members", "manage_webhooks", "manage_messages"]
            if role.is_default() or role.managed or any(getattr(role.permissions, permission, False) for permission in dangerous):
                raise RegistryError("ยศนี้มีสิทธิ์ผู้ดูแลหรือเป็นยศระบบ จึงใช้เป็นยศผู้เรียนไม่ได้ กรุณาติดต่อผู้ดูแล")
            if role >= guild.me.top_role:
                raise RegistryError("ยศผู้เรียนอยู่สูงกว่าหรือเท่ากับบอต กรุณาให้ผู้ดูแลจัดลำดับยศก่อน")
            args = [code.strip().upper(), str(interaction.user.id), str(role.id)]
            if self.class_mode:
                args.append(full_name)
            claim = await asyncio.to_thread(self.registry.reserve, *args)
            if claim.get("already_verified"):
                return await interaction.followup.send("✅ บัญชีนี้รับยศของคลาสแล้ว ไม่ใช้โควต้าเพิ่ม", ephemeral=True)
            try:
                await asyncio.wait_for(interaction.user.add_roles(role, reason="Roster enrollment verification"), timeout=30)
            except TimeoutError:
                # A timed-out request might have succeeded remotely. Leave it for reconciliation.
                await interaction.followup.send("กำลังตรวจผลการมอบยศ กรุณารอสักครู่แล้วตรวจสถานะอีกครั้ง", ephemeral=True)
                return
            except Exception:
                await asyncio.to_thread(self.registry.finish_claim, claim["id"], claim["claim_id"], "บอตมอบยศไม่สำเร็จ กรุณาตรวจสิทธิ์และลำดับยศ แล้วลองยืนยันใหม่")
                raise RegistryError("มอบยศไม่สำเร็จ ยังไม่ได้ยืนยันสิทธิ์ กรุณาแจ้งผู้ดูแลแล้วลองใหม่")
            committed = await asyncio.to_thread(self.registry.finish_claim, claim["id"], claim["claim_id"])
            if not committed:
                raise RegistryError("คำขอนี้มีการเปลี่ยนแปลง กรุณาติดต่อผู้ดูแลเพื่อตรวจสอบ")
            await interaction.followup.send(
                f"✅ ยืนยันสิทธิ์ของ **{claim['full_name']}** ในหลักสูตร **{claim['course']}** แล้ว\nยศที่ได้รับ: {role.mention}",
                ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        except RegistryError as error:
            await interaction.followup.send(f"❌ {error}", ephemeral=True)
        except Exception:
            logging.exception("Roster verification failed for Discord user %s", interaction.user.id)
            await interaction.followup.send("❌ ยังไม่สามารถยืนยันผลได้ ระบบจะตรวจคำขอค้างให้อัตโนมัติ กรุณารอสักครู่หรือติดต่อผู้ดูแล", ephemeral=True)

    @tasks.loop(seconds=45)
    async def reconcile(self):
        if not self.bot.is_ready():
            return
        try:
            guild = self.bot.get_guild(int(self.settings.guild_id)) if self.settings.guild_id else None
            if not guild:
                return
            await asyncio.to_thread(self.registry.heartbeat)
            for entry in await asyncio.to_thread(self.registry.stale_claims):
                try:
                    member = await guild.fetch_member(int(entry["discord_id"]))
                    assigned = any(str(role.id) == entry["role_id"] for role in member.roles)
                except discord.NotFound:
                    assigned = False
                except discord.HTTPException:
                    continue
                error = None if assigned else "คำขอก่อนหน้าไม่พบยศที่มอบ สามารถยืนยันด้วยรหัสเดิมอีกครั้งได้"
                await asyncio.to_thread(self.registry.finish_claim, entry["id"], entry["claim_id"], error)
            # Bound in-memory attempt tracking for servers with many visitors.
            cutoff = time.monotonic() - 60
            self.attempts = defaultdict(deque, {key: values for key, values in self.attempts.items() if values and values[-1] > cutoff})
        except Exception:
            logging.exception("Roster reconciliation failed; will retry")

    @reconcile.before_loop
    async def before_reconcile(self):
        await self.bot.wait_until_ready()
