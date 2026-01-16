import discord
from discord.ext import commands
import os
import sys

from utils.config import ConfigManager
from utils.discord_helpers import send_error_to_owner

# ====== Intents 設定 ======
intents = discord.Intents.default()
intents.members = True
intents.voice_states = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Attach shared config to bot instance
bot.config = ConfigManager()

# Validation on startup
if bot.config.OWNER_ID == 0:
    print("❌ エラー: OWNER_ID環境変数が設定されていません")
    # exit(1) # We won't exit hard here to allow repl healing if possible

@bot.event
async def on_ready():
    print(f"ログイン成功: {bot.user}")
    
    # Load Extensions
    initial_extensions = [
        "cogs.admin",
        "cogs.voice",
        "cogs.chat",
        "cogs.system",
        "cogs.brawlstars",
    ]
    
    for extension in initial_extensions:
        try:
            await bot.load_extension(extension)
            print(f"✅ Extension loaded: {extension}")
        except Exception as e:
            print(f"❌ Failed to load extension {extension}: {e}")

    # Sync Commands
    try:
        await bot.tree.sync()
        print(f"✅ Synced commands.")
    except Exception as e:
        print(f"❌ Failed to sync: {e}")

    # Set Presence
    activity = discord.Game(name="ブロスタ")
    await bot.change_presence(activity=activity)

    # Notify Owner
    try:
        owner = await bot.fetch_user(bot.config.OWNER_ID)
        await owner.send("✅ ボットがリファクタリング後の構成で再起動しました！")
    except Exception as e:
         print(f"❌ 起動通知失敗: {e}")

# Global Error Handler for Slash Commands (registered to tree)
async def on_tree_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    command = interaction.command.name if interaction.command else "unknown"
    print(f"🔴 Error in /{command}: {error}")
    # Forward to helper
    await send_error_to_owner(bot, bot.config, "SlashCommandError", error, f"/{command}")
    
    if not interaction.response.is_done():
        await interaction.response.send_message("エラーが発生しました。", ephemeral=True)
    else:
        await interaction.followup.send("エラーが発生しました。", ephemeral=True)

bot.tree.on_error = on_tree_error

if __name__ == "__main__":
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        print("エラー: DISCORD_BOT_TOKEN環境変数が設定されていません")
        exit(1)
    
    bot.run(token)
