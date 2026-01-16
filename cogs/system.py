import discord
from discord.ext import commands, tasks
from discord import app_commands
from datetime import datetime, timezone, timedelta, time
import sys
import traceback
from typing import Optional

from utils.discord_helpers import send_error_to_owner, log_to_owner
from utils.helpers import run_unit_tests # Added import

# Note: config is accessed via self.bot.config

JST = timezone(timedelta(hours=9))

class SystemCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.daily_ping.start()

    def cog_unload(self):
        self.daily_ping.cancel()

    # ====== Global Error Handler (Listeners) ======
    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        # Traditional command error handler (if needed)
        pass

    # ====== Tasks ======
    @tasks.loop(time=time(hour=15, minute=0, second=0))  # UTC 15:00 = JST 0:00
    async def daily_ping(self):
        """日本時間0時に自動でpingを送信"""
        config = self.bot.config
        if config.AUTO_PING_CHANNEL_ID == 0:
            return
        
        try:
            channel = self.bot.get_channel(config.AUTO_PING_CHANNEL_ID)
            if channel is None:
                print(f"❌ 自動ping: チャンネルが見つかりません (ID: {config.AUTO_PING_CHANNEL_ID})")
                return
            
            latency = round(self.bot.latency * 1000)
            current_time = datetime.now(JST).strftime("%Y年%m月%d日 %H:%M:%S")
            
            embed = discord.Embed(
                title="🏓 Daily Ping",
                description=f"レイテンシ: **{latency}ms**\n\n-# このメッセージはReplit.comによって自動実行されています",
                color=discord.Color.green() if latency < 200 else discord.Color.orange()
            )
            embed.set_footer(text=f"自動実行: {current_time}")
            
            await channel.send(embed=embed)
            print(f"✅ 自動ping送信完了 [{current_time}]")
            
            # 自動テストも実行
            await self.run_daily_test(channel)
        except Exception as e:
            print(f"❌ 自動ping送信失敗: {e}")

    async def run_daily_test(self, channel):
        """日本時間0時に自動でシステムテストを実行"""
        try:
            config = self.bot.config
            current_time = datetime.now(JST).strftime("%Y年%m月%d日 %H:%M:%S")
            results = []
            
            # 1. レイテンシチェック
            latency = round(self.bot.latency * 1000)
            if latency < 200:
                results.append(f"✅ レイテンシ: {latency}ms")
            else:
                results.append(f"⚠️ レイテンシ: {latency}ms（高め）")
            
            # 2. 設定ファイル読み書きチェック
            try:
                config.load_config()
                results.append("✅ 設定ファイル: 読み込み可能")
            except Exception as e:
                results.append(f"❌ 設定ファイル: {e}")
            
            results.append(f"✅ VC自動切断機能: {'ON' if config.vc_block_enabled else 'OFF'}")
            results.append(f"✅ 対象ユーザー数: {len(config.BLOCKED_USERS)}人")
            results.append(f"✅ 対象VC数: {len(config.TARGET_VC_IDS)}個")
            results.append(f"✅ 管理者数: {len(config.ADMIN_IDS)}人")
            
            # 3. 単体テスト
            test_results = run_unit_tests()
            results.extend(test_results)
            
            embed = discord.Embed(
                title="🔧 Daily System Check",
                description="\n".join(results) + "\n\n-# このメッセージはReplit.comによって自動実行されています",
                color=discord.Color.green()
            )
            embed.set_footer(text=f"自動実行: {current_time}")
            
            await channel.send(embed=embed)
            print(f"✅ 自動テスト送信完了 [{current_time}]")
        except Exception as e:
            print(f"❌ 自動テスト送信失敗: {e}")

    # ====== Commands ======
    @app_commands.command(name="ping", description="ボットの応答速度をテスト")
    async def ping_command(self, interaction: discord.Interaction):
        latency = round(self.bot.latency * 1000)
        embed = discord.Embed(
            title="🏓 Pong!",
            description=f"レイテンシ: **{latency}ms**",
            color=discord.Color.green() if latency < 200 else discord.Color.orange()
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="restart", description="ボットを再起動（オーナーのみ）")
    async def restart_command(self, interaction: discord.Interaction):
        config = self.bot.config
        if interaction.user.id != config.OWNER_ID:
            await interaction.response.send_message("権限がありません。", ephemeral=True)
            await log_to_owner(self.bot, config, "error", interaction.user, "/restart", "Unauthorized access attempt")
            return
        
        await interaction.response.send_message("🔄 ボットを再起動します...", ephemeral=True)
        print(f"🔄 再起動要求 by {interaction.user}")
        await self.bot.close()
        sys.exit(0)

    @app_commands.command(name="test", description="ボットのシステムチェック（オーナーのみ）")
    async def test_command(self, interaction: discord.Interaction):
        config = self.bot.config
        if interaction.user.id != config.OWNER_ID:
            await interaction.response.send_message("権限がありません。", ephemeral=True)
            await log_to_owner(self.bot, config, "error", interaction.user, "/test", "Unauthorized access attempt")
            return
        
        await interaction.response.defer()
        results = []
        
        # 1. レイテンシ
        latency = round(self.bot.latency * 1000)
        results.append(f"✅ レイテンシ: {latency}ms" if latency < 200 else f"⚠️ レイテンシ: {latency}ms（高め）")
        
        # 2. Config
        try:
            config.load_config()
            results.append("✅ 設定ファイル: OK")
        except Exception as e:
            results.append(f"❌ 設定ファイル: {e}")
        
        # Stats
        results.append(f"✅ VC自動切断: {'ON' if config.vc_block_enabled else 'OFF'}")
        results.append(f"✅ 対象ユーザー: {len(config.BLOCKED_USERS)}人")
        results.append(f"✅ 対象VC: {len(config.TARGET_VC_IDS)}個")
        results.append(f"✅ 管理者数: {len(config.ADMIN_IDS)}人")
        
        # Helper Test
        try:
            owner = self.bot.get_user(config.OWNER_ID) or await self.bot.fetch_user(config.OWNER_ID)
            await owner.send(embed=discord.Embed(title="🔧 DMテスト", description="System Check", color=discord.Color.blue()))
            results.append("✅ DM送信: 成功")
        except Exception as e:
            results.append(f"❌ DM送信: {e}")

        # Unit Tests
        test_results = run_unit_tests()
        results.extend(test_results)

        # Permissions
        if interaction.guild and interaction.guild.me:
            if interaction.guild.me.guild_permissions.move_members:
                results.append("✅ VC切断権限: あり")
            else:
                results.append("❌ VC切断権限: なし")

        embed = discord.Embed(title="🔧 システムチェック結果", description="\n".join(results), color=discord.Color.green())
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="autoping", description="毎日0時の自動pingを設定（オーナーのみ）")
    @app_commands.describe(action="設定するアクション", channel="pingを送信するチャンネル")
    @app_commands.choices(action=[
        app_commands.Choice(name="on - 有効化", value="on"),
        app_commands.Choice(name="off - 無効化", value="off"),
        app_commands.Choice(name="status - 確認", value="status")
    ])
    async def autoping_command(self, interaction: discord.Interaction, action: str, channel: Optional[discord.TextChannel] = None):
        config = self.bot.config
        if interaction.user.id != config.OWNER_ID:
            await interaction.response.send_message("権限がありません。", ephemeral=True)
            await log_to_owner(self.bot, config, "error", interaction.user, "/autoping", "Unauthorized access attempt")
            return

        if action == "on":
            if not channel:
                await interaction.response.send_message("❌ チャンネルを指定してください", ephemeral=True)
                return
            config.AUTO_PING_CHANNEL_ID = channel.id
            config.save_config()
            await interaction.response.send_message(f"✅ 自動pingを設定: {channel.mention}", ephemeral=True)
        elif action == "off":
            config.AUTO_PING_CHANNEL_ID = 0
            config.save_config()
            await interaction.response.send_message("✅ 自動pingを無効化", ephemeral=True)
        elif action == "status":
            if config.AUTO_PING_CHANNEL_ID == 0:
                await interaction.response.send_message("📋 自動ping: 無効", ephemeral=True)
            else:
                ch_mention = f"<#{config.AUTO_PING_CHANNEL_ID}>" # simple format in case cache missing
                await interaction.response.send_message(f"📋 自動ping: 有効 - {ch_mention}", ephemeral=True)

    # ====== Help Command ======
    class HelpView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=180)
            self.current_page = 0
            self.pages = [self.get_public_page(), self.get_admin_page(), self.get_owner_page()]
            self.update_buttons()

        def get_public_page(self):
            embed = discord.Embed(title="📖 ヘルプ - 一般", color=discord.Color.green())
            embed.add_field(name="🏓 /ping", value="応答速度確認", inline=False)
            embed.add_field(name="🎮 /playerlist", value="お荷物プレイヤーリスト表示", inline=False)
            embed.set_footer(text="Page 1/3")
            return embed
        
        def get_admin_page(self):
             embed = discord.Embed(title="📖 ヘルプ - 管理者", color=discord.Color.blue())
             embed.add_field(name="🔧 /switch", value="VC切断ON/OFF", inline=False)
             embed.add_field(name="👤 /blockuser", value="ユーザー追加/削除", inline=False)
             embed.add_field(name="🎙️ /blockvc", value="VC追加/削除", inline=False)
             embed.add_field(name="📋 /list", value="設定一覧", inline=False)
             embed.add_field(name="🎭 /simvc", value="VC切断シミュレーション", inline=False)
             embed.set_footer(text="Page 2/3")
             return embed

        def get_owner_page(self):
             embed = discord.Embed(title="📖 ヘルプ - オーナー", color=discord.Color.orange())
             embed.add_field(name="👨‍💼 /addadmin /removeadmin", value="管理者管理", inline=False)
             embed.add_field(name="📋 /listadmin", value="管理者一覧", inline=False)
             embed.add_field(name="🚪 /exit", value="管理者モード終了", inline=False)
             embed.add_field(name="💬 /say", value="代理発言", inline=False)
             embed.add_field(name="🧹 /clear", value="チャット削除", inline=False)
             embed.add_field(name="✉️ /dm", value="DM送信", inline=False)
             embed.add_field(name="🔧 /test", value="システムチェック", inline=False)
             embed.add_field(name="🔄 /restart", value="ボット再起動", inline=False)
             embed.add_field(name="⏰ /autoping", value="自動ping設定", inline=False)
             embed.add_field(name="🎮 プレイヤー管理", value="/player_edit, /player_delete\n/scanhistory", inline=False)
             embed.set_footer(text="Page 3/3")
             return embed

        def update_buttons(self):
            self.prev_button.disabled = self.current_page == 0
            self.next_button.disabled = self.current_page == len(self.pages) - 1

        @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
        async def prev_button(self, interaction, button):
            self.current_page -= 1
            self.update_buttons()
            await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)

        @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
        async def next_button(self, interaction, button):
            self.current_page += 1
            self.update_buttons()
            await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)

    @app_commands.command(name="help", description="ボットの使い方を表示")
    async def help_command(self, interaction: discord.Interaction):
        view = self.HelpView()
        await interaction.response.send_message(embed=view.pages[0], view=view, ephemeral=True)


async def setup(bot):
    await bot.add_cog(SystemCog(bot))
