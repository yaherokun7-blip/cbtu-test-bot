import datetime
import discord
from discord import app_commands
from discord.ext import commands
from utils.checks import is_authorized

class ModerationCog(commands.Cog, name="Moderation"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="kick", description="เตะสมาชิกออกจากเซิร์ฟเวอร์ (Kick Member)")
    @app_commands.describe(member="สมาชิกที่ต้องการเตะ", reason="เหตุผลในการเตะ")
    @is_authorized()
    @app_commands.checks.has_permissions(kick_members=True)
    async def kick(self, interaction: discord.Interaction, member: discord.Member, reason: str = "ไม่ได้ระบุเหตุผล"):
        if member.top_role >= interaction.guild.me.top_role:
            embed = discord.Embed(
                title="❌ เกิดข้อผิดพลาด",
                description=f"ไม่สามารถเตะ {member.mention} ได้ เนื่องจากยศสูงกว่าหรือเท่ากับบอท",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        await member.kick(reason=f"สั่งโดย {interaction.user}: {reason}")
        embed = discord.Embed(
            title="👢 เตะสมาชิกเรียบร้อย",
            description=f"**สมาชิก:** {member.mention} (`{member.id}`)\n**ผู้สั่งการ:** {interaction.user.mention}\n**เหตุผล:** {reason}",
            color=discord.Color.orange(),
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="ban", description="แบนสมาชิกออกจากเซิร์ฟเวอร์ (Ban Member)")
    @app_commands.describe(member="สมาชิกที่ต้องการแบน", reason="เหตุผลในการแบน", delete_messages_days="ลบข้อความย้อนหลัง (วัน)")
    @is_authorized()
    @app_commands.checks.has_permissions(ban_members=True)
    async def ban(self, interaction: discord.Interaction, member: discord.Member, reason: str = "ไม่ได้ระบุเหตุผล", delete_messages_days: int = 0):
        if member.top_role >= interaction.guild.me.top_role:
            embed = discord.Embed(
                title="❌ เกิดข้อผิดพลาด",
                description=f"ไม่สามารถแบน {member.mention} ได้ เนื่องจากยศสูงกว่าหรือเท่ากับบอท",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        delete_seconds = delete_messages_days * 86400
        await member.ban(reason=f"สั่งโดย {interaction.user}: {reason}", delete_message_seconds=delete_seconds)
        embed = discord.Embed(
            title="🔨 แบนสมาชิกเรียบร้อย",
            description=f"**สมาชิก:** {member.mention} (`{member.id}`)\n**ผู้สั่งการ:** {interaction.user.mention}\n**เหตุผล:** {reason}",
            color=discord.Color.dark_red(),
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="unban", description="ปลดแบนสมาชิกด้วย User ID (Unban Member)")
    @app_commands.describe(user_id="User ID ของคนที่ต้องการปลดแบน", reason="เหตุผลในการปลดแบน")
    @is_authorized()
    @app_commands.checks.has_permissions(ban_members=True)
    async def unban(self, interaction: discord.Interaction, user_id: str, reason: str = "ไม่ได้ระบุเหตุผล"):
        try:
            user_obj = await self.bot.fetch_user(int(user_id))
            await interaction.guild.unban(user_obj, reason=f"สั่งโดย {interaction.user}: {reason}")
            embed = discord.Embed(
                title="🔓 ปลดแบนเรียบร้อย",
                description=f"**สมาชิก:** {user_obj.name} (`{user_obj.id}`)\n**ผู้สั่งการ:** {interaction.user.mention}\n**เหตุผล:** {reason}",
                color=discord.Color.green(),
                timestamp=datetime.datetime.now(datetime.timezone.utc)
            )
            await interaction.response.send_message(embed=embed)
        except ValueError:
            await interaction.response.send_message("❌ User ID ต้องเป็นตัวเลขเท่านั้น", ephemeral=True)
        except discord.NotFound:
            await interaction.response.send_message("❌ ไม่พบผู้ใช้ในรายการแบน", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ เกิดข้อผิดพลาด: {e}", ephemeral=True)

    @app_commands.command(name="timeout", description="ระงับการใช้งานชั่วคราว (Timeout / Mute)")
    @app_commands.describe(member="สมาชิกที่ต้องการระงับ", minutes="จำนวนนาที", reason="เหตุผล")
    @is_authorized()
    @app_commands.checks.has_permissions(moderate_members=True)
    async def timeout(self, interaction: discord.Interaction, member: discord.Member, minutes: int, reason: str = "ไม่ได้ระบุเหตุผล"):
        duration = datetime.timedelta(minutes=minutes)
        await member.timeout(duration, reason=f"สั่งโดย {interaction.user}: {reason}")
        embed = discord.Embed(
            title="⏳ ระงับใช้งานชั่วคราว (Timeout)",
            description=f"**สมาชิก:** {member.mention}\n**ระยะเวลา:** {minutes} นาที\n**ผู้สั่งการ:** {interaction.user.mention}\n**เหตุผล:** {reason}",
            color=discord.Color.gold(),
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="untimeout", description="ยกเลิกการระงับใช้งานชั่วคราว (Untimeout)")
    @app_commands.describe(member="สมาชิกที่ต้องการยกเลิก Timeout")
    @is_authorized()
    @app_commands.checks.has_permissions(moderate_members=True)
    async def untimeout(self, interaction: discord.Interaction, member: discord.Member):
        await member.timeout(None, reason=f"สั่งโดย {interaction.user}")
        embed = discord.Embed(
            title="🔊 ยกเลิก Timeout เรียบร้อย",
            description=f"สมาชิก {member.mention} สามารถส่งข้อความและใช้งานช่องแชทได้ตามปกติ",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="purge", description="ลบข้อความจำนวนมากในช่องแชท (Bulk Delete Messages)")
    @app_commands.describe(amount="จำนวนข้อความที่ต้องการลบ (1-100)")
    @is_authorized()
    @app_commands.checks.has_permissions(manage_messages=True)
    async def purge(self, interaction: discord.Interaction, amount: int):
        if amount < 1 or amount > 100:
            return await interaction.response.send_message("❌ กรุณาระบุจำนวนข้อความระหว่าง 1 ถึง 100", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=amount)
        embed = discord.Embed(
            title="🧹 ลบข้อความเรียบร้อย",
            description=f"ลบข้อความไปทั้งหมด **{len(deleted)}** ข้อความใน {interaction.channel.mention}",
            color=discord.Color.blue()
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(ModerationCog(bot))
