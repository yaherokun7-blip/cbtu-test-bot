import os
import asyncio
import logging
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from utils.checks import UnauthorizedUserError

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

# Load environment variables
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")

if not TOKEN or TOKEN == "YOUR_BOT_TOKEN_HERE":
    logging.warning("⚠️ ไม่พบ DISCORD_TOKEN กรุณากรอก Token ในไฟล์ .env ก่อนรันบอท!")

# Configure Discord Intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    logging.info(f"✅ บอทเข้าสู่ระบบเรียบร้อยแล้ว: {bot.user.name} ({bot.user.id})")
    logging.info("--------------------------------------------------")

    # Set custom presence status
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="จัดการเซิร์ฟเวอร์ | Manager"
        )
    )

    # Sync slash commands
    try:
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            logging.info(f"⚡ Sync Slash Commands สำเร็จใน Guild ID {GUILD_ID} (ทั้งหมด {len(synced)} คำสั่ง)")
        else:
            synced = await bot.tree.sync()
            logging.info(f"⚡ Sync Slash Commands แบบ Global สำเร็จ (ทั้งหมด {len(synced)} คำสั่ง)")
    except Exception as e:
        logging.error(f"❌ เกิดข้อผิดพลาดในการ Sync Slash Commands: {e}")

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    real_error = getattr(error, "original", error)
    if isinstance(error, UnauthorizedUserError) or isinstance(real_error, UnauthorizedUserError):
        embed = discord.Embed(
            title="🚫 ปฏิเสธการเข้าถึง (Access Denied)",
            description="ขออภัย คุณไม่มีสิทธิ์เรียกใช้งานคำสั่งของบอทนี้\nคำสั่งใช้ได้เฉพาะบัญชี Discord User ID ที่ผู้ดูแลกำหนดเท่านั้น",
            color=discord.Color.red()
        )
        if not interaction.response.is_done():
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await interaction.followup.send(embed=embed, ephemeral=True)

    elif isinstance(error, app_commands.MissingPermissions):
        missing = ", ".join(error.missing_permissions)
        embed = discord.Embed(
            title="❌ คุณไม่มีสิทธิ์ในเซิร์ฟเวอร์",
            description=f"ต้องการสิทธิ์: `{missing}` ในการใช้คำสั่งนี้",
            color=discord.Color.red()
        )
        if not interaction.response.is_done():
            await interaction.response.send_message(embed=embed, ephemeral=True)

    elif isinstance(error, app_commands.BotMissingPermissions):
        missing = ", ".join(error.missing_permissions)
        embed = discord.Embed(
            title="❌ บอทขาดสิทธิ์ในการทำงาน",
            description=f"บอทจำเป็นต้องได้รับสิทธิ์: `{missing}` ในเซิร์ฟเวอร์",
            color=discord.Color.red()
        )
        if not interaction.response.is_done():
            await interaction.response.send_message(embed=embed, ephemeral=True)

    else:
        logging.error(f"Command Error: {error}", exc_info=error)
        embed = discord.Embed(
            title="❌ เกิดข้อผิดพลาดที่ไม่คาดคิด",
            description=f"```{error}```",
            color=discord.Color.dark_red()
        )
        if not interaction.response.is_done():
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await interaction.followup.send(embed=embed, ephemeral=True)

async def load_extensions():
    cogs_dir = os.path.join(os.path.dirname(__file__), "cogs")
    for filename in os.listdir(cogs_dir):
        if filename.endswith(".py") and not filename.startswith("__"):
            cog_name = f"cogs.{filename[:-3]}"
            await bot.load_extension(cog_name)
            logging.info(f"📦 โหลด Cog สำเร็จ: {cog_name}")

async def main():
    async with bot:
        await load_extensions()
        if TOKEN and TOKEN != "YOUR_BOT_TOKEN_HERE":
            await bot.start(TOKEN)
        else:
            print("\n❌ กรุณากรอก TOKEN ในไฟล์ .env ก่อนเริ่มรันบอท!\n")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 ปิดการทำงานของบอทเรียบร้อย")
