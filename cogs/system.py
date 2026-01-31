import discord
from discord.ext import commands, tasks
from discord import app_commands
from datetime import datetime, timezone, timedelta, time
import traceback
import psutil
import os
import gc
import asyncio # 不足していたインポートを追加
from typing import Optional

from utils.discord_helpers import send_error_to_owner, log_to_owner
from utils.helpers import run_unit_tests # インポートを追加

# 設定は self.bot.config 経由でアクセスされます

JST = timezone(timedelta(hours=9))

class SystemCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # リクエストにより廃止
        self.status_updater.start()
        # 0:00チェックのトリガーは依然として必要です
        self.midnight_check.start()

    def cog_unload(self):
        # self.daily_ping.cancel()
        self.status_updater.cancel()
        self.midnight_check.cancel()

    # ====== Global Error Handler (Listeners) ======
    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        # 従来のコマンドエラーハンドラー（必要な場合）
        pass

    # ====== Tasks ======
    @tasks.loop(time=time(hour=15, minute=0, second=0))  # UTC 15:00 = JST 0:00
    async def midnight_check(self):
        """日本時間0時に自動で実行される定期チェック"""
        if not self.bot:
            return
        await self.bot.wait_until_ready()
        
        config = self.bot.config
        if config.AUTO_PING_CHANNEL_ID == 0:
            return
        
        try:
            channel = self.bot.get_channel(config.AUTO_PING_CHANNEL_ID)
            if channel is None:
                # print(f"❌ 0時チェック: チャンネルが見つかりません (ID: {config.AUTO_PING_CHANNEL_ID})")
                return
            
            # システムテストを実行してメッセージを送信
            await self.run_daily_test(channel)
            # print(f"✅ 0時定期チェック完了 [{datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')}]")
        except Exception as e:
            print(f"❌ 0時定期チェック失敗: {e}")

    @tasks.loop(minutes=1.0)
    async def status_updater(self):
        """ボットのステータス（Presence）を定期的に更新"""
        if not self.bot:
            return
        await self.bot.wait_until_ready()
        
        try:
            # CPU使用率 (interval=None だと前回の呼び出しからの平均)
            cpu_usage = psutil.cpu_percent()
            
            # メモリ使用量 (現在のプロセスのみ)
            process = psutil.Process(os.getpid())
            mem_info = process.memory_info()
            mem_mb = mem_info.rss / 1024 / 1024
            
            # ステータスメッセージを作成
            status_text = f"CPU: {cpu_usage}% | RAM: {int(mem_mb)}MB"
            
            # Presence を更新
            activity = discord.Game(name=status_text)
            await self.bot.change_presence(activity=activity)
            # print(f"📊 Status Updated: {status_text}")
        except Exception as e:
            print(f"❌ ステータス更新失敗: {e}")

    async def memory_cleanup(self) -> float:
        """メモリを解放し、解放された量(MB)を返す"""
        process = psutil.Process(os.getpid())
        mem_before = process.memory_info().rss / 1024 / 1024
        
        # 1. 各Cogのクリーンアップを呼び出し
        # BrawlStarsCogの処理
        cog_bs = self.bot.get_cog("BrawlStarsCog")
        if cog_bs:
             # キャッシュクリアなどを検討（現在は特になし）
             pass
        
        # ChatCogの処理
        cog_chat = self.bot.get_cog("ChatCog")
        if cog_chat:
            # セッションのクリーンアップは通常tasks.loopだが、手動でコルーチンとして呼べるか確認
            # もしLoopなら、内部のロジックを手動で実行するか、あるいは単にgcに任せる
            if asyncio.iscoroutinefunction(cog_chat.session_cleanup):
                await cog_chat.session_cleanup()
        
        # 2. ガベージコレクション
        gc.collect()
        
        mem_after = process.memory_info().rss / 1024 / 1024
        released = mem_before - mem_after
        return max(0.0, released)

    async def run_daily_test(self, channel):
        """日本時間0時に自動でシステムテストを実行"""
        try:
            # メモリ解放を最初に実行
            released_mb = await self.memory_cleanup()
            
            config = self.bot.config
            current_time = datetime.now(JST).strftime("%Y年%m月%d日 %H:%M:%S")
            results = []
            has_error = False
            
            # 1. レイテンシチェック
            latency = round(self.bot.latency * 1000)
            if latency < 150: # ユーザーが「正常」の閾値として150を要求
                results.append(f"✅ レイテンシ: {latency}ms")
            else:
                results.append(f"⚠️ レイテンシ: {latency}ms（高め）")
                # has_error = True # レイテンシだけで「システム障害」とは言えないかもしれないが、ユーザーが150と言及したため
            
            # 2. 設定ファイル読み書きチェック
            try:
                config.load_config()
                results.append("✅ 設定ファイル: 読み込み可能")
            except Exception as e:
                results.append(f"❌ 設定ファイル: {e}")
                has_error = True
            
            # 3. 単体テスト
            test_results = run_unit_tests()
            results.extend(test_results)
            if any(r.startswith("❌") for r in test_results):
                has_error = True

            # 条件チェック: エラーがなく、レイテンシが150ms以下の場合
            if not has_error and latency <= 150:
                # 簡潔なメッセージ形式 (Embedを使用して「枠」をつける)
                reported_count = len(config.player_names)
                # checked_count = len(config.check_player_names) 
                # ユーザーが手動編集でこのラベルを「サーバーに登録されている総アカウント数」に変更しました
                checked_count = len(config.check_player_names)
                
                embed = discord.Embed(
                    title="✨ **システムステータス報告** ✨",
                    description=(
                        f"📶 レイテンシ: **{latency}ms**\n"
                        f"👥 報告されたプレイヤー数: **{reported_count}**\n"
                        f"🔍 サーバーに登録されている総アカウント数: **{checked_count}**\n"
                        f"🧹 メモリ解放量: **{released_mb:.1f}MB**\n\n"
                        "✅ **すべてのシステムは正常に稼働しています**"
                    ),
                    color=discord.Color.green()
                )
                embed.set_footer(text=f"Sparkedhost.com 自動実行 | {current_time}")
                await channel.send(embed=embed)
            else:
                # 異常がある場合は詳細を表示
                results.append(f"✅ メモリ解放: {released_mb:.1f}MB")
                results.append(f"✅ VC自動切断機能: {'ON' if config.vc_block_enabled else 'OFF'}")
                results.append(f"✅ 対象ユーザー数: {len(config.BLOCKED_USERS)}人")
                results.append(f"✅ 対象VC数: {len(config.TARGET_VC_IDS)}個")
                
                embed = discord.Embed(
                    title="🔧 デイリーシステムチェック (詳細/アラート)",
                    description="\n".join(results),
                    color=discord.Color.orange() if not has_error else discord.Color.red()
                )
                embed.set_footer(text=f"Sparkedhost.com 自動実行 | {current_time}")
                await channel.send(embed=embed)
                
            print(f"✅ システムチェック送信完了 [{current_time}] (解放: {released_mb:.1f}MB)")
        except Exception as e:
            print(f"❌ システムチェック送信失敗: {e}")
            traceback.print_exc()

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
        
        await interaction.response.defer(ephemeral=True)
        # 一貫性のために、0時チェックと同じロジックを使用します
        await self.run_daily_test(interaction.channel)
        await interaction.followup.send("システムチェックを実行しました。チャンネルのメッセージを確認してください。", ephemeral=True)

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
                ch_mention = f"<#{config.AUTO_PING_CHANNEL_ID}>" # キャッシュがない場合のシンプルなフォーマット
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
            embed.add_field(name="💬 /say", value="代理発言", inline=False)
            embed.add_field(name="🔍 /check", value="プレイヤー照会・ロール付与", inline=False)
            embed.add_field(name="🎮 /playerlist", value="お荷物プレイヤーリスト表示", inline=False)
            embed.set_footer(text="ページ 1/3")
            return embed
        
        def get_admin_page(self):
             embed = discord.Embed(title="📖 ヘルプ - 管理者", color=discord.Color.blue())
             embed.add_field(name="🔧 /switch", value="VC切断ON/OFF", inline=False)
             embed.add_field(name="👤 /blockuser", value="ユーザー追加/削除", inline=False)
             embed.add_field(name="🎙️ /blockvc", value="VC追加/削除", inline=False)
             embed.add_field(name="📋 /list", value="設定一覧", inline=False)
             embed.add_field(name="🎭 /simvc", value="VC切断シミュレーション", inline=False)
             embed.add_field(name="🧹 /clear", value="チャット削除", inline=False)
             embed.add_field(name="🎮 プレイヤー管理", value="/player_edit, /player_delete\n/scanhistory", inline=False)
             embed.set_footer(text="ページ 2/3")
             return embed

        def get_owner_page(self):
             embed = discord.Embed(title="📖 ヘルプ - オーナー", color=discord.Color.orange())
             embed.add_field(name="👨‍💼 /addadmin /removeadmin", value="管理者管理", inline=False)
             embed.add_field(name="📋 /listadmin", value="管理者一覧", inline=False)
             embed.add_field(name="🚪 /exit", value="管理者モード終了", inline=False)
             embed.add_field(name="✉️ /dm", value="DM送信", inline=False)
             embed.add_field(name="🔧 /test", value="システムチェック", inline=False)
             embed.add_field(name="🔄 /restart", value="ボット再起動", inline=False)
             embed.add_field(name="⏰ /autoping", value="自動ping設定", inline=False)
             embed.set_footer(text="ページ 3/3")
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
