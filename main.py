import discord
from discord.ext import commands
import os

from utils.config import ConfigManager
from utils.discord_helpers import send_error_to_owner

# ====== Bot Class Definition ======
class MyBot(commands.Bot):
    def __init__(self):
        # Intents 設定
        intents = discord.Intents.default()
        intents.members = True
        intents.voice_states = True
        intents.message_content = True
        
        super().__init__(command_prefix="!", intents=intents)
        
        # Attach shared config
        self.config = ConfigManager()

    async def setup_hook(self):
        # Validation on startup
        if self.config.OWNER_ID == 0:
            print("❌ エラー: OWNER_ID環境変数が設定されていません")
            # We continue but warn
        
        # Start console input listener
        self.loop.create_task(self.console_input_handler())

        # Load Extensions
        await self.load_all_extensions()

        # Sync Commands
        try:
            await self.tree.sync()
            print(f"✅ Synced commands.")
        except Exception as e:
            print(f"❌ Failed to sync: {e}")

    async def load_all_extensions(self):
        initial_extensions = [
            "cogs.admin",
            "cogs.voice",
            "cogs.chat",
            "cogs.system",
            "cogs.brawlstars",
        ]
        
        for extension in initial_extensions:
            try:
                # すでにロードされている場合はリロード
                if extension in self.extensions:
                    await self.reload_extension(extension)
                    print(f"✅ Extension reloaded: {extension}")
                else:
                    await self.load_extension(extension)
                    print(f"✅ Extension loaded: {extension}")
            except Exception as e:
                print(f"❌ Failed to load/reload extension {extension}: {e}")

    async def console_input_handler(self):
        """コンソール（SparkedHost/Terminal）からの入力を監視"""
        import aioconsole # 非同期で標準入力を待機するために必要
        print("⌨️  Console commands ready. Type 'reload' to refresh cogs.")
        
        while True:
            try:
                line = await aioconsole.ainput()
                command = line.strip().lower()
                
                if command == "reload":
                    print("🔄 Reloading all extensions...")
                    await self.load_all_extensions()
                    # コマンドの同期も再実行
                    await self.tree.sync()
                    print("✨ Reload complete!")
                elif command == "help":
                    print("📋 Available console commands: reload, help")
                elif command == "":
                    continue
                else:
                    print(f"❓ Unknown command: {command}")
            except Exception as e:
                print(f"❌ Console error: {e}")

    async def on_ready(self):
        print(f"ログイン成功: {self.user}")
        
        # Set Presence
        activity = discord.Game(name="ブロスタ")
        await self.change_presence(activity=activity)

        # Notify Owner
        try:
            owner = self.get_user(self.config.OWNER_ID) or await self.fetch_user(self.config.OWNER_ID)
            await owner.send("✅ ボットがリファクタリング後の構成で起動(再接続)しました！")
        except Exception as e:
             print(f"❌ 起動通知失敗: {e}")

bot = MyBot()

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
