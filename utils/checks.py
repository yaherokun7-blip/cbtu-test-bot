import os
import discord
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

# Names and nicknames are mutable and must never grant authority.
AUTHORIZED_USER_IDS = frozenset(
    int(value.strip())
    for value in os.getenv("AUTHORIZED_USER_IDS", "474113669295505409").split(",")
    if value.strip()
)

def is_authorized_user(user) -> bool:
    return getattr(user, "id", None) in AUTHORIZED_USER_IDS

class UnauthorizedUserError(app_commands.CheckFailure):
    """Exception raised when a user is not authorized to execute a command."""
    def __init__(self, user: str):
        self.user = user
        super().__init__(f"User '{user}' is not authorized to use this command.")

def is_authorized():
    """Allow only configured immutable user IDs, in a server."""
    async def predicate(interaction: discord.Interaction) -> bool:
        user = interaction.user
        if interaction.guild is not None and is_authorized_user(user):
            return True
        raise UnauthorizedUserError(user.name)

    return app_commands.check(predicate)
