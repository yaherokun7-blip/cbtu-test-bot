import os
import re
import asyncio
import datetime
import logging
import io
import discord
from discord.ext import commands
from utils.checks import is_authorized_user
from utils.confirmation import confirm_message

# Import google.genai
GEMINI_AVAILABLE = False
try:
    from google import genai
    GEMINI_AVAILABLE = True
except ImportError:
    pass

from cogs.verification import VerificationButtonView, load_data, save_data, data_lock, StorageError

class ChatAdminCog(commands.Cog, name="ChatAdmin"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # Setup Gemini AI Client if key is present
        self.gemini_key = os.getenv("GEMINI_API_KEY")
        self.ai_client = None
        if GEMINI_AVAILABLE and self.gemini_key and self.gemini_key != "YOUR_GEMINI_API_KEY":
            try:
                self.ai_client = genai.Client(api_key=self.gemini_key)
                logging.info("🧠 Gemini AI Client initialized successfully!")
            except Exception as e:
                logging.error(f"❌ Failed to init Gemini Client: {e}")

    def is_user_authorized(self, user: discord.User | discord.Member) -> bool:
        return is_authorized_user(user)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        # Only an explicit leading bot mention or !admin opens the command path.
        match = re.match(rf"^(?:<@!?{self.bot.user.id}>\s*|!admin\s+)(.*)$", message.content.strip(), re.DOTALL | re.IGNORECASE)
        if not match:
            return
        if not self.is_user_authorized(message.author) or not message.author.guild_permissions.administrator:
            await message.reply("❌ คำสั่งแชทใช้ได้เฉพาะ User ID ที่กำหนดและมีสิทธิ์ Administrator")
            return
        content = match.group(1).strip()
        try:
            handled = await self.process_natural_command(message, content)
            if not handled:
                if self.ai_client and await self.process_with_gemini(message, content):
                    return
                await self.reply_intelligent_ai(message, content)
        except StorageError as error:
            await message.reply(f"❌ {error}")
        except Exception:
            logging.exception("Chat command failed")
            await message.reply("❌ คำสั่งไม่สำเร็จ กรุณาตรวจสอบสิทธิ์บอทและบันทึกข้อผิดพลาด")

    async def process_with_gemini(self, message: discord.Message, content: str) -> bool:
        """Conversational fallback only; AI output never executes mutations."""
        try:
            response = await asyncio.to_thread(
                self.ai_client.models.generate_content,
                model='gemini-2.5-flash',
                contents=("คุณคือผู้ช่วยอธิบายการใช้บอท Discord ตอบภาษาไทย "
                          "คุณไม่มีเครื่องมือทำงาน ห้ามอ้างว่าดำเนินการแล้ว "
                          "หากต้องการสั่งงานให้แนะนำ Slash Commands ที่เกี่ยวข้อง\n"
                          + content),
            )
            if not response.text:
                return False
            await message.reply(response.text[:2000], allowed_mentions=discord.AllowedMentions.none())
            return True
        except Exception:
            logging.exception("Gemini conversation failed")
            return False

    async def process_natural_command(self, message: discord.Message, content: str) -> bool:
        text = content.lower().strip()
        guild = message.guild
        channel = message.channel

        if not text or guild is None:
            return False

        targets = [user for user in message.mentions if user.id != self.bot.user.id]

        if os.getenv("VERIFICATION_MODE", "legacy") in {"roster", "classes"} and any(word in text for word in ["สร้างรหัส", "ดูรหัส", "เช็ครหัส", "รายการรหัส", "ซิงค์รหัส", "อัปเดตรหัส", "sync รหัส"]):
            await message.reply("กรุณาจัดการคลาสและโค้ดจากหน้าเว็บแอดมิน")
            return True

        # --- 0.0 จัดการรหัสยืนยันตัวตน (Passcode Management) ---
        create_code_match = re.search(r'สร้างรหัส\s*(\S+)\s*(\d+)\s*(?:คน)?(?:\s*(?:ยศ|role)\s*(.+))?', content, re.IGNORECASE)
        if create_code_match:
            new_code = create_code_match.group(1).strip().upper()
            max_uses = int(create_code_match.group(2))
            role_name = create_code_match.group(3).strip() if create_code_match.group(3) else "สมาชิก (Verified)"

            # ตรวจสอบหรือสร้างยศในเซิร์ฟเวอร์ทันที ไม่ต้องไปสร้างใน Server Settings
            role = discord.utils.get(guild.roles, name=role_name)
            role_note = ""
            if not role:
                try:
                    role = await guild.create_role(name=role_name, color=discord.Color.blue(), reason=f"Auto-created by {message.author.name} for code {new_code}")
                    role_note = f"\n✨ *สร้างยศใหม่ `{role_name}` ในเซิร์ฟเวอร์ให้อัตโนมัติเรียบร้อยแล้ว*"
                except Exception as e:
                    logging.error(f"Failed to auto-create role: {e}")

            async with data_lock:
                data = load_data()
                if new_code in data["codes"] or max_uses <= 0:
                    await message.reply("❌ รหัสซ้ำหรือโควต้าไม่มากกว่า 0 กรุณาใช้รหัสใหม่และโควต้าที่ถูกต้อง")
                    return True
                data["codes"][new_code] = {
                    "role_name": role_name,
                    "max_uses": max_uses,
                    "used_count": 0,
                    "created_by": message.author.name,
                    "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
                }
                save_data(data)
            embed = discord.Embed(
                title="🔑 สร้างรหัสยืนยันสำเร็จ!",
                description=f"**รหัส:** `{new_code}`\n"
                            f"👥 **โควต้า:** ใช้งานได้สูงสุด **{max_uses}** คน\n"
                            f"🏷️ **ยศที่จะได้รับ:** {role.mention if role else f'`{role_name}`'}{role_note}\n\n"
                            f"💡 คุณสามารถนำรหัส `{new_code}` ไปแจ้งในกลุ่ม LINE ของผู้เข้าอบรมได้เลยครับ",
                color=discord.Color.green()
            )
            await message.reply(embed=embed)
            return True

        if any(k in text for k in ['ดูรหัส', 'เช็ครหัส', 'รายการรหัส']):
            data = load_data()
            codes = data.get("codes", {})
            if not codes:
                await message.reply("ℹ️ ยังไม่มีการสร้างรหัสยืนยันในระบบครับ (สามารถสั่ง `สร้างรหัส [ชื่อรหัส] [จำนวนคน]` ได้เลยครับ)")
                return True
            embed = discord.Embed(title="📋 รายการรหัสยืนยันตัวตนในระบบ", color=discord.Color.blue())
            for c, info in codes.items():
                u = info.get("used_count", 0)
                m = info.get("max_uses", 0)
                status = "🔴 เต็มแล้ว" if u >= m else "🟢 ใช้งานได้"
                embed.add_field(name=f"🔑 `{c}` ({status})", value=f"• คนใช้: **{u}/{m}** คน\n• ยศ: `{info.get('role_name', 'สมาชิก')}`", inline=False)
            await message.reply(embed=embed)
            return True

        if any(k in text for k in ['ซิงค์รหัส', 'อัปเดตรหัส', 'sync รหัส']):
            async with data_lock:
                data = load_data()
                codes = data.get("codes", {})
                verified_users = data.get("verified_users", [])

                active_verified_users = []
                counts = {c: 0 for c in codes}

                for record in verified_users:
                    u_id = record.get("user_id")
                    c_used = record.get("code_used")
                    mem = guild.get_member(u_id)
                    if mem:
                        target_role_name = codes.get(c_used, {}).get("role_name", "สมาชิก (Verified)")
                        r = discord.utils.get(guild.roles, name=target_role_name)
                        if r and r in mem.roles:
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
            await message.reply(embed=embed)
            return True

        # --- 0.1 คำถาม / สั่งทำระบบ VERIFY ---
        if any(k in text for k in ['verify', 'ยืนยันตัวตน']):
            is_asking = any(k in text for k in ['ไหม', 'ยังไง', 'มั้ย', 'ทำไง', 'ต้อง']) and not any(k in text for k in ['สร้าง', 'ทำ', 'จัด', 'เปิด'])
            if is_asking:
                embed = discord.Embed(
                    title="🤖 ผู้ช่วย AI: วิเคราะห์ระบบยืนยันตัวตน (Verification)",
                    description="สำหรับสมาชิกใหม่ที่เข้ามา **แนะนำให้ตั้งค่าระบบ Verify ครับ!**\n\n"
                                "เมื่อตั้งค่าแล้ว สมาชิกใหม่จะเห็นเฉพาะช่อง `#ยืนยันตัวตน` และเมื่อกดปุ่ม **✅ ยืนยันตัวตน** บอทจะเปิดหน้าต่างให้กรอกชื่อจริง + รหัสผ่าน เพื่อปลดล็อกเข้าดูช่องอื่นๆ ทันทีครับ\n\n"
                                "👉 หากคุณ ต้องการให้ผมสร้างปุ่ม Verify ตอนนี้ บอกผมได้เลยครับ เช่น พิมพ์ว่า **`สร้างระบบ verify`** หรือ **`ทำระบบยืนยันตัวตน`** ครับ!",
                    color=discord.Color.blue()
                )
                await message.reply(embed=embed)
                return True
            else:
                await self.setup_verification_system(message)
                return True

        # --- 0.2 DYNAMIC SERVER SETUP DETECTOR ---
        setup_requested = text.startswith((
            'จัดโครงสร้าง', 'สร้างห้องตามนี้', 'สร้างหมวดหมู่',
            'เคลียร์ดิสคอร์ด', 'ล้างเซิร์ฟเวอร์',
        ))
        if setup_requested:
            parsed_structure, should_clear = self.parse_server_structure_from_text(content)
            if parsed_structure:
                await self.execute_dynamic_server_setup(message, parsed_structure, should_clear)
                return True

        # --- 1. Purge ---
        purge_match = re.search(r'(ลบ|เคลียร์|ล้าง)(ข้อความ|แชท)?\s*(\d+)', text) or re.search(r'purge\s*(\d+)', text)
        if purge_match:
            amount = int(purge_match.group(3) if len(purge_match.groups()) >= 3 and purge_match.group(3) else purge_match.group(1))
            amount = min(max(amount, 1), 100)
            try:
                deleted = await channel.purge(limit=amount + 1)
                embed = discord.Embed(title="🧹 ลบข้อความเรียบร้อย", description=f"ลบข้อความไปทั้งหมด **{len(deleted) - 1}** ข้อความ", color=discord.Color.blue())
                msg = await channel.send(embed=embed)
                await msg.delete(delay=5)
                return True
            except Exception as e:
                await message.reply(f"❌ เกิดข้อผิดพลาดในการลบข้อความ: {e}")
                return True

        # --- 2. Nuke ---
        if any(keyword in text for keyword in ['ล้างห้องนี้', 'นุกห้องนี้', 'nuke ห้องนี้', 'nuke channel']):
            if isinstance(channel, discord.TextChannel):
                async def nuke_channel():
                    new_channel = await channel.clone(reason=f"Nuke confirmed by {message.author.id}")
                    await new_channel.edit(position=channel.position)
                    await channel.delete(reason=f"Nuke confirmed by {message.author.id}")
                    await new_channel.send("✅ ล้างห้องตามที่ยืนยันแล้ว")
                await confirm_message(message, f"ล้างข้อความทั้งหมดใน #{channel.name} (ID {channel.id}) โดยสร้างห้องแทน", nuke_channel)
                return True

        # --- 3. Lockdown / Unlock ---
        if any(keyword in text for keyword in ['ปลดล็อกห้องนี้', 'เปิดห้องนี้', 'unlock']):
            if isinstance(channel, discord.TextChannel):
                overwrite = channel.overwrites_for(guild.default_role)
                overwrite.send_messages = None
                await channel.set_permissions(guild.default_role, overwrite=overwrite)
                embed = discord.Embed(title="🔓 ปลดล็อกช่องแชทเรียบร้อย", description="สมาชิกส่งข้อความได้ตามปกติแล้ว", color=discord.Color.green())
                await message.reply(embed=embed)
                return True

        if any(keyword in text for keyword in ['ล็อกห้องนี้', 'ปิดห้องนี้', 'lockdown']):
            if isinstance(channel, discord.TextChannel):
                overwrite = channel.overwrites_for(guild.default_role)
                overwrite.send_messages = False
                await channel.set_permissions(guild.default_role, overwrite=overwrite)
                embed = discord.Embed(title="🔒 ล็อกช่องแชทเรียบร้อย", description="สมาชิกทั่วไปไม่สามารถพิมพ์ข้อความได้ชั่วคราว", color=discord.Color.red())
                await message.reply(embed=embed)
                return True

        # --- 4. Create Channel ---
        create_chan_match = re.search(r'สร้าง(ห้อง|ช่อง)(ข้อความ|แชท|เสียง)?\s*(ชื่อ)?\s*(.+)', content, re.IGNORECASE)
        if create_chan_match:
            c_type_str = create_chan_match.group(2) or ""
            c_name = create_chan_match.group(4).strip()
            is_voice = "เสียง" in c_type_str or "voice" in c_type_str.lower()
            try:
                if is_voice:
                    new_c = await guild.create_voice_channel(name=c_name)
                else:
                    new_c = await guild.create_text_channel(name=c_name)
                embed = discord.Embed(title="📁 สร้างช่องแชทสำเร็จ", description=f"สร้างช่อง **{new_c.mention}** เรียบร้อยแล้ว", color=discord.Color.green())
                await message.reply(embed=embed)
                return True
            except Exception as e:
                await message.reply(f"❌ เกิดข้อผิดพลาดในการสร้างห้อง: {e}")
                return True

        # --- 5. Delete Channel ---
        if 'ลบห้องนี้' in text or 'ลบช่องนี้' in text:
            async def delete_channel():
                await channel.delete(reason=f"Delete confirmed by {message.author.id}")
            await confirm_message(message, f"ลบห้อง #{channel.name} (ID {channel.id}) และข้อความทั้งหมด", delete_channel)
            return True

        # --- 6. Kick ---
        if text.startswith('เตะ') or 'เตะ' in text:
            if targets:
                target = targets[0]
                reason_str = content.replace("เตะ", "").replace(target.mention, "").strip() or "ไม่ได้ระบุเหตุผล"
                try:
                    await guild.kick(target, reason=f"สั่งโดย {message.author}: {reason_str}")
                    embed = discord.Embed(title="👢 เตะสมาชิกเรียบร้อย", description=f"**สมาชิก:** {target.mention}\n**เหตุผล:** {reason_str}", color=discord.Color.orange())
                    await message.reply(embed=embed)
                    return True
                except Exception as e:
                    await message.reply(f"❌ ไม่สามารถเตะสมาชิกได้: {e}")
                    return True

        # --- 7. Ban ---
        if text.startswith('แบน') or 'แบน' in text:
            if targets:
                target = targets[0]
                reason_str = content.replace("แบน", "").replace(target.mention, "").strip() or "ไม่ได้ระบุเหตุผล"
                try:
                    await guild.ban(target, reason=f"สั่งโดย {message.author}: {reason_str}")
                    embed = discord.Embed(title="🔨 แบนสมาชิกเรียบร้อย", description=f"**สมาชิก:** {target.mention}\n**เหตุผล:** {reason_str}", color=discord.Color.dark_red())
                    await message.reply(embed=embed)
                    return True
                except Exception as e:
                    await message.reply(f"❌ ไม่สามารถแบนสมาชิกได้: {e}")
                    return True

        # --- 8. Timeout ---
        timeout_match = re.search(r'(มิวท์|ไทม์เอาท์|ระงับ)\s*(.+?)\s*(\d+)\s*นาที', content)
        if timeout_match and targets:
            target = targets[0]
            minutes = int(timeout_match.group(3))
            try:
                duration = datetime.timedelta(minutes=minutes)
                await target.timeout(duration, reason=f"สั่งโดย {message.author}")
                embed = discord.Embed(title="⏳ ระงับใช้งานชั่วคราว (Timeout)", description=f"**สมาชิก:** {target.mention}\n**ระยะเวลา:** {minutes} นาที", color=discord.Color.gold())
                await message.reply(embed=embed)
                return True
            except Exception as e:
                await message.reply(f"❌ ไม่สามารถตั้งไทม์เอาท์ได้: {e}")
                return True

        if 'ปลดมิวท์' in text or 'ยกเลิกไทม์เอาท์' in text:
            if targets:
                target = targets[0]
                try:
                    await target.timeout(None, reason=f"สั่งโดย {message.author}")
                    embed = discord.Embed(title="🔊 ยกเลิก Timeout เรียบร้อย", description=f"สมาชิก {target.mention} พิมพ์แชทได้ตามปกติแล้ว", color=discord.Color.green())
                    await message.reply(embed=embed)
                    return True
                except Exception as e:
                    await message.reply(f"❌ เกิดข้อผิดพลาด: {e}")
                    return True

        # --- 9. Create Role ---
        create_role_match = re.search(r'สร้างยศ\s*(.+)', content, re.IGNORECASE)
        if create_role_match:
            r_name = create_role_match.group(1).strip()
            try:
                role = await guild.create_role(name=r_name)
                embed = discord.Embed(title="🏷️ สร้างยศสำเร็จ", description=f"สร้างยศ {role.mention} เรียบร้อยแล้ว", color=discord.Color.blue())
                await message.reply(embed=embed)
                return True
            except Exception as e:
                await message.reply(f"❌ เกิดข้อผิดพลาดในการสร้างยศ: {e}")
                return True

        # --- 10. Info / Ping ---
        if any(keyword in text for keyword in ['ข้อมูลเซิร์ฟเวอร์', 'serverinfo', 'ข้อมูลเซิร์ฟ']):
            embed = discord.Embed(title=f"📊 ข้อมูลเซิร์ฟเวอร์: {guild.name}", color=discord.Color.blue())
            if guild.icon:
                embed.set_thumbnail(url=guild.icon.url)
            embed.add_field(name="👥 สมาชิกทั้งหมด", value=f"{guild.member_count} คน", inline=True)
            embed.add_field(name="💬 ช่องแชททั้งหมด", value=f"{len(guild.channels)} ช่อง", inline=True)
            embed.add_field(name="🏷️ จำนวนยศ", value=f"{len(guild.roles)} ยศ", inline=True)
            await message.reply(embed=embed)
            return True

        if text in ['ปิง', 'ping', 'เช็คปิง']:
            latency = round(self.bot.latency * 1000)
            await message.reply(f"🏓 Pong! ความหน่วงปัจจุบัน: **{latency} ms**")
            return True

        return False

    async def setup_verification_system(self, message: discord.Message):
        guild = message.guild

        channel_name = "เข้าหลักสูตร"
        verify_channel = discord.utils.get(guild.text_channels, name=channel_name)
        if not verify_channel:
            verify_channel = await guild.create_text_channel(name=channel_name)

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

        verify_cog = self.bot.get_cog("Verification")
        view = VerificationButtonView(verify_cog)
        await verify_channel.send(embed=embed, view=view)

        reply_embed = discord.Embed(
            title="✅ ติดตั้งระบบยืนยันตัวตน 3 ชั้นเรียบร้อยแล้ว!",
            description=f"ติดตั้งช่อง {verify_channel.mention} พร้อมปุ่ม **✅ ยืนยันตัวตน** แบบกรอกชื่อจริง+รหัสลับเรียบร้อยแล้วครับ\n\n"
                        f"💡 คุณสามารถสั่งพิมพ์ `สร้างรหัส [ชื่อรหัส] [จำนวนคน] ยศ [ชื่อยศ]` ได้ทันที เช่น:\n"
                        f"• `สร้างรหัส DWMA-2026 30 คน ยศ นักศึกษา AI`\n"
                        f"*(หากยังไม่มียศนี้ บอทจะสร้างยศใหม่ให้อัตโนมัติทันที ไม่ต้องไปสร้างเองใน Settings)*",
            color=discord.Color.green()
        )
        await message.reply(embed=reply_embed)

    def parse_server_structure_from_text(self, text: str) -> tuple[list[dict], bool]:
        lines = text.split("\n")
        structure = []
        current_cat = None
        should_clear = text.strip().startswith(('เคลียร์ดิสคอร์ด', 'ล้างเซิร์ฟเวอร์'))

        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue

            cat_match = re.match(r'^(?:\d+[\.\)]|หมวดหมู่\s*:?|category\s*:?)\s*(.+)', line, re.IGNORECASE)
            is_sub = bool(re.match(r'^\d+\.\d+', line))

            if cat_match and not is_sub:
                cat_name = cat_match.group(1).strip()
                cat_clean = re.sub(r'\s*(ที่จะ|สำหรับการ|สำหรับ|หมวดหมู่).*$', '', cat_name, flags=re.IGNORECASE).strip() or cat_name
                current_cat = {
                    "category": cat_clean,
                    "text_channels": [],
                    "voice_channels": []
                }
                structure.append(current_cat)
                continue

            sub_match = re.match(r'^(?:\d+\.\d+[\.\)]?|[\-\*\•])\s*(.+)', line)
            if sub_match:
                ch_name = sub_match.group(1).strip()
                ch_name = re.sub(r'[\:\.\,].*$', '', ch_name).strip()
                if not current_cat:
                    current_cat = {"category": "ทั่วไป", "text_channels": [], "voice_channels": []}
                    structure.append(current_cat)

                is_voice = "เสียง" in ch_name or "voice" in ch_name.lower()
                clean_name = ch_name.replace(" ", "-").lower()

                if is_voice:
                    current_cat["voice_channels"].append(clean_name)
                else:
                    current_cat["text_channels"].append(clean_name)
                continue

            if 'หลักสูตร' in line.lower() and current_cat:
                if not any(c['category'] == '🎓 พื้นที่หลักสูตร (Courses)' for c in structure):
                    structure.append({
                        "category": "🎓 พื้นที่หลักสูตร (Courses)",
                        "text_channels": ["แนะนำหลักสูตร", "ห้องเรียน-หลักสูตร-1", "ห้องเรียน-หลักสูตร-2"],
                        "voice_channels": ["ห้องเสียงเรียน-หลักสูตร-1", "ห้องเสียงเรียน-หลักสูตร-2"]
                    })

        if not structure and should_clear:
            structure = [
                {"category": "📌 Important", "text_channels": ["information", "announcement", "join-leave"], "voice_channels": []},
                {"category": "💬 ทั่วไป", "text_channels": ["พูดคุย", "bug-report"], "voice_channels": []},
                {"category": "🔒 Staff", "text_channels": ["staff-chat", "staff-command"], "voice_channels": []},
                {"category": "🤖 Bot Log", "text_channels": ["bot-log"], "voice_channels": []},
                {"category": "🎓 หลักสูตร / Course Rooms", "text_channels": ["แนะนำหลักสูตร", "ห้องเรียน-หลักสูตร-1"], "voice_channels": ["ห้องเสียง-หลักสูตร-1"]}
            ]

        return structure, should_clear

    async def execute_dynamic_server_setup(self, message: discord.Message, structure: list[dict], should_clear: bool):
        old_channels = tuple(message.guild.channels) if should_clear else ()
        if should_clear:
            # Bind approval to this snapshot; newly added channels are never deleted.
            preview = ", ".join(f"{ch.name} ({ch.id})" for ch in old_channels)
            new_preview = "\n".join(
                f"{group['category']}: " + ", ".join(group['text_channels'] + group['voice_channels'])
                for group in structure
            )
            plan = discord.File(io.BytesIO(("ห้องเดิมที่จะลบ:\n" + preview + "\n\nโครงสร้างใหม่:\n" + new_preview).encode('utf-8')), filename="server-change.txt")
            await message.reply("รายละเอียดห้องที่จะลบและโครงสร้างที่จะสร้าง:", file=plan)
            async def rebuild():
                await self.build_server_structure(message, structure, old_channels)
            await confirm_message(message, f"ลบห้อง/หมวดหมู่เดิม {len(old_channels)} รายการตามรายการแนบ และสร้าง {len(structure)} หมวดหมู่ใหม่", rebuild)
            return
        await self.build_server_structure(message, structure, old_channels)

    async def build_server_structure(self, message, structure, old_channels):
        guild = message.guild
        first_channel = None
        created = []
        # Build first. If any creation fails, keep every original channel.
        try:
            for group in structure:
                category = await guild.create_category(name=group["category"])
                created.append(category.name)
                for name in group["text_channels"]:
                    channel = await guild.create_text_channel(name=name, category=category)
                    first_channel = first_channel or channel
                for name in group["voice_channels"]:
                    await guild.create_voice_channel(name=name, category=category)
        except Exception:
            logging.exception("Server structure creation failed; originals retained")
            await message.channel.send("❌ สร้างโครงสร้างไม่ครบ จึงยังไม่ลบห้องเดิม กรุณาตรวจสอบห้องใหม่ที่สร้างไปแล้วก่อนลองซ้ำ")
            return
        if old_channels and first_channel is None:
            await message.channel.send("❌ ต้องมีห้องข้อความใหม่อย่างน้อยหนึ่งห้อง จึงยังไม่ลบห้องเดิม")
            return
        # Preserve newly created channels even if another admin moved them into an old category.
        for channel in old_channels:
            if channel != message.channel:
                await channel.delete(reason=f"Server rebuild confirmed by {message.author.id}")
        if message.channel in old_channels:
            await message.channel.delete(reason=f"Server rebuild confirmed by {message.author.id}")
        target = first_channel or message.channel
        await target.send(f"✅ สร้างโครงสร้างครบ {len(created)} หมวดหมู่" + (" และลบห้องเดิมตามที่ยืนยันแล้ว" if old_channels else ""))

    async def reply_intelligent_ai(self, message: discord.Message, content: str):
        """Intelligent conversational AI response in natural Thai"""
        text = content.lower()
        user_name = message.author.display_name

        if any(w in text for w in ['ทำอะไรได้บ้าง', 'ทำไรได้บ้าง', 'ช่วยอะไรได้บ้าง', 'ความสามารถ']):
            embed = discord.Embed(
                title=f"🤖 สวัสดีครับคุณ {user_name}! ผมคือ Manager ผู้ช่วย AI ประจำเซิร์ฟเวอร์",
                description="ผมเปรียบเสมือนผู้ช่วยส่วนตัวปัญญาประดิษฐ์ของคุณ สามารถพูดคุยภาษาธรรมชาติและจัดการ Discord ให้ได้ดังนี้ครับ:\n\n"
                            "1. **จัดโครงสร้างเซิร์ฟเวอร์อัตโนมัติ:** ใช้ `!admin จัดโครงสร้าง` ตามด้วยรายการห้อง หากต้องการลบของเก่าใช้ `!admin ล้างเซิร์ฟเวอร์` และกดยืนยัน\n"
                            "2. **ระบบยืนยันตัวตน 3 ชั้น (Verify):** พิมพ์ `สร้างระบบ verify` (สร้างช่องปุ่มยืนยัน) และ `สร้างรหัส [ชื่อรหัส] [จำนวนคน]` เช่น `สร้างรหัส DWMA-2026 30 คน`\n"
                            "3. **เคลียร์แชท / ลบข้อความ:** เช่น `ลบ 20 ข้อความ`, `ล้างห้องนี้ (nuke)`\n"
                            "4. **จัดการสมาชิก:** เช่น `เตะ @User`, `แบน @User`, `มิวท์ @User 10 นาที`\n"
                            "5. **จัดการยศและช่องแชท:** เช่น `สร้างยศ VIP`, `สร้างห้องชื่อ ข่าวสาร`, `ล็อกห้องนี้`\n\n"
                            "💡 *คุณสามารถนำ `GEMINI_API_KEY` ใส่ในไฟล์ `.env` เพื่อเปิดโหมด AI แชทอัจฉริยะ (Google Gemini) คุยและวิเคราะห์คำสั่งแบบอิสระเหมือน ChatGPT ได้ทันทีครับ!*",
                color=discord.Color.blurple()
            )
            await message.reply(embed=embed)
        elif any(w in text for w in ['สวัสดี', 'หวัดดี', 'ทักทาย', 'hi', 'hello', 'เฮ้']):
            await message.reply(f"สวัสดีครับคุณ {user_name}! 👋 ผมพร้อมรับคำสั่งหรือตอบคำถามทุกอย่างแล้วครับ ต้องการให้จัดการอะไรในเซิร์ฟเวอร์แจ้งได้เลยครับ!")
        else:
            await message.reply(f"สวัสดีครับคุณ {user_name}! ผมเข้าใจคำถาม/ข้อความของคุณครับ 😊\n\n"
                                f"หากต้องการให้ผม **สร้างระบบ Verify**, **จัดโครงสร้างเซิร์ฟเวอร์**, หรือ **จัดการช่อง/ยศ/ข้อความ** ให้ขึ้นต้นด้วยการแท็กบอทหรือ `!admin` ครับ หรือใส่ `GEMINI_API_KEY` ใน `.env` เพื่อเปิดใช้งานโหมด AI คุยอิสระได้ครับ!")

async def setup(bot: commands.Bot):
    await bot.add_cog(ChatAdminCog(bot))
