import asyncio
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from cogs import verification as verification
from cogs import chat_admin
from cogs.chat_admin import ChatAdminCog
from cogs.channels import ChannelsCog
from utils import checks
from utils.confirmation import DestructiveConfirmation


ADMIN_ID = 474113669295505409


def user(user_id=ADMIN_ID, admin=True):
    return NS(id=user_id, name="Sabatiel", display_name="themimoze", global_name="Sabatiel",
              mention=f"<@{user_id}>", bot=False,
              guild_permissions=NS(administrator=admin, manage_channels=admin))


def interaction(member=None, guild=None):
    return NS(user=member or user(), guild=guild or NS(id=1), guild_id=1, channel_id=2,
              response=NS(defer=AsyncMock(), send_message=AsyncMock(), edit_message=AsyncMock()),
              followup=NS(send=AsyncMock()), original_response=AsyncMock())


class ChatSafetyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mode_patch = patch.dict(os.environ, {"VERIFICATION_MODE": "legacy"})
        self.mode_patch.start()
        self.addCleanup(self.mode_patch.stop)
        self.cog = ChatAdminCog.__new__(ChatAdminCog)
        self.cog.bot = NS(user=NS(id=99), latency=0)
        self.cog.ai_client = None
        self.channel = MagicMock(spec=discord.TextChannel)
        self.channel.id = 2
        self.channel.name = "general"
        self.channel.position = 0
        self.channel.overwrites_for.return_value = discord.PermissionOverwrite(send_messages=False)
        self.guild = NS(id=1, default_role=object(), channels=[self.channel], roles=[])

    def message(self, content, author=None):
        return NS(content=content, author=author or user(), guild=self.guild, channel=self.channel,
                  mentions=[], reply=AsyncMock())

    async def test_same_names_do_not_grant_access(self):
        with patch.object(checks, "AUTHORIZED_USER_IDS", frozenset({ADMIN_ID})):
            self.assertFalse(checks.is_authorized_user(user(123)))
            self.assertTrue(checks.is_authorized_user(user()))
            self.cog.process_natural_command = AsyncMock()
            await self.cog.on_message(self.message("!admin ลบห้องนี้", user(123)))
            self.cog.process_natural_command.assert_not_awaited()

    async def test_slash_authorization_uses_id_and_rejects_dm(self):
        async def callback(ctx):
            pass
        predicate = checks.is_authorized()(callback).__discord_app_commands_checks__[0]
        self.assertTrue(await predicate(interaction()))
        with self.assertRaises(checks.UnauthorizedUserError):
            await predicate(interaction(user(123)))
        ctx = interaction()
        ctx.guild = None
        with self.assertRaises(checks.UnauthorizedUserError):
            await predicate(ctx)

    async def test_unaddressed_conversation_never_executes(self):
        self.cog.process_natural_command = AsyncMock()
        for text in ["ลบห้องนี้", "template\n1. Rooms\n- chat", "คุยกับ <@99> ลบห้องนี้"]:
            await self.cog.on_message(self.message(text))
        self.cog.process_natural_command.assert_not_awaited()

    async def test_explicit_prefix_and_leading_mention_work(self):
        self.cog.process_natural_command = AsyncMock(return_value=True)
        for text in ["!admin ping", "<@99> ping", "<@!99> ping"]:
            await self.cog.on_message(self.message(text))
        self.assertEqual(self.cog.process_natural_command.await_count, 3)
        self.assertEqual(self.cog.process_natural_command.await_args.args[1], "ping")

    async def test_admin_permission_and_guild_required(self):
        self.cog.process_natural_command = AsyncMock()
        await self.cog.on_message(self.message("!admin ping", user(admin=False)))
        message = self.message("!admin ping")
        message.guild = None
        await self.cog.on_message(message)
        self.cog.process_natural_command.assert_not_awaited()

    async def test_unlock_does_not_match_lock(self):
        await self.cog.process_natural_command(self.message(""), "ปลดล็อกห้องนี้")
        self.assertIsNone(self.channel.set_permissions.await_args.kwargs["overwrite"].send_messages)
        self.channel.set_permissions.assert_awaited_once()

    async def test_lock_still_works(self):
        await self.cog.process_natural_command(self.message(""), "ล็อกห้องนี้")
        self.assertIs(self.channel.set_permissions.await_args.kwargs["overwrite"].send_messages, False)

    async def test_template_and_numbered_list_do_not_request_reset(self):
        self.cog.execute_dynamic_server_setup = AsyncMock()
        for content in ["template\n1. Rooms\n- chat", "1. Rooms\n- chat"]:
            self.assertFalse(await self.cog.process_natural_command(self.message(""), content))
            _, clear = self.cog.parse_server_structure_from_text(content)
            self.assertFalse(clear)
        self.cog.execute_dynamic_server_setup.assert_not_awaited()

    async def test_chat_delete_and_nuke_wait_for_confirmation(self):
        for text in ["ลบห้องนี้", "ล้างห้องนี้"]:
            message = self.message("")
            await self.cog.process_natural_command(message, text)
            self.assertIsInstance(message.reply.await_args.kwargs["view"], DestructiveConfirmation)
        self.channel.delete.assert_not_awaited()
        self.channel.clone.assert_not_awaited()

    async def test_reset_captures_old_channels_before_confirmation(self):
        message = self.message("")
        structure = [{"category": "new", "text_channels": ["chat"], "voice_channels": []}]
        self.cog.build_server_structure = AsyncMock()
        await self.cog.execute_dynamic_server_setup(message, structure, True)
        view = message.reply.await_args.kwargs["view"]
        self.cog.build_server_structure.assert_not_awaited()
        self.guild.channels.append(NS(id=3))
        await view.action()
        self.assertEqual(self.cog.build_server_structure.await_args.args[2], (self.channel,))

    async def test_creation_failure_keeps_original_channels(self):
        self.guild.create_category = AsyncMock(side_effect=RuntimeError("cannot create"))
        await self.cog.build_server_structure(self.message(""), [{"category": "new"}], (self.channel,))
        self.channel.delete.assert_not_awaited()

    async def test_ai_output_cannot_delete(self):
        self.cog.ai_client = NS(models=NS(generate_content=MagicMock(return_value=NS(
            text=json.dumps({"action": "purge", "params": {"amount": 100}})))))
        await self.cog.process_with_gemini(self.message(""), "hello")
        self.channel.purge.assert_not_awaited()
        self.channel.delete.assert_not_awaited()

    async def test_slash_delete_and_nuke_wait_for_confirmation(self):
        cog = ChannelsCog(NS())
        for command in [ChannelsCog.delete_channel, ChannelsCog.nuke]:
            ctx = interaction()
            ctx.channel = self.channel
            await command.callback(cog, ctx)
            self.assertIsInstance(ctx.response.send_message.await_args.kwargs["view"], DestructiveConfirmation)
        self.channel.delete.assert_not_awaited()
        self.channel.clone.assert_not_awaited()


class ConfirmationTests(unittest.IsolatedAsyncioTestCase):
    async def test_confirm_executes_once(self):
        action = AsyncMock()
        view = DestructiveConfirmation(ADMIN_ID, 1, 2, action)
        await view.confirm.callback(interaction())
        await view.confirm.callback(interaction())
        action.assert_awaited_once()

    async def test_unauthorized_expired_wrong_context_and_revoked_permission(self):
        action = AsyncMock()
        for case in ["user", "guild", "channel", "permission", "expired"]:
            view = DestructiveConfirmation(ADMIN_ID, 1, 2, action)
            ctx = interaction()
            if case == "user": ctx.user = user(123)
            if case == "guild": ctx.guild_id = 3
            if case == "channel": ctx.channel_id = 3
            if case == "permission": ctx.user = user(admin=False)
            if case == "expired": view.expires_at = time.monotonic() - 1
            await view.confirm.callback(ctx)
        action.assert_not_awaited()

    async def test_cancel_and_timeout_never_execute(self):
        for cancel in [True, False]:
            action = AsyncMock()
            view = DestructiveConfirmation(ADMIN_ID, 1, 2, action)
            if cancel:
                await view.cancel.callback(interaction())
            else:
                await view.on_timeout()
            await view.confirm.callback(interaction())
            action.assert_not_awaited()


class VerificationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mode_patch = patch.dict(os.environ, {"VERIFICATION_MODE": "legacy"})
        self.mode_patch.start()
        self.addCleanup(self.mode_patch.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "verification.json"
        self.patch = patch.multiple(verification, DATA_FILE=str(self.path), data_lock=asyncio.Lock())
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.chat_lock_patch = patch.object(chat_admin, "data_lock", verification.data_lock)
        self.chat_lock_patch.start()
        self.addCleanup(self.chat_lock_patch.stop)
        self.role = NS(id=7, name="Verified", mention="<@&7>")
        self.guild = NS(id=1, roles=[self.role], create_role=AsyncMock(return_value=self.role))
        self.cog = NS(send_audit_log=AsyncMock())
        verification.save_data({"codes": {"CLASS": {"role_name": "Verified", "max_uses": 1, "used_count": 0}}, "verified_users": []})

    def request(self, user_id=10):
        member = user(user_id)
        member.roles = []
        member.add_roles = AsyncMock()
        member.remove_roles = AsyncMock()
        modal = verification.VerifyModal(self.cog)
        modal.code._value = "class"
        modal.real_name._value = "Test Student"
        return modal, interaction(member, self.guild)

    async def test_success_grants_role_and_commits_once(self):
        modal, ctx = self.request()
        await modal.on_submit(ctx)
        ctx.user.add_roles.assert_awaited_once()
        data = verification.load_data()
        self.assertEqual(data["codes"]["CLASS"]["used_count"], 1)
        self.assertEqual(len(data["verified_users"]), 1)
        self.cog.send_audit_log.assert_awaited_once()

    async def test_role_failure_does_not_consume_quota_and_can_retry(self):
        modal, ctx = self.request()
        ctx.user.add_roles.side_effect = RuntimeError("forbidden")
        await modal.on_submit(ctx)
        self.assertEqual(verification.load_data()["codes"]["CLASS"]["used_count"], 0)
        self.assertEqual(verification.load_data()["verified_users"], [])
        self.cog.send_audit_log.assert_not_awaited()
        ctx.user.add_roles.side_effect = None
        await modal.on_submit(ctx)
        self.assertEqual(verification.load_data()["codes"]["CLASS"]["used_count"], 1)

    async def test_role_creation_failure_does_not_consume_quota(self):
        self.guild.roles = []
        self.guild.create_role.side_effect = RuntimeError("forbidden")
        modal, ctx = self.request()
        await modal.on_submit(ctx)
        ctx.user.add_roles.assert_not_awaited()
        self.assertEqual(verification.load_data()["verified_users"], [])

    async def test_simultaneous_requests_cannot_exceed_final_slot(self):
        requests = [self.request(10), self.request(11)]
        async def network_delay(*args, **kwargs):
            await asyncio.sleep(0.01)
        for modal, ctx in requests:
            ctx.user.add_roles.side_effect = network_delay
        await asyncio.gather(*(modal.on_submit(ctx) for modal, ctx in requests))
        self.assertEqual(sum(ctx.user.add_roles.await_count for _, ctx in requests), 1)
        data = verification.load_data()
        self.assertEqual(data["codes"]["CLASS"]["used_count"], 1)
        self.assertEqual(len(data["verified_users"]), 1)

    async def test_two_slots_preserve_both_records(self):
        data = verification.load_data()
        data["codes"]["CLASS"]["max_uses"] = 2
        verification.save_data(data)
        requests = [self.request(10), self.request(11)]
        async def network_delay(*args, **kwargs):
            await asyncio.sleep(0.01)
        for _, ctx in requests:
            ctx.user.add_roles.side_effect = network_delay
        await asyncio.gather(*(modal.on_submit(ctx) for modal, ctx in requests))
        data = verification.load_data()
        self.assertEqual(data["codes"]["CLASS"]["used_count"], 2)
        self.assertEqual({r["user_id"] for r in data["verified_users"]}, {10, 11})

    async def test_same_user_cannot_consume_twice(self):
        requests = [self.request(10), self.request(10)]
        await asyncio.gather(*(modal.on_submit(ctx) for modal, ctx in requests))
        self.assertEqual(sum(ctx.user.add_roles.await_count for _, ctx in requests), 1)

    async def test_failed_disk_commit_keeps_original_and_rolls_back_role(self):
        before = self.path.read_bytes()
        modal, ctx = self.request()
        with patch.object(verification.os, "replace", side_effect=OSError("disk error")):
            await modal.on_submit(ctx)
        self.assertEqual(self.path.read_bytes(), before)
        ctx.user.remove_roles.assert_awaited_once()
        self.cog.send_audit_log.assert_not_awaited()
        self.assertIn("ยังไม่ได้ยืนยันสำเร็จ", ctx.followup.send.await_args.args[0])

    async def test_corrupt_file_is_not_treated_as_empty(self):
        self.path.write_text("broken json", encoding="utf-8")
        modal, ctx = self.request()
        await modal.on_submit(ctx)
        ctx.user.add_roles.assert_not_awaited()
        self.assertEqual(self.path.read_text(encoding="utf-8"), "broken json")

    async def test_code_delete_waits_for_verification_transaction(self):
        started, finish = asyncio.Event(), asyncio.Event()
        modal, ctx = self.request()
        async def give_role(*args, **kwargs):
            started.set()
            await finish.wait()
        ctx.user.add_roles.side_effect = give_role
        verify_task = asyncio.create_task(modal.on_submit(ctx))
        await started.wait()
        admin_ctx = interaction()
        delete_task = asyncio.create_task(verification.VerificationCog.delete_code.callback(NS(), admin_ctx, "CLASS"))
        await asyncio.sleep(0)
        self.assertFalse(delete_task.done())
        finish.set()
        await asyncio.gather(verify_task, delete_task)
        data = verification.load_data()
        self.assertNotIn("CLASS", data["codes"])
        self.assertEqual(len(data["verified_users"]), 1)

    async def test_chat_code_creation_waits_and_preserves_verification(self):
        started, finish = asyncio.Event(), asyncio.Event()
        modal, ctx = self.request()
        async def give_role(*args, **kwargs):
            started.set()
            await finish.wait()
        ctx.user.add_roles.side_effect = give_role
        verify_task = asyncio.create_task(modal.on_submit(ctx))
        await started.wait()
        cog = ChatAdminCog.__new__(ChatAdminCog)
        cog.bot = NS(user=NS(id=99))
        message = NS(author=user(), guild=self.guild, channel=NS(), mentions=[], reply=AsyncMock())
        create_task = asyncio.create_task(cog.process_natural_command(message, "สร้างรหัส NEW 2 คน ยศ Verified"))
        await asyncio.sleep(0)
        self.assertFalse(create_task.done())
        finish.set()
        await asyncio.gather(verify_task, create_task)
        data = verification.load_data()
        self.assertIn("NEW", data["codes"])
        self.assertEqual(data["codes"]["CLASS"]["used_count"], 1)
        self.assertEqual(len(data["verified_users"]), 1)

    async def test_duplicate_code_cannot_reset_consumed_quota(self):
        modal, ctx = self.request()
        await modal.on_submit(ctx)
        before = self.path.read_bytes()
        await verification.VerificationCog.create_code.callback(
            NS(), interaction(guild=self.guild), "CLASS", 20, "Verified"
        )
        self.assertEqual(self.path.read_bytes(), before)

    async def test_member_leave_returns_quota_once(self):
        modal, ctx = self.request()
        await modal.on_submit(ctx)
        for _ in range(2):
            await verification.VerificationCog.on_member_remove(NS(roster=None), ctx.user)
        data = verification.load_data()
        self.assertEqual(data["codes"]["CLASS"]["used_count"], 0)
        self.assertEqual(data["verified_users"], [])

    async def test_unrelated_role_removal_keeps_verification(self):
        modal, ctx = self.request()
        await modal.on_submit(ctx)
        other_role = NS(name="Other")
        before = NS(roles=[self.role, other_role])
        after = NS(id=ctx.user.id, roles=[self.role])
        await verification.VerificationCog.on_member_update(NS(roster=None), before, after)
        self.assertEqual(verification.load_data()["codes"]["CLASS"]["used_count"], 1)

    async def test_verified_role_removal_returns_quota(self):
        modal, ctx = self.request()
        await modal.on_submit(ctx)
        before = NS(roles=[self.role])
        after = NS(id=ctx.user.id, name="student", roles=[], guild=NS(text_channels=[]))
        await verification.VerificationCog.on_member_update(NS(roster=None), before, after)
        self.assertEqual(verification.load_data()["codes"]["CLASS"]["used_count"], 0)


if __name__ == "__main__":
    unittest.main()
