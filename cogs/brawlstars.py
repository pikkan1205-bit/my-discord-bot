import discord
from discord.ext import commands, tasks
from discord import app_commands
from typing import Optional, List
import datetime
import unicodedata
from datetime import datetime, timezone, timedelta
import os
import aiohttp
import asyncio
import json as json_lib
import gc
# Google関連のライブラリ
from google.cloud import vision
from google.oauth2 import service_account
import google.generativeai as genai
from PIL import Image
import io

from utils.discord_helpers import log_to_owner, send_error_to_owner
from utils.helpers import normalize_text

JST = timezone(timedelta(hours=9))

class BrawlStarsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.BRAWLSTARS_CHANNELS = {
            1379353245658648717,
            1445382523449376911,
            1464623789584285880
        }
        self.CHECK_CHANNEL_IDS = {
            1379796929667661824,
            1464623880189513890
        }
        self.CHECK_CHANNEL_ID = 1379796929667661824 # 一括コマンド用のプライマリチャンネル
        self.LOG_CHANNEL_ID = 1451604528171585667
        self.SAFE_ROLE_ID = 1379322863215186094
        self.vision_client = self.setup_vision_api()
        self.gemini_flash, self.gemini_lite = self.setup_gemini_api()
        self.last_list_message = None # 自動更新用のメモリ内参照用
        
        # エラー追跡 {user_id: message_object}
        self.pending_error_messages = {}
        self.ERRO_CLEANUP_TIMEOUT = 180 # 3分間
        self.error_cleanup.start()
        
        # レート制限履歴 {user_id: [タイムスタンプ]}
        self.SCAN_HISTORY_FILE = "scan_history.json"
        self.scan_history = self.load_scan_history()
        
        # Cogロード時に永続的なViewを登録
        # これにより、再起動後もボタンが機能するようになります
        self.bot.add_view(self.PlayerListPagination(self.bot))
        
        # レート制限用の並行処理ロック
        self.lock = asyncio.Lock()

        # 並行処理制限（待機列）用のセマフォとカウンター
        self.queue_semaphore = asyncio.Semaphore(1)
        self.queue_count = 0
        self.queue_msg = None
        self.queue_lock = asyncio.Lock() # 通知更新用ロック

    def cog_unload(self):
        self.error_cleanup.cancel()

    @tasks.loop(minutes=2.0)
    async def error_cleanup(self):
        """定期的に古いエラーメッセージへの参照をクリア（メモリリーク対策）"""
        now = datetime.now(JST)
        # メモ: エラーにタイムスタンプがないため、定期的にクリアします。
        # または、辞書が大きくなりすぎた場合にすべてクリアすることもできます。
        # しかし、安全策として、cleanup_user_errorsで使用されるため、参照をクリアするだけにします。
        # ボットがアクティブであれば、この辞書が無制限に大きくなることはありませんが、これは安全網です。
        if len(self.pending_error_messages) > 100:
            self.pending_error_messages.clear()

    def load_scan_history(self):
        """スキャン履歴をJSONから読み込む"""
        if os.path.exists(self.SCAN_HISTORY_FILE):
            try:
                with open(self.SCAN_HISTORY_FILE, "r", encoding="utf-8") as f:
                    data = json_lib.load(f)
                    # keyをint型に戻す (GLOBAL_KEY=0も含む)
                    return {int(k): v for k, v in data.items()}
            except:
                pass
        return {}

    def save_scan_history(self):
        """スキャン履歴をJSONに保存"""
        try:
            with open(self.SCAN_HISTORY_FILE, "w", encoding="utf-8") as f:
                json_lib.dump(self.scan_history, f, indent=2)
        except Exception as e:
            print(f"⚠️ スキャン履歴保存エラー: {e}")


    class HazardDecisionView(discord.ui.View):
        def __init__(self, bot, user, player_name, player_id, sc_id, message_id, channel_id, cog):
            super().__init__(timeout=None)
            self.bot = bot
            self.user = user
            self.player_name = player_name
            self.player_id = player_id
            self.sc_id = sc_id
            self.message_id = message_id
            self.channel_id = channel_id
            self.cog = cog
            self.authorized_users = {1163117069173272576, 1127253848155754557}

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id not in self.authorized_users:
                # インタラクションに失敗しました（標準エラーメッセージ）
                return False
            return True

        @discord.ui.button(label="このメンバーを受け入れる", style=discord.ButtonStyle.green)
        async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
            config = self.bot.config
            
            # 1. データの移動 (player_names -> check_player_names)
            if self.player_name in config.player_names:
                del config.player_names[self.player_name]
            
            config.check_player_names[self.player_name] = {
                'name': self.player_name,
                'player_id': self.player_id,
                'sc_id': self.sc_id,
                'checked_at': datetime.now(JST).isoformat(),
                'user_id': self.user.id,
                'message_id': self.message_id
            }
            config.save_player_names()
            config.save_check_player_names()
            
            # 2. ロール付与
            try:
                role = interaction.guild.get_role(self.cog.SAFE_ROLE_ID)
                if role:
                    member = interaction.guild.get_member(self.user.id) or await interaction.guild.fetch_member(self.user.id)
                    if member:
                        await member.add_roles(role)
            except Exception as e:
                print(f"❌ Role grant failed in HazardDecision: {e}")

            # 3. リアクション（もし元のメッセージがあれば）
            try:
                channel = self.bot.get_channel(self.channel_id) or await self.bot.fetch_channel(self.channel_id)
                msg = await channel.fetch_message(self.message_id)
                emoji = self.bot.get_emoji(1342392510764286012)
                await msg.add_reaction(emoji or "✅")
            except:
                pass

            await interaction.response.edit_message(content=f"✅ {self.user.name} を受け入れました。プレイヤー: {self.player_name}", view=None)

        @discord.ui.button(label="拒否する", style=discord.ButtonStyle.red)
        async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
            view = BrawlStarsCog.HazardBanConfirmView(self.bot, self.user, self.player_name, self.cog, self)
            await interaction.response.edit_message(content=f"⚠️ **本当に拒否しますか？**\n（はいを選ぶと {self.user.mention} がバンされます）", view=view)

    class HazardBanConfirmView(discord.ui.View):
        def __init__(self, bot, user, player_name, cog, original_view):
            super().__init__(timeout=60)
            self.bot = bot
            self.user = user
            self.player_name = player_name
            self.cog = cog
            self.original_view = original_view
            self.authorized_users = {1163117069173272576, 1127253848155754557}

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id not in self.authorized_users:
                return False
            return True

        @discord.ui.button(label="はい", style=discord.ButtonStyle.danger)
        async def confirm_ban(self, interaction: discord.Interaction, button: discord.ui.Button):
            try:
                await interaction.guild.ban(self.user, reason=f"Hazard Player registration rejected: {self.player_name}")
                await interaction.response.edit_message(content="❌ 受け入れを拒否しました。実行者をBANしました。", view=None)
            except Exception as e:
                await interaction.response.edit_message(content=f"❌ BANに失敗しました: {e}", view=None)

        @discord.ui.button(label="いいえ", style=discord.ButtonStyle.gray)
        async def cancel_ban(self, interaction: discord.Interaction, button: discord.ui.Button):
            await interaction.response.edit_message(content=f"⚠️ 要注意人物の来訪\nプレイヤー: {self.player_name}\n実行者: {self.user.mention}", view=self.original_view)

    class ConfirmUpdateView(discord.ui.View):
        def __init__(self, bot, user_id, existing_accounts, new_player_name, new_message, channel_id, cog):
            """
            existing_accounts: (player_name, message_id) などのリスト
            """
            super().__init__(timeout=180) # 3分間
            self.bot = bot
            self.user_id = user_id
            self.existing_accounts = existing_accounts # 名前リスト
            self.new_player_name = new_player_name
            self.new_message = new_message
            self.channel_id = channel_id
            self.cog = cog
            self.message = None # 送信後に設定される

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id != self.user_id:
                await interaction.response.send_message("この操作は画像を送信した本人のみ可能です。", ephemeral=True)
                return False
            return True

        async def on_timeout(self):
            if self.message:
                try:
                    await self.message.delete()
                except:
                    pass
            if self.new_message:
                try:
                    await self.new_message.delete()
                except:
                    pass

        @discord.ui.button(label="はい", style=discord.ButtonStyle.green)
        async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
            config = self.bot.config
            
            # 登録済みアカウントが1つの場合 -> 即座に上書き
            if len(self.existing_accounts) == 1:
                old_player_name = self.existing_accounts[0]
                old_entry = config.check_player_names.get(old_player_name, {})
                old_message_id = old_entry.get('message_id')

                # 以前の画像を削除
                if old_message_id:
                    try:
                        channel = self.bot.get_channel(self.channel_id) or await self.bot.fetch_channel(self.channel_id)
                        old_msg = await channel.fetch_message(old_message_id)
                        await old_msg.delete()
                    except:
                        pass

                # データを差し替え
                if old_player_name in config.check_player_names:
                    del config.check_player_names[old_player_name]
                
                config.check_player_names[self.new_player_name] = {
                    'name': self.new_player_name,
                    'checked_at': datetime.now(JST).isoformat(),
                    'user_id': self.user_id,
                    'message_id': self.new_message.id
                }
                config.check_player_register_count[self.new_player_name] = config.check_player_register_count.get(self.new_player_name, 0) + 1
                config.save_check_player_names()

                await interaction.response.send_message("✨ データを上書きして記録しました！", delete_after=15)
                try:
                    await self.message.delete()
                except:
                    pass
                try:
                    emoji = self.bot.get_emoji(1342392510764286012)
                    await self.new_message.add_reaction(emoji or "✅")
                except:
                    pass
            
            # 登録済みアカウントが複数の場合 -> 選択へ進む
            else:
                view = self.cog.AccountSelectView(
                    self.bot, self.user_id, self.existing_accounts, 
                    self.new_player_name, self.new_message, self.channel_id, self.cog
                )
                await interaction.response.edit_message(
                    content=f"{interaction.user.mention} どのアカウントのプロフィールを更新しますか？",
                    view=view
                )
                view.message = self.message

        @discord.ui.button(label="いいえ", style=discord.ButtonStyle.red)
        async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
            try:
                await self.new_message.delete()
            except:
                pass
            await interaction.response.send_message("データは記録しません。", delete_after=15)
            try:
                await self.message.delete()
            except:
                pass

        @discord.ui.button(label="新規アカウントとして登録", style=discord.ButtonStyle.blurple)
        async def add_new(self, interaction: discord.Interaction, button: discord.ui.Button):
            config = self.bot.config
            # 新規登録（過去ログは削除しない）
            config.check_player_names[self.new_player_name] = {
                'name': self.new_player_name,
                'checked_at': datetime.now(JST).isoformat(),
                'user_id': self.user_id,
                'message_id': self.new_message.id
            }
            config.check_player_register_count[self.new_player_name] = config.check_player_register_count.get(self.new_player_name, 0) + 1
            config.save_check_player_names()

            await interaction.response.send_message(f"✨ {len(self.existing_accounts)+1}個目のアカウントを新しく記録しました！", delete_after=15)
            try:
                await self.message.delete()
            except:
                pass
            try:
                emoji = self.bot.get_emoji(1342392510764286012)
                await self.new_message.add_reaction(emoji or "✅")
            except:
                pass

    class AccountSelectView(discord.ui.View):
        def __init__(self, bot, user_id, existing_account_names, new_player_name, new_message, channel_id, cog):
            super().__init__(timeout=180)
            self.bot = bot
            self.user_id = user_id
            self.new_player_name = new_player_name
            self.new_message = new_message
            self.channel_id = channel_id
            self.cog = cog
            self.message = None

            # 登録済みアカウントごとにボタン生成
            for name in existing_account_names:
                btn = self.AccountButton(name)
                self.add_item(btn)

        async def on_timeout(self):
            if self.message:
                try:
                    await self.message.delete()
                except:
                    pass
            if self.new_message:
                try:
                    await self.new_message.delete()
                except:
                    pass

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id != self.user_id:
                await interaction.response.send_message("本人以外は操作できません。", ephemeral=True)
                return False
            return True

        class AccountButton(discord.ui.Button):
            def __init__(self, account_name):
                super().__init__(label=account_name, style=discord.ButtonStyle.gray)
                self.account_name = account_name

            async def callback(self, interaction: discord.Interaction):
                view: 'BrawlStarsCog.AccountSelectView' = self.view
                config = view.bot.config
                
                # 選択されたアカウントのデータを取得
                old_entry = config.check_player_names.get(self.account_name, {})
                old_message_id = old_entry.get('message_id')



                # 指定されたアカウントの過去画像を削除 (指定されたアカウントのみ)
                if old_message_id:
                    try:
                        channel = view.bot.get_channel(view.channel_id) or await view.bot.fetch_channel(view.channel_id)
                        old_msg = await channel.fetch_message(old_message_id)
                        await old_msg.delete()
                    except:
                        pass

                # データを差し替え
                if self.account_name in config.check_player_names:
                    del config.check_player_names[self.account_name]
                
                config.check_player_names[view.new_player_name] = {
                    'name': view.new_player_name,
                    'checked_at': datetime.now(JST).isoformat(),
                    'user_id': view.user_id,
                    'message_id': view.new_message.id
                }
                config.check_player_register_count[view.new_player_name] = config.check_player_register_count.get(view.new_player_name, 0) + 1
                config.save_check_player_names()

                await interaction.response.send_message(f"✨ 「{self.account_name}」のデータを更新しました！", delete_after=15)
                try:
                    await view.message.delete()
                except:
                    pass
                try:
                    emoji = view.bot.get_emoji(1342392510764286012)
                    await view.new_message.add_reaction(emoji or "✅")
                except:
                    pass

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

    def setup_gemini_api(self):
        try:
            api_key = os.environ.get("GEMINI_API_KEY")
            if api_key:
                genai.configure(api_key=api_key)
                flash_model = genai.GenerativeModel('gemini-2.5-flash')
                lite_model = genai.GenerativeModel('gemini-2.5-flash-lite')
                print(f"✅ Gemini API初期化完了 (Flash & Flash-Lite)")
                return flash_model, lite_model
            else:
                print("⚠️ Gemini APIキー未設定（Gemini機能は無効、Vision APIのみ使用されます）")
                return None, None
        except Exception as e:
            print(f"❌ Gemini API初期化失敗: {e}")
            return None, None

    async def cleanup_user_errors(self, user_id: int):
        """ユーザーの古いエラーメッセージがあれば削除する"""
        prev_err = self.pending_error_messages.pop(user_id, None)
        if prev_err:
            try:
                await prev_err.delete()
            except:
                pass

    async def update_queue_status(self, channel: discord.abc.Messageable):
        """待機列の状況を通知メッセージとして更新または送信する"""
        async with self.queue_lock:
            if self.queue_count == 0:
                if self.queue_msg:
                    try:
                        await self.queue_msg.delete()
                    except:
                        pass
                    self.queue_msg = None
                return

            if self.queue_count == 1:
                # 1枚だけの時はシンプルに
                msg_text = "プレイヤーを記録します... 最大10秒後に完了します"
            else:
                # 2枚以上の時は詳細を表示
                wait_time = 10 * self.queue_count
                msg_text = (
                    "プレイヤーを記録します...\n"
                    f"現在{self.queue_count}枚の画像が処理実行待機中です。すべて完了するまで最大{wait_time}秒かかります。"
                )

            try:
                # 古いメッセージがあれば削除
                if self.queue_msg:
                    try:
                        await self.queue_msg.delete()
                    except:
                        pass
                
                self.queue_msg = await channel.send(msg_text)
            except Exception as e:
                print(f"⚠️ 待機通知更新エラー: {e}")

    # ====== 名前オートコンプリート ======
    async def name_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        config = self.bot.config
        choices = [
            app_commands.Choice(name=name, value=name)
            for name in config.player_names.keys() if current.lower() in name.lower()
        ]
        return choices[:25]

    async def check_and_update_rate_limit(self, user_id: int) -> tuple[bool, Optional[str], Optional[str]]:
        """
        レート制限をチェックし、最初に使用を試みる推奨エンジンを返す。
        実際のフォールバック（429エラー時など）は解析実行時に行う。
        戻り値: (いずれかのモデルが実行可能か, エラーメッセージ, 推奨エンジン 'flash' | 'lite' | 'vision')
        """
        async with self.lock:
            config = self.bot.config
            now = datetime.now(JST).timestamp()
            GLOBAL_KEY = 0
            
            # 履歴の読み込みとクリーンアップ
            history_data = self.scan_history.get(GLOBAL_KEY, {"flash": [], "lite": [], "vision": []})
            if not isinstance(history_data, dict) or "flash" not in history_data: # データ移行用
                history_data = {"flash": [], "lite": [], "vision": []}

            flash_hist = [ts for ts in history_data.get("flash", []) if now - ts < 86400]
            lite_hist = [ts for ts in history_data.get("lite", []) if now - ts < 86400]
            vision_hist = [ts for ts in history_data.get("vision", []) if now - ts < 86400]
            
            # 1. Flash チェック
            flash_1h = [ts for ts in flash_hist if now - ts < 3600]
            if len(flash_1h) < config.RATELIMIT_FLASH_1H and len(flash_hist) < config.RATELIMIT_FLASH_24H:
                # ここではカウントを増やさず、実際に成功したタイミングまたは試行するエンジンとして返す
                # 解析ループ内でカウントを管理する
                return True, None, "flash"
            
            # 2. Lite チェック
            lite_1h = [ts for ts in lite_hist if now - ts < 3600]
            if len(lite_1h) < config.RATELIMIT_LITE_1H and len(lite_hist) < config.RATELIMIT_LITE_24H:
                return True, None, "lite"
            
            # 3. Vision チェック
            vision_1h = [ts for ts in vision_hist if now - ts < 3600]
            if len(vision_1h) < config.RATELIMIT_VISION_1H and len(vision_hist) < config.RATELIMIT_VISION_24H:
                return True, None, "vision"
            
            # 全て制限
            if (len(flash_hist) >= config.RATELIMIT_FLASH_24H and 
                len(lite_hist) >= config.RATELIMIT_LITE_24H and 
                len(vision_hist) >= config.RATELIMIT_VISION_24H):
                return False, "✖エラーが発生しました：エラーコード006\n現在アクセスが集中しています。明日またお試しください。", None
            
            return False, "✖エラーが発生しました：エラーコード005\n現在アクセスが集中しています。しばらく待ってから再度お試しください。", None

    # ====== 画像スキャン Listener ======
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        # ブロスタチャンネルまたはチェック用チャンネルでのみ動作
        is_report_channel = message.channel.id in self.BRAWLSTARS_CHANNELS
        is_check_channel = message.channel.id in self.CHECK_CHANNEL_IDS

        if (is_report_channel or is_check_channel) and message.attachments:
            config = self.bot.config
            valid_images = [a for a in message.attachments if a.content_type and a.content_type.startswith('image/')]
            
            if not valid_images:
                return

            # 枚数分をカウントに追加
            self.queue_count += len(valid_images)
            await self.update_queue_status(message.channel)

            try:
                for attachment in valid_images:
                    # === レートリミットチェック (Step 0) ===
                    is_allowed, error_message, engine = await self.check_and_update_rate_limit(message.author.id)
                    
                    if not is_allowed:
                        await self.cleanup_user_errors(message.author.id)
                        try: await message.delete()
                        except: pass
                        
                        err_msg = await message.channel.send(f"{message.author.mention} {error_message}", delete_after=180)
                        self.pending_error_messages[message.author.id] = err_msg
                        # このメッセージ内の残りの画像もスキップ
                        self.queue_count -= len(valid_images[valid_images.index(attachment):])
                        await self.update_queue_status(message.channel)
                        break

                    async with self.queue_semaphore:
                        print(f"🚀 画像解析開始: {attachment.filename} (Queue: {self.queue_count})")
                        
                        try:
                            async with message.channel.typing():
                                # === 画像解析実行 ===
                                result = await self.hybrid_extract_all_info(attachment.url, engine)

                            if not result or not result.get('name'):
                                await self.cleanup_user_errors(message.author.id)
                                try: await message.delete()
                                except: pass
                                
                                err_msg_text = (
                                    "✖エラーが発生しました：エラーコード004\n"
                                    "ブロスタの名前を正しく認識できませんでした。\n"
                                    "画像が加工されていない、直撮りでないことを確認し、もう一度プロフィール画像を送信してください。"
                                )
                                err_msg = await message.channel.send(f"{message.author.mention} {err_msg_text}", delete_after=180)
                                self.pending_error_messages[message.author.id] = err_msg
                                continue

                            player_name = result['name']
                            player_id = result.get('player_id', 'Unknown')
                            sc_id = result.get('sc_id', 'Unknown')

                            # チェック用チャンネルの挙動: プレイヤー名のみ表示、他は破棄
                            if is_check_channel:
                                # お荷物リスト判定
                                is_hazard = player_name in config.player_names
                                if is_hazard:
                                    # ユーザーへのエラーメッセージ
                                    err_msg_text = (
                                        "✖エラーが発生しました：エラーコード001\n"
                                        "確認が必要なプレイヤーです。\n"
                                        "<@1163117069173272576> にプロフィール画像とこのエラーコードをお伝えください。\n"
                                        "※よくある名前を使用していると意図せずこのメッセージが表示されることがあります。"
                                    )
                                    err_msg = await message.channel.send(f"{message.author.mention} {err_msg_text}", delete_after=180)
                                    self.pending_error_messages[message.author.id] = err_msg
                                    
                                    try: await message.delete()
                                    except: pass

                                    # 管理者へのログと意思決定ボタン
                                    log_channel = self.bot.get_channel(self.LOG_CHANNEL_ID) or await self.bot.fetch_channel(self.LOG_CHANNEL_ID)
                                    if log_channel:
                                        embed = discord.Embed(
                                            title="⚠️ 要注意人物の来訪 (画像送信)",
                                            description=f"プレイヤー: **{player_name}**\n実行者: {message.author.mention} ({message.author.id})",
                                            color=discord.Color.red()
                                        )
                                        embed.set_footer(text=f"判定時刻: {datetime.now(JST).strftime('%Y/%m/%d %H:%M:%S')}")
                                        view = self.HazardDecisionView(self.bot, message.author, player_name, player_id, sc_id, message.id, message.channel.id, self)
                                        await log_channel.send(embed=embed, view=view)
                                    continue
                                
                                # 重複チェック (エラーコード 003)
                                if player_name in config.check_player_names:
                                    if config.check_player_names[player_name].get('user_id') != message.author.id:
                                        try: await message.delete()
                                        except: pass
                                        err_msg = await message.channel.send(f"{message.author.mention} ✖エラーが発生しました；エラーコード003\n既に同じ名前が登録されています。", delete_after=180)
                                        self.pending_error_messages[message.author.id] = err_msg
                                        continue

                                # OK判定
                                emoji = self.bot.get_emoji(1342392510764286012)
                                await message.add_reaction(emoji or "✅")
                                
                                config.check_player_names[player_name] = {
                                    'name': player_name,
                                    'checked_at': datetime.now(JST).isoformat(),
                                    'user_id': message.author.id,
                                    'message_id': message.id
                                }
                                config.save_check_player_names()
                                
                                # ロール付与
                                role = message.guild.get_role(self.SAFE_ROLE_ID)
                                if role:
                                    await message.author.add_roles(role)
                                    try: await message.author.send(f"✨ {role.name} ロールを付与しました！")
                                    except: pass
                            
                            elif is_report_channel:
                                # 報告用チャンネルの挙動: 全情報を記録
                                formatted_info = f"プレイヤー名: {player_name}\nSupercell ID: {sc_id}\nプレイヤーID: {player_id}"
                                
                                if player_name in config.player_names:
                                    config.player_register_count[player_name] = config.player_register_count.get(player_name, 0) + 1
                                    count = config.player_register_count[player_name]
                                    config.player_names[player_name].update({
                                        'last_updated': datetime.now(JST).isoformat(),
                                        'player_id': player_id,
                                        'sc_id': sc_id
                                    })
                                    msg_text = f"{formatted_info}\n『{player_name}』はすでに追加されているよ！通算{count}回目だね"
                                else:
                                    config.player_names[player_name] = {
                                        'name': player_name,
                                        'player_id': player_id,
                                        'sc_id': sc_id,
                                        'registered_at': datetime.now(JST).isoformat(),
                                        'last_updated': datetime.now(JST).isoformat()
                                    }
                                    config.player_register_count[player_name] = 1
                                    msg_text = f"{formatted_info}\nお荷物プレイヤー『{player_name}』を新しく記録したよ！"
                                
                                config.save_player_names()
                                await self.update_latest_list()
                                await message.channel.send(msg_text)

                        except Exception as e:
                            print(f"❌ 画像認識エラー: {e}")
                            await send_error_to_owner(self.bot, config, "BrawlStars Scan Error", e, f"User: {message.author.name}")
                        finally:
                            # 画像保存（認識結果に関わらず保存）
                            player_name_clean = player_name if 'player_name' in locals() and player_name else "Unknown"
                            save_dir = config.REPORT_IMAGES_DIR if is_report_channel else config.CHECK_IMAGES_DIR
                            await self.save_image(attachment, save_dir, message.author.id, player_name_clean, message.created_at)

                            # 1枚終わるごとにカウントを減らして通知を更新
                            self.queue_count -= 1
                            await self.update_queue_status(message.channel)
                            print(f"🏁 画像解析終了: {attachment.filename} (Remaining: {self.queue_count})")
            except Exception as e:
                print(f"❌ on_messageループエラー: {e}")

    async def save_image(self, attachment: discord.Attachment, save_dir: str, user_id: int, player_name: str, created_at: datetime):
        """画像をダウンロードし、圧縮して保存する"""
        try:
            # 禁則文字の置換
            safe_name = "".join(c for c in player_name if c.isalnum() or c in (' ', '_', '-')).strip()
            if not safe_name: safe_name = "Unknown"
            
            # ファイル名: YYYYMMDD_HHMMSS_UserID_Name.webp
            timestamp = created_at.strftime("%Y%m%d_%H%M%S")
            filename = f"{timestamp}_{user_id}_{safe_name}.webp"
            save_path = os.path.join(save_dir, filename)

            # 既に存在する場合はスキップ
            if os.path.exists(save_path):
                return

            async with self.bot.session.get(attachment.url) as resp:
                if resp.status != 200: return
                data = await resp.read()

            def process_and_save():
                with Image.open(io.BytesIO(data)) as img:
                    # RGBに変換
                    img_rgb = img.convert("RGB")
                    
                    # リサイズ (長辺1280px限制)
                    max_size = 1280
                    w, h = img_rgb.size
                    if max(w, h) > max_size:
                        scale = max_size / max(w, h)
                        img_rgb = img_rgb.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
                    
                    # 保存 (WebP, quality=75)
                    img_rgb.save(save_path, "WEBP", quality=75)
                    img_rgb.close()
                return True

            await asyncio.to_thread(process_and_save)
            print(f"💾 画像を保存しました: {filename}")
        except Exception as e:
            print(f"❌ 画像保存エラー ({attachment.filename}): {e}")

    async def batch_collect_images_command(self, interaction: discord.Interaction, target: str, limit: int = 500):
        """過去の画像をチャンネル履歴から取得・保存する (管理者用)"""
        config = self.bot.config
        if interaction.user.id != config.OWNER_ID and interaction.user.id not in config.ADMIN_IDS:
            await interaction.response.send_message("管理者のみ実行可能です。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        await self.batch_collect_images(target, limit)
        await interaction.followup.send(f"✅ {target} の画像収集が完了しました。", ephemeral=True)

    async def batch_collect_images(self, target: str, limit=500):
        """過去の画像をチャンネル履歴から取得・保存する (コンソール用)"""
        config = self.bot.config
        
        if target == "reports":
            channel_sources = [(self.BRAWLSTARS_CHANNELS, config.REPORT_IMAGES_DIR)]
        elif target == "checks":
            channel_sources = [(self.CHECK_CHANNEL_IDS, config.CHECK_IMAGES_DIR)]
        else:
            print(f"❌ 不明なターゲット: {target} (reports または checks を指定してください)")
            return

        for channel_ids, save_dir in channel_sources:
            for cid in channel_ids:
                channel = self.bot.get_channel(cid) or await self.bot.fetch_channel(cid)
                if not channel:
                    print(f"⚠️ チャンネルが見つかりません ID: {cid}")
                    continue
                
                print(f"🔍 チャンネル #{channel.name} ({cid}) の履歴をスキャン中... (Target: {target})")
                count = 0
                async for msg in channel.history(limit=limit):
                    if msg.author.bot: continue
                    for attachment in msg.attachments:
                        if attachment.content_type and attachment.content_type.startswith('image/'):
                            # 過去画像なのでプレイヤー名は 'Legacy'
                            await self.save_image(attachment, save_dir, msg.author.id, "Legacy", msg.created_at)
                            count += 1
                print(f"✅ #{channel.name} から {count} 枚の画像を処理しました。")

    async def hybrid_extract_all_info(self, image_url: str, recommended_engine: str) -> Optional[dict]:
        """階層的なフォールバックロジック: Flash -> Lite -> Vision"""
        config = self.bot.config
        
        engines_to_try = []
        if recommended_engine == "flash":
            engines_to_try = ["flash", "lite", "vision"]
        elif recommended_engine == "lite":
            engines_to_try = ["lite", "vision"]
        else:
            engines_to_try = ["vision"]

        for engine in engines_to_try:
            # 各エンジンの実行前にカウント制限を再チェック（ループ内での動的切り替え用）
            async with self.lock:
                now = datetime.now(JST).timestamp()
                hist_data = self.scan_history.get(0, {"flash": [], "lite": [], "vision": []})
                
                if engine == "flash":
                    h = [ts for ts in hist_data.get("flash", []) if now - ts < 86400]
                    h1 = [ts for ts in h if now - ts < 3600]
                    if len(h1) >= config.RATELIMIT_FLASH_1H or len(h) >= config.RATELIMIT_FLASH_24H:
                        continue
                elif engine == "lite":
                    h = [ts for ts in hist_data.get("lite", []) if now - ts < 86400]
                    h1 = [ts for ts in h if now - ts < 3600]
                    if len(h1) >= config.RATELIMIT_LITE_1H or len(h) >= config.RATELIMIT_LITE_24H:
                        continue
                elif engine == "vision":
                    h = [ts for ts in hist_data.get("vision", []) if now - ts < 86400]
                    h1 = [ts for ts in h if now - ts < 3600]
                    if len(h1) >= config.RATELIMIT_VISION_1H or len(h) >= config.RATELIMIT_VISION_24H:
                        continue

            # 実行
            result = None
            try:
                if engine == "flash":
                    result = await self.extract_all_with_gemini(image_url, "flash")
                elif engine == "lite":
                    result = await self.extract_all_with_gemini(image_url, "lite")
                elif engine == "vision":
                    result = await self.extract_all_with_vision(image_url)
                
                if result:
                    # 成功時にカウントを増やす
                    async with self.lock:
                        now = datetime.now(JST).timestamp()
                        hist_data = self.scan_history.get(0, {"flash": [], "lite": [], "vision": []})
                        hist_data[engine].append(now)
                        self.scan_history[0] = hist_data
                        self.save_scan_history()
                    
                    print(f"📊 画像解析成功: 使用モデル = {engine.upper()}")
                    return result

            except Exception as e:
                # 429エラーを検知
                error_str = str(e).lower()
                if "429" in error_str or "too many requests" in error_str:
                    print(f"⚠️ {engine.upper()} 429制限検知: 次のモデルへフォールバックします")
                    continue
                else:
                    # それ以外の深刻なエラーは即座に停止せずに次を試すか判断
                    print(f"❌ {engine.upper()} エラー: {e}")
                    if engine != "vision":
                        continue
        
        return None

    async def extract_all_with_gemini(self, image_url: str, model_type: str = "flash") -> Optional[dict]:
        model = self.gemini_flash if model_type == "flash" else self.gemini_lite
        if not model: return None
        
        try:
            async with self.bot.session.get(image_url) as resp:
                if resp.status != 200: return None
                data = await resp.read()
            
            with Image.open(io.BytesIO(data)) as img:
                # 念のため、メモリ消費を抑えるためにRGBに変換
                with img.convert("RGB") as img_rgb:
                    # --- 画像リサイズ (メモリ最適化) ---
                    # 長辺を 1600px に制限
                    max_size = 1600
                    w, h = img_rgb.size
                    if max(w, h) > max_size:
                        scale = max_size / max(w, h)
                        new_size = (int(w * scale), int(h * scale))
                        # BICUBIC フィルタで高速にリサイズ
                        img_final = img_rgb.resize(new_size, Image.Resampling.BICUBIC)
                        print(f"🖼️ 画像リサイズ実行: {w}x{h} -> {new_size[0]}x{new_size[1]}")
                    else:
                        img_final = img_rgb

                    prompt = (
                        "まず、この画像がブロスタ（Brawl Stars）のプロフィール画面かどうかを厳格に判定してください。\n"
                        "プロフィール画面ではない、あるいは確信が持てない場合は、他の情報を抽出せずに以下のJSONのみを返してください：\n"
                        "{\"error\": \"not_brawl_stars\"}\n\n"
                        "プロフィール画面である場合は、以下の3点を抽出してJSON形式で返してください。\n"
                        "1. name: プレイヤー名。画面中央上部の最も大きく表示されている名前です。絵文字や記号も全て含めてください。全角の数字や記号は全て半角（NFKC規格）に変換してください。\n"
                        "2. player_id: 左側のキャラアイコンの下にある#から始まる大文字英数字。'O'と'0'は全て'0'（ゼロ）に変換してください。\n"
                        "3. sc_id: 名前のすぐ下にある、2〜3つの英単語を組み合わせたID（例: HeroicHungryNebula）。IDアイコンの隣にある文字列を正確に抽出してください。\n\n"
                        "JSONフォーマット（プロフィール画面の場合）: {\"name\": \"...\", \"player_id\": \"...\", \"sc_id\": \"...\"}"
                    )
                    
                    def run_gemini():
                        return model.generate_content([prompt, img_final])
                    
                    response = await asyncio.to_thread(run_gemini)
                    
                    # リサイズされた画像オブジェクトがあれば解放
                    if img_final != img_rgb:
                        del img_final

            # 重いRawデータを明示的に削除
            del data
            if 'img' in locals(): del img
            if 'img_rgb' in locals(): del img_rgb
            
            import json as json_lib_local
            # JSON部分を抽出
            text = response.text
            start = text.find('{')
            end = text.rfind('}') + 1
            if start != -1 and end != -1:
                result = json_lib_local.loads(text[start:end])
                
                # エラー返答チェック
                if 'error' in result:
                    return None
                
                # 正規化
                if result.get('name'):
                    # プレイヤー名のみ全角を半角に変換 (NFKC)
                    result['name'] = unicodedata.normalize('NFKC', result['name'])
                if result.get('player_id'):
                    result['player_id'] = result['player_id'].replace('O', '0').replace('o', '0')
                
                # 最後にガベージコレクションを実行
                gc.collect()
                return result
            
            gc.collect()
            return None
        except Exception as e:
            print(f"❌ Gemini抽出エラー: {e}")
            gc.collect()
            return None

    async def extract_all_with_vision(self, image_url: str) -> Optional[dict]:
        # 既存の Vision ロジックを拡張
        annotations = await self.extract_text_from_image(image_url)
        if not annotations: return None
        
        full_text = annotations[0].description
        lines = full_text.split('\n')
        
        result = {'name': None, 'player_id': 'Unknown', 'sc_id': 'Unknown'}
        
        # プレイヤー名 (既存のロジックを流用)
        # 二重フェッチを避けるため annotations を直接渡す
        name_res, _, _ = await self.extract_brawlstars_name_from_annotations(annotations)
        if name_res:
             result['name'] = name_res['name']
        
        # ID類の抽出 (単純な正規表現/パターンマッチ)
        import re
        player_id_match = re.search(r'#[0-9A-Z]+', full_text.replace('O', '0'))
        if player_id_match:
            result['player_id'] = player_id_match.group(0).replace('O', '0')
            
        # Supercell ID: 通常、名前の下にある英単語の組み合わせ
        # 特定のプレフィックスに依存せず、位置関係や複数の英単語の連続から推測（Visionでは限界があるが、可能な限り抽出）
        sc_id_match = re.search(r'[A-Z][a-z]+[A-Z][a-z]+[A-Z][a-z]+', full_text) # CamelCaseパターン
        if not sc_id_match:
            sc_id_match = re.search(r'Hero[0-9A-Za-z]+', full_text) # プレフィックス Hero にフォールバック
            
        if sc_id_match:
            result['sc_id'] = sc_id_match.group(0)
        else:
            # 緩和された正規表現: 大文字のみや2単語などもカバー
            # 例: HungryNebula, HEROICNEBULA, BrawlStarsPlayer
            sc_id_match = re.search(r'[A-Z0-9]{3,}', full_text)
            if sc_id_match:
                 result['sc_id'] = sc_id_match.group(0)
            
        # プレイヤー名の正規化 (Vision フォールバック用)
        if result.get('name'):
            result['name'] = unicodedata.normalize('NFKC', result['name'])
            
        return result

    async def extract_text_from_image(self, image_url: str) -> List[vision.EntityAnnotation]:
        if not self.vision_client:
            return []
        
        try:
            async with self.bot.session.get(image_url) as response:
                if response.status != 200:
                    print(f"⚠️ 画像取得失敗: HTTP {response.status}")
                    return []
                content_length = response.headers.get('Content-Length')
                MAX_SIZE = 16 * 1024 * 1024
                if content_length and int(content_length) > MAX_SIZE:
                    print(f"⚠️ 画像サイズ超過 (Header): {content_length}")
                    return []
                image_data = await response.read()
                if len(image_data) > MAX_SIZE:
                        print(f"⚠️ 画像サイズ超過 (Body): {len(image_data)}")
                        return []
            
            image = vision.Image(content=image_data)
            
            def run_vision():
                return self.vision_client.text_detection(image=image)
            
            response = await asyncio.to_thread(run_vision)
            
            texts = response.text_annotations
            return texts if texts else []
        except aiohttp.ClientError as e:
            print(f"❌ 画像ダウンロードエラー: {e}")
            return []
        except Exception as e:
            print(f"❌ 画像認識エラー: {e}")
            return []

    async def extract_brawlstars_name(self, image_url: str) -> tuple[Optional[dict], Optional[str], bool]:
        annotations = await self.extract_text_from_image(image_url)
        return await self.extract_brawlstars_name_from_annotations(annotations)

    async def extract_brawlstars_name_from_annotations(self, annotations: List[vision.EntityAnnotation]) -> tuple[Optional[dict], Optional[str], bool]:
        if not annotations:
            return None, None, False
        
        text = annotations[0].description
        is_err002 = False
        
        # エラーコード 002: キャラクター画面判定
        try:
            vertices = annotations[0].bounding_poly.vertices
            center_x = (max(v.x for v in vertices) + min(v.x for v in vertices)) / 2
            for ann in annotations[1:]:
                if "キャラクター" in ann.description:
                    if (sum(v.x for v in ann.bounding_poly.vertices) / 4) > center_x:
                        is_err002 = True
                        break
        except:
            pass

        # 基本的な検証
        anchor_keywords = ["トロフィー", "ガチバトル", "勝利数", "ポイント", "最高", "現在", "プロフィール", "シーズン記録", "歴代記録"]
        if len([kw for kw in anchor_keywords if kw in text]) < 2:
            return None, text, is_err002

        if "報告" in text:
            return None, text, is_err002
        
        lines = [line.strip() for line in text.strip().split('\n') if line.strip() and "BOO!" not in line]
        result = {'name': None}
        
        # 「プロフィール」のY座標を特定（断片化にも対応）
        profile_y = None
        for ann in annotations[1:]:
            upper = ann.description.upper()
            # 「プロフィール」の断片や「PROFILE」等、ヘッダーらしき文字を幅広く探す
            if any(k in upper for k in ['プロフィール', 'PROFILE', 'プロフィ', 'フィール', 'ROFIL']):
                cy = sum(v.y for v in ann.bounding_poly.vertices) / 4
                # ヘッダーは通常画面上部にある
                if cy < 250:
                    profile_y = cy
                    break

        # フラグメント収集
        fragments = []
        sc_id_y_levels = [] # IDアイコン等のY座標を保持
        for ann in annotations[1:]:
            content = ann.description.strip()
            if not content: continue
            v = ann.bounding_poly.vertices
            y_coords = [p.y for p in v]
            h = max(y_coords) - min(y_coords)
            cy = sum(y_coords) / 4
            
            # Supercell IDマーカーの検出
            upper_content = content.upper()
            if (upper_content == "ID" and h < 45) or (len(upper_content) <= 3 and "ID" in upper_content and h < 45) or "SUPERCELL" in upper_content:
                sc_id_y_levels.append(cy)

            # 探索範囲の決定:
            # プロフィールが見つかっている場合はその下から、見つからない場合は全体（後のソートで制御）
            min_y_limit = (profile_y + 10) if profile_y else 0
            
            if cy > min_y_limit and cy < 1000:
                fragments.append({'text': content, 'height': h, 'y': cy, 'x': min(v.x for v in v)})

        # 同一行の連結と候補作成
        candidates = []
        if fragments:
            fragments.sort(key=lambda f: f['y'])
            grouped = []
            if fragments:
                cur = [fragments[0]]
                for i in range(1, len(fragments)):
                    f = fragments[i]
                    if abs(f['y'] - cur[-1]['y']) < cur[-1]['height'] * 0.7:
                        cur.append(f)
                    else:
                        grouped.append(cur)
                        cur = [f]
                grouped.append(cur)

            for line_frags in grouped:
                line_frags.sort(key=lambda f: f['x'])
                
                # 単語を連結（空白なし）
                combined_text = "".join(f['text'] for f in line_frags)
                combined_text = combined_text.strip()
                avg_h = sum(f['height'] for f in line_frags) / len(line_frags)
                avg_y = sum(f['y'] for f in line_frags) / len(line_frags)

                # キーワード除外
                # キーワード除外
                if (len(combined_text) < 2 or 
                    any(k in combined_text.upper() for k in ["プロフィール", "キャラクター", "SUPERCELL"]) or 
                    combined_text.startswith('#') or 
                    any(kw in combined_text for kw in ["勝利数", "トロフィー", "バトルカード", "ガチバトル", "クラブ", "お気に入り", "ザ・ファースト"]) or 
                    combined_text.replace(',','').replace('.','').replace(' ','').isdigit()):
                    continue

                # IDマーカーと同じ高さ、または非常に近い行を除外
                if sc_id_y_levels and any(abs(avg_y - y) < avg_h * 1.2 for y in sc_id_y_levels):
                    continue

                # プロフィールヘッダーが見つかっている場合、離れすぎているテキスト（フッターなど）を除外
                # 名前はヘッダー（profile_y）の直下にあるはず
                # User Feedback: 400px制限は機種によって誤判定の原因になるため削除
                # if profile_y and avg_y > profile_y + 400:
                #    continue

                candidates.append({'text': combined_text, 'height': avg_h, 'y': avg_y})

        if candidates:
            if profile_y:
                # 【通常モード】プロフィールが検出された場合（99%正確）
                # 従来の高精度ソート（高さを優先し、ほぼ同じなら上を優先）
                candidates.sort(key=lambda x: (-round(x['height'] / 5) * 5, x['y']))
                result['name'] = candidates[0]['text']
            else:
                # 【適応モード】プロフィール欠損時（今回の特例）
                # 背景やボタン等を避けるため、一定の高さがあるものから「最も上にあるもの」を選択
                # 画面上端（Y < 100）に残っている断片はボタン等として除外
                robust_candidates = [c for c in candidates if c['height'] > 25 and c['y'] > 100]
                if robust_candidates:
                    robust_candidates.sort(key=lambda x: x['y'])
                    result['name'] = robust_candidates[0]['text']
                else:
                    # それでも候補がない場合のフォールバック
                    candidates.sort(key=lambda x: (-round(x['height'] / 15) * 15, x['y']))
                    result['name'] = candidates[0]['text']
        
        # フォールバック
        if not result['name']:
            for i, line in enumerate(lines):
                if line.startswith('#') and len(line) > 5:
                    if i > 0 and len(lines[i-1]) >= 2:
                        result['name'] = lines[i-1]; break
        
        return (result if result['name'] else None), text, is_err002

    # ====== Player List View ======
    class PlayerListPagination(discord.ui.View):
        def __init__(self, bot_instance, page=0):
            super().__init__(timeout=None)
            self.bot = bot_instance
            self.current_page = page

        async def update_view(self, interaction: discord.Interaction):
            cog = self.bot.get_cog("BrawlStarsCog")
            if not cog: return
            
            embed, max_pages = cog.create_player_list_embed(page=self.current_page)
            
            # ボタンの有効/無効制御
            self.prev_button.disabled = (self.current_page <= 0)
            self.next_button.disabled = (self.current_page >= max_pages - 1)
            
            await interaction.response.edit_message(embed=embed, view=self)
            cog.last_list_message = interaction.message

        @discord.ui.button(label="前へ", style=discord.ButtonStyle.gray, emoji="⬅️", custom_id="player_list:prev")
        async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.current_page -= 1
            await self.update_view(interaction)

        @discord.ui.button(label="次へ", style=discord.ButtonStyle.gray, emoji="➡️", custom_id="player_list:next")
        async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.current_page += 1
            await self.update_view(interaction)

        @discord.ui.button(label="リストを更新", style=discord.ButtonStyle.green, emoji="🔄", custom_id="player_list:refresh")
        async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            await self.update_view(interaction)

    def create_player_list_embed(self, page=0):
        config = self.bot.config
        
        # 1. 登録回数でグループ分け
        priority_players = [] # 2回以上
        normal_players = []   # 1回
        
        for name in config.player_names.keys():
            count = config.player_register_count.get(name, 1)
            if count >= 2:
                priority_players.append((name, count))
            else:
                normal_players.append((name, count))
        
        # 2. ソート
        priority_players.sort(key=lambda x: (-x[1], x[0])) # 2回以上は回数降順 -> 名前順
        normal_players.sort(key=lambda x: x[0])           # 1回は名前順
        
        # 3. ページ分割ロジックの再定義
        # Page 1 (index 0): 要注意プレイヤー
        # Page 2+ (index 1+): 一般プレイヤー (50人ずつ)
        
        page_size = 50
        normal_pages = max(1, (len(normal_players) + page_size - 1) // page_size)
        max_pages = 1 + normal_pages
        
        # 範囲チェック
        page = max(0, min(page, max_pages - 1))
        
        embed = discord.Embed(color=discord.Color.red())
        lines = []
        
        if page == 0:
            # 要注意プレイヤーのページ
            embed.title = "🔴 要注意プレイヤーリスト (2回以上報告)"
            if priority_players:
                for name, count in priority_players:
                    lines.append(f"🔴 **{name}** — `{count}回報告`")
            else:
                lines.append("該当するプレイヤーはいません。")
        else:
            # 一般プレイヤーのページ
            normal_page_idx = page - 1
            start_idx = normal_page_idx * page_size
            end_idx = start_idx + page_size
            paged_normal = normal_players[start_idx:end_idx]
            
            embed.title = f"📋 一般プレイヤーリスト ({normal_page_idx + 1})"
            if paged_normal:
                for name, count in paged_normal:
                    lines.append(f"• **{name}**")
            else:
                lines.append("登録者はまだいません。")
            
        embed.description = "\n".join(lines)
        
        footer_text = f"合計: {len(config.player_names)}人 | ページ: {page + 1} / {max_pages}"
        footer_text += f" | 最終更新: {datetime.now(JST).strftime('%H:%M:%S')}"
        embed.set_footer(text=footer_text)
        
        return embed, max_pages

    async def update_latest_list(self):
        config = self.bot.config
        if self.last_list_message and config.player_names:
            try:
                # 既存のビューがあれば現在のページを取得、なければ 0
                current_view = getattr(self.last_list_message, "view", None)
                page = 0
                if isinstance(current_view, self.PlayerListPagination):
                    page = current_view.current_page
                
                embed, max_pages = self.create_player_list_embed(page=page)
                view = self.PlayerListPagination(self.bot, page=page)
                
                # ボタンの有効状態を初期設定
                view.prev_button.disabled = (page <= 0)
                view.next_button.disabled = (page >= max_pages - 1)
                
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
        
        view = self.PlayerListPagination(self.bot, page=0)
        embed, max_pages = self.create_player_list_embed(page=0)
        
        # 初期ページのボタン状態設定
        view.prev_button.disabled = True # 最初のページなので「前へ」は無効
        view.next_button.disabled = (max_pages <= 1)
        
        await interaction.response.send_message(embed=embed, view=view)
        self.last_list_message = await interaction.original_response()

    @app_commands.command(name="check", description="プロフィールをスキャンし、メンバーロールを付与します。")
    @app_commands.describe(image="スキャンする画像")
    async def check_command(self, interaction: discord.Interaction, image: discord.Attachment):
        if not image.content_type or not image.content_type.startswith('image/'):
            await interaction.response.send_message("❌ 画像ファイルを指定してください。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        
        # === レートリミットチェック ===
        is_allowed, error_message, engine = await self.check_and_update_rate_limit(interaction.user.id)
        if not is_allowed:
             await interaction.followup.send(error_message, ephemeral=True)
             return

        config = self.bot.config

        try:
            # === 画像解析実行 ===
            result = await self.hybrid_extract_all_info(image.url, engine)
            
            if not result or not result.get('name'):
                await interaction.followup.send("⚠️ プレイヤー名を認識できませんでした。文字が鮮明な画像でもう一度お試しください。", ephemeral=True)
                return

            player_name = result['name']
            player_id = result.get('player_id', 'Unknown')
            sc_id = result.get('sc_id', 'Unknown')
            
            # Error Code 003: 重複登録チェック
            if player_name in config.check_player_names:
                err_msg_text = (
                    "✖エラーが発生しました；エラーコード003\n"
                    "既に同じ名前がこのサーバーに登録されています。\n"
                    "ブロスタプレイヤーやイキイキした毎日などよくある名前を利用しているとエラーが発生することがあります。\n"
                    "このエラーが発生した場合は、お手数ですが<@1163117069173272576> にdmにこのエラーコードとブロスタの名前を送信してください。"
                )
                await interaction.followup.send(err_msg_text, ephemeral=True)
                return

            # 判定
            if player_name in config.player_names:
                # ユーザーへのエラーメッセージ
                err_msg_text = (
                    "✖エラーが発生しました：エラーコード001\n"
                    "確認が必要なプレイヤーです。\n"
                    "<@1163117069173272576> にプロフィール画像とこのエラーコードをお伝えください。\n"
                    "※よくある名前を使用していると意図せずこのメッセージが表示されることがあります。"
                )
                await interaction.followup.send(err_msg_text, ephemeral=True)
                
                # 管理者へのログと意思決定ボタン
                log_channel = self.bot.get_channel(self.LOG_CHANNEL_ID) or await self.bot.fetch_channel(self.LOG_CHANNEL_ID)
                if log_channel:
                    embed = discord.Embed(
                        title="⚠️ 要注意人物の来訪 (コマンド経由)",
                        description=f"プレイヤー: **{player_name}**\n実行者: {interaction.user.mention} ({interaction.user.id})",
                        color=discord.Color.red()
                    )
                    embed.set_footer(text=f"判定時刻: {datetime.now(JST).strftime('%Y/%m/%d %H:%M:%S')}")
                    view = self.HazardDecisionView(self.bot, interaction.user, player_name, player_id, sc_id, interaction.id, interaction.channel_id, self)
                    await log_channel.send(embed=embed, view=view)
            else:
                # OK判定: 記録とロール付与
                # 1. 記録
                config.check_player_names[player_name] = {
                    'name': player_name,
                    'player_id': player_id,
                    'sc_id': sc_id,
                    'checked_at': datetime.now(JST).isoformat(),
                    'user_id': interaction.user.id
                }
                config.save_check_player_names()
                print(f"📝 確認ログ記録 (Slash OK): {player_name}")

                # 2. ロール付与
                try:
                    role = interaction.guild.get_role(self.SAFE_ROLE_ID)
                    if role:
                        await interaction.user.add_roles(role)
                        await interaction.followup.send(f"✨ {role.name} ロールを付与しました！", ephemeral=True)
                    else:
                        await interaction.followup.send("✅ リストにはいませんが、付与するロールが見つかりませんでした。", ephemeral=True)
                except Exception as role_err:
                    print(f"❌ ロール付与失敗: {role_err}")
                    await interaction.followup.send("⚠️ ロールの付与に失敗しました。ボットの権限を確認してください。", ephemeral=True)

        except Exception as e:
            await interaction.followup.send(f"❌ エラーが発生しました: {e}", ephemeral=True)
            await send_error_to_owner(self.bot, config, "Check Command Error", e, f"User: {interaction.user.name}")

    @app_commands.command(name="player_edit", description="登録されたプレイヤー名を修正します")
    @app_commands.autocomplete(old_name=name_autocomplete)
    async def player_edit_command(self, interaction: discord.Interaction, old_name: str, new_name: str):
        config = self.bot.config
        # オーナーまたは管理者のみ
        if interaction.user.id != config.OWNER_ID and interaction.user.id not in config.ADMIN_IDS:
            await interaction.response.send_message("管理者のみ使用可能です。", ephemeral=True)
            await log_to_owner(self.bot, config, "error", interaction.user, "/player_edit", "Unauthorized access attempt")
            return

        if old_name not in config.player_names:
            await interaction.response.send_message(f"❌ 「{old_name}」は見つかりませんでした。", ephemeral=True)
            return

        config.player_names[new_name] = config.player_names.pop(old_name)
        config.player_names[new_name]['name'] = new_name
        
        # カウント情報の移行と初期化漏れ防止
        old_count = config.player_register_count.pop(old_name, 1)
        config.player_register_count[new_name] = old_count

        config.save_player_names()
        await interaction.response.send_message(f"✅ 修正完了：`{old_name}` → `{new_name}`")

    @app_commands.command(name="player_delete", description="指定したプレイヤーのデータを削除します")
    @app_commands.autocomplete(name=name_autocomplete)
    async def player_delete_command(self, interaction: discord.Interaction, name: str):
        config = self.bot.config
        # オーナーまたは管理者のみ
        if interaction.user.id != config.OWNER_ID and interaction.user.id not in config.ADMIN_IDS:
            await interaction.response.send_message("管理者のみ使用可能です。", ephemeral=True)
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

    @app_commands.command(name="scanhistory", description="過去の画像を遡って一括登録")
    async def scanhistory_command(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None, limit: int = 100):
        config = self.bot.config
        # オーナーまたは管理者のみ
        if interaction.user.id != config.OWNER_ID and interaction.user.id not in config.ADMIN_IDS:
            await interaction.response.send_message("このコマンドは管理者のみが使用できます。", ephemeral=True)
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
                # 一括処理は Vision のみ使用 (Rate limit 考慮)
                result = await self.hybrid_extract_all_info(attachment.url, "vision")
                if result and result.get('name'):
                    player_name = result['name']
                    if player_name in config.player_names:
                        config.player_register_count[player_name] = config.player_register_count.get(player_name, 1) + 1
                        updated_count += 1
                        config.player_names[player_name].update({
                            'last_updated': msg.created_at.isoformat(),
                            'player_id': result.get('player_id', 'Unknown'),
                            'sc_id': result.get('sc_id', 'Unknown')
                        })
                    else:
                        player_data = {
                            'name': player_name,
                            'player_id': result.get('player_id', 'Unknown'),
                            'sc_id': result.get('sc_id', 'Unknown'),
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

    async def batch_react_history(self, limit=100):
        """コンソールから呼び出される、チェック用チャンネルの画像への一括リアクション付与"""
        config = self.bot.config
        target_channel = self.bot.get_channel(self.CHECK_CHANNEL_ID) or await self.bot.fetch_channel(self.CHECK_CHANNEL_ID)
        if not target_channel:
            print("❌ チェック用チャンネルが見つかりません。")
            return

        print(f"🚀 直近{limit}件のメッセージに対してリアクション付与を開始します...")
        
        emoji = self.bot.get_emoji(1342392510764286012)
        target_emoji = emoji or "✅"
        
        processed_count = 0
        reacted_count = 0
        skip_count = 0
        
        try:
            async for msg in target_channel.history(limit=limit):
                if msg.author.bot: continue
                
                for attachment in msg.attachments:
                    if attachment.content_type and attachment.content_type.startswith('image/'):
                        processed_count += 1
                        # OCRで内容を確認
                        result, full_text, is_err002 = await self.extract_brawlstars_name(attachment.url)
                        
                        # OK信号の条件:
                        # 1. 正常に名前が取れている 
                        # 2. Error 002判定ではない
                        # 3. リザルト画面（報告）ではない
                        # 4. お荷物リスト（Error 001）にいない
                        if result and result['name'] and not is_err002:
                            player_name = result['name']
                            is_hazard = player_name in config.player_names
                            
                            if not is_hazard:
                                # すでにリアクションが付いていないか確認（簡易チェック）
                                already_reacted = any(str(r.emoji) == str(target_emoji) for r in msg.reactions)
                                if not already_reacted:
                                    try:
                                        await msg.add_reaction(target_emoji)
                                        reacted_count += 1
                                        print(f"✅ Reacted to {player_name}'s message")
                                    except:
                                        pass
                                else:
                                    skip_count += 1
                            else:
                                skip_count += 1
                        else:
                            skip_count += 1
                        break # 1メッセージにつき1枚まで
            
            print(f"📊 リアクション付与完了: 処理{processed_count}枚 / 付与{reacted_count}件 / スキップ{skip_count}件")
        except Exception as e:
            print(f"❌ 一括リアクションエラー: {e}")

    async def batch_check_history(self, limit=100):
        """コンソールから呼び出される、チェック用チャンネルの一括スキャン"""
        config = self.bot.config
        target_channel = self.bot.get_channel(self.CHECK_CHANNEL_ID) or await self.bot.fetch_channel(self.CHECK_CHANNEL_ID)
        
        if not target_channel:
            print(f"❌ Error: Check channel {self.CHECK_CHANNEL_ID} not found.")
            return

        print(f"🔍 Checking history in #{target_channel.name} (limit={limit})...")
        
        success_count = 0
        role_count = 0
        failed_count = 0
        
        try:
            guild = target_channel.guild
            safe_role = guild.get_role(self.SAFE_ROLE_ID)
            
            async for msg in target_channel.history(limit=limit):
                if msg.author.bot: continue
                if not msg.attachments: continue
                
                for attachment in msg.attachments:
                    if attachment.content_type and attachment.content_type.startswith('image/'):
                        result, _, _ = await self.extract_brawlstars_name(attachment.url)
                        if result and result['name']:
                            player_name = result['name']
                            is_hazard = player_name in config.player_names
                            
                            # 記録 (お荷物リストにいない場合のみ一貫性のため)
                            if not is_hazard:
                                config.check_player_names[player_name] = {
                                    'name': player_name,
                                    'checked_at': msg.created_at.isoformat(),
                                    'user_id': msg.author.id,
                                    'message_id': msg.id, # メッセージIDを追加
                                    'batch': True
                                }
                                config.check_player_register_count[player_name] = config.check_player_register_count.get(player_name, 0) + 1
                                success_count += 1
                            
                            # ロール付与 (お荷物でない場合)
                            if not is_hazard and safe_role:
                                try:
                                    # メンバーオブジェクトの取得
                                    member = guild.get_member(msg.author.id) or await guild.fetch_member(msg.author.id)
                                    if member and safe_role not in member.roles:
                                        await member.add_roles(safe_role)
                                        role_count += 1
                                except Exception as re:
                                    print(f"⚠️ Failed to grant role to {msg.author.name}: {re}")
                        else:
                            failed_count += 1
                        break # 1メッセージ1枚まで
            
            config.save_check_player_names()
            print(f"📊 Batch Check complete: {success_count} recorded, {role_count} roles granted, {failed_count} failed.")
        except Exception as e:
            print(f"❌ Batch Check error: {e}")

async def setup(bot):
    await bot.add_cog(BrawlStarsCog(bot))