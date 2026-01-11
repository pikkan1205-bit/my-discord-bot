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
from google.cloud import vision  # ← 追加
from google.oauth2 import service_account  # ← 追加
import io  # ← 追加
import json as json_lib  # ← 追加

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

# ====== Google Vision API設定 ======
vision_client = None
try:
    # 環境変数からJSONを読み込む方法
    credentials_json = os.environ.get("GOOGLE_VISION_CREDENTIALS_JSON")
    if credentials_json:
        credentials_dict = json_lib.loads(credentials_json)
        credentials = service_account.Credentials.from_service_account_info(credentials_dict)
        vision_client = vision.ImageAnnotatorClient(credentials=credentials)
        print("✅ Google Vision API初期化完了")
    # または、ファイルパスから読み込む方法
    elif os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        vision_client = vision.ImageAnnotatorClient()
        print("✅ Google Vision API初期化完了")
    else:
        print("⚠️ Google Vision API未設定（画像認識機能は無効）")
except Exception as e:
    print(f"❌ Google Vision API初期化失敗: {e}")

# ====== 環境変数から設定を読み込み ======
# オーナーID（必須）
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))
if OWNER_ID == 0:
    print("❌ エラー: OWNER_ID環境変数が設定されていません")
    exit(1)

# 初期管理者（オーナーのみ）
ADMIN_IDS = set()

# 初期対象ユーザー（カンマ区切りで複数指定可能）
blocked_str = os.environ.get("INITIAL_BLOCKED_USERS", "")
if blocked_str:
    try:
        BLOCKED_USERS = set(int(x.strip()) for x in blocked_str.split(",") if x.strip())
        print(f"📋 初期ブロックユーザー: {len(BLOCKED_USERS)}人")
    except ValueError as e:
        print(f"⚠️ INITIAL_BLOCKED_USERS の形式エラー: {e}")
        BLOCKED_USERS = set()
else:
    BLOCKED_USERS = set()

# 初期対象VC（カンマ区切りで複数指定可能）
vc_str = os.environ.get("INITIAL_TARGET_VCS", "")
if vc_str:
    try:
        TARGET_VC_IDS = set(int(x.strip()) for x in vc_str.split(",") if x.strip())
        print(f"📋 初期対象VC: {len(TARGET_VC_IDS)}個")
    except ValueError as e:
        print(f"⚠️ INITIAL_TARGET_VCS の形式エラー: {e}")
        TARGET_VC_IDS = set()
else:
    TARGET_VC_IDS = set()

# VCブロック機能の初期状態
vc_block_enabled = True

# 自動pingを送信するチャンネルID（0の場合は無効）
AUTO_PING_CHANNEL_ID = int(os.environ.get("AUTO_PING_CHANNEL_ID", "0"))

# データ永続化用ファイル
CONFIG_FILE = "vcblock_config.json"

# ====== ブロスタプロフィール認識用の設定 ======
# プレイヤー名を保存する辞書
player_names = {}  # {user_id: player_data}
player_register_count = {}  # {user_id: count} 登録回数

# 画像認識を有効にするチャンネルID
BRAWLSTARS_CHANNELS = {
    1379353245658648717,
    1445382523449376911
}

# データ永続化用ファイル
PLAYER_NAMES_FILE = "player_names.json"

# ====== プレイヤーリスト自動更新設定 ======
PLAYERLIST_CHANNEL_ID = 1459797964091428937  # 自動更新するチャンネルID
playerlist_message_id = None  # 現在のリストメッセージID
last_update_time = {}  # {user_id: timestamp} クールタイム管理用
UPDATE_COOLDOWN = 15  # 更新ボタンのクールタイム（秒）

# ===================================


# ====== 認可チェック関数 ======
def is_authorized(user_id: int) -> bool:
    """ユーザーがオーナーまたは管理者かチェック"""
    return user_id == OWNER_ID or user_id in ADMIN_IDS


# ====== 日本時間のタイムゾーン ======
JST = timezone(timedelta(hours=9))

# ====== 管理者モード状態管理 ======
# {user_id: last_activity_timestamp}
admin_mode_users = {}
ADMIN_MODE_TIMEOUT = 120  # 2分（秒）

def is_in_admin_mode(user_id: int) -> bool:
    """ユーザーが管理者モード中かチェック"""
    if user_id not in admin_mode_users:
        return False
    last_activity = admin_mode_users[user_id]
    if (datetime.now(JST) - last_activity).total_seconds() > ADMIN_MODE_TIMEOUT:
        del admin_mode_users[user_id]
        return False
    return True

def enter_admin_mode(user_id: int):
    """管理者モードに入る"""
    admin_mode_users[user_id] = datetime.now(JST)

def update_admin_mode(user_id: int):
    """管理者モードのタイムスタンプを更新"""
    admin_mode_users[user_id] = datetime.now(JST)

def exit_admin_mode(user_id: int):
    """管理者モードから抜ける"""
    if user_id in admin_mode_users:
        del admin_mode_users[user_id]

def normalize_text(text: str) -> str:
    """テキストを正規化（スペース除去、小文字化）"""
    text = text.replace(" ", "").replace("　", "")
    return text.lower()

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
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        print(f"💾 設定を保存しました")
    except Exception as e:
        print(f"❌ 設定保存エラー: {e}")

def load_config():
    """JSONファイルから設定を読み込む"""
    global BLOCKED_USERS, TARGET_VC_IDS, vc_block_enabled, ADMIN_IDS, AUTO_PING_CHANNEL_ID
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
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
    except json.JSONDecodeError as e:
        print(f"❌ 設定ファイルが破損しています: {e}")
        print(f"ℹ️ バックアップを作成して初期化します")
        if os.path.exists(CONFIG_FILE):
            import shutil
            shutil.copy(CONFIG_FILE, f"{CONFIG_FILE}.backup")
        save_config()
    except Exception as e:
        print(f"❌ 設定の読み込みに失敗しました: {e}")


# ====== 管理者モードタイムアウトチェック ======
@tasks.loop(seconds=30)
async def check_admin_mode_timeout():
    """管理者モードのタイムアウトをチェック"""
    now = datetime.now(JST)
    timed_out_users = []
    
    for user_id, last_activity in list(admin_mode_users.items()):
        if (now - last_activity).total_seconds() > ADMIN_MODE_TIMEOUT:
            timed_out_users.append(user_id)
            del admin_mode_users[user_id]
    
    # タイムアウトしたユーザーに通知
    for user_id in timed_out_users:
        try:
            user = await bot.fetch_user(user_id)
            await user.send("またいつでも呼んでね！")
            print(f"⏰ 管理者モードタイムアウト: {user.name}")
        except Exception as e:
            print(f"❌ タイムアウト通知失敗: {e}")


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
    load_player_names()
    await bot.tree.sync()
    
    # ステータスを設定
    activity = discord.Game(name="ブロスタ")
    await bot.change_presence(activity=activity)
    
    # 自動pingタスクを開始
    if not daily_ping.is_running():
        daily_ping.start()
    
    # 管理者モードタイムアウトチェックを開始
    if not check_admin_mode_timeout.is_running():
        check_admin_mode_timeout.start()
    
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

    
    # ステータスを設定
    activity = discord.Game(name="ブロスタ")
    await bot.change_presence(activity=activity)
    
    # 自動pingタスクを開始
    if not daily_ping.is_running():
        daily_ping.start()
    
    # 管理者モードタイムアウトチェックを開始
    if not check_admin_mode_timeout.is_running():
        check_admin_mode_timeout.start()
    
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

    content = message.content
    normalized = normalize_text(content)
    
    # ====== ブロスタプロフィール画像認識（指定チャンネルのみ） ======
    if message.channel.id in BRAWLSTARS_CHANNELS and message.attachments:
        for attachment in message.attachments:
            if attachment.content_type and attachment.content_type.startswith('image/'):
                async with message.channel.typing():
                    result = await extract_brawlstars_name(attachment.url)
                    
                    if result and result['name']:
                        player_name = result['name']
                        
                        # 名前がすでに登録されているかチェック
                        if player_name in player_names:
                            # 登録回数を増やす
                            player_register_count[player_name] = player_register_count.get(player_name, 0) + 1
                            count = player_register_count[player_name]
                            
                            # データの更新
                            player_names[player_name]['last_updated'] = datetime.now(JST).isoformat()
                            save_player_names()
                            
                            await message.channel.send(f"「{player_name}」は既に追加されてるよ！通算{count}回目だね")
                            print(f"🔄 報告カウントアップ: {player_name} ({count}回目)")
                        
                        else:
                            # 新規登録
                            player_names[player_name] = {
                                'name': player_name,
                                'registered_at': datetime.now(JST).isoformat(),
                                'last_updated': datetime.now(JST).isoformat()
                            }
                            player_register_count[player_name] = 1
                            save_player_names()
                            
                            await message.channel.send(f"お荷物プレイヤー「{player_name}」を新しく記録したよ！")
                            print(f"✅ 新規名前登録: {player_name}")
                    else:
                        print(f"⚠️ プロフィール認識失敗: {message.author.name}")
                break # 最初の1枚のみ処理
        return

    # --- 名前候補を表示するための関数 ---
async def name_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    # 登録されている名前の中から、入力中の文字が含まれるものを最大25件抽出
    choices = [
        app_commands.Choice(name=name, value=name)
        for name in player_names.keys() if current.lower() in name.lower()
    ]
    return choices[:25]

    # ... (これ以降に「フィーロちゃん」呼びかけや管理者モードのコードを続ける) ...

    
    # フィーロちゃん呼びかけ検出
    firo_keywords = ["フィーロちゃん", "ふぃーろちゃん", "フィーロ", "ふぃーろ"]
    firo_called = any(normalize_text(k) in normalized for k in firo_keywords)
    
    if firo_called:
        if message.author.id == OWNER_ID:
            enter_admin_mode(message.author.id)
            await message.reply("ご主人様！どうしたの？")
            return
        else:
            await message.reply("フィーロは、フィーロ！")
            return
    
    # 🆕 管理者モード終了チェック（最優先）
    if message.author.id == OWNER_ID and is_in_admin_mode(message.author.id):
        exit_keywords = ["終了", "おわり", "終わり", "exit", "quit", "bye", "バイバイ", "またね", "さようなら", "帰って", "もういい", "閉じて"]
        if any(normalize_text(k) in normalized for k in exit_keywords):
            exit_admin_mode(message.author.id)
            await message.reply("了解！またいつでも呼んでね！")
            print(f"✅ 管理者モード終了（直接チェック）: {message.author.name}")
            return
    
    # 管理者モード中の処理
    if message.author.id == OWNER_ID and is_in_admin_mode(message.author.id):
        handled = await handle_admin_mode_command(message)
        if handled:
            # まだ管理者モードにいる場合のみタイムスタンプ更新
            if is_in_admin_mode(message.author.id):
                update_admin_mode(message.author.id)
            return
        else:
            # キーワードに当てはまらない場合
            await message.reply("ごめんね！もう一回いい？")
            update_admin_mode(message.author.id)
            return
    
    # DM転送処理
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
    
    # 「〇〇と検索して」パターンに反応（管理者モード外でも動作）
    if "と検索して" in message.content:
        await handle_search_request(message)

    
    # 「チャットを削除して」コマンド（管理者モード外でも動作、オーナーのみ）
    if message.author.id == OWNER_ID:
        if ("チャット" in normalized or "メッセージ" in normalized) and "削除" in normalized:
            if "監視" not in normalized:  # 監視対象削除と区別
                match = re.search(r"(\d+)件", content)
                limit = int(match.group(1)) if match else 100
                
                if isinstance(message.channel, discord.TextChannel):
                    await message.channel.purge(limit=limit + 1)
                    await message.channel.send("お掃除完了！綺麗になったね！", delete_after=5)
                    return


def normalize_synonyms(text: str) -> str:
    """類義語を統一形に正規化"""
    synonyms = {
        # autoping関連
        "オートピング": "autoping", "おーとぴんぐ": "autoping", "自動ピング": "autoping",
        "自動ping": "autoping", "オートping": "autoping", "自動通知": "autoping",
        # DM関連
        "ダイレクトメッセージ": "dm", "ディーエム": "dm", "プライベートメッセージ": "dm",
        # 出禁関連
        "ブロック": "出禁", "ban": "出禁", "バン": "出禁", "追放": "出禁", "キック": "出禁",
        "締め出し": "出禁", "入室禁止": "出禁", "参加禁止": "出禁",
        # 管理者関連
        "admin": "管理者", "アドミン": "管理者", "モデレーター": "管理者", "mod": "管理者",
        # 追加関連
        "入れて": "追加", "登録": "追加", "加えて": "追加", "つけて": "追加", "付けて": "追加",
        "いれて": "追加", "加入": "追加", "参加": "追加",
        # 削除関連
        "外して": "削除", "消して": "削除", "除外": "削除", "取り消し": "削除", "はずして": "削除",
        "抜いて": "削除", "除いて": "削除", "取って": "削除", "とって": "削除",
        # 解除関連
        "外す": "解除", "やめて": "解除", "取り消して": "解除", "取消": "解除", "キャンセル": "解除",
        # オン関連
        "有効": "オン", "つけて": "オン", "入れて": "オン", "開始": "オン", "スタート": "オン",
        "起動": "オン", "enable": "オン", "on": "オン",
        # オフ関連
        "無効": "オフ", "止めて": "オフ", "停止": "オフ", "ストップ": "オフ", "終了": "オフ",
        "disable": "オフ", "off": "オフ",
        # 設定関連
        "セット": "設定", "変更": "設定", "指定": "設定", "切り替え": "設定",
        # チャット関連
        "メッセージ": "チャット", "発言": "チャット", "ログ": "チャット", "履歴": "チャット",
        "会話": "チャット", "投稿": "チャット",
        # 監視関連
        "ウォッチ": "監視", "watch": "監視", "対象": "監視", "見張り": "監視", "チェック対象": "監視",
        # VC関連
        "ボイスチャンネル": "vc", "ボイチャ": "vc", "通話": "vc", "ボイス": "vc", "音声チャンネル": "vc",
    }
    result = text.lower()
    for old, new in synonyms.items():
        result = result.replace(old.lower(), new.lower())
    return result


def has_any(text: str, keywords: list) -> bool:
    """キーワードのいずれかが含まれるか"""
    return any(k in text for k in keywords)


async def handle_admin_mode_command(message: discord.Message) -> bool:
    """管理者モードのコマンドを処理。処理した場合True、しなかった場合Falseを返す"""
    global vc_block_enabled, BLOCKED_USERS, TARGET_VC_IDS, ADMIN_IDS, AUTO_PING_CHANNEL_ID
    
    content = message.content
    # メンションとチャンネル参照を除去してからパターンマッチング
    content_no_mentions = re.sub(r"<@!?\d+>", "", content)
    content_no_mentions = re.sub(r"<#\d+>", "", content_no_mentions)
    normalized = normalize_text(content_no_mentions)
    # 類義語を統一
    unified = normalize_synonyms(normalized)
    
    # 追加系キーワード
    ADD_KEYWORDS = ["追加", "入れて", "登録", "加えて", "つけて", "付けて", "いれて", "加入", "参加", "にして", "として"]
    # 削除・解除系キーワード
    REMOVE_KEYWORDS = ["削除", "解除", "外して", "消して", "除外", "取り消し", "はずして", "抜いて", "除いて", "取って", "とって", "やめて", "外す", "キャンセル", "なくして"]
    # オン系キーワード
    ON_KEYWORDS = ["オン", "有効", "つけて", "入れて", "開始", "スタート", "起動", "enable", "on", "始めて"]
    # オフ系キーワード
    OFF_KEYWORDS = ["オフ", "無効", "止めて", "停止", "ストップ", "終了", "disable", "off", "やめて", "切って"]
    
    try:
        # ===== 管理者モード終了 ===== 
        exit_keywords = ["終了", "おわり", "終わり", "exit", "quit", "bye", "バイバイ", "またね", "さようなら", "帰って", "もういい", "閉じて"]
        
        # デバッグ出力を追加
        print(f"🔍 デバッグ: unified = {unified}")
        print(f"🔍 exit判定: {has_any(unified, exit_keywords)}")
        print(f"🔍 除外判定: {has_any(unified, ['vc', 'ボイス', '監視', '機能', 'システム', 'autoping', 'ブロック'])}")
        
        if has_any(unified, exit_keywords):
            # "終了"が他のコマンド（例：VCブロック終了）と混同されないようチェック
            if not has_any(unified, ["vc", "ボイス", "監視", "機能", "システム", "autoping", "ブロック"]):
                exit_admin_mode(message.author.id)
                await message.reply("了解！またいつでも呼んでね！")
                print(f"✅ 管理者モード終了: {message.author.name}")
                return True
            else:
                print(f"⚠️ 終了コマンドだが除外ワードが含まれているためスキップ")

                
        # ===== 管理者追加 =====
        admin_add_keywords = ["管理者", "admin", "アドミン", "モデレーター", "mod", "権限"]
        if has_any(unified, admin_add_keywords) and has_any(unified, ADD_KEYWORDS):
            if not has_any(unified, REMOVE_KEYWORDS):
                if message.mentions:
                    user = message.mentions[0]
                    ADMIN_IDS.add(user.id)
                    save_config()
                    await message.reply(f"{user.mention} を管理者に追加したよ！")
                    return True
        
        # ===== 管理者削除 =====
        if has_any(unified, admin_add_keywords) and has_any(unified, REMOVE_KEYWORDS):
            if message.mentions:
                user = message.mentions[0]
                ADMIN_IDS.discard(user.id)
                save_config()
                await message.reply(f"{user.mention} を管理者から削除したよ！")
                return True
        
        # ===== autoping設定 =====
        autoping_keywords = ["autoping", "オートピング", "おーとぴんぐ", "自動ピング", "自動ping", "オートping", "自動通知", "ping通知"]
        if has_any(unified, autoping_keywords):
            # オフにする
            if has_any(unified, OFF_KEYWORDS):
                AUTO_PING_CHANNEL_ID = 0
                save_config()
                await message.reply("オートピングを無効化したよ！")
                return True
            # オンにする（チャンネル指定）
            if has_any(unified, ON_KEYWORDS + ["設定", "セット", "変更", "指定"]):
                if message.channel_mentions:
                    channel = message.channel_mentions[0]
                    AUTO_PING_CHANNEL_ID = channel.id
                    save_config()
                    await message.reply("オートピングを設定したよ！")
                    return True
        
        # ===== VC出禁追加 =====
        block_keywords = ["出禁", "ブロック", "ban", "バン", "追放", "キック", "締め出し", "入室禁止", "参加禁止", "vcブロック", "vcban"]
        if has_any(unified, block_keywords) and not has_any(unified, REMOVE_KEYWORDS):
            if message.mentions:
                user = message.mentions[0]
                BLOCKED_USERS.add(user.id)
                save_config()
                await message.reply(f"{user.mention} を出禁にしたよ！")
                return True
        
        # ===== VC出禁解除 =====
        if has_any(unified, block_keywords) and has_any(unified, REMOVE_KEYWORDS):
            if message.mentions:
                user = message.mentions[0]
                BLOCKED_USERS.discard(user.id)
                save_config()
                await message.reply(f"{user.mention} を出禁から解除したよ！")
                return True
        
        # ===== 監視対象追加 =====
        watch_keywords = ["監視", "ウォッチ", "watch", "対象", "見張り", "チェック対象", "vc対象", "チャンネル対象"]
        if has_any(unified, watch_keywords) and has_any(unified, ADD_KEYWORDS):
            if not has_any(unified, REMOVE_KEYWORDS):
                match = re.search(r"(\d{17,20})", content)
                if match:
                    vc_id = int(match.group(1))
                    TARGET_VC_IDS.add(vc_id)
                    save_config()
                    await message.reply(f"チャンネルID {vc_id} を監視対象に追加したよ！")
                    return True
        
        # ===== 監視対象削除 =====
        if has_any(unified, watch_keywords) and has_any(unified, REMOVE_KEYWORDS):
            match = re.search(r"(\d{17,20})", content)
            if match:
                vc_id = int(match.group(1))
                TARGET_VC_IDS.discard(vc_id)
                save_config()
                await message.reply(f"チャンネルID {vc_id} を監視対象から削除したよ！")
                return True
        
        # ===== チャット削除 =====
        chat_keywords = ["チャット", "メッセージ", "発言", "ログ", "履歴", "会話", "投稿", "掃除", "クリア", "clear"]
        delete_keywords = ["削除", "消して", "掃除", "クリア", "clear", "消去", "片付け", "きれいに", "綺麗に"]
        if has_any(unified, chat_keywords) and has_any(unified, delete_keywords):
            if not has_any(unified, watch_keywords):  # 監視対象削除と区別
                match = re.search(r"(\d+)件", content)
                limit = int(match.group(1)) if match else 100
                
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
                
                if BLOCKED_USERS:
                    user_list = []
                    for uid in BLOCKED_USERS:
                        try:
                            user = await bot.fetch_user(uid)
                            user_list.append(f"• {user.name} ({uid})")
                        except:
                            user_list.append(f"• 不明なユーザー ({uid})")
                    embed1 = discord.Embed(title="🚫 対象ユーザーリスト", description="\n".join(user_list), color=discord.Color.red())
                else:
                    embed1 = discord.Embed(title="🚫 対象ユーザーリスト", description="登録なし", color=discord.Color.red())
                await message.channel.send(embed=embed1)
                
                if ADMIN_IDS:
                    admin_list = []
                    for uid in ADMIN_IDS:
                        try:
                            user = await bot.fetch_user(uid)
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
            latency = round(bot.latency * 1000)
            embed = discord.Embed(title="🏓 Pong!", description=f"レイテンシ: **{latency}ms**", color=discord.Color.green())
            await message.channel.send(embed=embed)
            return True
        
        # ===== 再起動 =====
        restart_keywords = ["再起動", "リスタート", "restart", "reboot", "リブート", "再開", "起動し直し", "立ち上げ直し", "もう一回起動", "再立ち上げ"]
        if has_any(unified, restart_keywords):
            await message.reply("再起動するね！")
            import asyncio
            await asyncio.sleep(3)
            await bot.close()
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
                vc_block_enabled = True
                save_config()
                await message.reply("監視機能をオンにしたよ！")
                return True
            if has_any(unified, OFF_KEYWORDS):
                vc_block_enabled = False
                save_config()
                await message.reply("監視機能をオフにしたよ！")
                return True
        
        # ===== システムチェック =====
        check_keywords = ["システムチェック", "systemcheck", "テスト", "test", "診断", "ヘルスチェック", "healthcheck", "動作確認", "状態確認", "チェック"]
        if has_any(unified, check_keywords) and has_any(unified, ["システム", "ボット", "bot", "動作", "状態", "実行", "確認"]):
            await message.reply("システムをチェックするね！")
            
            results = []
            all_ok = True
            
            latency = round(bot.latency * 1000)
            if latency < 200:
                results.append(f"✅ レイテンシ: {latency}ms")
            else:
                results.append(f"⚠️ レイテンシ: {latency}ms（高め）")
                all_ok = False
            
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    json.load(f)
                results.append("✅ 設定ファイル: 読み込み可能")
            except:
                results.append("❌ 設定ファイル: エラー")
                all_ok = False
            
            results.append(f"✅ VC自動切断機能: {'ON' if vc_block_enabled else 'OFF'}")
            results.append(f"✅ 対象ユーザー数: {len(BLOCKED_USERS)}人")
            results.append(f"✅ 対象VC数: {len(TARGET_VC_IDS)}個")
            results.append(f"✅ 管理者数: {len(ADMIN_IDS)}人")
            
            embed = discord.Embed(title="🔧 システムチェック結果", description="\n".join(results), color=discord.Color.green())
            await message.channel.send(embed=embed)
            
            if all_ok:
                await message.channel.send("問題なし！全てのシステムは正常に作動しているよ！")
            return True
        
        # ===== フォールバック処理 =====
        # どのコマンドにも当てはまらなかった場合、最後の手段として推測
        
        # 「削除」が含まれていて、メンションがあれば出禁解除を推測
        if "削除" in unified and message.mentions and not has_any(unified, watch_keywords):
            user = message.mentions[0]
            BLOCKED_USERS.discard(user.id)
            ADMIN_IDS.discard(user.id)
            save_config()
            await message.reply(f"{user.mention} を削除したよ！（出禁リストと管理者リストから）")
            return True
        
        # 「削除」が含まれていて、数字があれば監視対象から削除を推測
        if "削除" in unified:
            match = re.search(r"(\d{17,20})", content)
            if match:
                vc_id = int(match.group(1))
                TARGET_VC_IDS.discard(vc_id)
                save_config()
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
            ADMIN_IDS.add(user.id)
            save_config()
            await message.reply(f"{user.mention} を管理者に追加したよ！")
            return True
        
        return False
        
    except Exception as e:
        # エラーをオーナーに報告
        try:
            owner = await bot.fetch_user(OWNER_ID)
            await owner.send(f"❌ 管理者モードエラー: {e}\nコマンド: {content}")
        except:
            pass
        await message.reply(f"エラーが発生したよ: {e}")
        return True


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
@bot.tree.command(name="say", description="ボットにメッセージを発言させる（管理者のみ）")
@app_commands.describe(
    message="発言させるメッセージ",
    channel="発言するチャンネル（省略時は現在のチャンネル）"
)
async def say_command(interaction: discord.Interaction, message: str, channel: Optional[discord.TextChannel] = None):
    if not is_authorized(interaction.user.id):  # ← ここを修正
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

# ====== スラッシュコマンド /exit ====== ← ここに追加
@bot.tree.command(name="exit", description="管理者モードを終了（オーナーのみ）")
async def exit_command(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("このコマンドはオーナーのみが使用できます。", ephemeral=True)
        return
    
    if is_in_admin_mode(interaction.user.id):
        exit_admin_mode(interaction.user.id)
        await interaction.response.send_message("✅ 管理者モードを終了しました", ephemeral=True)
    else:
        await interaction.response.send_message("ℹ️ 管理者モードは起動していません", ephemeral=True)

# ====== スラッシュコマンド /playerlist ======
@bot.tree.command(name="playerlist", description="登録されているお荷物プレイヤー一覧を表示")
async def playerlist_command(interaction: discord.Interaction):
    if not player_names:
        await interaction.response.send_message("📋 登録されているプレイヤーはいません", ephemeral=False)
        return
    
    embed = discord.Embed(
        title="🎮 お荷物プレイヤーリスト",
        description="報告回数が多い順に表示しています",
        color=discord.Color.red()
    )
    
    # 報告回数（player_register_count）でソート
    sorted_players = sorted(
        player_names.keys(),
        key=lambda name: player_register_count.get(name, 0),
        reverse=True
    )
    
    player_list = []
    for name in sorted_players:
        count = player_register_count.get(name, 1)
        player_list.append(f"• **{name}** — `{count}回報告`")
    
    # Discordのエンドベッド制限（4096文字）対策
    description_text = "\n".join(player_list)
    if len(description_text) > 4000:
        description_text = description_text[:3997] + "..."
        
    embed.description = description_text
    embed.set_footer(text=f"合計登録人数: {len(player_names)}人")
    
    await interaction.response.send_message(embed=embed, ephemeral=False)



# ====== スラッシュコマンド /myprofile ======
@bot.tree.command(name="myprofile", description="自分のブロスタプロフィールを確認")
async def myprofile_command(interaction: discord.Interaction):
    user_id_str = str(interaction.user.id)
    
    if user_id_str in player_names:
        player_data = player_names[user_id_str]
        count = player_register_count.get(user_id_str, 1)
        
        # 古いデータ形式への対応
        if isinstance(player_data, str):
            bs_name = player_data
            embed = discord.Embed(
                title="🎮 あなたのブロスタプロフィール",
                color=discord.Color.blue()
            )
            embed.add_field(name="名前", value=f"**{bs_name}**", inline=False)
        else:
            bs_name = player_data.get('name', 'Unknown')
            player_id = player_data.get('player_id')
            trophies = player_data.get('trophies')
            
            embed = discord.Embed(
                title="🎮 あなたのブロスタプロフィール",
                color=discord.Color.blue()
            )
            embed.add_field(name="名前", value=f"**{bs_name}**", inline=False)
            
            if player_id:
                embed.add_field(name="プレイヤーID", value=f"`{player_id}`", inline=True)
            
            if trophies:
                embed.add_field(name="トロフィー", value=f"🏆 {trophies:,}", inline=True)
            
            embed.add_field(name="登録回数", value=f"{count}回", inline=True)
            
            if player_data.get('registered_at'):
                from datetime import datetime as dt
                registered = dt.fromisoformat(player_data['registered_at'])
                embed.set_footer(text=f"初回登録: {registered.strftime('%Y/%m/%d')}")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        channel_ids = list(BRAWLSTARS_CHANNELS)
        channels_str = " または ".join([f"<#{ch_id}>" for ch_id in channel_ids[:2]])
        await interaction.response.send_message(
            f"❌ ブロスタプロフィールが登録されていません\n"
            f"{channels_str}でプロフィール画像を送信してください！",
            ephemeral=True
        )


# ====== スラッシュコマンド /scanhistory ======
@bot.tree.command(name="scanhistory", description="過去の画像を遡って一括登録（オーナーのみ）")
@app_commands.describe(
    channel="スキャンするチャンネル（省略時は現在のチャンネル）",
    limit="遡るメッセージ数（デフォルト: 100、最大2000）"
)
async def scanhistory_command(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None, limit: int = 100):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("このコマンドはオーナーのみが使用できます。", ephemeral=True)
        return
    
    target_channel = channel or interaction.channel
    
    if target_channel.id not in BRAWLSTARS_CHANNELS:
        channel_ids = list(BRAWLSTARS_CHANNELS)
        channels_str = ", ".join([f"<#{ch_id}>" for ch_id in channel_ids])
        await interaction.response.send_message(
            f"❌ 指定されたブロスタチャンネルでのみ使用できます。\n有効なチャンネル: {channels_str}",
            ephemeral=True
        )
        return
    
    if limit > 2000:
        limit = 2000
    
    await interaction.response.defer(ephemeral=True)
    
    try:
        start_time = datetime.now(JST)
        
        # メッセージ履歴を取得
        messages_with_images = []
        async for msg in target_channel.history(limit=limit):
            if msg.author.bot:
                continue
            if msg.attachments:
                for attachment in msg.attachments:
                    if attachment.content_type and attachment.content_type.startswith('image/'):
                        messages_with_images.append((msg, attachment))
                        break
        
        if not messages_with_images:
            await interaction.followup.send("📋 画像が見つかりませんでした。")
            return
        
        await interaction.followup.send(f"🔍 {len(messages_with_images)}件の画像を検出しました。処理を開始します...")
        
        success_count = 0  # 新しいプレイヤー名の数
        updated_count = 0  # 既にあった名前の追加報告数
        failed_count = 0   # 認識失敗
        
        for msg, attachment in messages_with_images:
            result = await extract_brawlstars_name(attachment.url)
            
            if result and result['name']:
                player_name = result['name']
                
                # 【重要】プレイヤー名がすでに登録されているかチェック
                if player_name in player_names:
                    # 既に登録されている名前なら回数を増やす
                    player_register_count[player_name] = player_register_count.get(player_name, 1) + 1
                    updated_count += 1
                    # 最終更新日だけ更新
                    player_names[player_name]['last_updated'] = msg.created_at.isoformat()
                    print(f"🔄 重複報告: {player_name} (通算 {player_register_count[player_name]}回)")
                else:
                    # まったく新しい名前なら新規登録
                    player_data = {
                        'name': player_name,
                        'registered_at': msg.created_at.isoformat(),
                        'last_updated': msg.created_at.isoformat()
                    }
                    player_names[player_name] = player_data
                    player_register_count[player_name] = 1
                    success_count += 1
                    print(f"✅ 新規名前登録: {player_name}")
            else:
                failed_count += 1
        
        save_player_names()
        
        end_time = datetime.now(JST)
        elapsed = int((end_time - start_time).total_seconds())
        
        result_embed = discord.Embed(
            title="📊 過去データ一括登録完了",
            color=discord.Color.green()
        )
        result_embed.add_field(name="👤 新規プレイヤー", value=f"{success_count}人", inline=True)
        result_embed.add_field(name="🔄 追加報告(回数UP)", value=f"{updated_count}件", inline=True)
        result_embed.add_field(name="❌ 認識失敗", value=f"{failed_count}枚", inline=True)
        result_embed.set_footer(text=f"合計処理画像: {len(messages_with_images)}枚 | 処理時間: {elapsed}秒")
        
        await interaction.followup.send(embed=result_embed)
        
    except Exception as e:
        await interaction.followup.send(f"❌ エラーが発生しました: {e}")
        print(f"❌ 一括登録エラー: {e}")

# ====== スラッシュコマンド /player_edit (名前の修正) ======
@bot.tree.command(name="player_edit", description="登録されたプレイヤー名を修正します（オーナーのみ）")
@app_commands.describe(old_name="修正したい現在の名前（候補から選択可）", new_name="正しい名前")
@app_commands.autocomplete(old_name=name_autocomplete) # 候補を出す設定
async def player_edit_command(interaction: discord.Interaction, old_name: str, new_name: str):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("オーナーのみ使用可能です。", ephemeral=True)
        return

    if old_name not in player_names:
        await interaction.response.send_message(f"❌ 「{old_name}」は見つかりませんでした。", ephemeral=True)
        return

    player_names[new_name] = player_names.pop(old_name)
    player_names[new_name]['name'] = new_name
    if old_name in player_register_count:
        player_register_count[new_name] = player_register_count.pop(old_name)

    save_player_names()
    await interaction.response.send_message(f"✅ 修正完了：`{old_name}` → `{new_name}`")

# ====== スラッシュコマンド /player_delete (データの削除) ======
@bot.tree.command(name="player_delete", description="指定したプレイヤーのデータを削除します（オーナーのみ）")
@app_commands.describe(name="削除したいプレイヤー名（候補から選択可）")
@app_commands.autocomplete(name=name_autocomplete) # 候補を出す設定
async def player_delete_command(interaction: discord.Interaction, name: str):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("オーナーのみ使用可能です。", ephemeral=True)
        return

    if name not in player_names:
        await interaction.response.send_message(f"❌ 「{name}」は見つかりませんでした。", ephemeral=True)
        return

    del player_names[name]
    if name in player_register_count:
        del player_register_count[name]

    save_player_names()
    await interaction.response.send_message(f"🗑️ 「{name}」のデータを削除しました。")


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

# ====== 画像認識機能 ======
async def extract_text_from_image(image_url: str) -> Optional[str]:
    """画像から文字を抽出"""
    if not vision_client:
        return None
    
    try:
        # 画像をダウンロード
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(image_url) as response:
                if response.status != 200:
                    return None
                image_data = await response.read()
        
        # Vision APIで文字認識
        image = vision.Image(content=image_data)
        response = vision_client.text_detection(image=image)
        texts = response.text_annotations
        
        if texts:
            # 最初の要素が全体のテキスト
            return texts[0].description
        return None
        
    except Exception as e:
        print(f"❌ 画像認識エラー: {e}")
        return None


async def extract_brawlstars_name(image_url: str) -> Optional[dict]:
    """ブロスタのプロフィール画像から名前とIDを抽出"""
    text = await extract_text_from_image(image_url)
    
    if not text:
        return None
    
    # 【追加】「報告」という文字が含まれていたらリザルト画面と判断して終了
    if "報告" in text:
        print("⚠️ リザルト画面（報告ボタンあり）を検出したためスキップします。")
        return None
    
    lines = [line.strip() for line in text.strip().split('\n') if line.strip()]
    
    result = {
        'name': None,
        'player_id': None,
        'trophies': None
    }
    # ...（この後に続く名前抽出ロジック）

    
    # デバッグ用：認識された全テキストを出力
    print(f"🔍 認識テキスト:\n{text}\n")
    
    # パターン1: 「プロフィール」の次の行が名前
    for i, line in enumerate(lines):
        if 'プロフィール' in line or 'PROFILE' in line.upper():
            # 次の行をチェック（アイコン行をスキップ）
            for j in range(i+1, min(i+4, len(lines))):
                next_line = lines[j].strip()
                # 名前の可能性が高い行の条件
                if (len(next_line) >= 2 and 
                    'キャラクター' not in next_line and
                    'CHARACTER' not in next_line.upper() and
                    not next_line.startswith('#') and
                    not next_line.replace(',', '').isdigit()):
                    result['name'] = next_line
                    print(f"✅ 名前検出（パターン1）: {next_line}")
                    break
            break
    
    # パターン2: プレイヤーIDの前の行が名前
    if not result['name']:
        for i, line in enumerate(lines):
            # プレイヤーID（#から始まる）を探す
            if line.startswith('#') and len(line) > 5:
                result['player_id'] = line
                # 前の行が名前
                if i > 0:
                    prev_line = lines[i-1].strip()
                    if len(prev_line) >= 2:
                        result['name'] = prev_line
                        print(f"✅ 名前検出（パターン2）: {prev_line}")
                break
    
    # パターン3: DreamerAikosuのようなIDの前が名前
    if not result['name']:
        for i, line in enumerate(lines):
            # 英数字のみのID（プレイヤー名の下に表示される）
            if (line.replace('_', '').replace('-', '').isalnum() and 
                len(line) >= 5 and 
                any(c.isalpha() for c in line)):
                result['player_id'] = line
                # 前の行が名前
                if i > 0:
                    prev_line = lines[i-1].strip()
                    # 名前の可能性が高い（絵文字や多言語文字を含む）
                    if len(prev_line) >= 2 and prev_line != 'プロフィール':
                        result['name'] = prev_line
                        print(f"✅ 名前検出（パターン3）: {prev_line}")
                break
    
    # トロフィー数も抽出（オプション）
    for i, line in enumerate(lines):
        if 'トロフィー' in line or 'TROPHIES' in line.upper():
            # 次の行が数字
            if i+1 < len(lines):
                trophy_line = lines[i+1].replace(',', '').strip()
                if trophy_line.isdigit():
                    result['trophies'] = int(trophy_line)
                    print(f"🏆 トロフィー: {result['trophies']}")
            break
    
    return result if result['name'] else None


def save_player_names():
    """プレイヤー名をJSONに保存"""
    global player_names, player_register_count
    try:
        data = {
            'players': player_names,
            'counts': player_register_count
        }
        with open(PLAYER_NAMES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"💾 プレイヤー名を保存しました")
    except Exception as e:
        print(f"❌ プレイヤー名保存エラー: {e}")


def load_player_names():
    """プレイヤー名をJSONから読み込み"""
    global player_names, player_register_count
    try:
        if os.path.exists(PLAYER_NAMES_FILE):
            with open(PLAYER_NAMES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            # 新形式（players, counts）
            if isinstance(data, dict) and 'players' in data:
                player_names = data.get('players', {})
                player_register_count = data.get('counts', {})
            # 旧形式（互換性のため）
            else:
                player_names = data
                player_register_count = {}
            
            print(f"📂 プレイヤー名を読み込みました: {len(player_names)}人")
        else:
            player_names = {}
            player_register_count = {}
    except Exception as e:
        print(f"❌ プレイヤー名読み込みエラー: {e}")
        player_names = {}
        player_register_count = {}


# ====== VCブロック処理 ======
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
                    
                    # オーナーに通知
                    try:
                        owner = await bot.fetch_user(OWNER_ID)
                        if owner:
                            vc_name = after.channel.name if after.channel else "不明"
                            vc_id = after.channel.id if after.channel else "不明"
                            current_time = datetime.now(JST).strftime("%Y年%m月%d日 %H:%M:%S")
                            
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
                    except discord.Forbidden:
                        print(f"⚠️ オーナー({OWNER_ID})にDMを送信できません（DM拒否設定）")
                    except discord.NotFound:
                        print(f"❌ オーナー({OWNER_ID})が見つかりません")
                    except Exception as e:
                        print(f"❌ DM送信エラー: {type(e).__name__}: {e}")
                        
                except discord.Forbidden:
                    print(f"❌ 権限不足: {member.name} を切断できません（Move Members権限が必要）")
                    # オーナーに権限エラーを通知
                    try:
                        owner = await bot.fetch_user(OWNER_ID)
                        if owner:
                            await owner.send(
                                f"⚠️ **権限エラー**\n"
                                f"{member.name} を切断しようとしましたが、権限が不足しています。\n"
                                f"ボットに「メンバーを移動」権限を付与してください。"
                            )
                    except:
                        pass
                except discord.HTTPException as e:
                    print(f"❌ Discord APIエラー: {e}")
                except Exception as e:
                    print(f"❌ 予期しないエラー: {type(e).__name__}: {e}")
                    await send_error_to_owner("VC切断エラー", e, f"ユーザー: {member.name}")


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
