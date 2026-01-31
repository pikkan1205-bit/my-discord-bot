import discord
from discord.ext import commands
import os

from utils.config import ConfigManager
from utils.discord_helpers import send_error_to_owner

import aiohttp

# ====== Bot Class Definition ======
class MyBot(commands.Bot):
    def __init__(self):
        # インテントの設定
        intents = discord.Intents.default()
        intents.members = True
        intents.voice_states = True
        intents.message_content = True
        
        super().__init__(command_prefix="!", intents=intents)
        
        # 共有設定をアタッチ
        self.config = ConfigManager()
        self.session = None # setup_hook内で割り当て

    async def setup_hook(self):
        # 共有のaiohttpセッションを作成
        self.session = aiohttp.ClientSession()
        
        # 起動時のバリデーション
        # 起動時のバリデーション
        if self.config.OWNER_ID == 0:
            print("❌ エラー: OWNER_ID環境変数が設定されていません")
            # そのまま続行するが警告を表示
        
        # コンソール入力リスナーを開始
        self.loop.create_task(self.console_input_handler())

        # 拡張機能（Cog）をロード
        await self.load_all_extensions()

        # コマンドを同期
        try:
            await self.tree.sync()
            print(f"✅ コマンドを同期しました。")
        except Exception as e:
            print(f"❌ 同期に失敗しました: {e}")

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
        print("⌨️  コンソールコマンドの準備ができました。'reload' と入力するとCogを更新します。")
        
        while True:
            try:
                line = await aioconsole.ainput()
                command = line.strip().lower()
                
                if command == "reload":
                    print("🔄 すべての拡張機能をリロード中...")
                    await self.load_all_extensions()
                    # コマンドの同期も再実行
                    await self.tree.sync()
                    print("✨ リロード完了！")
                elif command.startswith("say "):
                    # say <メッセージ> (チャンネルIDは 1379135420960604362 に固定)
                    parts = line.strip().split(" ", 1)
                    if len(parts) < 2:
                        print("⚠️ 使用法: say <メッセージ>")
                        continue
                    
                    say_content = parts[1]
                    target_channel_id = 1379135420960604362
                    
                    channel = self.get_channel(target_channel_id) or await self.fetch_channel(target_channel_id)
                    if channel:
                        await channel.send(say_content)
                        print(f"✅ #{channel.name} に送信しました: {say_content}")
                    else:
                        print(f"❌ エラー: チャンネル {target_channel_id} が見つかりません。")
                elif command == "check":
                    print("🔄 'check_player_names.json' の履歴をチェック中...")
                    cog = self.get_cog("BrawlStarsCog")
                    if cog:
                        # 非同期タスクとして実行
                        self.loop.create_task(cog.batch_check_history(limit=300))
                    else:
                        print("❌ エラー: BrawlStarsCog がロードされていません。")
                elif command == "react":
                    print("🔄 履歴にリアクションを追加中...")
                    cog = self.get_cog("BrawlStarsCog")
                    if cog:
                        self.loop.create_task(cog.batch_react_history(limit=300))
                    else:
                        print("❌ エラー: BrawlStarsCog がロードされていません。")
                elif command.startswith("ratelimit "):
                    # ratelimit flash/lite/vision <1h_limit> <24h_limit>
                    parts = line.strip().split()
                    if len(parts) != 4 or parts[1] not in ["flash", "lite", "vision"]:
                        print("⚠️ 使用法: ratelimit flash/lite/vision <1時間あたりの制限> <24時間あたりの制限>")
                        continue
                    try:
                        target = parts[1]
                        h1 = int(parts[2])
                        h24 = int(parts[3])
                        if target == "flash":
                            self.config.RATELIMIT_FLASH_1H = h1
                            self.config.RATELIMIT_FLASH_24H = h24
                        elif target == "lite":
                            self.config.RATELIMIT_LITE_1H = h1
                            self.config.RATELIMIT_LITE_24H = h24
                        else:
                            self.config.RATELIMIT_VISION_1H = h1
                            self.config.RATELIMIT_VISION_24H = h24
                        
                        self.config.save_config()
                        print(f"✅ {target.capitalize()} のレート制限を更新しました: 1時間={h1}, 24時間={h24}")
                    except ValueError:
                        print("❌ エラー: 制限値は整数である必要があります。")
                elif command == "testgemini":
                    print("🔄 Gemini APIの接続テスト中...")
                    try:
                        import google.generativeai as genai
                        from PIL import Image
                        
                        api_key = os.environ.get("GEMINI_API_KEY")
                        if not api_key:
                            print("❌ 環境変数 GEMINI_API_KEY が設定されていません")
                            continue
                        
                        print(f"✅ APIキーを検出しました: {api_key[:10]}...")
                        
                        genai.configure(api_key=api_key)
                        print("✅ genai.configure() 成功")
                        
                        print("--- 利用可能なモデルを検索中 ---")
                        target_model_name = None
                        for m in genai.list_models():
                            if 'generateContent' in m.supported_generation_methods:
                                print(f"利用可能: {m.name}")
                                if 'gemini-2.5-flash-lite' in m.name:
                                    target_model_name = m.name
                        
                        if not target_model_name:
                            print("⚠️ gemini-2.5-flash-lite が見つかりませんでした。別のモデルを試します。")
                            for m in genai.list_models():
                                if 'generateContent' in m.supported_generation_methods:
                                    target_model_name = m.name
                                    break
                        
                        if not target_model_name:
                             print("❌ 利用可能なモデルが見つかりませんでした。")
                             continue

                        print(f"👉 使用するモデル: {target_model_name}")
                        
                        model = genai.GenerativeModel(target_model_name)
                        print(f"✅ モデルを初期化しました: {target_model_name}")
                        
                        print("📤 テストリクエストを送信中...")
                        response = model.generate_content("こんにちは、接続テストです。")
                        
                        print("\n✅ 成功！レスポンス:")
                        print("=" * 50)
                        print(response.text)
                        print("=" * 50)
                        print("🎉 Gemini APIは正常に動作しています！\n")
                    except Exception as e:
                        print(f"メッセージ: {e}\n")
                elif command == "testgroq":
                    print("🔄 Groq APIの接続テスト中...")
                    try:
                        from groq import Groq
                        api_key = os.environ.get("GROQ_API_KEY")
                        if not api_key:
                            print("❌ 環境変数 GROQ_API_KEY が設定されていません")
                            continue
                        
                        client = Groq(api_key=api_key)
                        print("--- 利用可能なモデルをリストアップ ---")
                        models = client.models.list()
                        for m in models.data:
                            print(f"利用可能: {m.id}")
                        
                        # おすすめのモデル（llama-3.1-8bなど）を探す
                        target_model = "llama-3.1-8b-instant" # デフォルト候補
                        found_target = False
                        for m in models.data:
                            if "llama-3.1-8b-instant" in m.id:
                                target_model = m.id
                                found_target = True
                                break
                            elif "llama-3.3-70b-versatile" in m.id:
                                target_model = m.id
                                found_target = True
                                continue
                        
                        if not found_target:
                            # 3.1が見つからない場合は先頭のモデルとかを使う
                            target_model = models.data[0].id
                        
                        print(f"👉 テストに使用するモデル: {target_model}")
                        
                        print("📤 テストリクエストを送信中...")
                        response = client.chat.completions.create(
                            model=target_model,
                            messages=[{"role": "user", "content": "こんにちは、接続テストです。"}],
                            max_tokens=100
                        )
                        
                        print("\n✅ 成功！レスポンス:")
                        print("=" * 50)
                        print(response.choices[0].message.content)
                        print("=" * 50)
                        print("🎉 Groq APIは正常に動作しています！\n")
                    except Exception as e:
                        print(f"メッセージ: {e}\n")
                elif command.startswith("collect"):
                    parts = line.strip().split()
                    # 使用法: collect <reports/checks> [limit]
                    if len(parts) < 2 or parts[1] not in ["reports", "checks"]:
                        print("⚠️ 使用法: collect reports [limit] または collect checks [limit]")
                        continue
                    
                    target = parts[1]
                    limit = 500
                    if len(parts) > 2:
                        try: limit = int(parts[2])
                        except: pass
                    
                    print(f"🔄 {target} の過去画像収集を開始します (上限: {limit}件)...")
                    cog = self.get_cog("BrawlStarsCog")
                    if cog:
                        self.loop.create_task(cog.batch_collect_images(target=target, limit=limit))
                    else:
                        print("❌ エラー: BrawlStarsCog がロードされていません。")
                elif command == "help":
                    print("\n" + "="*40)
                    print("📋 フィーロ コンソールコマンド一覧")
                    print("="*40)
                    print("  reload              - 全機能を最新の状態に更新（スラッシュ同期含む）")
                    print("  say <メッセージ>    - 指定チャンネルにメッセージを送信")
                    print("  check               - チェック用CHの画像を全件再スキャン")
                    print("  react               - チェック用CHの全件にリアクション付与")
                    print("  ratelimit flash <1h> <24h>  - Flashの回数制限を更新")
                    print("  ratelimit lite <1h> <24h>   - Flash-Liteの回数制限を更新")
                    print("  ratelimit vision <1h> <24h> - Visionの回数制限を更新")
                    print("  testgemini          - Gemini API接続テスト（診断用）")
                    print("  testgroq            - Groq API接続テスト＆モデル確認")
                    print("  collect reports [n] - 報告チャンネルの画像を一括取得")
                    print("  collect checks [n]  - チェックチャンネルの画像を一括取得")
                    print("  help                - このヘルプを表示")
                    print("="*40 + "\n")
                elif command == "":
                    continue
                else:
                    print(f"❓ 不明なコマンド: {command}")
            except Exception as e:
                print(f"❌ コンソールエラー: {e}")

    async def on_ready(self):
        print(f"ログイン成功: {self.user}")
        
        # プレゼンス（活動状態）を設定
        activity = discord.Game(name="ブロスタ")
        await self.change_presence(activity=activity)

        # オーナーに通知
        try:
            owner = self.get_user(self.config.OWNER_ID) or await self.fetch_user(self.config.OWNER_ID)
            await owner.send("✅ ボットがリファクタリング後の構成で起動(再接続)しました！")
        except Exception as e:
             print(f"❌ 起動通知失敗: {e}")

    async def close(self):
        if self.session:
            await self.session.close()
        await super().close()

bot = MyBot()

# スラッシュコマンド用のグローバルエラーハンドラー (treeに登録)
async def on_tree_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    command = interaction.command.name if interaction.command else "不明"
    print(f"🔴 /{command} でエラーが発生しました: {error}")
    # ヘルパー経由でオーナーに通知
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
