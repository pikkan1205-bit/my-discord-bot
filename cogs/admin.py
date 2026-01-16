import discord
from discord.ext import commands, tasks
from discord import app_commands
import re
from datetime import datetime, timezone, timedelta
from typing import Optional, Union

from utils.helpers import normalize_text, normalize_synonyms, has_any
# Note: config is accessed via self.bot.config

# 日本時間のタイムゾーン
JST = timezone(timedelta(hours=9))

class AdminCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.check_admin_mode_timeout.start()  # Loop check

    def cog_unload(self):
        self.check_admin_mode_timeout.cancel()

    # ====== 管理者追加確認用View ======
    class AddAdminConfirmView(discord.ui.View):
        def __init__(self, target_user: discord.Member, owner: Union[discord.User, discord.Member], config_manager):
            super().__init__()
            self.target_user = target_user
            self.owner = owner
            self.config = config_manager
        
        @discord.ui.button(label="確認", style=discord.ButtonStyle.green)
        async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.config.ADMIN_IDS.add(self.target_user.id)
            self.config.save_config()
            
            # 新しい管理者にDMを送信
            try:
                embed = discord.Embed(
                    title="共同管理者になりました",
                    description=f"{self.owner.name}によってこのbotの共同管理者になりました。",
                    color=discord.Color.gold()
                )
                await self.target_user.send(embed=embed)
            except Exception as e:
                print(f"⚠️ {self.target_user.name} へのDM送信に失敗しました: {e}")
            
            await interaction.response.edit_message(content=f"✅ {self.target_user.name} を管理者に追加しました", view=None)
            print(f"✅ {self.target_user.name} ({self.target_user.id}) を管理者に追加しました")
        
        @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.red)
        async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            await interaction.response.edit_message(content="❌ キャンセルしました", view=None)

    # ====== 管理者削除確認用View ======
    class RemoveAdminConfirmView(discord.ui.View):
        def __init__(self, target_user: discord.Member, config_manager):
            super().__init__()
            self.target_user = target_user
            self.config = config_manager
        
        @discord.ui.button(label="確認", style=discord.ButtonStyle.green)
        async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.config.ADMIN_IDS.discard(self.target_user.id)
            self.config.save_config()
            
            await interaction.response.edit_message(content=f"✅ {self.target_user.name} を管理者から削除しました", view=None)
            print(f"✅ {self.target_user.name} ({self.target_user.id}) を管理者から削除しました")
        
        @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.red)
        async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            await interaction.response.edit_message(content="❌ キャンセルしました", view=None)


    # ====== イベントハンドラ (on_message) ======
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        config = self.bot.config
        
        # フィーロちゃん呼びかけ検出 -> 管理者モード入り
        # ※ ここは全ユーザー対象ではなくオーナーのみなので注意
        content = message.content
        normalized = normalize_text(content)
        firo_keywords = ["フィーロちゃん", "ふぃーろちゃん", "フィーロ", "ふぃーろ"]
        firo_called = any(normalize_text(k) in normalized for k in firo_keywords)
        
        if firo_called:
            if message.author.id == config.OWNER_ID:
                config.enter_admin_mode(message.author.id)
                await message.reply("ご主人様！どうしたの？")
                return # 処理終了
            else:
                await message.reply("フィーロは、フィーロ！")
                return # 処理終了

        # 🆕 管理者モード終了チェック（最優先）
        if message.author.id == config.OWNER_ID and config.is_in_admin_mode(message.author.id):
            exit_keywords = ["終了", "おわり", "終わり", "exit", "quit", "bye", "バイバイ", "またね", "さようなら", "帰って", "もういい", "閉じて"]
            if any(normalize_text(k) in normalized for k in exit_keywords):
                config.exit_admin_mode(message.author.id)
                await message.reply("了解！またいつでも呼んでね！")
                print(f"✅ 管理者モード終了（直接チェック）: {message.author.name}")
                return

        # 管理者モード中のコマンド処理
        if message.author.id == config.OWNER_ID and config.is_in_admin_mode(message.author.id):
            handled = await self.handle_admin_mode_command(message)
            if handled:
                # まだ管理者モードにいる場合のみタイムスタンプ更新
                if config.is_in_admin_mode(message.author.id):
                    config.update_admin_mode(message.author.id)
                return
            else:
                # キーワードに当てはまらない場合（他のCogが処理するかもしれないが、管理者モード中は占有する仕様ならばここで返信）
                # 仕様: 管理者モード中はBotと対話している状態
                # 「チャット削除」などはChatCogが持つロジックだが、管理者モードからも呼べるように重複実装するか、共有メソッドを使うか。
                # 現状のmain.pyではhandle_admin_mode_command内にすべてのロジックがある。
                
                await message.reply("ごめんね！もう一回いい？")
                config.update_admin_mode(message.author.id)
                return

    # ====== 管理者モードコマンド処理ロジック ======
    async def handle_admin_mode_command(self, message: discord.Message) -> bool:
        """管理者モードのコマンドを処理。処理した場合True、しなかった場合Falseを返す"""
        config = self.bot.config
        
        content = message.content
        # メンションとチャンネル参照を除去してからパターンマッチング
        content_no_mentions = re.sub(r"<@!?\d+>", "", content)
        content_no_mentions = re.sub(r"<#\d+>", "", content_no_mentions)
        normalized = normalize_text(content_no_mentions)
        # 類義語を統一
        unified = normalize_synonyms(normalized)
        
        # キーワード定義
        ADD_KEYWORDS = ["追加", "入れて", "登録", "加えて", "つけて", "付けて", "いれて", "加入", "参加", "にして", "として"]
        REMOVE_KEYWORDS = ["削除", "解除", "外して", "消して", "除外", "取り消し", "はずして", "抜いて", "除いて", "取って", "とって", "やめて", "外す", "キャンセル", "なくして"]
        ON_KEYWORDS = ["オン", "有効", "つけて", "入れて", "開始", "スタート", "起動", "enable", "on", "始めて"]
        OFF_KEYWORDS = ["オフ", "無効", "止めて", "停止", "ストップ", "終了", "disable", "off", "やめて", "切って"]
        
        try:
             # ===== 管理者追加 =====
            admin_add_keywords = ["管理者", "admin", "アドミン", "モデレーター", "mod", "権限"]
            if has_any(unified, admin_add_keywords) and has_any(unified, ADD_KEYWORDS):
                if not has_any(unified, REMOVE_KEYWORDS):
                    if message.mentions:
                        user = message.mentions[0]
                        config.ADMIN_IDS.add(user.id)
                        config.save_config()
                        await message.reply(f"{user.mention} を管理者に追加したよ！")
                        return True
            
            # ===== 管理者削除 =====
            if has_any(unified, admin_add_keywords) and has_any(unified, REMOVE_KEYWORDS):
                if message.mentions:
                    user = message.mentions[0]
                    config.ADMIN_IDS.discard(user.id)
                    config.save_config()
                    await message.reply(f"{user.mention} を管理者から削除したよ！")
                    return True
            
            # ===== autoping設定 =====
            autoping_keywords = ["autoping", "オートピング", "おーとぴんぐ", "自動ピング", "自動ping", "オートping", "自動通知", "ping通知"]
            if has_any(unified, autoping_keywords):
                if has_any(unified, OFF_KEYWORDS):
                    config.AUTO_PING_CHANNEL_ID = 0
                    config.save_config()
                    await message.reply("オートピングを無効化したよ！")
                    return True
                if has_any(unified, ON_KEYWORDS + ["設定", "セット", "変更", "指定"]):
                    if message.channel_mentions:
                        channel = message.channel_mentions[0]
                        config.AUTO_PING_CHANNEL_ID = channel.id
                        config.save_config()
                        await message.reply("オートピングを設定したよ！")
                        return True
            
            # ===== VC出禁追加 =====
            block_keywords = ["出禁", "ブロック", "ban", "バン", "追放", "キック", "締め出し", "入室禁止", "参加禁止", "vcブロック", "vcban"]
            if has_any(unified, block_keywords) and not has_any(unified, REMOVE_KEYWORDS):
                if message.mentions:
                    user = message.mentions[0]
                    config.BLOCKED_USERS.add(user.id)
                    config.save_config()
                    await message.reply(f"{user.mention} を出禁にしたよ！")
                    return True
            
            # ===== VC出禁解除 =====
            if has_any(unified, block_keywords) and has_any(unified, REMOVE_KEYWORDS):
                if message.mentions:
                    user = message.mentions[0]
                    config.BLOCKED_USERS.discard(user.id)
                    config.save_config()
                    await message.reply(f"{user.mention} を出禁から解除したよ！")
                    return True
            
            # ===== 監視対象追加 =====
            watch_keywords = ["監視", "ウォッチ", "watch", "対象", "見張り", "チェック対象", "vc対象", "チャンネル対象"]
            if has_any(unified, watch_keywords) and has_any(unified, ADD_KEYWORDS):
                if not has_any(unified, REMOVE_KEYWORDS):
                    match = re.search(r"(\d{17,20})", content)
                    if match:
                        vc_id = int(match.group(1))
                        config.TARGET_VC_IDS.add(vc_id)
                        config.save_config()
                        await message.reply(f"チャンネルID {vc_id} を監視対象に追加したよ！")
                        return True
            
            # ===== 監視対象削除 =====
            if has_any(unified, watch_keywords) and has_any(unified, REMOVE_KEYWORDS):
                match = re.search(r"(\d{17,20})", content)
                if match:
                    vc_id = int(match.group(1))
                    config.TARGET_VC_IDS.discard(vc_id)
                    config.save_config()
                    await message.reply(f"チャンネルID {vc_id} を監視対象から削除したよ！")
                    return True
            
             # ===== チャット削除 =====
            chat_keywords = ["チャット", "メッセージ", "発言", "ログ", "履歴", "会話", "投稿", "掃除", "クリア", "clear"]
            delete_keywords = ["削除", "消して", "掃除", "クリア", "clear", "消去", "片付け", "きれいに", "綺麗に"]
            if has_any(unified, chat_keywords) and has_any(unified, delete_keywords):
                if not has_any(unified, watch_keywords):  # 監視対象削除と区別
                    match = re.search(r"(\d+)件", content)
                    limit = int(match.group(1)) if match else 300
                    
                    if isinstance(message.channel, discord.TextChannel):
                        await message.channel.purge(limit=limit + 1)
                        await message.channel.send("お掃除完了！綺麗になったね！", delete_after=5)
                        return True
            
            # ===== DM送信 =====
            dm_keywords = ["dm", "ダイレクトメッセージ", "ディーエム", "プライベートメッセージ", "個人メッセージ"]
            send_keywords = ["送信", "送って", "送る", "伝えて", "伝える", "届けて", "届ける"]
            if has_any(unified, dm_keywords) and has_any(unified, send_keywords):
                if message.mentions:
                    user = message.mentions[0]
                    dm_match = re.search(r"(?:に|へ)(.+?)(?:と|って)(?:dm|DM)", content, re.IGNORECASE)
                    if not dm_match:
                        dm_match = re.search(r"(?:dm|DM)(?:送信|送って)(.+)", content, re.IGNORECASE)
                    
                    dm_content = ""
                    if dm_match:
                        dm_content = dm_match.group(1).strip()
                    
                    files = [await att.to_file() for att in message.attachments] if message.attachments else []
                    
                    try:
                        await user.send(content=dm_content if dm_content else None, files=files if files else None)
                        await message.reply("メッセージを送信したよ！")
                    except:
                        await message.reply("DMの送信に失敗したよ...")
                    return True
            
            # ===== ヘルプ =====
            help_keywords = ["ヘルプ", "困った", "help", "使い方", "わからない", "教えて", "どうすれば", "何ができる", "コマンド一覧", "機能一覧"]
            if has_any(unified, help_keywords):
                await message.reply("ヘルプを表示するね！")
                # ヘルプ内容を表示
                embed = discord.Embed(title="📖 ヘルプ", description="管理者モードで使えるコマンド一覧", color=discord.Color.blue())
                embed.add_field(name="管理者管理", value="「@ユーザーを管理者に追加して」\n「@ユーザーを管理者から削除して」", inline=False)
                embed.add_field(name="オートピング", value="「autopingを#チャンネルに設定して」\n「autopingを無効化して」", inline=False)
                embed.add_field(name="VC出禁", value="「@ユーザーをボイスチャット出禁にして」\n「@ユーザーをボイスチャット出禁解除して」", inline=False)
                embed.add_field(name="監視対象", value="「チャンネルidXXXを監視対象に追加して」\n「チャンネルidXXXを監視対象から削除して」", inline=False)
                embed.add_field(name="その他", value="「チャットをX件削除して」\n「@ユーザーに○○とdm送信して」\n「リストを表示して」\n「pingを表示して」\n「再起動して」\n「○○と発言して」\n「監視機能をオン/オフにして」\n「システムチェック」", inline=False)
                await message.channel.send(embed=embed)
                return True
            
            # ===== リスト表示 =====
            list_keywords = ["リスト", "一覧", "設定", "確認", "状態", "ステータス", "status", "list", "見せて", "表示", "誰が", "何が", "登録されてる", "今の"]
            if has_any(unified, list_keywords) and not has_any(unified, delete_keywords + ADD_KEYWORDS):
                if has_any(unified, ["表示", "見せて", "確認", "教えて", "見たい", "知りたい"]) or ("リスト" in unified):
                    await message.reply("リストを表示するね！")
                    
                    if config.BLOCKED_USERS:
                        user_list = []
                        for uid in config.BLOCKED_USERS:
                            try:
                                user = await self.bot.fetch_user(uid)
                                user_list.append(f"• {user.name} ({uid})")
                            except:
                                user_list.append(f"• 不明なユーザー ({uid})")
                        embed1 = discord.Embed(title="🚫 対象ユーザーリスト", description="\n".join(user_list), color=discord.Color.red())
                    else:
                        embed1 = discord.Embed(title="🚫 対象ユーザーリスト", description="登録なし", color=discord.Color.red())
                    await message.channel.send(embed=embed1)
                    
                    if config.ADMIN_IDS:
                        admin_list = []
                        for uid in config.ADMIN_IDS:
                            try:
                                user = await self.bot.fetch_user(uid)
                                admin_list.append(f"• {user.name} ({uid})")
                            except:
                                admin_list.append(f"• 不明なユーザー ({uid})")
                        embed2 = discord.Embed(title="👑 管理者リスト", description="\n".join(admin_list), color=discord.Color.gold())
                    else:
                        embed2 = discord.Embed(title="👑 管理者リスト", description="登録なし", color=discord.Color.gold())
                    await message.channel.send(embed=embed2)
                    return True
            
            # ===== ping表示 =====
            ping_keywords = ["ping", "ピング", "ピン", "遅延", "レイテンシ", "latency", "応答速度", "反応速度", "速度"]
            if has_any(unified, ping_keywords):
                await message.reply("pingを表示するね！")
                latency = round(self.bot.latency * 1000)
                embed = discord.Embed(title="🏓 Pong!", description=f"レイテンシ: **{latency}ms**", color=discord.Color.green())
                await message.channel.send(embed=embed)
                return True
            
            # ===== 再起動 (SystemCogに任せたいが、機能としてはここ) =====
            restart_keywords = ["再起動", "リスタート", "restart", "reboot", "リブート", "再開", "起動し直し", "立ち上げ直し", "もう一回起動", "再立ち上げ"]
            if has_any(unified, restart_keywords):
                await message.reply("再起動するね！")
                import sys
                import asyncio
                await asyncio.sleep(3)
                await self.bot.close()
                sys.exit(0)
            
            # ===== 発言 =====
            say_keywords = ["発言", "言って", "しゃべって", "喋って", "話して", "送って", "投稿", "つぶやいて", "呟いて", "say"]
            if has_any(unified, say_keywords):
                match = re.search(r"(.+?)(?:と発言|って言|と言|をしゃべ|を喋|を話|と送|を投稿|とつぶや|と呟|とsay)", content, re.IGNORECASE)
                if match:
                    say_content = match.group(1).strip()
                    if say_content:
                        await message.channel.send(say_content)
                        return True
            
            # ===== 監視機能オン/オフ =====
            monitor_keywords = ["監視", "ウォッチ", "watch", "ブロック機能", "出禁機能", "vc機能", "自動切断", "自動キック"]
            if has_any(unified, monitor_keywords) and has_any(unified, ["機能", "システム", "モード"]):
                if has_any(unified, ON_KEYWORDS):
                    config.vc_block_enabled = True
                    config.save_config()
                    await message.reply("監視機能をオンにしたよ！")
                    return True
                if has_any(unified, OFF_KEYWORDS):
                    config.vc_block_enabled = False
                    config.save_config()
                    await message.reply("監視機能をオフにしたよ！")
                    return True
            
            # ===== システムチェック =====
            check_keywords = ["システムチェック", "systemcheck", "テスト", "test", "診断", "ヘルスチェック", "healthcheck", "動作確認", "状態確認", "チェック"]
            if has_any(unified, check_keywords) and has_any(unified, ["システム", "ボット", "bot", "動作", "状態", "実行", "確認"]):
                await message.reply("システムをチェックするね！")
                
                results = []
                all_ok = True
                
                latency = round(self.bot.latency * 1000)
                if latency < 200:
                    results.append(f"✅ レイテンシ: {latency}ms")
                else:
                    results.append(f"⚠️ レイテンシ: {latency}ms（高め）")
                    all_ok = False
                
                # Config checks
                try:
                    config.load_config() # Reload to check
                    results.append("✅ 設定ファイル: 読み込み可能")
                except:
                    results.append("❌ 設定ファイル: エラー")
                    all_ok = False
                
                results.append(f"✅ VC自動切断機能: {'ON' if config.vc_block_enabled else 'OFF'}")
                results.append(f"✅ 対象ユーザー数: {len(config.BLOCKED_USERS)}人")
                results.append(f"✅ 対象VC数: {len(config.TARGET_VC_IDS)}個")
                results.append(f"✅ 管理者数: {len(config.ADMIN_IDS)}人")
                
                embed = discord.Embed(title="🔧 システムチェック結果", description="\n".join(results), color=discord.Color.green())
                await message.channel.send(embed=embed)
                
                if all_ok:
                    await message.channel.send("問題なし！全てのシステムは正常に作動しているよ！")
                return True
            
            # ===== フォールバック処理 =====
             # 「削除」が含まれていて、メンションがあれば出禁解除を推測
            if "削除" in unified and message.mentions and not has_any(unified, watch_keywords):
                user = message.mentions[0]
                config.BLOCKED_USERS.discard(user.id)
                config.ADMIN_IDS.discard(user.id)
                config.save_config()
                await message.reply(f"{user.mention} を削除したよ！（出禁リストと管理者リストから）")
                return True
            
            # 「削除」が含まれていて、数字があれば監視対象から削除を推測
            if "削除" in unified:
                match = re.search(r"(\d{17,20})", content)
                if match:
                    vc_id = int(match.group(1))
                    config.TARGET_VC_IDS.discard(vc_id)
                    config.save_config()
                    await message.reply(f"チャンネルID {vc_id} を監視対象から削除したよ！")
                    return True
            
            # 「削除」のみが含まれていればチャット削除を推測
            if "削除" in unified or "消して" in unified or "掃除" in unified:
                if isinstance(message.channel, discord.TextChannel):
                    match = re.search(r"(\d+)件", content)
                    limit = int(match.group(1)) if match else 100
                    await message.channel.purge(limit=limit + 1)
                    await message.channel.send("お掃除完了！綺麗になったね！", delete_after=5)
                    return True
            
            # 「追加」が含まれていて、メンションがあれば管理者追加を推測
            if has_any(unified, ADD_KEYWORDS) and message.mentions:
                user = message.mentions[0]
                config.ADMIN_IDS.add(user.id)
                config.save_config()
                await message.reply(f"{user.mention} を管理者に追加したよ！")
                return True
            
            return False

        except Exception as e:
            # エラーをオーナーに報告
            try:
                owner = await self.bot.fetch_user(config.OWNER_ID)
                await owner.send(f"❌ 管理者モードエラー: {e}\nコマンド: {content}")
            except:
                pass
            await message.reply(f"エラーが発生したよ: {e}")
            return True


    # ====== 管理者モードタイムアウトチェック ======
    @tasks.loop(seconds=30)
    async def check_admin_mode_timeout(self):
        """管理者モードのタイムアウトをチェック"""
        config = self.bot.config
        now = datetime.now(JST)
        timed_out_users = []
        
        for user_id, last_activity in list(config.admin_mode_users.items()):
            if (now - last_activity).total_seconds() > config.ADMIN_MODE_TIMEOUT:
                timed_out_users.append(user_id)
                del config.admin_mode_users[user_id]
        
        # タイムアウトしたユーザーに通知
        for user_id in timed_out_users:
            try:
                user = await self.bot.fetch_user(user_id)
                await user.send("またいつでも呼んでね！")
                print(f"⏰ 管理者モードタイムアウト: {user.name}")
            except Exception as e:
                print(f"❌ タイムアウト通知失敗: {e}")

    # ====== スラッシュコマンド ======

    @app_commands.command(name="addadmin", description="管理者を追加（オーナーのみ）")
    @app_commands.describe(user="追加する管理者（@メンション）")
    async def addadmin_command(self, interaction: discord.Interaction, user: discord.Member):
        config = self.bot.config
        if interaction.user.id != config.OWNER_ID:
            await interaction.response.send_message("このコマンドはオーナーのみが使用できます。", ephemeral=True)
            return
        
        if user.id == interaction.user.id:
            await interaction.response.send_message("⚠️ 自分自身を管理者に追加することはできません", ephemeral=True)
            return
        
        if user.id in config.ADMIN_IDS:
            await interaction.response.send_message(f"⚠️ {user.name} は既に管理者です", ephemeral=True)
            return
        
        view = self.AddAdminConfirmView(user, interaction.user, config)
        await interaction.response.send_message(
            f"本当に {user.name} を管理者に追加しますか？",
            view=view,
            ephemeral=True
        )

    @app_commands.command(name="removeadmin", description="管理者を削除（オーナーのみ）")
    @app_commands.describe(user="削除する管理者（@メンション）")
    async def removeadmin_command(self, interaction: discord.Interaction, user: discord.Member):
        config = self.bot.config
        if interaction.user.id != config.OWNER_ID:
            await interaction.response.send_message("このコマンドはオーナーのみが使用できます。", ephemeral=True)
            return
        
        if user.id not in config.ADMIN_IDS:
            await interaction.response.send_message(f"⚠️ {user.name} は管理者ではありません", ephemeral=True)
            return
        
        view = self.RemoveAdminConfirmView(user, config)
        await interaction.response.send_message(
            f"本当に {user.name} を管理者から削除しますか？",
            view=view,
            ephemeral=True
        )

    @app_commands.command(name="listadmin", description="管理者一覧を表示（オーナーのみ）")
    async def listadmin_command(self, interaction: discord.Interaction):
        config = self.bot.config
        if interaction.user.id != config.OWNER_ID:
            await interaction.response.send_message("このコマンドはオーナーのみが使用できます。", ephemeral=True)
            return
        
        admin_list = "なし"
        guild = interaction.guild
        if config.ADMIN_IDS and guild:
            admin_names = []
            for admin_id in config.ADMIN_IDS:
                try:
                    member = await guild.fetch_member(admin_id)
                    admin_names.append(f"- {member.name} ({admin_id})")
                except:
                    admin_names.append(f"- ID: {admin_id} (未確認)")
            admin_list = "\n".join(admin_names)
        
        embed = discord.Embed(
            title="管理者一覧",
            description="現在の管理者",
            color=discord.Color.orange()
        )
        embed.add_field(name="管理者", value=admin_list, inline=False)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="exit", description="管理者モードを終了（オーナーのみ）")
    async def exit_command(self, interaction: discord.Interaction):
        config = self.bot.config
        if interaction.user.id != config.OWNER_ID:
            await interaction.response.send_message("このコマンドはオーナーのみが使用できます。", ephemeral=True)
            return
        
        if config.is_in_admin_mode(interaction.user.id):
            config.exit_admin_mode(interaction.user.id)
            await interaction.response.send_message("✅ 管理者モードを終了しました", ephemeral=True)
        else:
            await interaction.response.send_message("ℹ️ 管理者モードは起動していません", ephemeral=True)

async def setup(bot):
    await bot.add_cog(AdminCog(bot))
