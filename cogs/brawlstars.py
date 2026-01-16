import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional, List
import datetime
from datetime import datetime, timezone, timedelta
import os
import aiohttp
import asyncio
import json as json_lib
# Google libraries
from google.cloud import vision
from google.oauth2 import service_account

from utils.discord_helpers import log_to_owner, send_error_to_owner
from utils.helpers import normalize_text

JST = timezone(timedelta(hours=9))

class BrawlStarsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.BRAWLSTARS_CHANNELS = {
            1379353245658648717,
            1445382523449376911
        }
        self.vision_client = self.setup_vision_api()
        self.last_list_message = None # In-memory reference for auto-update
        
        # Register Persistent View on Cog load
        # This makes the button work even after restart
        self.bot.add_view(self.PlayerListPagination(self.bot))

    def setup_vision_api(self):
        try:
            credentials_json = os.environ.get("GOOGLE_VISION_CREDENTIALS_JSON")
            if credentials_json:
                credentials_dict = json_lib.loads(credentials_json)
                credentials = service_account.Credentials.from_service_account_info(credentials_dict)
                client = vision.ImageAnnotatorClient(credentials=credentials)
                print("✅ Google Vision API初期化完了")
                return client
            elif os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
                client = vision.ImageAnnotatorClient()
                print("✅ Google Vision API初期化完了")
                return client
            else:
                print("⚠️ Google Vision API未設定（画像認識機能は無効）")
                return None
        except Exception as e:
            print(f"❌ Google Vision API初期化失敗: {e}")
            return None

    # ====== 名前オートコンプリート ======
    async def name_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        config = self.bot.config
        choices = [
            app_commands.Choice(name=name, value=name)
            for name in config.player_names.keys() if current.lower() in name.lower()
        ]
        return choices[:25]

    # ====== 画像スキャン Listener ======
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        # ブロスタチャンネルでのみ動作
        if message.channel.id in self.BRAWLSTARS_CHANNELS and message.attachments:
            config = self.bot.config
            for attachment in message.attachments:
                if attachment.content_type and attachment.content_type.startswith('image/'):
                    async with message.channel.typing():
                        try:
                            result = await self.extract_brawlstars_name(attachment.url)
                            
                            if result and result['name']:
                                player_name = result['name']
                                
                                # 名前がすでに登録されているかチェック
                                if player_name in config.player_names:
                                    # 登録回数を増やす
                                    config.player_register_count[player_name] = config.player_register_count.get(player_name, 0) + 1
                                    count = config.player_register_count[player_name]
                                    
                                    # データの更新
                                    config.player_names[player_name]['last_updated'] = datetime.now(JST).isoformat()
                                    config.save_player_names()

                                    await self.update_latest_list()
                                    
                                    await message.channel.send(f"「{player_name}」は既に追加されてるよ！通算{count}回目だね")
                                    print(f"🔄 報告カウントアップ: {player_name} ({count}回目)")
                                
                                else:
                                    # 新規登録
                                    config.player_names[player_name] = {
                                        'name': player_name,
                                        'registered_at': datetime.now(JST).isoformat(),
                                        'last_updated': datetime.now(JST).isoformat()
                                    }
                                    config.player_register_count[player_name] = 1
                                    config.save_player_names()
                                    await self.update_latest_list()
                                    await message.channel.send(f"お荷物プレイヤー「{player_name}」を新しく記録したよ！")
                                    print(f"✅ 新規名前登録: {player_name}")
                            else:
                                print(f"⚠️ プロフィール認識失敗: {message.author.name}")
                        except Exception as e:
                            print(f"❌ 画像認識エラー: {e}")
                            await send_error_to_owner(self.bot, config, "BrawlStars Scan Error", e, f"User: {message.author.name}")
                    break # 最初の1枚のみ処理

    # ====== 内部ロジック ======
    async def extract_text_from_image(self, image_url: str) -> Optional[str]:
        if not self.vision_client:
            return None
        
        try:
            # import aiohttp (Moved to top)
            async with aiohttp.ClientSession() as session:
                async with session.get(image_url) as response:
                    if response.status != 200:
                        print(f"⚠️ 画像取得失敗: HTTP {response.status}")
                        return None
                    content_length = response.headers.get('Content-Length')
                    MAX_SIZE = 16 * 1024 * 1024
                    if content_length and int(content_length) > MAX_SIZE:
                        print(f"⚠️ 画像サイズ超過 (Header): {content_length}")
                        return None
                    image_data = await response.read()
                    if len(image_data) > MAX_SIZE:
                         print(f"⚠️ 画像サイズ超過 (Body): {len(image_data)}")
                         return None
            
            image = vision.Image(content=image_data)
            
            def run_vision():
                return self.vision_client.text_detection(image=image)
            
            response = await asyncio.to_thread(run_vision)
            
            texts = response.text_annotations
            if texts:
                return texts[0].description
            return None
        except aiohttp.ClientError as e:
            print(f"❌ 画像ダウンロードエラー: {e}")
            return None
        except Exception as e:
            print(f"❌ 画像認識エラー: {e}")
            return None

    async def extract_brawlstars_name(self, image_url: str) -> Optional[dict]:
        text = await self.extract_text_from_image(image_url)
        if not text:
            return None
        
        if "報告" in text:
            print("⚠️ リザルト画面（報告ボタンあり）を検出したためスキップします。")
            return None
        
        lines = [line.strip() for line in text.strip().split('\n') if line.strip() and "BOO!" not in line]
        
        result = {'name': None, 'player_id': None, 'trophies': None}
        print(f"🔍 認識テキスト:\n{text}\n")
        
        # Pattern 1
        for i, line in enumerate(lines):
            if 'プロフィール' in line or 'PROFILE' in line.upper():
                for j in range(i+1, min(i+4, len(lines))):
                    next_line = lines[j].strip()
                    if (len(next_line) >= 2 and 
                        'キャラクター' not in next_line and
                        'CHARACTER' not in next_line.upper() and
                        not next_line.startswith('#') and
                        not next_line.replace(',', '').isdigit()):
                        result['name'] = next_line
                        break
                break
        
        # Pattern 2
        if not result['name']:
            for i, line in enumerate(lines):
                if line.startswith('#') and len(line) > 5:
                    result['player_id'] = line
                    if i > 0:
                        prev_line = lines[i-1].strip()
                        if len(prev_line) >= 2:
                            result['name'] = prev_line
                    break

        # Pattern 3
        if not result['name']:
            for i, line in enumerate(lines):
                if (line.replace('_', '').replace('-', '').isalnum() and 
                    len(line) >= 5 and 
                    any(c.isalpha() for c in line)):
                    result['player_id'] = line
                    if i > 0:
                        prev_line = lines[i-1].strip()
                        if len(prev_line) >= 2 and prev_line != 'プロフィール':
                            result['name'] = prev_line
                    break
        
        return result if result['name'] else None

    # ====== Player List View ======
    class PlayerListPagination(discord.ui.View):
        def __init__(self, bot_instance):
            super().__init__(timeout=None)
            self.bot = bot_instance

        @discord.ui.button(label="リストを更新", style=discord.ButtonStyle.green, emoji="🔄", custom_id="player_list:refresh")
        async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            cog = self.bot.get_cog("BrawlStarsCog")
            if not cog: return
            
            config = self.bot.config
            if not config.player_names:
                await interaction.response.edit_message(content="📋 登録されているプレイヤーはいません", embed=None, view=None)
                return
            
            embed = cog.create_player_list_embed()
            await interaction.response.edit_message(embed=embed, view=self)
            cog.last_list_message = interaction.message

    def create_player_list_embed(self):
        config = self.bot.config
        def get_count(name):
            val = config.player_register_count.get(name)
            return val if isinstance(val, int) else 0

        sorted_players = sorted(
            config.player_names.keys(),
            key=get_count,
            reverse=True
        )

        player_list = []
        for name in sorted_players:
            count = config.player_register_count.get(name, 1)
            player_list.append(f"• **{name}** — `{count}回報告`")

        description_text = "\n".join(player_list) if player_list else "登録者はまだいません。"
        if len(description_text) > 4000:
            description_text = description_text[:3997] + "..."

        embed = discord.Embed(
            title="🎮 お荷物プレイヤーリスト",
            description=description_text,
            color=discord.Color.red()
        )
        embed.set_footer(text=f"合計: {len(config.player_names)}人 | 最終更新: {datetime.now(JST).strftime('%H:%M:%S')}")
        return embed

    async def update_latest_list(self):
        config = self.bot.config
        if self.last_list_message and config.player_names:
            try:
                view = self.PlayerListPagination(self.bot)
                embed = self.create_player_list_embed()
                embed.set_footer(text=f"{embed.footer.text} (自動更新済み)")
                await self.last_list_message.edit(embed=embed, view=view)
                print("✨ リストを自動更新しました")
            except discord.NotFound:
                print("⚠️ リスト更新失敗: メッセージが見つかりません (削除された可能性があります)")
                self.last_list_message = None
            except Exception as e:
                print(f"⚠️ 自動更新失敗: {e}")
                self.last_list_message = None

    # ====== Commands ======
    @app_commands.command(name="playerlist", description="登録されているお荷物プレイヤー一覧を表示")
    async def playerlist_command(self, interaction: discord.Interaction):
        config = self.bot.config
        if not config.player_names:
            await interaction.response.send_message("📋 登録されているプレイヤーはいません", ephemeral=False)
            return
        
        view = self.PlayerListPagination(self.bot)
        embed = self.create_player_list_embed()
        
        await interaction.response.send_message(embed=embed, view=view)
        self.last_list_message = await interaction.original_response()

    @app_commands.command(name="player_edit", description="登録されたプレイヤー名を修正します（オーナーのみ）")
    @app_commands.autocomplete(old_name=name_autocomplete)
    async def player_edit_command(self, interaction: discord.Interaction, old_name: str, new_name: str):
        config = self.bot.config
        if interaction.user.id != config.OWNER_ID:
            await interaction.response.send_message("オーナーのみ使用可能です。", ephemeral=True)
            await log_to_owner(self.bot, config, "error", interaction.user, "/player_edit", "Unauthorized access attempt")
            return

        if old_name not in config.player_names:
            await interaction.response.send_message(f"❌ 「{old_name}」は見つかりませんでした。", ephemeral=True)
            return

        config.player_names[new_name] = config.player_names.pop(old_name)
        config.player_names[new_name]['name'] = new_name
        if old_name in config.player_register_count:
            config.player_register_count[new_name] = config.player_register_count.pop(old_name)

        config.save_player_names()
        await interaction.response.send_message(f"✅ 修正完了：`{old_name}` → `{new_name}`")

    @app_commands.command(name="player_delete", description="指定したプレイヤーのデータを削除します（オーナーのみ）")
    @app_commands.autocomplete(name=name_autocomplete)
    async def player_delete_command(self, interaction: discord.Interaction, name: str):
        config = self.bot.config
        if interaction.user.id != config.OWNER_ID:
            await interaction.response.send_message("オーナーのみ使用可能です。", ephemeral=True)
            await log_to_owner(self.bot, config, "error", interaction.user, "/player_delete", "Unauthorized access attempt")
            return

        if name not in config.player_names:
            await interaction.response.send_message(f"❌ 「{name}」は見つかりませんでした。", ephemeral=True)
            return

        del config.player_names[name]
        if name in config.player_register_count:
            del config.player_register_count[name]

        config.save_player_names()
        await interaction.response.send_message(f"🗑️ 「{name}」のデータを削除しました。")

    @app_commands.command(name="scanhistory", description="過去の画像を遡って一括登録（オーナーのみ）")
    async def scanhistory_command(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None, limit: int = 100):
        config = self.bot.config
        if interaction.user.id != config.OWNER_ID:
            await interaction.response.send_message("このコマンドはオーナーのみが使用できます。", ephemeral=True)
            await log_to_owner(self.bot, config, "error", interaction.user, "/scanhistory", "Unauthorized access attempt")
            return
        
        target_channel = channel or interaction.channel
        if target_channel.id not in self.BRAWLSTARS_CHANNELS:
            await interaction.response.send_message(f"❌ 指定されたブロスタチャンネルでのみ使用できます。", ephemeral=True)
            return
        
        if limit > 2000: limit = 2000
        await interaction.response.defer(ephemeral=True)

        try:
            start_time = datetime.now(JST)
            messages_with_images = []
            async for msg in target_channel.history(limit=limit):
                if msg.author.bot: continue
                if msg.attachments:
                    for attachment in msg.attachments:
                        if attachment.content_type and attachment.content_type.startswith('image/'):
                            messages_with_images.append((msg, attachment))
                            break
            
            if not messages_with_images:
                await interaction.followup.send("📋 画像が見つかりませんでした。")
                return

            await interaction.followup.send(f"🔍 {len(messages_with_images)}件の画像を検出しました。処理を開始します...")
            
            success_count = 0 
            updated_count = 0
            failed_count = 0
            
            for msg, attachment in messages_with_images:
                result = await self.extract_brawlstars_name(attachment.url)
                if result and result['name']:
                    player_name = result['name']
                    if player_name in config.player_names:
                        config.player_register_count[player_name] = config.player_register_count.get(player_name, 1) + 1
                        updated_count += 1
                        config.player_names[player_name]['last_updated'] = msg.created_at.isoformat()
                    else:
                        player_data = {
                            'name': player_name,
                            'registered_at': msg.created_at.isoformat(),
                            'last_updated': msg.created_at.isoformat()
                        }
                        config.player_names[player_name] = player_data
                        config.player_register_count[player_name] = 1
                        success_count += 1
                else:
                    failed_count += 1
            
            config.save_player_names()
            elapsed = int((datetime.now(JST) - start_time).total_seconds())
            
            result_embed = discord.Embed(title="📊 過去データ一括登録完了", color=discord.Color.green())
            result_embed.add_field(name="👤 新規", value=f"{success_count}人", inline=True)
            result_embed.add_field(name="🔄 更新", value=f"{updated_count}件", inline=True)
            result_embed.add_field(name="❌ 失敗", value=f"{failed_count}枚", inline=True)
            result_embed.set_footer(text=f"合計: {len(messages_with_images)}枚 | 時間: {elapsed}秒")
            await interaction.followup.send(embed=result_embed)
        except Exception as e:
            await interaction.followup.send(f"❌ エラー: {e}")
            await send_error_to_owner(self.bot, config, "ScanHistory Error", e)
            print(f"❌ 一括登録エラー: {e}")

async def setup(bot):
    await bot.add_cog(BrawlStarsCog(bot))
