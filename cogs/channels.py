import discord
from discord import app_commands
from discord.ext import commands
from utils.checks import is_authorized
from utils.confirmation import confirm_interaction

class ChannelsCog(commands.Cog, name="Channels"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="create_channel", description="สร้างช่องแชทใหม่ (Create Text or Voice Channel)")
    @app_commands.describe(name="ชื่อช่องแชท", channel_type="ประเภทของช่อง (text / voice)", category="หมวดหมู่ที่ต้องการให้อยู่")
    @app_commands.choices(channel_type=[
        app_commands.Choice(name="Text Channel (ข้อความ)", value="text"),
        app_commands.Choice(name="Voice Channel (เสียง)", value="voice")
    ])
    @is_authorized()
    @app_commands.checks.has_permissions(manage_channels=True)
    async def create_channel(self, interaction: discord.Interaction, name: str, channel_type: str, category: discord.CategoryChannel = None):
        guild = interaction.guild
        if channel_type == "text":
            new_channel = await guild.create_text_channel(name=name, category=category)
        else:
            new_channel = await guild.create_voice_channel(name=name, category=category)

        embed = discord.Embed(
            title="📁 สร้างช่องแชทสำเร็จ",
            description=f"**ชื่อช่อง:** {new_channel.mention}\n**ประเภท:** {channel_type.capitalize()}\n**หมวดหมู่:** {category.name if category else 'ไม่มี'}",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="delete_channel", description="ลบช่องแชท (Delete Channel)")
    @app_commands.describe(channel="ช่องแชทที่ต้องการลบ", reason="เหตุผลในการลบ")
    @is_authorized()
    @app_commands.checks.has_permissions(manage_channels=True)
    async def delete_channel(self, interaction: discord.Interaction, channel: discord.abc.GuildChannel = None, reason: str = "ไม่ได้ระบุเหตุผล"):
        target_channel = channel or interaction.channel
        channel_name = target_channel.name

        async def delete_confirmed():
            await target_channel.delete(reason=f"Confirmed by {interaction.user.id}: {reason}")
            if target_channel != interaction.channel:
                await interaction.followup.send(f"✅ ลบห้อง #{channel_name} แล้ว", ephemeral=True)

        await confirm_interaction(
            interaction, f"ลบห้อง #{channel_name} (ID {target_channel.id}) และข้อความทั้งหมด", delete_confirmed
        )

    @app_commands.command(name="create_category", description="สร้างหมวดหมู่ใหม่ (Create Category)")
    @app_commands.describe(name="ชื่อหมวดหมู่")
    @is_authorized()
    @app_commands.checks.has_permissions(manage_channels=True)
    async def create_category(self, interaction: discord.Interaction, name: str):
        category = await interaction.guild.create_category(name=name)
        embed = discord.Embed(
            title="📂 สร้างหมวดหมู่สำเร็จ",
            description=f"สร้างหมวดหมู่ **{category.name}** เรียบร้อยแล้ว",
            color=discord.Color.blue()
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="slowmode", description="ตั้งเวลาสโลว์โหมดในช่องแชท (Slowmode)")
    @app_commands.describe(seconds="ระยะเวลารอส่งข้อความ (วินาที, 0 = ปิด)")
    @is_authorized()
    @app_commands.checks.has_permissions(manage_channels=True)
    async def slowmode(self, interaction: discord.Interaction, seconds: int):
        if not isinstance(interaction.channel, discord.TextChannel):
            return await interaction.response.send_message("❌ คำสั่งนี้ใช้ได้เฉพาะใน Text Channel เท่านั้น", ephemeral=True)

        await interaction.channel.edit(slowmode_delay=seconds)
        if seconds == 0:
            embed = discord.Embed(title="⚡ ปิด Slowmode เรียบร้อย", color=discord.Color.green())
        else:
            embed = discord.Embed(title="⏱️ ตั้งค่า Slowmode เรียบร้อย", description=f"สมาชิกต้องเว้นระยะ **{seconds}** วินาที ต่อการส่งข้อความ", color=discord.Color.gold())
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="lockdown", description="ล็อกช่องแชทไม่ให้คนทั่วไปพิมพ์ (Lock Channel)")
    @is_authorized()
    @app_commands.checks.has_permissions(manage_channels=True)
    async def lockdown(self, interaction: discord.Interaction):
        if not isinstance(interaction.channel, discord.TextChannel):
            return await interaction.response.send_message("❌ คำสั่งนี้ใช้ได้เฉพาะใน Text Channel เท่านั้น", ephemeral=True)

        overwrite = interaction.channel.overwrites_for(interaction.guild.default_role)
        overwrite.send_messages = False
        await interaction.channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)

        embed = discord.Embed(
            title="🔒 ล็อกช่องแชทเรียบร้อย",
            description="สมาชิกทั่วไปไม่สามารถพิมพ์ข้อความในช่องนี้ได้ชั่วคราว",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="unlock", description="ปลดล็อกช่องแชทให้กลับมาพิมพ์ได้ (Unlock Channel)")
    @is_authorized()
    @app_commands.checks.has_permissions(manage_channels=True)
    async def unlock(self, interaction: discord.Interaction):
        if not isinstance(interaction.channel, discord.TextChannel):
            return await interaction.response.send_message("❌ คำสั่งนี้ใช้ได้เฉพาะใน Text Channel เท่านั้น", ephemeral=True)

        overwrite = interaction.channel.overwrites_for(interaction.guild.default_role)
        overwrite.send_messages = None
        await interaction.channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)

        embed = discord.Embed(
            title="🔓 ปลดล็อกช่องแชทเรียบร้อย",
            description="สมาชิกสามารถส่งข้อความได้ตามปกติแล้ว",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="nuke", description="ล้างช่องแชททั้งหมดโดยการโคลนช่องใหม่ (Nuke Channel)")
    @is_authorized()
    @app_commands.checks.has_permissions(manage_channels=True)
    async def nuke(self, interaction: discord.Interaction):
        if not isinstance(interaction.channel, discord.TextChannel):
            return await interaction.response.send_message("❌ คำสั่งนี้ใช้ได้เฉพาะใน Text Channel เท่านั้น", ephemeral=True)

        old_channel = interaction.channel

        async def nuke_confirmed():
            new_channel = await old_channel.clone(reason=f"Nuke confirmed by {interaction.user.id}")
            await new_channel.edit(position=old_channel.position)
            await old_channel.delete(reason=f"Nuke confirmed by {interaction.user.id}")
            await new_channel.send("✅ ล้างห้องตามที่ยืนยันแล้ว")

        await confirm_interaction(
            interaction, f"ล้างข้อความทั้งหมดใน #{old_channel.name} (ID {old_channel.id}) โดยสร้างห้องแทน", nuke_confirmed
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(ChannelsCog(bot))
