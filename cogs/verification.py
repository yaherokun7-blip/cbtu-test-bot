import os
import json
import datetime
import logging
import asyncio
import tempfile
import discord
from discord import app_commands
from discord.ext import commands
from utils.checks import is_authorized

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
DATA_FILE = os.path.join(DATA_DIR, "verification_data.json")
# All read-modify-write paths share this lock, including chat commands.
# This JSON backend supports one running bot process.
data_lock = asyncio.Lock()

class StorageError(RuntimeError):
    pass

def load_data() -> dict:
    if not os.path.exists(DATA_FILE):
        return {"codes": {}, "verified_users": []}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("codes"), dict) or not isinstance(data.get("verified_users"), list):
            raise ValueError("Invalid verification data structure")
        return data
    except Exception as e:
        logging.error(f"Error loading verification data: {e}")
        raise StorageError("ไม่สามารถอ่านข้อมูลยืนยันตัวตนได้ กรุณาติดต่อผู้ดูแล") from e

def save_data(data: dict):
    temporary = None
    try:
        directory = os.path.dirname(DATA_FILE)
        os.makedirs(directory, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, delete=False) as f:
            temporary = f.name
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, DATA_FILE)
    except Exception as e:
        logging.error(f"Error saving verification data: {e}")
        raise StorageError("ไม่สามารถบันทึกข้อมูลยืนยันตัวตนได้ กรุณาติดต่อผู้ดูแล") from e
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


# --- MODAL FOR SECURE VERIFICATION INPUT ---
class VerifyModal(discord.ui.Modal, title="🛡️ ยืนยันตัวตนเข้าร่วมเซิร์ฟเวอร์"):
    def __init__(self, cog: "VerificationCog"):
        super().__init__()
        self.cog = cog
        if os.getenv("VERIFICATION_MODE", "legacy") in {"roster", "classes"}:
            if os.getenv("VERIFICATION_MODE") == "roster":
                self.remove_item(self.real_name)
            else:
                self.real_name.label = "ชื่อ–นามสกุลตามรายชื่อที่ลงทะเบียน"
                self.real_name.max_length = 100
            self.code.label = "โค้ดคลาส" if os.getenv("VERIFICATION_MODE") == "classes" else "รหัสยืนยันรายบุคคล"

    real_name = discord.ui.TextInput(
        label="ชื่อ - นามสกุลจริง (จัดเก็บเพื่อเช็คชื่อ)",
        placeholder="ตัวอย่าง: นายสมชาย ใจดี",
        min_length=3,
        max_length=50,
        required=True
    )

    code = discord.ui.TextInput(
        label="รหัสยืนยันจากกลุ่ม LINE",
        placeholder="กรอกรหัสที่ได้รับจากผู้ดูแลระบบ",
        min_length=2,
        max_length=50,
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if interaction.guild is None:
            return await interaction.followup.send("❌ กรุณายืนยันตัวตนภายในเซิร์ฟเวอร์", ephemeral=True)
        if os.getenv("VERIFICATION_MODE", "legacy") in {"roster", "classes"}:
            roster = getattr(self.cog, "roster", None)
            if roster is None:
                return await interaction.followup.send("❌ ระบบรายชื่อยังไม่พร้อม กรุณาติดต่อผู้ดูแล", ephemeral=True)
            if os.getenv("VERIFICATION_MODE") == "classes":
                return await roster.verify(interaction, self.code.value, self.real_name.value)
            return await roster.verify(interaction, self.code.value)
        try:
            async with data_lock:
                await self.verify_locked(interaction)
        except StorageError as error:
            await interaction.followup.send(f"❌ {error} ยังไม่ได้ยืนยันสำเร็จ", ephemeral=True)

    async def verify_locked(self, interaction: discord.Interaction):

        user_code = self.code.value.strip().upper()
        full_name = self.real_name.value.strip()
        guild = interaction.guild
        member = interaction.user

        data = load_data()
        codes = data.get("codes", {})

        # 1. ตรวจสอบว่ารหัสถูกต้องหรือไม่
        if user_code not in codes:
            embed = discord.Embed(
                title="❌ รหัสยืนยันไม่ถูกต้อง",
                description="ไม่พบรหัสนี้ในระบบ กรุณาตรวจสอบรหัสที่ได้รับจากกลุ่ม LINE อีกครั้งครับ",
                color=discord.Color.red()
            )
            return await interaction.followup.send(embed=embed, ephemeral=True)

        # 1.1 ดึงข้อมูลรหัส
        code_info = codes[user_code]
        max_uses = code_info.get("max_uses", 1)
        used_count = code_info.get("used_count", 0)

        # 1.2 ตรวจสอบว่าผู้ใช้ได้รับการยืนยันตัวตนอยู่แล้วหรือไม่ (ป้องกันการกินโควต้าซ้ำ)
        target_role_name = code_info.get("role_name", "สมาชิก (Verified)")
        role = discord.utils.get(guild.roles, name=target_role_name)
        existing_record = next((u for u in data.get("verified_users", []) if u.get("user_id") == member.id), None)
        if existing_record or (role and role in member.roles):
            embed = discord.Embed(
                title="ℹ️ คุณได้รับการยืนยันตัวตนอยู่แล้ว",
                description=f"คุณมียศ **{role.name if role else target_role_name}** ในเซิร์ฟเวอร์อยู่แล้วครับ\n"
                            f"หากต้องการแก้ไขชื่อหรือข้อมูล กรุณาติดต่อผู้ดูแลระบบครับ",
                color=discord.Color.blue()
            )
            return await interaction.followup.send(embed=embed, ephemeral=True)

        # 2. ตรวจสอบโควต้า
        if used_count >= max_uses:
            embed = discord.Embed(
                title="⚠️ โควต้าของรหัสนี้เต็มแล้ว",
                description=f"รหัส **`{user_code}`** ถูกใช้งานครบจำนวนที่กำหนดแล้ว ({max_uses}/{max_uses} คน)\n"
                            f"กรุณาติดต่อผู้ดูแลระบบเพื่อขอรับรหัสใหม่ครับ",
                color=discord.Color.gold()
            )
            return await interaction.followup.send(embed=embed, ephemeral=True)

        # 3. ตรวจสอบหรือสร้างยศเป้าหมาย
        if not role:
            try:
                role = await guild.create_role(name=target_role_name, color=discord.Color.green(), reason="Auto-created for Verified Member")
            except Exception as e:
                logging.error(f"Failed to create role: {e}")
                return await interaction.followup.send("❌ สร้างยศไม่สำเร็จ กรุณาแจ้งผู้ดูแล ยังไม่หักโควต้า", ephemeral=True)

        # 4. มอบยศ
        if role:
            try:
                await member.add_roles(role, reason=f"ยืนยันตัวตนสำเร็จด้วยรหัส {user_code}")
            except Exception as e:
                logging.error(f"Failed to add role to {member}: {e}")
                return await interaction.followup.send("❌ มอบยศไม่สำเร็จ กรุณาแจ้งผู้ดูแล ยังไม่หักโควต้าและสามารถลองใหม่ได้", ephemeral=True)

        # 5. อัปเดตสถิติการใช้งานและบันทึกข้อมูล (เก็บชื่อจริงเพื่อใช้ตรวจสอบย้อนหลัง)
        codes[user_code]["used_count"] = used_count + 1
        record = {
            "user_id": member.id,
            "username": f"{member.name} ({member.display_name})",
            "real_name": full_name,
            "code_used": user_code,
            "verified_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }
        data["verified_users"].append(record)
        try:
            save_data(data)
        except StorageError:
            # Compensate the Discord mutation if the local commit failed.
            try:
                await member.remove_roles(role, reason="Verification persistence failed")
            except Exception:
                logging.exception("Could not roll back verification role for user %s", member.id)
                await interaction.followup.send("❌ บันทึกไม่สำเร็จและถอดยศคืนไม่ได้ กรุณาให้ผู้ดูแลตรวจสอบยศก่อนลองใหม่", ephemeral=True)
                return
            raise

        # 6. ตอบกลับผู้ใช้งานแบบ Ephemeral (เห็นคนเดียว)
        embed_success = discord.Embed(
            title="🎉 ยืนยันตัวตนสำเร็จเรียบร้อย!",
            description=f"ยินดีต้อนรับคุณ **{full_name}** เข้าสู่เซิร์ฟเวอร์!\n\n"
                        f"🏷️ **ยศที่ได้รับ:** {role.mention if role else target_role_name}\n\n"
                        f"คุณสามารถเข้าถึงห้องแชทและห้องเรียนหลักสูตรต่างๆ ได้แล้วครับ 🚀",
            color=discord.Color.green()
        )
        await interaction.followup.send(embed=embed_success, ephemeral=True)

        # 8. ส่งบันทึก (Audit Log) เข้าห้องแอดมิน (#bot-log หรือ #staff-chat)
        cog_instance = self.cog or interaction.client.get_cog("Verification")
        if cog_instance:
            await cog_instance.send_audit_log(
                guild=guild,
                member=member,
                real_name=full_name,
                code=user_code,
                current_uses=used_count + 1,
                max_uses=max_uses
            )


# --- PERSISTENT BUTTON VIEW ---
class VerificationButtonView(discord.ui.View):
    def __init__(self, cog: "VerificationCog" = None):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="✅ ยืนยันตัวตน (กดที่นี่)",
        style=discord.ButtonStyle.success,
        custom_id="verification_persistent_modal_button"
    )
    async def verify_button_click(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog_instance = self.cog or interaction.client.get_cog("Verification")
        await interaction.response.send_modal(VerifyModal(cog_instance))


# --- COG IMPLEMENTATION ---
class VerificationCog(commands.Cog, name="Verification"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Register persistent view on startup
        self.bot.add_view(VerificationButtonView(self))
        self.roster = None

    async def cog_load(self):
        if os.getenv("VERIFICATION_MODE", "legacy") in {"roster", "classes"}:
            from portal.discord_roster import DiscordRoster
            self.roster = DiscordRoster(self.bot)
            await self.roster.start()

    def cog_unload(self):
        if self.roster:
            self.roster.stop()

    async def send_audit_log(self, guild: discord.Guild, member: discord.Member, real_name: str, code: str, current_uses: int, max_uses: int):
        """ส่งบันทึกการยืนยันตัวตนเข้าห้อง log สำหรับทีมงาน"""
        target_channel = (
            discord.utils.get(guild.text_channels, name="bot-log") or
            discord.utils.get(guild.text_channels, name="staff-chat") or
            discord.utils.get(guild.text_channels, name="staff-command")
        )
        if not target_channel:
            return

        embed = discord.Embed(
            title="🛡️ บันทึกการยืนยันตัวตนใหม่ (Member Verified)",
            color=discord.Color.blue(),
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        if member.avatar:
            embed.set_thumbnail(url=member.avatar.url)

        embed.add_field(name="👤 สมาชิก", value=f"{member.mention} (`{member.id}`)", inline=True)
        embed.add_field(name="📝 ชื่อ-นามสกุลจริง", value=f"**{real_name}**", inline=True)
        embed.add_field(name="🔑 รหัสที่ใช้", value=f"`{code}` (ใช้ไปแล้ว **{current_uses}/{max_uses}** คน)", inline=False)
        embed.set_footer(text="ระบบรักษาความปลอดภัย 3 ชั้น | Manager Bot")

        try:
            await target_channel.send(embed=embed)
        except Exception as e:
            logging.error(f"Failed to send audit log: {e}")

    # --- AUTO QUOTA RESTORATION LISTENERS ---

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        """เมื่อมีการลบหรือถอดยศยืนยันตัวตนออกจากสมาชิก ให้คืนโควต้าให้อัตโนมัติ"""
        removed_roles = [r for r in before.roles if r not in after.roles]
        if self.roster:
            if self.roster.correct_guild(after.guild) and removed_roles:
                await asyncio.to_thread(self.roster.registry.mark_departed, str(after.id), [str(role.id) for role in removed_roles])
            return
        if not removed_roles:
            return

        async with data_lock:
            data = load_data()
            verified_users = data.get("verified_users", [])
            user_record = next((u for u in verified_users if u.get("user_id") == after.id), None)
            if not user_record:
                return

            code_used = user_record.get("code_used")
            codes = data.get("codes", {})
            target_role_name = codes.get(code_used, {}).get("role_name", "สมาชิก (Verified)")

            if not any(r.name == target_role_name or r.name == "สมาชิก (Verified)" for r in removed_roles):
                return
            if code_used in codes:
                codes[code_used]["used_count"] = max(0, codes[code_used].get("used_count", 1) - 1)
            data["verified_users"] = [u for u in verified_users if u.get("user_id") != after.id]
            save_data(data)

        new_used = codes.get(code_used, {}).get("used_count", 0)
        max_u = codes.get(code_used, {}).get("max_uses", 0)
        logging.info(f"🔄 ถอดยศจาก {after.name}: คืนโควต้ารหัส {code_used} (เหลือคนใช้ {new_used}/{max_u})")

        # ส่งแจ้งเตือนเข้าห้องแอดมิน
        target_channel = (
            discord.utils.get(after.guild.text_channels, name="bot-log") or
            discord.utils.get(after.guild.text_channels, name="staff-chat")
        )
        if target_channel:
            embed = discord.Embed(
                title="🔄 คืนโควต้ารหัสอัตโนมัติ (Role Removed)",
                description=f"มีการถอดยศจาก {after.mention}\n"
                            f"• **รหัสที่คืนโควต้า:** `{code_used}`\n"
                            f"• **จำนวนคนใช้ปัจจุบัน:** **{new_used}/{max_u}** คน",
                color=discord.Color.orange(),
                timestamp=datetime.datetime.now(datetime.timezone.utc)
            )
            try:
                await target_channel.send(embed=embed)
            except Exception as e:
                logging.error(f"Error sending quota return log: {e}")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """เมื่อสมาชิกออกจากเซิร์ฟเวอร์ หรือถูกเตะ/แบน คืนโควต้าให้อัตโนมัติ"""
        if self.roster:
            if self.roster.correct_guild(member.guild):
                await asyncio.to_thread(self.roster.registry.mark_departed, str(member.id))
            return
        async with data_lock:
            data = load_data()
            verified_users = data.get("verified_users", [])
            user_record = next((u for u in verified_users if u.get("user_id") == member.id), None)
            if not user_record:
                return

            code_used = user_record.get("code_used")
            codes = data.get("codes", {})

            if code_used in codes:
                codes[code_used]["used_count"] = max(0, codes[code_used].get("used_count", 1) - 1)

            data["verified_users"] = [u for u in verified_users if u.get("user_id") != member.id]
            save_data(data)
        logging.info(f"🚪 สมาชิก {member.name} ออกจากเซิร์ฟเวอร์: คืนโควต้ารหัส {code_used}")

    # --- SLASH COMMANDS FOR ADMINS ---

    @app_commands.command(name="create_code", description="สร้างรหัสยืนยันตัวตนใหม่ พร้อมกำหนดโควต้าและสร้างยศให้อัตโนมัติ (Create Passcode)")
    @app_commands.describe(
        code="รหัสยืนยัน เช่น DWMA-2026",
        max_uses="จำนวนคนที่สามารถใช้รหัสนี้ได้ เช่น 30",
        role_name="ชื่อยศที่จะมอบให้ เช่น นักเรียนรุ่น 1 (หากยังไม่มียศนี้ บอทจะสร้างยศให้อัตโนมัติทันที)"
    )
    @is_authorized()
    @app_commands.checks.has_permissions(administrator=True)
    async def create_code(self, interaction: discord.Interaction, code: str, max_uses: int, role_name: str = "สมาชิก (Verified)"):
        await interaction.response.defer(ephemeral=True)
        if os.getenv("VERIFICATION_MODE") == "classes":
            from portal.store import RegistryError
            if not self.roster or not self.roster.correct_guild(interaction.guild):
                return await interaction.followup.send("กรุณาใช้ในเซิร์ฟเวอร์ที่กำหนด", ephemeral=True)
            try:
                created = await asyncio.to_thread(self.roster.registry.create_class, role_name, role_name, max_uses, code)
                return await interaction.followup.send(
                    f"✅ สร้างคลาสแล้ว โค้ด `{created['access_code']}` รับได้ {created['capacity']} คน ยศ {created['role_name']}",
                    ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
            except RegistryError as error:
                return await interaction.followup.send(str(error), ephemeral=True)
        if os.getenv("VERIFICATION_MODE", "legacy") in {"roster", "classes"}:
            return await interaction.followup.send("กรุณาจัดการคลาสและโค้ดจากหน้าเว็บแอดมิน", ephemeral=True)
        clean_code = code.strip().upper()
        if max_uses <= 0:
            return await interaction.followup.send("❌ จำนวนโควต้าผู้ใช้ต้องมากกว่า 0", ephemeral=True)

        guild = interaction.guild
        # ตรวจสอบหรือสร้างยศในเซิร์ฟเวอร์ทันที ไม่ต้องไปสร้างใน Server Settings
        role = discord.utils.get(guild.roles, name=role_name)
        role_note = ""
        if not role:
            try:
                role = await guild.create_role(name=role_name, color=discord.Color.blue(), reason=f"Auto-created by {interaction.user.name} for code {clean_code}")
                role_note = f"\n✨ *สร้างยศใหม่ `{role_name}` ในเซิร์ฟเวอร์ให้อัตโนมัติเรียบร้อยแล้ว*"
            except Exception as e:
                logging.error(f"Failed to auto-create role: {e}")

        async with data_lock:
            data = load_data()
            if clean_code in data["codes"]:
                return await interaction.followup.send("❌ รหัสนี้มีอยู่แล้ว กรุณาใช้รหัสใหม่", ephemeral=True)
            data["codes"][clean_code] = {
                "role_name": role_name,
                "max_uses": max_uses,
                "used_count": 0,
                "created_by": interaction.user.name,
                "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
            }
            save_data(data)

        embed = discord.Embed(
            title="🔑 สร้างรหัสยืนยันสำเร็จ!",
            description=f"**รหัส:** `{clean_code}`\n"
                        f"👥 **โควต้า:** ใช้ได้สูงสุด **{max_uses}** คน\n"
                        f"🏷️ **ยศที่จะได้รับ:** {role.mention if role else f'`{role_name}`'}{role_note}\n\n"
                        f"💡 คุณสามารถนำรหัส `{clean_code}` ไปแจ้งในกลุ่ม LINE ของผู้เข้าอบรมได้เลยครับ",
            color=discord.Color.green()
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="list_codes", description="ดูรายการรหัสยืนยันทั้งหมดและสถานะโควต้า (List All Codes)")
    @is_authorized()
    @app_commands.checks.has_permissions(administrator=True)
    async def list_codes(self, interaction: discord.Interaction):
        if os.getenv("VERIFICATION_MODE", "legacy") in {"roster", "classes"}:
            return await interaction.response.send_message("กรุณาจัดการคลาสและโค้ดจากหน้าเว็บแอดมิน", ephemeral=True)
        data = load_data()
        codes = data.get("codes", {})

        if not codes:
            return await interaction.response.send_message("ℹ️ ยังไม่มีการสร้างรหัสยืนยันในระบบ", ephemeral=True)

        embed = discord.Embed(
            title="📋 รายการรหัสยืนยันตัวตนในระบบ",
            color=discord.Color.blue(),
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )

        for code, info in codes.items():
            used = info.get("used_count", 0)
            max_u = info.get("max_uses", 0)
            role = info.get("role_name", "สมาชิก")
            status = "🔴 เต็มแล้ว" if used >= max_u else "🟢 ใช้งานได้"

            embed.add_field(
                name=f"🔑 `{code}` ({status})",
                value=f"• ผู้ใช้งาน: **{used}/{max_u}** คน\n• ยศ: `{role}`",
                inline=False
            )

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="delete_code", description="ลบรหัสยืนยันตัวตนออกจากระบบ (Delete Code)")
    @app_commands.describe(code="รหัสที่ต้องการลบ")
    @is_authorized()
    @app_commands.checks.has_permissions(administrator=True)
    async def delete_code(self, interaction: discord.Interaction, code: str):
        await interaction.response.defer(ephemeral=True)
        if os.getenv("VERIFICATION_MODE", "legacy") in {"roster", "classes"}:
            return await interaction.followup.send("กรุณาจัดการคลาสและโค้ดจากหน้าเว็บแอดมิน", ephemeral=True)
        clean_code = code.strip().upper()
        async with data_lock:
            data = load_data()

            if clean_code not in data.get("codes", {}):
                return await interaction.followup.send(f"❌ ไม่พบรหัส `{clean_code}` ในระบบ", ephemeral=True)

            del data["codes"][clean_code]
            save_data(data)

        embed = discord.Embed(
            title="🗑️ ลบรหัสสำเร็จ",
            description=f"ลบรหัส `{clean_code}` ออกจากระบบเรียบร้อยแล้ว",
            color=discord.Color.red()
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="sync_codes", description="ซิงค์และตรวจสอบโควต้ารหัสกับสมาชิกในเซิร์ฟเวอร์จริง (Sync Quotas)")
    @is_authorized()
    @app_commands.checks.has_permissions(administrator=True)
    async def sync_codes(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if os.getenv("VERIFICATION_MODE", "legacy") in {"roster", "classes"}:
            return await interaction.followup.send("กรุณาจัดการคลาสและโค้ดจากหน้าเว็บแอดมิน", ephemeral=True)
        async with data_lock:
            data = load_data()
            codes = data.get("codes", {})
            verified_users = data.get("verified_users", [])
            guild = interaction.guild

            active_verified_users = []
            counts = {c: 0 for c in codes}

            for record in verified_users:
                u_id = record.get("user_id")
                c_used = record.get("code_used")
                member = guild.get_member(u_id)
                if member:
                    target_role_name = codes.get(c_used, {}).get("role_name", "สมาชิก (Verified)")
                    role = discord.utils.get(guild.roles, name=target_role_name)
                    if role and role in member.roles:
                        active_verified_users.append(record)
                        if c_used in counts:
                            counts[c_used] += 1
                        continue

            for c, count in counts.items():
                codes[c]["used_count"] = count

            data["codes"] = codes
            data["verified_users"] = active_verified_users
            save_data(data)

        embed = discord.Embed(
            title="🔄 ซิงค์โควต้ารหัสสำเร็จ!",
            description="ตรวจสอบและอัปเดตจำนวนผู้ใช้รหัสจริงกับยศในเซิร์ฟเวอร์เรียบร้อยแล้วครับ",
            color=discord.Color.green()
        )
        for c, info in codes.items():
            embed.add_field(
                name=f"🔑 `{c}`",
                value=f"• คนใช้จริง: **{info.get('used_count', 0)}/{info.get('max_uses', 0)}** คน",
                inline=False
            )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="setup_verify", description="ส่งปุ่มยืนยันตัวตน (Modal) ลงในห้อง #เข้าหลักสูตร หรือห้องที่ระบุ")
    @app_commands.describe(channel="ห้องที่ต้องการวางปุ่ม (เว้นว่างเพื่อวางในห้อง #เข้าหลักสูตร อัตโนมัติ)")
    @is_authorized()
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_verify(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        guild = interaction.guild
        target_channel = channel
        if not target_channel:
            target_channel = discord.utils.get(guild.text_channels, name="เข้าหลักสูตร")
            if not target_channel:
                target_channel = await guild.create_text_channel(name="เข้าหลักสูตร", reason="Create course entrance verification channel")

        embed = discord.Embed(
            title="🛡️ ยืนยันตัวตนเพื่อเข้าสู่หลักสูตร (Discord Server)",
            description="ยินดีต้อนรับผู้เข้าร่วมอบรมและสมาชิกทุกท่าน! 👋\n\n"
                        "📌 **ขั้นตอนการเข้าใช้งาน:**\n"
                        "1. กรอก **ชื่อ - นามสกุลจริง** ของท่าน\n"
                        "2. กรอก **รหัสยืนยัน** ที่ได้รับจากกลุ่ม LINE\n\n"
                        "⚖️ **ข้อตกลงการจัดเก็บข้อมูล (Privacy Notice):**\n"
                        "• ระบบจะจัดเก็บ **ชื่อ-นามสกุลจริง** คู่กับบัญชี Discord เพื่อใช้ตรวจสอบสิทธิ์ผู้เข้าอบรมและบันทึกสถิติการเรียน\n"
                        "• ข้อมูลนี้จะถูกเก็บรักษาเป็นความลับเฉพาะทีมงานผู้ดูแลระบบเท่านั้น และไม่เปิดเผยสู่สาธารณะ\n"
                        "• การกดยืนยันตัวตนถือว่าท่านยินยอมให้จัดเก็บข้อมูลดังกล่าวเพื่อการดำเนินงานของหลักสูตร\n\n"
                        "🔒 *ข้อมูลของท่านจะถูกกรอกผ่านหน้าต่างส่วนตัว (ปลอดภัยและคนอื่นมองไม่เห็น)*",
            color=discord.Color.green()
        )
        embed.set_footer(text="ระบบยืนยันตัวตน 3 ชั้น | Manager Bot")

        if os.getenv("VERIFICATION_MODE") in {"classes", "roster"}:
            embed.description = ("กดปุ่มด้านล่างแล้วกรอก **ชื่อ–นามสกุลตามที่ลงทะเบียน** และ **โค้ดคลาส** เพื่อรับยศ\n"
                "ทุกคนในคลาสใช้โค้ดเดียวกัน รับได้ตามจำนวนที่กำหนด บัญชีเดิมกรอกซ้ำไม่ใช้โควต้าเพิ่ม\n"
                "ชื่อจะต้องตรงกับลิสต์ของคลาส และผูกกับบัญชี Discord ได้เพียงบัญชีเดียว\n"
                "ระบบเก็บชื่อและ ID Discord ให้ผู้ดูแลตรวจสิทธิ์") if os.getenv("VERIFICATION_MODE") == "classes" else "กดปุ่มแล้วกรอกรหัสยืนยันที่ได้รับจากผู้ดูแล"
            embed.set_footer(text="ยืนยันสิทธิ์เข้าเรียน | CSPACE")

        view = VerificationButtonView(self)
        await target_channel.send(embed=embed, view=view)
        await interaction.response.send_message(f"✅ ติดตั้งปุ่มยืนยันตัวตนลงในห้อง {target_channel.mention} เรียบร้อยแล้วครับ", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(VerificationCog(bot))
