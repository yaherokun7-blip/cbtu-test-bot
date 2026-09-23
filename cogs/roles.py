import discord
from discord import app_commands
from discord.ext import commands
from utils.checks import is_authorized

class RolesCog(commands.Cog, name="Roles"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="create_role", description="สร้างยศใหม่ (Create Role)")
    @app_commands.describe(name="ชื่อยศ", hex_color="โค้ดสี Hex เช่น #FF0000 หรือ 3498db")
    @is_authorized()
    @app_commands.checks.has_permissions(manage_roles=True)
    async def create_role(self, interaction: discord.Interaction, name: str, hex_color: str = "000000"):
        hex_clean = hex_color.lstrip("#")
        try:
            color_int = int(hex_clean, 16)
        except ValueError:
            return await interaction.response.send_message("❌ รหัสสี Hex ไม่ถูกต้อง ตัวอย่างที่ถูกต้อง: `#FF0000` หรือ `3498db`", ephemeral=True)

        role = await interaction.guild.create_role(name=name, color=discord.Color(color_int))
        embed = discord.Embed(
            title="🏷️ สร้างยศสำเร็จ",
            description=f"**ชื่อยศ:** {role.mention}\n**ID:** `{role.id}`\n**โค้ดสี:** `#{hex_clean.upper()}`",
            color=role.color
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="delete_role", description="ลบยศ (Delete Role)")
    @app_commands.describe(role="ยศที่ต้องการลบ")
    @is_authorized()
    @app_commands.checks.has_permissions(manage_roles=True)
    async def delete_role(self, interaction: discord.Interaction, role: discord.Role):
        if role >= interaction.guild.me.top_role:
            return await interaction.response.send_message("❌ ไม่สามารถลบยศนี้ได้ เนื่องจากยศอยู่สูงกว่าหรือเท่ากับยศของบอท", ephemeral=True)

        role_name = role.name
        await role.delete(reason=f"สั่งโดย {interaction.user}")
        embed = discord.Embed(
            title="🗑️ ลบยศเรียบร้อย",
            description=f"ลบยศ **{role_name}** ออกจากเซิร์ฟเวอร์เรียบร้อยแล้ว",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="add_role", description="มอบยศให้สมาชิก (Add Role to Member)")
    @app_commands.describe(member="สมาชิกที่ต้องการมอบยศ", role="ยศที่ต้องการเพิ่ม")
    @is_authorized()
    @app_commands.checks.has_permissions(manage_roles=True)
    async def add_role(self, interaction: discord.Interaction, member: discord.Member, role: discord.Role):
        if role >= interaction.guild.me.top_role:
            return await interaction.response.send_message("❌ ไม่สามารถมอบยศนี้ได้ เนื่องจากยศอยู่สูงกว่าหรือเท่ากับยศของบอท", ephemeral=True)

        await member.add_roles(role)
        embed = discord.Embed(
            title="✅ มอบยศสำเร็จ",
            description=f"เพิ่มยศ {role.mention} ให้กับ {member.mention} เรียบร้อยแล้ว",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="remove_role", description="ถอดยศออกจากสมาชิก (Remove Role from Member)")
    @app_commands.describe(member="สมาชิกที่ต้องการถอดยศ", role="ยศที่ต้องการถอด")
    @is_authorized()
    @app_commands.checks.has_permissions(manage_roles=True)
    async def remove_role(self, interaction: discord.Interaction, member: discord.Member, role: discord.Role):
        if role >= interaction.guild.me.top_role:
            return await interaction.response.send_message("❌ ไม่สามารถถอดยศนี้ได้ เนื่องจากยศอยู่สูงกว่าหรือเท่ากับยศของบอท", ephemeral=True)

        await member.remove_roles(role)
        embed = discord.Embed(
            title="➖ ถอดยศสำเร็จ",
            description=f"ถอดยศ {role.mention} ออกจาก {member.mention} เรียบร้อยแล้ว",
            color=discord.Color.orange()
        )
        await interaction.response.send_message(embed=embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(RolesCog(bot))
