import discord
from discord import app_commands
from discord.ext import commands
from utils.checks import is_authorized

class GeneralCog(commands.Cog, name="General"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="ping", description="ตรวจสอบความเร็วและสถานะของบอท (Ping Latency)")
    @is_authorized()
    async def ping(self, interaction: discord.Interaction):
        latency = round(self.bot.latency * 1000)
        embed = discord.Embed(
            title="🏓 Pong!",
            description=f"ความหน่วงของบอท (Latency): **{latency} ms**",
            color=discord.Color.blurple()
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="serverinfo", description="แสดงข้อมูลรายละเอียดของเซิร์ฟเวอร์ (Server Info)")
    @is_authorized()
    async def serverinfo(self, interaction: discord.Interaction):
        guild = interaction.guild
        embed = discord.Embed(
            title=f"📊 ข้อมูลเซิร์ฟเวอร์: {guild.name}",
            color=discord.Color.blue()
        )
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)

        embed.add_field(name="👑 เจ้าของเซิร์ฟเวอร์", value=guild.owner.mention if guild.owner else "ไม่ทราบ", inline=True)
        embed.add_field(name="🆔 Server ID", value=f"`{guild.id}`", inline=True)
        embed.add_field(name="👥 สมาชิกทั้งหมด", value=f"{guild.member_count} คน", inline=True)
        embed.add_field(name="💬 ช่องแชททั้งหมด", value=f"{len(guild.channels)} ช่อง", inline=True)
        embed.add_field(name="🏷️ จำนวนยศ (Roles)", value=f"{len(guild.roles)} ยศ", inline=True)
        embed.add_field(name="📅 วันที่สร้างเซิร์ฟเวอร์", value=f"<t:{int(guild.created_at.timestamp())}:D>", inline=True)

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="userinfo", description="แสดงข้อมูลเกี่ยวกับสมาชิก (User Info)")
    @app_commands.describe(member="สมาชิกที่ต้องการดูข้อมูล")
    @is_authorized()
    async def userinfo(self, interaction: discord.Interaction, member: discord.Member = None):
        target = member or interaction.user
        roles = [role.mention for role in target.roles if role != interaction.guild.default_role]

        embed = discord.Embed(
            title=f"👤 ข้อมูลสมาชิก: {target.display_name}",
            color=target.color or discord.Color.blue()
        )
        if target.avatar:
            embed.set_thumbnail(url=target.avatar.url)

        embed.add_field(name="Username", value=f"`{target.name}`", inline=True)
        embed.add_field(name="User ID", value=f"`{target.id}`", inline=True)
        embed.add_field(name="📅 วันที่เข้าร่วมดิสคอร์ด", value=f"<t:{int(target.created_at.timestamp())}:D>", inline=False)
        embed.add_field(name="📥 วันที่เข้าร่วมเซิร์ฟเวอร์", value=f"<t:{int(target.joined_at.timestamp())}:D>" if target.joined_at else "ไม่ทราบ", inline=False)
        embed.add_field(name=f"🏷️ ยศที่ครอบครอง ({len(roles)})", value=", ".join(roles) if roles else "ไม่มียศพิเศษ", inline=False)

        await interaction.response.send_message(embed=embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(GeneralCog(bot))
