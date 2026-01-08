import discord
from discord.ext import commands, tasks
from discord import app_commands
from discord.ui import View, Button
from typing import Optional, List, Union
import os
import sys
import re
from datetime import datetime, timezone, timedelta, time
import json
from googleapiclient.discovery import build

# ====== Intents 設定 ======
intents = discord.Intents.default()
intents.members = True
intents.voice_states = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ====== Google検索API設定 ======
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
GOOGLE_CSE_ID = os.environ.get("GOOGLE_CSE_ID", "")

# Google Custom Search サービス初期化
google_service = None
if GOOGLE_API_KEY and GOOGLE_CSE_ID:
    try:
        google_service = build("customsearch", "v1", developerKey=GOOGLE_API_KEY)
        print("✅ Google検索API初期化完了")
    except Exception as e:
        print(f"❌ Google検索API初期化失敗: {e}")

# ====== 設定ここだけ書き換える ======
OWNER_ID = 1163117069173272576  # あなたのID

# 初期管理者（オーナーのみ）
ADMIN_IDS = set()

# 初期対象ユーザー（複数可）
BLOCKED_USERS = {
    778146015571345418,  # 人①
    991272401293811753,  # 人②
}

# 初期対象VC（複数可）
TARGET_VC_IDS = {
    1311666056124825691,
}

# VC自動切断機能の初期状態
vc_block_enabled = True  # 初期ON

# 自動pingを送信するチャンネルID（0の場合は無効）
AUTO_PING_CHANNEL_ID = 0

# データ永続化用ファイル
CONFIG_FILE = "vcblock_config.json"

# ===================================

# ====== 認可チェック関数 ======
def is_authorized(user_id: int) -> bool:
    """ユーザーがオーナーまたは管理者かチェック"""
    return user_id == OWNER_ID or user_id in ADMIN_IDS


# ====== 日本時間のタイムゾーン ======
JST = timezone(timedelta(hours=9))

# ====== オーナーへのログ通知関数 ======
async def log_to_owner(log_type: str, user: Union[discord.User, discord.Member], command: str, details: str = ""):
    """管理者アクションまたは権限エラーをオーナーにDMでログ通知"""
    try:
        owner = await bot.fetch_user(OWNER_ID)
        current_time = datetime.now(JST).strftime("%Y年%m月%d日 %H:%M:%S")
        
        if log_type == "action":
            embed = discord.Embed(
                title="📋 管理者アクションログ",
                color=discord.Color.blue()
            )
        else:
            embed = discord.Embed(
                title="⚠️ 権限エラーログ",
                color=discord.Color.red()
            )
        
        embed.add_field(name="時刻", value=current_time, inline=False)
        embed.add_field(name="実行者", value=f"{user.name} ({user.id})", inline=False)
        embed.add_field(name="コマンド", value=command, inline=False)
        if details:
            embed.add_field(name="詳細", value=details, inline=False)
        
        await owner.send(embed=embed)
    except Exception as e:
        print(f"❌ オーナーへのログ送信に失敗: {e}")

# ====== データ永続化関数 ======
def save_config():
    """設定をJSONファイルに保存"""
    global vc_block_enabled, BLOCKED_USERS, TARGET_VC_IDS, ADMIN_IDS, AUTO_PING_CHANNEL_ID
    config = {
        "admin_ids": list(ADMIN_IDS),
        "blocked_users": list(BLOCKED_USERS),
        "target_vc_ids": list(TARGET_VC_IDS),
        "vc_block_enabled": vc_block_enabled,
        "auto_ping_channel_id": AUTO_PING_CHANNEL_ID
    }
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)
    print(f"💾 設定を保存しました")

def load_config():
    """JSONファイルから設定を読み込む"""
    global BLOCKED_USERS, TARGET_VC_IDS, vc_block_enabled, ADMIN_IDS, AUTO_PING_CHANNEL_ID
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f:
                config = json.load(f)
            ADMIN_IDS = set(config.get("admin_ids", []))
            BLOCKED_USERS = set(config.get("blocked_users", []))
            TARGET_VC_IDS = set(config.get("target_vc_ids", []))
            vc_block_enabled = config.get("vc_block_enabled", True)
            AUTO_PING_CHANNEL_ID = config.get("auto_ping_channel_id", 0)
            print(f"📂 設定を読み込みました")
        else:
            print(f"⚠️ 設定ファイルが見つかりません。初期値を使用します")
            save_config()
    except Exception as e:
        print(f"❌ 設定の読み込みに失敗しました: {e}")

# ====== 自動pingタスク（日本時間0時） ======
@tasks.loop(time=time(hour=15, minute=0, second=0))  # UTC 15:00 = JST 0:00
async def daily_ping():
    """日本時間0時に自動でpingを送信"""
    global AUTO_PING_CHANNEL_ID
    if AUTO_PING_CHANNEL_ID == 0:
        return
    
    try:
        channel = bot.get_channel(AUTO_PING_CHANNEL_ID)
        if channel is None:
            print(f"❌ 自動ping: チャンネルが見つかりません (ID: {AUTO_PING_CHANNEL_ID})")
            return
        
        latency = round(bot.latency * 1000)
        current_time = datetime.now(JST).strftime("%Y年%m月%d日 %H:%M:%S")
        
        embed = discord.Embed(
            title="🏓 Daily Ping",
            description=f"レイテンシ: **{latency}ms**\n\n-# このメッセージはReplit.comによって自動実行されています",
            color=discord.Color.green() if latency < 200 else discord.Color.orange()
        )
        embed.set_footer(text=f"自動実行: {current_time}")
        
        await channel.send(embed=embed)  # type: ignore
        print(f"✅ 自動ping送信完了 [{current_time}]")
        
        # 自動テストも実行
        await run_daily_test(channel)
    except Exception as e:
        print(f"❌ 自動ping送信失敗: {e}")


async def run_daily_test(channel):
    """日本時間0時に自動でシステムテストを実行"""
    try:
        current_time = datetime.now(JST).strftime("%Y年%m月%d日 %H:%M:%S")
        results = []
        
        # 1. レイテンシチェック
        latency = round(bot.latency * 1000)
        if latency < 200:
            results.append(f"✅ レイテンシ: {latency}ms")
        else:
            results.append(f"⚠️ レイテンシ: {latency}ms（高め）")
        
        # 2. 設定ファイル読み書きチェック
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                json.load(f)
            results.append("✅ 設定ファイル: 読み込み可能")
        except Exception as e:
            results.append(f"❌ 設定ファイル: {e}")
        
        # 3. VC監視機能の状態
        status = "ON" if vc_block_enabled else "OFF"
        results.append(f"✅ VC自動切断機能: {status}")
        
        # 4. 対象ユーザー数
        results.append(f"✅ 対象ユーザー数: {len(BLOCKED_USERS)}人")
        
        # 5. 対象VC数
        results.append(f"✅ 対象VC数: {len(TARGET_VC_IDS)}個")
        
        # 6. 管理者数
        results.append(f"✅ 管理者数: {len(ADMIN_IDS)}人")
        
        embed = discord.Embed(
            title="🔧 Daily System Check",
            description="\n".join(results) + "\n\n-# このメッセージはReplit.comによって自動実行されています",
            color=discord.Color.green()
        )
        embed.set_footer(text=f"自動実行: {current_time}")
        
        await channel.send(embed=embed)  # type: ignore
        print(f"✅ 自動テスト送信完了 [{current_time}]")
    except Exception as e:
        print(f"❌ 自動テスト送信失敗: {e}")


@bot.event
async def on_ready():
    load_config()
    await bot.tree.sync()
    
    # ステータスを設定
    activity = discord.Game(name="ブロスタ")
    await bot.change_presence(activity=activity)
    
    # 自動pingタスクを開始
    if not daily_ping.is_running():
        daily_ping.start()
    
    print(f"ログイン成功: {bot.user}")
    
    # 起動完了メッセージをオーナーにDM送信
    try:
        owner = await bot.fetch_user(OWNER_ID)
        current_time = datetime.now(JST).strftime("%Y年%m月%d日 %H:%M:%S")
        embed = discord.Embed(
            title="✅ 起動完了",
            description=f"ボットが正常に起動しました。",
            color=discord.Color.green()
        )
        embed.set_footer(text=f"起動時刻: {current_time}")
        await owner.send(embed=embed)
    except Exception as e:
        print(f"❌ 起動メッセージ送信失敗: {e}")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    
    if isinstance(message.channel, discord.DMChannel):
        if message.author.id == OWNER_ID:
            return
        
        try:
            owner = await bot.fetch_user(OWNER_ID)
            current_time = datetime.now(JST).strftime("%Y年%m月%d日 %H:%M:%S")
            
            embed = discord.Embed(
                title="📩 DM受信",
                description=message.content if message.content else "(メッセージなし)",
                color=discord.Color.blue()
            )
            embed.add_field(name="送信者", value=f"{message.author.name} ({message.author.id})", inline=False)
            embed.add_field(name="時刻", value=current_time, inline=False)
            
            if message.attachments:
                attachment_list = "\n".join([att.url for att in message.attachments])
                embed.add_field(name="添付ファイル", value=attachment_list[:1000], inline=False)
            
            await owner.send(embed=embed)
            print(f"📩 DM転送完了: {message.author.name} [{current_time}]")
        except Exception as e:
            print(f"❌ DM転送失敗: {e}")
    
    # 「〇〇と検索して」パターンに反応
    if "と検索して" in message.content:
        await handle_search_request(message)


async def handle_search_request(message: discord.Message):
    """「〇〇と検索して」に反応してGoogle検索を実行"""
    global google_service
    
    if not google_service:
        await message.reply("❌ Google検索APIが設定されていません。")
        return
    
    # 「〇〇と検索して」のパターンから検索ワードを抽出
    match = re.search(r"(.+?)と検索して", message.content)
    if not match:
        return
    
    query = match.group(1).strip()
    if not query:
        await message.reply("❌ 検索ワードが見つかりませんでした。")
        return
    
    try:
        async with message.channel.typing():
            result = google_service.cse().list(
                q=query,
                cx=GOOGLE_CSE_ID,
                num=5
            ).execute()
            
            if 'items' not in result:
                await message.reply(f"🔍 「{query}」の検索結果が見つかりませんでした。")
                return
            
            embed = discord.Embed(
                title=f"🔍 「{query}」の検索結果",
                color=discord.Color.blue()
            )
            
            for i, item in enumerate(result['items'][:5], 1):
                title = item['title'][:100]
                link = item['link']
                snippet = item.get('snippet', 'No description')[:150]
                
                embed.add_field(
                    name=f"{i}. {title}",
                    value=f"{snippet}...\n[リンク]({link})",
                    inline=False
                )
            
            embed.set_footer(text=f"検索者: {message.author.name}")
            await message.reply(embed=embed)
            print(f"🔍 検索実行: {query} by {message.author.name}")
            
    except Exception as e:
        await message.reply(f"❌ 検索エラー: {e}")
        print(f"❌ 検索エラー: {e}")


# ====== オートコンプリート関数 ======
async def switch_autocomplete(interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
    modes = ["on", "off"]
    return [
        app_commands.Choice(name=mode, value=mode)
        for mode in modes if mode.startswith(current.lower())
    ]

async def blockuser_autocomplete(interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
    modes = ["add", "remove"]
    return [
        app_commands.Choice(name=mode, value=mode)
        for mode in modes if mode.startswith(current.lower())
    ]

async def blockvc_autocomplete(interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
    modes = ["add", "remove"]
    return [
        app_commands.Choice(name=mode, value=mode)
        for mode in modes if mode.startswith(current.lower())
    ]


# ====== スラッシュコマンド /switch ======
@bot.tree.command(name="switch", description="VC自動切断機能のON/OFF切り替え")
@app_commands.describe(mode="on または off")
@app_commands.autocomplete(mode=switch_autocomplete)
async def switch_command(interaction: discord.Interaction, mode: str):
    global vc_block_enabled

    if not is_authorized(interaction.user.id):
        await interaction.response.send_message("このコマンドを使う権限はありません。", ephemeral=True)
        await log_to_owner("error", interaction.user, "/switch", f"mode: {mode}")
        return

    mode = mode.lower()
    if mode == "on":
        vc_block_enabled = True
        save_config()
        await interaction.response.send_message("✅ VC自動切断：ON", ephemeral=True)
        if interaction.user.id != OWNER_ID:
            await log_to_owner("action", interaction.user, "/switch", "VC自動切断をONに変更")
    elif mode == "off":
        vc_block_enabled = False
        save_config()
        await interaction.response.send_message("⛔ VC自動切断：OFF", ephemeral=True)
        if interaction.user.id != OWNER_ID:
            await log_to_owner("action", interaction.user, "/switch", "VC自動切断をOFFに変更")
    else:
        await interaction.response.send_message("❌ on または off を指定してください", ephemeral=True)


# ====== スラッシュコマンド /blockuser ======
@bot.tree.command(name="blockuser", description="対象ユーザーの追加/削除")
@app_commands.describe(
    mode="add または remove",
    user="対象ユーザー（@メンション）"
)
@app_commands.autocomplete(mode=blockuser_autocomplete)
async def blockuser_command(interaction: discord.Interaction, mode: str, user: discord.Member):
    if not is_authorized(interaction.user.id):
        await interaction.response.send_message("このコマンドを使う権限はありません。", ephemeral=True)
        await log_to_owner("error", interaction.user, "/blockuser", f"mode: {mode}, user: {user.name}")
        return

    mode = mode.lower()
    if mode == "add":
        if user.id in BLOCKED_USERS:
            await interaction.response.send_message(f"⚠️ {user.name} は既に対象ユーザーに追加されています", ephemeral=True)
        else:
            BLOCKED_USERS.add(user.id)
            save_config()
            await interaction.response.send_message(f"✅ {user.name} を対象ユーザーに追加", ephemeral=True)
            if interaction.user.id != OWNER_ID:
                await log_to_owner("action", interaction.user, "/blockuser", f"{user.name} を対象ユーザーに追加")
    elif mode == "remove":
        if user.id not in BLOCKED_USERS:
            await interaction.response.send_message(f"⚠️ {user.name} は対象ユーザーリストに含まれていません", ephemeral=True)
        else:
            BLOCKED_USERS.discard(user.id)
            save_config()
            await interaction.response.send_message(f"✅ {user.name} を対象ユーザーから削除しました", ephemeral=True)
            if interaction.user.id != OWNER_ID:
                await log_to_owner("action", interaction.user, "/blockuser", f"{user.name} を対象ユーザーから削除")
    else:
        await interaction.response.send_message("❌ add または remove を指定してください", ephemeral=True)


# ====== スラッシュコマンド /blockvc ======
@bot.tree.command(name="blockvc", description="対象VCの追加/削除")
@app_commands.describe(
    mode="add または remove",
    vc="対象VCのID（数字のみ）"
)
@app_commands.autocomplete(mode=blockvc_autocomplete)
async def blockvc_command(interaction: discord.Interaction, mode: str, vc: str):
    if not is_authorized(interaction.user.id):
        await interaction.response.send_message("このコマンドを使う権限はありません。", ephemeral=True)
        await log_to_owner("error", interaction.user, "/blockvc", f"mode: {mode}, vc: {vc}")
        return

    if not vc.isdigit():
        await interaction.response.send_message("❌ VCのIDを正しく指定してください", ephemeral=True)
        return

    mode = mode.lower()
    vc_int = int(vc)
    
    if mode == "add":
        if vc_int in TARGET_VC_IDS:
            await interaction.response.send_message(f"⚠️ VC {vc} は既に対象に追加されています", ephemeral=True)
        else:
            TARGET_VC_IDS.add(vc_int)
            save_config()
            await interaction.response.send_message(f"✅ VC {vc} を対象に追加", ephemeral=True)
            if interaction.user.id != OWNER_ID:
                await log_to_owner("action", interaction.user, "/blockvc", f"VC {vc} を対象に追加")
    elif mode == "remove":
        if vc_int not in TARGET_VC_IDS:
            await interaction.response.send_message(f"⚠️ VC {vc} は対象VCリストに含まれていません", ephemeral=True)
        else:
            TARGET_VC_IDS.discard(vc_int)
            save_config()
            await interaction.response.send_message(f"✅ VC {vc} を対象から削除しました", ephemeral=True)
            if interaction.user.id != OWNER_ID:
                await log_to_owner("action", interaction.user, "/blockvc", f"VC {vc} を対象から削除")
    else:
        await interaction.response.send_message("❌ add または remove を指定してください", ephemeral=True)


# ====== スラッシュコマンド /list ======
@bot.tree.command(name="list", description="現在の設定一覧を表示")
async def list_command(interaction: discord.Interaction):
    if not is_authorized(interaction.user.id):
        await interaction.response.send_message("このコマンドを使う権限はありません。", ephemeral=True)
        await log_to_owner("error", interaction.user, "/list", "設定一覧の閲覧を試行")
        return

    # 対象ユーザーのリスト取得
    user_list = "なし"
    guild = interaction.guild
    if BLOCKED_USERS and guild:
        user_names = []
        for user_id in BLOCKED_USERS:
            try:
                member = await guild.fetch_member(user_id)
                user_names.append(f"- {member.name} ({user_id})")
            except:
                user_names.append(f"- ID: {user_id} (未確認)")
        user_list = "\n".join(user_names)
    
    # 対象VCのリスト取得
    vc_list = "なし"
    if TARGET_VC_IDS and guild:
        vc_names = []
        for vc_id in TARGET_VC_IDS:
            try:
                channel = await guild.fetch_channel(vc_id)
                vc_names.append(f"- {channel.name} ({vc_id})")
            except:
                vc_names.append(f"- ID: {vc_id} (未確認)")
        vc_list = "\n".join(vc_names)
    
    status = "✅ ON" if vc_block_enabled else "⛔ OFF"
    
    embed = discord.Embed(
        title="VC自動切断の設定",
        description=f"状態: {status}",
        color=discord.Color.blue()
    )
    embed.add_field(name="対象ユーザー", value=user_list, inline=False)
    embed.add_field(name="対象VC", value=vc_list, inline=False)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ====== 管理者追加確認用View ======
class AddAdminConfirmView(View):
    def __init__(self, target_user: discord.Member, owner: Union[discord.User, discord.Member]):
        super().__init__()
        self.target_user = target_user
        self.owner = owner
    
    @discord.ui.button(label="確認", style=discord.ButtonStyle.green)
    async def confirm_button(self, interaction: discord.Interaction, button: Button):
        global ADMIN_IDS
        ADMIN_IDS.add(self.target_user.id)
        save_config()
        
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
    async def cancel_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.edit_message(content="❌ キャンセルしました", view=None)


# ====== 管理者削除確認用View ======
class RemoveAdminConfirmView(View):
    def __init__(self, target_user: discord.Member):
        super().__init__()
        self.target_user = target_user
    
    @discord.ui.button(label="確認", style=discord.ButtonStyle.green)
    async def confirm_button(self, interaction: discord.Interaction, button: Button):
        global ADMIN_IDS
        ADMIN_IDS.discard(self.target_user.id)
        save_config()
        
        await interaction.response.edit_message(content=f"✅ {self.target_user.name} を管理者から削除しました", view=None)
        print(f"✅ {self.target_user.name} ({self.target_user.id}) を管理者から削除しました")
    
    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.red)
    async def cancel_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.edit_message(content="❌ キャンセルしました", view=None)


# ====== スラッシュコマンド /addadmin ======
@bot.tree.command(name="addadmin", description="管理者を追加（オーナーのみ）")
@app_commands.describe(user="追加する管理者（@メンション）")
async def addadmin_command(interaction: discord.Interaction, user: discord.Member):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("このコマンドはオーナーのみが使用できます。", ephemeral=True)
        await log_to_owner("error", interaction.user, "/addadmin", f"対象: {user.name}")
        return
    
    if user.id == interaction.user.id:
        await interaction.response.send_message("⚠️ 自分自身を管理者に追加することはできません", ephemeral=True)
        return
    
    if user.id in ADMIN_IDS:
        await interaction.response.send_message(f"⚠️ {user.name} は既に管理者です", ephemeral=True)
        return
    
    view = AddAdminConfirmView(user, interaction.user)
    await interaction.response.send_message(
        f"本当に {user.name} を管理者に追加しますか？",
        view=view,
        ephemeral=True
    )


# ====== スラッシュコマンド /removeadmin ======
@bot.tree.command(name="removeadmin", description="管理者を削除（オーナーのみ）")
@app_commands.describe(user="削除する管理者（@メンション）")
async def removeadmin_command(interaction: discord.Interaction, user: discord.Member):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("このコマンドはオーナーのみが使用できます。", ephemeral=True)
        await log_to_owner("error", interaction.user, "/removeadmin", f"対象: {user.name}")
        return
    
    if user.id not in ADMIN_IDS:
        await interaction.response.send_message(f"⚠️ {user.name} は管理者ではありません", ephemeral=True)
        return
    
    view = RemoveAdminConfirmView(user)
    await interaction.response.send_message(
        f"本当に {user.name} を管理者から削除しますか？",
        view=view,
        ephemeral=True
    )


# ====== スラッシュコマンド /listadmin ======
@bot.tree.command(name="listadmin", description="管理者一覧を表示（オーナーのみ）")
async def listadmin_command(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("このコマンドはオーナーのみが使用できます。", ephemeral=True)
        await log_to_owner("error", interaction.user, "/listadmin", "管理者一覧の閲覧を試行")
        return
    
    admin_list = "なし"
    guild = interaction.guild
    if ADMIN_IDS and guild:
        admin_names = []
        for admin_id in ADMIN_IDS:
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


# ====== スラッシュコマンド /say ======
@bot.tree.command(name="say", description="ボットにメッセージを発言させる（オーナーのみ）")
@app_commands.describe(
    message="発言させるメッセージ",
    channel="発言するチャンネル（省略時は現在のチャンネル）"
)
async def say_command(interaction: discord.Interaction, message: str, channel: Optional[discord.TextChannel] = None):
    SAY_ALLOWED_USERS = [OWNER_ID, 1127253848155754557]
    if interaction.user.id not in SAY_ALLOWED_USERS:
        await interaction.response.send_message("このコマンドを使う権限はありません。", ephemeral=True)
        await log_to_owner("error", interaction.user, "/say", f"メッセージ: {message}")
        return
    
    target_channel = channel or interaction.channel
    if target_channel is None:
        await interaction.response.send_message("❌ チャンネルが見つかりません", ephemeral=True)
        return
    
    if not hasattr(target_channel, 'send'):
        await interaction.response.send_message("❌ このチャンネルにはメッセージを送信できません", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=True)
    try:
        await target_channel.send(message)  # type: ignore
        await interaction.followup.send(f"✅ メッセージを送信しました", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ メッセージの送信に失敗しました: {e}", ephemeral=True)


# ====== スラッシュコマンド /clear ======
@bot.tree.command(name="clear", description="メッセージを削除（オーナーのみ）")
@app_commands.describe(
    user="メッセージを削除するユーザー（省略時: 全メッセージ）",
    limit="検索するメッセージ数（デフォルト: 100）"
)
async def clear_command(interaction: discord.Interaction, user: Optional[discord.User] = None, limit: Optional[int] = 100):
    if not is_authorized(interaction.user.id):
        await interaction.response.send_message("このコマンドを使う権限はありません。", ephemeral=True)
        target_name = user.name if user else "全メッセージ"
        await log_to_owner("error", interaction.user, "/clear", f"対象: {target_name}")
        return
    
    if not interaction.channel or not hasattr(interaction.channel, 'purge'):
        await interaction.response.send_message("❌ このチャンネルではメッセージを削除できません", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=True)
    
    try:
        if user:
            def check(msg):
                return msg.author.id == user.id
            deleted = await interaction.channel.purge(limit=limit, check=check)  # type: ignore
            await interaction.followup.send(f"✅ {user.name} のメッセージを **{len(deleted)}件** 削除しました", ephemeral=True)
            await log_to_owner("action", interaction.user, "/clear", f"対象: {user.name}\n削除数: {len(deleted)}件")
        else:
            deleted = await interaction.channel.purge(limit=limit)  # type: ignore
            await interaction.followup.send(f"✅ チャンネルのメッセージを **{len(deleted)}件** 削除しました", ephemeral=True)
            await log_to_owner("action", interaction.user, "/clear", f"対象: 全メッセージ\n削除数: {len(deleted)}件")
    except discord.Forbidden:
        await interaction.followup.send("❌ メッセージを削除する権限がありません", ephemeral=True)
    except discord.HTTPException as e:
        if "14 days" in str(e) or "older than" in str(e):
            await interaction.followup.send("❌ 14日以上前のメッセージは一括削除できません", ephemeral=True)
        else:
            await interaction.followup.send(f"❌ 削除に失敗しました: {e}", ephemeral=True)


# ====== スラッシュコマンド /dm ======
@bot.tree.command(name="dm", description="特定のユーザーにDMを送信（オーナーのみ）")
@app_commands.describe(
    user="DMを送信するユーザー（@メンション）",
    message="送信するメッセージ"
)
async def dm_command(interaction: discord.Interaction, user: discord.User, message: str):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("このコマンドはオーナーのみが使用できます。", ephemeral=True)
        await log_to_owner("error", interaction.user, "/dm", f"対象: {user.name}")
        return
    
    await interaction.response.defer(ephemeral=True)
    try:
        await user.send(message)
        await interaction.followup.send(f"✅ {user.name} にDMを送信しました", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send(f"❌ {user.name} はDMを受け付けていません", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ DM送信に失敗しました: {e}", ephemeral=True)


# ====== スラッシュコマンド /ping ======
@bot.tree.command(name="ping", description="ボットの応答速度をテスト")
async def ping_command(interaction: discord.Interaction):
    latency = round(bot.latency * 1000)
    embed = discord.Embed(
        title="🏓 Pong!",
        description=f"レイテンシ: **{latency}ms**",
        color=discord.Color.green() if latency < 200 else discord.Color.orange()
    )
    await interaction.response.send_message(embed=embed)


# ====== スラッシュコマンド /restart ======
@bot.tree.command(name="restart", description="ボットを再起動（オーナーのみ）")
async def restart_command(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("このコマンドはオーナーのみが使用できます。", ephemeral=True)
        return
    
    await interaction.response.send_message("🔄 ボットを再起動します...", ephemeral=True)
    print(f"🔄 再起動要求 by {interaction.user}")
    
    await bot.close()
    sys.exit(0)


# ====== スラッシュコマンド /test ======
@bot.tree.command(name="test", description="ボットのシステムチェック（オーナーのみ）")
async def test_command(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("このコマンドはオーナーのみが使用できます。", ephemeral=True)
        return
    
    await interaction.response.defer()
    
    results = []
    
    # 1. レイテンシチェック
    latency = round(bot.latency * 1000)
    if latency < 200:
        results.append(f"✅ レイテンシ: {latency}ms")
    else:
        results.append(f"⚠️ レイテンシ: {latency}ms（高め）")
    
    # 2. 設定ファイル読み書きチェック
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            json.load(f)
        results.append("✅ 設定ファイル: 読み込み可能")
    except Exception as e:
        results.append(f"❌ 設定ファイル: {e}")
    
    # 3. VC監視機能の状態
    status = "ON" if vc_block_enabled else "OFF"
    results.append(f"✅ VC自動切断機能: {status}")
    
    # 4. 対象ユーザー数
    results.append(f"✅ 対象ユーザー数: {len(BLOCKED_USERS)}人")
    
    # 5. 対象VC数
    results.append(f"✅ 対象VC数: {len(TARGET_VC_IDS)}個")
    
    # 6. 管理者数
    results.append(f"✅ 管理者数: {len(ADMIN_IDS)}人")
    
    # 7. DM送信テスト
    try:
        owner = await bot.fetch_user(OWNER_ID)
        test_embed = discord.Embed(
            title="🔧 DMテスト",
            description="これはシステムチェックからのテストDMです",
            color=discord.Color.blue()
        )
        await owner.send(embed=test_embed)
        results.append("✅ DM送信: 成功")
    except Exception as e:
        results.append(f"❌ DM送信: {e}")
    
    # 8. サーバー権限チェック
    guild = interaction.guild
    if guild and guild.me:
        perms = guild.me.guild_permissions
        if perms.move_members:
            results.append("✅ VC切断権限: あり")
        else:
            results.append("❌ VC切断権限: なし（メンバーを移動の権限が必要）")
    
    embed = discord.Embed(
        title="🔧 システムチェック結果",
        description="\n".join(results),
        color=discord.Color.green()
    )
    embed.set_footer(text=f"チェック時刻: {datetime.now(JST).strftime('%Y年%m月%d日 %H:%M:%S')}")
    
    await interaction.followup.send(embed=embed)


# ====== スラッシュコマンド /simvc ======
@bot.tree.command(name="simvc", description="VC切断処理のシミュレーション（オーナーのみ）")
@app_commands.describe(user="テスト対象のユーザー（@メンション）")
async def simvc_command(interaction: discord.Interaction, user: discord.Member):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("このコマンドはオーナーのみが使用できます。", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=True)
    results = []
    
    # ユーザーがブロック対象かチェック
    if user.id in BLOCKED_USERS:
        results.append(f"✅ {user.name} はブロック対象です")
    else:
        results.append(f"❌ {user.name} はブロック対象ではありません")
    
    # VC監視機能の状態
    if vc_block_enabled:
        results.append("✅ VC自動切断機能: ON")
    else:
        results.append("⚠️ VC自動切断機能: OFF（切断されません）")
    
    # 対象VCの確認
    if TARGET_VC_IDS:
        vc_list = []
        guild = interaction.guild
        for vc_id in TARGET_VC_IDS:
            if guild:
                vc = guild.get_channel(vc_id)
                if vc:
                    vc_list.append(f"- {vc.name} ({vc_id})")
                else:
                    vc_list.append(f"- ID: {vc_id} (未確認)")
            else:
                vc_list.append(f"- ID: {vc_id}")
        results.append(f"✅ 対象VC:\n" + "\n".join(vc_list))
    else:
        results.append("❌ 対象VCが設定されていません")
    
    # シミュレーション結果
    if user.id in BLOCKED_USERS and vc_block_enabled and TARGET_VC_IDS:
        results.append("\n🔔 **結果**: このユーザーが対象VCに入室すると切断されます")
    else:
        results.append("\n⚠️ **結果**: このユーザーは切断されません")
    
    embed = discord.Embed(
        title="🎭 VC切断シミュレーション",
        description="\n".join(results),
        color=discord.Color.purple()
    )
    
    await interaction.followup.send(embed=embed, ephemeral=True)


# ====== スラッシュコマンド /autoping ======
@bot.tree.command(name="autoping", description="毎日0時の自動pingを設定（オーナーのみ）")
@app_commands.describe(
    action="設定するアクション",
    channel="pingを送信するチャンネル（設定時のみ必要）"
)
@app_commands.choices(action=[
    app_commands.Choice(name="on - チャンネルを指定して有効化", value="on"),
    app_commands.Choice(name="off - 自動pingを無効化", value="off"),
    app_commands.Choice(name="status - 現在の設定を確認", value="status")
])
async def autoping_command(interaction: discord.Interaction, action: str, channel: Optional[discord.TextChannel] = None):
    global AUTO_PING_CHANNEL_ID
    
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("このコマンドはオーナーのみが使用できます。", ephemeral=True)
        return
    
    if action == "on":
        if channel is None:
            await interaction.response.send_message("❌ チャンネルを指定してください", ephemeral=True)
            return
        AUTO_PING_CHANNEL_ID = channel.id
        save_config()
        await interaction.response.send_message(f"✅ 自動ping（毎日0時）を {channel.mention} に設定しました", ephemeral=True)
    
    elif action == "off":
        AUTO_PING_CHANNEL_ID = 0
        save_config()
        await interaction.response.send_message("✅ 自動pingを無効にしました", ephemeral=True)
    
    elif action == "status":
        if AUTO_PING_CHANNEL_ID == 0:
            await interaction.response.send_message("📋 自動ping: **無効**", ephemeral=True)
        else:
            ch = bot.get_channel(AUTO_PING_CHANNEL_ID)
            if ch:
                await interaction.response.send_message(f"📋 自動ping: **有効** - {ch.mention} (毎日0時)", ephemeral=True)
            else:
                await interaction.response.send_message(f"📋 自動ping: **有効** - ID: {AUTO_PING_CHANNEL_ID} (毎日0時)", ephemeral=True)


# ====== ヘルプページ用View ======
class HelpView(View):
    def __init__(self):
        super().__init__(timeout=180)
        self.current_page = 0
        self.pages = [
            self.get_public_page(),
            self.get_admin_page(),
            self.get_owner_page()
        ]
        self.update_buttons()
    
    def get_public_page(self) -> discord.Embed:
        embed = discord.Embed(
            title="📖 ヘルプ - 一般コマンド",
            description="誰でも使用できるコマンド",
            color=discord.Color.green()
        )
        embed.add_field(
            name="🏓 /ping",
            value="ボットの応答速度を確認",
            inline=False
        )
        embed.add_field(
            name="❓ /help",
            value="このヘルプを表示",
            inline=False
        )
        embed.set_footer(text="ページ 1/3 - 一般コマンド")
        return embed
    
    def get_admin_page(self) -> discord.Embed:
        embed = discord.Embed(
            title="📖 ヘルプ - 管理者コマンド",
            description="オーナーと管理者が使用できるコマンド",
            color=discord.Color.blue()
        )
        embed.add_field(
            name="🔧 /switch on/off",
            value="VC自動切断機能のON/OFF切り替え",
            inline=False
        )
        embed.add_field(
            name="👤 /blockuser add/remove @ユーザー",
            value="対象ユーザーの追加/削除",
            inline=False
        )
        embed.add_field(
            name="🎙️ /blockvc add/remove <VC_ID>",
            value="対象VCの追加/削除\n※VC IDは右クリック → 「ID をコピー」で取得",
            inline=False
        )
        embed.add_field(
            name="📋 /list",
            value="現在の設定一覧を表示",
            inline=False
        )
        embed.add_field(
            name="🗑️ /clear [@ユーザー] [limit:数]",
            value="チャンネルのメッセージを削除\n省略時は全メッセージ、ユーザー指定で特定の人のみ",
            inline=False
        )
        embed.set_footer(text="ページ 2/3 - 管理者コマンド")
        return embed
    
    def get_owner_page(self) -> discord.Embed:
        embed = discord.Embed(
            title="📖 ヘルプ - オーナー専用コマンド",
            description="オーナーのみが使用できるコマンド",
            color=discord.Color.orange()
        )
        embed.add_field(
            name="👨‍💼 /addadmin @ユーザー",
            value="管理者を追加",
            inline=False
        )
        embed.add_field(
            name="👨‍💼 /removeadmin @ユーザー",
            value="管理者を削除",
            inline=False
        )
        embed.add_field(
            name="👨‍💼 /listadmin",
            value="管理者一覧を表示",
            inline=False
        )
        embed.add_field(
            name="💬 /say message:メッセージ [channel:#チャンネル]",
            value="ボットにチャンネルでメッセージを発言させる",
            inline=False
        )
        embed.add_field(
            name="✉️ /dm @ユーザー message:メッセージ",
            value="特定のユーザーにDMを送信",
            inline=False
        )
        embed.add_field(
            name="🔧 /test",
            value="システムチェックを実行",
            inline=False
        )
        embed.add_field(
            name="🎭 /simvc @ユーザー",
            value="VC切断シミュレーション",
            inline=False
        )
        embed.add_field(
            name="⏰ /autoping on/off/status [channel:#チャンネル]",
            value="毎日0時（JST）の自動ping設定",
            inline=False
        )
        embed.add_field(
            name="📩 DM転送機能",
            value="ボットへのDMは自動でオーナーに転送されます",
            inline=False
        )
        embed.set_footer(text="ページ 3/3 - オーナー専用コマンド")
        return embed
    
    def update_buttons(self):
        self.prev_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page == len(self.pages) - 1
    
    @discord.ui.button(label="◀ 前へ", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: Button):
        self.current_page -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)
    
    @discord.ui.button(label="次へ ▶", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: Button):
        self.current_page += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)


# ====== スラッシュコマンド /help ======
@bot.tree.command(name="help", description="ボットの使い方を表示")
async def help_command(interaction: discord.Interaction):
    view = HelpView()
    await interaction.response.send_message(embed=view.pages[0], view=view, ephemeral=True)


# ====== VC監視処理 ======
@bot.event
async def on_voice_state_update(member, before, after):
    global vc_block_enabled, BLOCKED_USERS, TARGET_VC_IDS
    if not vc_block_enabled:
        return

    if before.channel is None and after.channel is not None:
        if after.channel.id in TARGET_VC_IDS:
            if member.id in BLOCKED_USERS:
                try:
                    await member.move_to(None)
                    log_message = f"{member.name} をVCから切断しました"
                    print(log_message)
                    
                    # オーナーにDM送信
                    try:
                        owner = await bot.fetch_user(OWNER_ID)
                        if owner is None:
                            print(f"❌ オーナー（ID: {OWNER_ID}）が見つかりません")
                        else:
                            # VC情報を安全に取得
                            if after.channel:
                                vc_name = after.channel.name
                                vc_id = after.channel.id
                            else:
                                vc_name = "不明"
                                vc_id = "不明"
                            
                            # 現在時刻を取得
                            current_time = datetime.now().strftime("%Y年%m月%d日 %H:%M:%S")
                            
                            embed = discord.Embed(
                                title="VC自動切断 - ログ",
                                description=f"対象ユーザーがVCに入室したため自動切断しました",
                                color=discord.Color.red()
                            )
                            embed.add_field(name="ユーザー", value=f"{member.name} ({member.id})", inline=False)
                            embed.add_field(name="VC", value=f"{vc_name} ({vc_id})", inline=False)
                            embed.add_field(name="時刻", value=current_time, inline=False)
                            await owner.send(embed=embed)
                            print(f"✅ オーナーにDMを送信しました [{current_time}]")
                    except Exception as e:
                        print(f"❌ DMの送信に失敗しました: {type(e).__name__}: {e}")
                except:
                    print("❌ 権限不足で切断できません")


# ====== エラーハンドラ ======
async def send_error_to_owner(error_type: str, error: Exception, context: str = ""):
    """エラーをオーナーにDMで通知"""
    try:
        owner = await bot.fetch_user(OWNER_ID)
        current_time = datetime.now(JST).strftime("%Y年%m月%d日 %H:%M:%S")
        
        embed = discord.Embed(
            title="🚨 ボットエラー通知",
            description="ボットでエラーが発生しました",
            color=discord.Color.dark_red()
        )
        embed.add_field(name="時刻", value=current_time, inline=False)
        embed.add_field(name="エラー種類", value=error_type, inline=False)
        embed.add_field(name="エラー内容", value=f"```{type(error).__name__}: {str(error)[:500]}```", inline=False)
        if context:
            embed.add_field(name="発生箇所", value=context, inline=False)
        
        await owner.send(embed=embed)
        print(f"✅ エラー通知をオーナーに送信しました [{current_time}]")
    except Exception as e:
        print(f"❌ エラー通知の送信に失敗: {e}")


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """スラッシュコマンドのエラーハンドラ"""
    import traceback
    command_name = interaction.command.name if interaction.command else "不明"
    
    # コンソールに詳細なエラーログを出力
    print(f"🔴 スラッシュコマンドエラー: /{command_name}")
    print(f"   実行者: {interaction.user.name} ({interaction.user.id})")
    print(f"   エラー: {type(error).__name__}: {error}")
    if hasattr(error, 'original'):
        print(f"   元のエラー: {type(error.original).__name__}: {error.original}")
        traceback.print_exception(type(error.original), error.original, error.original.__traceback__)
    
    await send_error_to_owner(
        "スラッシュコマンドエラー",
        error.original if hasattr(error, 'original') else error,
        f"コマンド: /{command_name}\n実行者: {interaction.user.name} ({interaction.user.id})"
    )
    
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message("エラーが発生しました。", ephemeral=True)
        else:
            await interaction.followup.send("エラーが発生しました。", ephemeral=True)
    except:
        pass


@bot.event
async def on_error(event: str, *args, **kwargs):
    """一般的なイベントエラーハンドラ"""
    import traceback
    error_msg = traceback.format_exc()
    
    try:
        owner = await bot.fetch_user(OWNER_ID)
        current_time = datetime.now(JST).strftime("%Y年%m月%d日 %H:%M:%S")
        
        embed = discord.Embed(
            title="🚨 ボットエラー通知",
            description="イベント処理中にエラーが発生しました",
            color=discord.Color.dark_red()
        )
        embed.add_field(name="時刻", value=current_time, inline=False)
        embed.add_field(name="イベント", value=event, inline=False)
        embed.add_field(name="エラー内容", value=f"```{error_msg[:800]}```", inline=False)
        
        await owner.send(embed=embed)
    except Exception as e:
        print(f"❌ エラー通知の送信に失敗: {e}")

# ====== Bot起動 ======
if __name__ == "__main__":

    # 環境変数名は Render の設定と合わせます（ここでは DISCORD_BOT_TOKEN）
    token = os.getenv("DISCORD_BOT_TOKEN")

    if not token:
        print("エラー: DISCORD_BOT_TOKEN環境変数が設定されていません")
        exit(1)

    bot.run(token) # ← 最後に ) を忘れずに
