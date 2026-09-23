"""Short-lived, requester-bound confirmation for destructive channel actions."""
import logging
import time

import discord

from utils.checks import is_authorized_user


class DestructiveConfirmation(discord.ui.View):
    def __init__(self, user_id, guild_id, channel_id, action, *, permission="manage_channels"):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.action = action
        self.permission = permission
        self.consumed = False
        self.expires_at = time.monotonic() + 60
        self.message = None

    async def interaction_check(self, interaction):
        valid = (
            not self.consumed and time.monotonic() < self.expires_at
            and interaction.guild_id == self.guild_id
            and interaction.channel_id == self.channel_id
            and interaction.user.id == self.user_id
            and is_authorized_user(interaction.user)
            and getattr(interaction.user.guild_permissions, self.permission, False)
        )
        if not valid:
            await interaction.response.send_message(
                "❌ คำขอหมดอายุ ถูกใช้แล้ว หรือคุณไม่มีสิทธิ์ยืนยันคำขอนี้", ephemeral=True
            )
        return valid

    def disable(self):
        self.consumed = True
        for child in self.children:
            child.disabled = True
        self.stop()

    @discord.ui.button(label="ยืนยันการลบ", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        # Recheck here too: two button dispatches may already be in flight.
        if not await self.interaction_check(interaction):
            return
        self.disable()
        await interaction.response.edit_message(content="กำลังดำเนินการตามที่ยืนยัน…", view=self)
        try:
            await self.action()
        except Exception:
            logging.exception("Confirmed channel operation failed")
            await interaction.followup.send(
                "❌ ดำเนินการไม่ครบ กรุณาตรวจสอบห้องและบันทึกข้อผิดพลาดก่อนสั่งซ้ำ", ephemeral=True
            )

    @discord.ui.button(label="ยกเลิก", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        if not await self.interaction_check(interaction):
            return
        self.disable()
        await interaction.response.edit_message(content="ยกเลิกแล้ว ไม่มีการลบจากคำขอนี้", view=self)

    async def on_timeout(self):
        self.disable()
        if self.message:
            try:
                await self.message.edit(content="คำขอหมดอายุ ไม่มีการลบจากคำขอนี้", view=self)
            except discord.HTTPException:
                pass


async def confirm_message(message, description, action):
    view = DestructiveConfirmation(
        message.author.id, message.guild.id, message.channel.id, action, permission="administrator"
    )
    view.message = await message.reply(
        f"⚠️ {description}\nการลบกู้คืนไม่ได้ ยืนยันภายใน 60 วินาที", view=view,
        allowed_mentions=discord.AllowedMentions.none(),
    )
    return view


async def confirm_interaction(interaction, description, action):
    view = DestructiveConfirmation(
        interaction.user.id, interaction.guild_id, interaction.channel_id, action
    )
    await interaction.response.send_message(
        f"⚠️ {description}\nการลบกู้คืนไม่ได้ ยืนยันภายใน 60 วินาที", view=view, ephemeral=True
    )
    view.message = await interaction.original_response()
    return view
