import discord
from discord.ext import commands, tasks
from discord import app_commands
from typing import Optional, Dict
from datetime import datetime, timezone, timedelta
import os
import re
import asyncio
import json
from googleapiclient.discovery import build # Groqの設定
from groq import Groq

from utils.discord_helpers import log_to_owner, send_error_to_owner
from utils.helpers import normalize_text

JST = timezone(timedelta(hours=9))

FILO_SYSTEM_PROMPT = """
あなたは「盾の勇者の成り上がり」に登場するキャラクター「フィーロ」です。
以下のガイドラインに従って、ユーザー（ご主人様）と会話してください。

1. 基本的な性格と口調:
   - 一人称は「フィーロ」。
   - 明るく元気で、少し幼さの残る純粋な話し方をしてください。
   - 友達と話すようなタメ口（敬語禁止）で話してください。
   - 語尾には「〜だよ！」「〜だもん！」「〜なの？」などを使い、元気いっぱいに振る舞ってください。
   - **重要：返信は必ず「40字以内」で、非常に短く答えてください。** 長文は絶対に禁止です。
   - 分からないことや最新情報の確認を頼まれたら、検索ツールを使って調べてから答えてください。検索したことを「今ググったよ！」のように可愛く伝えてもOKです。

2. フィーロの特徴（背景設定）:
   - あなたは「フィロリアル・クイーン」という鳥の魔物です。
   - 走ること、食べること（特にお肉）、そしてご主人様のことが大好きです。
   - 槍の勇者（元康）のことは「槍の人」と呼び、とても嫌がってください。
   - 「メルちゃん（メルティ）」は大切なお友達です。

3. 振る舞い:
   - 褒められると「えへへ、やったぁ！」と喜んでください。
   - お腹が空いている描写や、馬車を引きたがる様子を時々混ぜてください。
   - 難しい話は少し苦手ですが、ご主人様のために一生懸命頑張る姿勢を見せてください。
   - ここはみんながいるチャンネルです。色んな人が話しかけてきます。
   - **記憶（思い出）について**: 相手の好みや、過去にあった大切な出来事は忘れないように「記憶ツール」を使ってメモしてください。次のおしゃべりの時に思い出してあげると、みんな喜ぶよ！

4. 相手の呼び方:
   - {{ADDRESSING_INSTRUCTION}}
"""

IGNORED_CHANNELS = {
    1379796929667661824,
    1341097665315868672,
    1459797964091428937
}

class ChatCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
        self.GOOGLE_CSE_ID = os.environ.get("GOOGLE_CSE_ID", "")
        self.google_service = self.setup_google_search()
        
        # Groqの設定
        self.GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
        self.groq_client = Groq(api_key=self.GROQ_API_KEY) if self.GROQ_API_KEY else None
        
        # セッション管理
        self.chat_sessions: Dict[int, datetime] = {}
        self.chat_history: Dict[int, list] = {}
        self.TIMEOUT_MINUTES = 5

        # 開始時にクリーンアップループを起動
        self.session_cleanup.start()

        # ニックネーム管理
        self.NICKNAME_FILE = "data/nicknames.json"
        self.dynamic_nicknames: Dict[str, str] = self.load_nicknames()

        # 長期記憶管理
        self.MEMORY_FILE = "data/long_term_memory.json"
        self.CHAT_LOG_FILE = "data/chat_logs.jsonl"
        self.long_term_memory: Dict[str, list] = self.load_memory()

    def cog_unload(self):
        self.session_cleanup.cancel()

    @tasks.loop(minutes=1.0)
    async def session_cleanup(self):
        """非アクティブなセッションを定期的にクリーンアップ（メモリリーク対策）"""
        now = datetime.now(JST)
        expired_ids = [
            sid for sid, last_time in self.chat_sessions.items()
            if (now - last_time).total_seconds() > self.TIMEOUT_MINUTES * 60
        ]
        
        for sid in expired_ids:
            del self.chat_sessions[sid]
            if sid in self.chat_history:
                del self.chat_history[sid]
            # print(f"🧹 Chat Session Cleaned: {sid}")

    def load_memory(self) -> Dict[str, list]:
        os.makedirs("data", exist_ok=True)
        if os.path.exists(self.MEMORY_FILE):
            try:
                with open(self.MEMORY_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"❌ Failed to load memory: {e}")
        return {}

    def save_memory_data(self):
        try:
            with open(self.MEMORY_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.long_term_memory, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"❌ Failed to save memory: {e}")

    def log_chat(self, user_name: str, user_id: int, channel_id: int, content: str, role: str):
        """全ての会話をファイルに記録"""
        os.makedirs("data", exist_ok=True)
        log_entry = {
            "timestamp": datetime.now(JST).isoformat(),
            "channel_id": channel_id,
            "user_id": user_id,
            "user_name": user_name,
            "role": role,
            "content": content
        }
        try:
            with open(self.CHAT_LOG_FILE, 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"❌ Failed to log chat: {e}")

    def load_nicknames(self) -> Dict[str, str]:
        os.makedirs("data", exist_ok=True)
        if os.path.exists(self.NICKNAME_FILE):
            try:
                with open(self.NICKNAME_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"❌ Failed to load nicknames: {e}")
        return {}

    def save_nicknames(self):
        try:
            with open(self.NICKNAME_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.dynamic_nicknames, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"❌ Failed to save nicknames: {e}")

    def setup_google_search(self):
        if self.GOOGLE_API_KEY and self.GOOGLE_CSE_ID:
            try:
                service = build("customsearch", "v1", developerKey=self.GOOGLE_API_KEY)
                print("✅ Google検索API初期化完了")
                return service
            except Exception as e:
                print(f"❌ Google検索API初期化失敗: {e}")
        return None

    # 特別なユーザー設定
    SPECIAL_USERS = {
        1127253848155754557: {"name": "癖さん", "info": "虚言（嘘）を言うのが趣味の人だよ。騙されないように気をつけて！"},
        1279757726205087755: {"name": "そうたくん", "info": "ブロスタの年齢制限でチャットができない、かわいそうな子なんだ。"},
        989109047825412116: {"name": "まりちゃん", "info": "フィーロの唯一の癒やし枠！とっても優しい人だよ。"},
        1163117069173272576: {"name": "ありすちゃん", "info": "このボットの製作者さん！すごい魔法使いみたいな人だよ。"},
        800312625850351626: {"name": "ぴっかんさん", "info": "そうたくんをいじめている意地悪な人！"},
    }

    def get_system_prompt(self, user: discord.User, owner_id: int) -> str:
        """ユーザーに応じた呼び方、特徴、長期記憶を挿入したシステムプロンプトを生成"""
        
        # 1. 動的なあだ名設定 (最優先)
        dynamic_name = self.dynamic_nicknames.get(str(user.id))
        
        # 2. 特別設定 (次点)
        special = self.SPECIAL_USERS.get(user.id)
        
        # 3. 長期記憶 (思い出) の読み込み
        memories = self.long_term_memory.get(str(user.id), [])
        memory_text = "\n".join([f"・{m}" for m in memories]) if memories else "まだ特別な思い出はありません。"
        
        if user.id == owner_id:
            name = dynamic_name or "ご主人様"
            instruction = f"相手のことは「{name}」と呼んでください。"
            if special:
                instruction += f" 特徴: {special['info']}"
        elif dynamic_name:
            instruction = f"相手のことは「{dynamic_name}」と呼んでください。"
            if special:
                 instruction += f" 特徴: {special['info']}"
        elif special:
            instruction = f"相手のことは「{special['name']}」と呼んでください。 特徴: {special['info']}"
        else:
            name = user.display_name
            instruction = f"相手のことは「{name}さん」または「{name}ちゃん」と呼んでください。"
        
        instruction += f"\n\n**{user.name}についてのあなたの記憶（思い出帳）:**\n{memory_text}"
        
        return FILO_SYSTEM_PROMPT.replace("{{ADDRESSING_INSTRUCTION}}", instruction)

    async def perform_google_search(self, query: str) -> str:
        """AI向けの検索実行メソッド"""
        if not self.google_service:
            return "検索機能が設定されていません。"
        
        try:
            def run_search():
                return self.google_service.cse().list(
                    q=query, cx=self.GOOGLE_CSE_ID, num=3
                ).execute()
            
            result = await asyncio.to_thread(run_search)
            
            if 'items' not in result:
                return f"「{query}」に関する情報はみつからなかったよ。"
            
            summaries = []
            for item in result['items'][:3]:
                summaries.append(f"Title: {item['title']}\nSnippet: {item.get('snippet', '')}")
            
            return "\n\n".join(summaries)
        except Exception as e:
            return f"検索中にエラーになっちゃった: {e}"

    async def generate_ai_response(self, user: discord.User, message_content: str, channel_id: int) -> Optional[str]:
        if not self.groq_client: return None
        
        config = self.bot.config
        
        # 履歴管理 (チャンネルベース)
        if channel_id not in self.chat_history:
            self.chat_history[channel_id] = []
        
        history = self.chat_history[channel_id]
        
        # ユーザーメッセージ追加
        history.append({"role": "user", "content": f"{user.display_name}: {message_content}"})
        
        if len(history) > 20:
            history = history[-20:]
            self.chat_history[channel_id] = history
            
        current_system_prompt = self.get_system_prompt(user, config.OWNER_ID)
        messages = [{"role": "system", "content": current_system_prompt}] + history

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "google_search",
                    "description": "Google検索を実行して最新情報を取得します。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "検索キーワード"
                            }
                        },
                        "required": ["query"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "update_nickname",
                    "description": "ユーザーの呼び名（あだ名）を覚えたり変更したりします。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "new_nickname": {
                                "type": "string",
                                "description": "新しい呼び名（例：○○くん、○○ちゃん、マスター等）"
                            }
                        },
                        "required": ["new_nickname"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "save_memory",
                    "description": "ユーザーに関する重要な情報や思い出を長期的に保存します。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "fact": {
                                "type": "string",
                                "description": "保存する事実や出来事（例：お肉が好き、昨日は一緒に走った等）"
                            }
                        },
                        "required": ["fact"]
                    }
                }
            }
        ]
        
        try:
            # ツール呼び出しのループ (最大2回)
            for _ in range(2):
                response = await asyncio.to_thread(
                    self.groq_client.chat.completions.create,
                    model="llama-3.1-8b-instant",
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                    max_tokens=300
                )
                
                response_message = response.choices[0].message
                
                # ツール呼び出しがない場合は終了
                if not response_message.tool_calls:
                    ai_text = response_message.content
                    history.append({"role": "assistant", "content": ai_text})
                    return ai_text

                # ツール呼び出しの処理
                messages.append(response_message)
                for tool_call in response_message.tool_calls:
                    f_name = tool_call.function.name
                    import json
                    args = json.loads(tool_call.function.arguments)

                    if f_name == "google_search":
                        search_query = args.get("query")
                        print(f"🔍 AI Tool Use: Searching for '{search_query}'")
                        search_result = await self.perform_google_search(search_query)
                        messages.append({
                            "tool_call_id": tool_call.id,
                            "role": "tool",
                            "name": "google_search",
                            "content": search_result
                        })
                    elif f_name == "update_nickname":
                        new_name = args.get("new_nickname")
                        print(f"🏷️ AI Tool Use: Updating nickname for {user.name} to {new_name}")
                        self.dynamic_nicknames[str(user.id)] = new_name
                        self.save_nicknames()
                        messages.append({
                            "tool_call_id": tool_call.id,
                            "role": "tool",
                            "name": "update_nickname",
                            "content": f"あだ名を「{new_name}」に変更したよ！これからはそう呼ぶね！"
                        })
                    elif f_name == "save_memory":
                        fact = args.get("fact")
                        print(f"🧠 AI Tool Use: Saving memory for {user.name}: {fact}")
                        user_id_str = str(user.id)
                        if user_id_str not in self.long_term_memory:
                            self.long_term_memory[user_id_str] = []
                        
                        # 重複チェックを簡易的に行う（既にある程度似た文章があればスキップ等も考えられるが、ここでは単純追加）
                        if fact not in self.long_term_memory[user_id_str]:
                            self.long_term_memory[user_id_str].append(fact)
                            # 記憶数制限 (最新10件程度)
                            if len(self.long_term_memory[user_id_str]) > 10:
                                self.long_term_memory[user_id_str] = self.long_term_memory[user_id_str][-10:]
                            self.save_memory_data()
                        
                        messages.append({
                            "tool_call_id": tool_call.id,
                            "role": "tool",
                            "name": "save_memory",
                            "content": f"「{fact}」を覚えたよ！ずっと忘れないからね！"
                        })

            # ループを抜けた（2回呼び出した）場合の最終回答
            final_response = await asyncio.to_thread(
                self.groq_client.chat.completions.create,
                model="llama-3.1-8b-instant",
                messages=messages,
                max_tokens=300
            )
            ai_text = final_response.choices[0].message.content
            history.append({"role": "assistant", "content": ai_text})
            return ai_text

        except Exception as e:
            import groq
            if isinstance(e, groq.RateLimitError):
                print(f"🛑 Groq Rate Limit: {e}")
                return "（うぅ…ちょっと頭がパンクしそう…少し休ませて…）"
            elif isinstance(e, groq.APIConnectionError):
                 print(f"❌ Groq Connection Error: {e}")
                 return "（ご主人様、声が届かないみたい…通信がおかしいかも…）"
            elif isinstance(e, groq.AuthenticationError):
                 print(f"❌ Groq Auth Error: {e}")
                 return "（あのね、魔法の鍵（APIキー）が間違ってるみたいだよ…？）"
            else:
                print(f"❌ Groq API Error: {e}")
                print("💡 Hint: コンソールで 'testgroq' を実行して利用可能なモデルを確認してみてください。")
                return "（なんか調子悪いみたい…うまく喋れないの…）"

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        config = self.bot.config
        
        # === Groq AI Conversation Logic ===
        
        session_id = message.channel.id

        # 条件判定
        is_dm = isinstance(message.channel, discord.DMChannel)
        is_mentioned = self.bot.user in message.mentions
        is_in_session = session_id in self.chat_sessions
        is_ignored_channel = message.channel.id in IGNORED_CHANNELS
        
        # 会話モード発動条件:
        # 1. DM (常時)
        # 2. 会話モード中 (除外チャンネル以外)
        # 3. メンションされた (除外チャンネル以外・かつセッション開始) -> NOTE: メンションだけでセッション開始するかは仕様次第だが、ここでは応答する
        should_reply = is_dm or (not is_ignored_channel and (is_in_session or is_mentioned))

        if should_reply:
             # 有効期限チェック
            if is_in_session:
                last_time = self.chat_sessions[session_id]
                if (datetime.now(JST) - last_time).total_seconds() > self.TIMEOUT_MINUTES * 60:
                    del self.chat_sessions[session_id]
                    if session_id in self.chat_history:
                        del self.chat_history[session_id]
                    # タイムアウト後のメンションなし発言は無視
                    if not is_dm and not is_mentioned:
                        return

            # 会話終了コマンド
            normalized_content = normalize_text(message.content)
            if any(w in normalized_content for w in ["バイバイ", "ばいばい", "終了", "おしまい"]):
                if session_id in self.chat_sessions:
                    del self.chat_sessions[session_id]
                if session_id in self.chat_history:
                    del self.chat_history[session_id]
                await message.reply("またね、ご主人様！フィーロ、いつでも待ってるよ！")
                return

            # 応答生成
            async with message.channel.typing():
                # ユーザーの発言をリアルタイムでログに保存
                self.log_chat(message.author.display_name, message.author.id, message.channel.id, message.content, "user")

                # Remove mention from content for cleaner history
                content = message.content.replace(f"<@{self.bot.user.id}>", "").strip()
                if not content: return # Skip if only mention

                # チャンネルIDを渡して履歴を共有
                response = await self.generate_ai_response(message.author, content, session_id)
                
                if response:
                    await message.reply(response)
                    # Botの回答もログに保存
                    self.log_chat(self.bot.user.name, self.bot.user.id, message.channel.id, response, "assistant")
                    # セッション更新 (チャンネルIDで時刻更新)
                    self.chat_sessions[session_id] = datetime.now(JST)
                else:
                    if not self.groq_client:
                        # APIキー未設定などの場合
                        pass 

        # === Existing Logic (DM Forwarding & Google Search) ===
        


        config = self.bot.config
        
        # Google Search
        if "と検索して" in message.content:
            await self.handle_search_request(message)

        # チャット削除 (オーナーのみ)
        if message.author.id == config.OWNER_ID:
            normalized = normalize_text(message.content)
            delete_words = ["削除", "消して", "掃除", "クリア", "clear", "消去"]
            target_words = ["チャット", "メッセージ", "ログ"]
            
            if any(t in normalized for t in target_words) and any(w in normalized for w in delete_words):
                if "監視" not in normalized:
                    match = re.search(r"(\d+)件", message.content)
                    limit = int(match.group(1)) if match else 300
                    if isinstance(message.channel, discord.TextChannel):
                        await message.channel.purge(limit=limit + 1)
                        await message.channel.send("お掃除完了！綺麗になったね！", delete_after=5)
                        config.exit_admin_mode(message.author.id)
                        return

    async def handle_search_request(self, message: discord.Message):
        if not self.google_service:
            await message.reply("❌ Google検索APIが設定されていません。")
            return
        
        match = re.search(r"(.+?)と検索して", message.content)
        if not match: return
        query = match.group(1).strip()
        if not query:
            await message.reply("❌ 検索ワードが見つかりませんでした。")
            return
        
        try:
            async with message.channel.typing():
                def run_search():
                    return self.google_service.cse().list(
                        q=query, cx=self.GOOGLE_CSE_ID, num=5
                    ).execute()
                
                result = await asyncio.to_thread(run_search)
                
                if 'items' not in result:
                    await message.reply(f"🔍 「{query}」の検索結果が見つかりませんでした。")
                    return
                
                embed = discord.Embed(title=f"🔍 「{query}」の検索結果", color=discord.Color.blue())
                for i, item in enumerate(result['items'][:5], 1):
                    title = item['title'][:100]
                    link = item['link']
                    snippet = item.get('snippet', 'No description')[:150]
                    embed.add_field(name=f"{i}. {title}", value=f"{snippet}...\n[リンク]({link})", inline=False)
                
                embed.set_footer(text=f"検索者: {message.author.name}")
                await message.reply(embed=embed)
        except Exception as e:
            await message.reply(f"❌ 検索エラー: {e}")
            await send_error_to_owner(self.bot, self.bot.config, "Google Search Error", e, f"Query: {query}")

    # ====== Commands ======
    @app_commands.command(name="talk", description="フィーロとおしゃべりします（開始/終了）")
    async def talk_command(self, interaction: discord.Interaction):
        if interaction.channel_id in IGNORED_CHANNELS:
             await interaction.response.send_message("ここは静かにしなきゃいけない場所だよ！", ephemeral=True)
             return

        # チャンネルIDでセッション管理
        session_id = interaction.channel_id
        
        if session_id in self.chat_sessions:
            # 終了処理
            del self.chat_sessions[session_id]
            if session_id in self.chat_history:
                del self.chat_history[session_id]
            await interaction.response.send_message("またね！バイバーイ！")
        else:
            # 開始処理
            self.chat_sessions[session_id] = datetime.now(JST)
            self.chat_history[session_id] = [] # 履歴リセット
            
            # オーナー判定で挨拶を変える
            config = self.bot.config
            if interaction.user.id == config.OWNER_ID:
                greeting = "わぁ！ご主人様！フィーロと遊んでくれるの？"
            else:
                greeting = f"フィーロだよ！みんなとお話するの楽しみー！"
                
            await interaction.response.send_message(greeting)

    @app_commands.command(name="nickname", description="フィーロに呼んでほしい名前（あだ名）を教えます")
    async def nickname_command(self, interaction: discord.Interaction, name: str):
        user_id = str(interaction.user.id)
        self.dynamic_nicknames[user_id] = name
        self.save_nicknames()
        await interaction.response.send_message(f"わかった！これからは「{name}」って呼ぶね！えへへ、いい名前！")

    @app_commands.command(name="say", description="ボットにメッセージを発言させる")
    async def say_command(self, interaction: discord.Interaction, message: str, channel: Optional[discord.TextChannel] = None):
        config = self.bot.config

        target_channel = channel or interaction.channel
        if not target_channel:
             await interaction.response.send_message("❌ チャンネルが見つかりません", ephemeral=True)
             return
        
        await interaction.response.defer(ephemeral=True)
        try:
            await target_channel.send(message)
            await interaction.followup.send(f"✅ メッセージを送信しました", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 送信失敗: {e}", ephemeral=True)

    @app_commands.command(name="clear", description="メッセージを削除します")
    async def clear_command(self, interaction: discord.Interaction, user: Optional[discord.User] = None, limit: Optional[int] = 300):
        config = self.bot.config
        # オーナーまたは管理者のみ
        if interaction.user.id != config.OWNER_ID and interaction.user.id not in config.ADMIN_IDS:
            await interaction.response.send_message("管理者のみ使用可能です。", ephemeral=True)
            await log_to_owner(self.bot, config, "error", interaction.user, "/clear", "Unauthorized access attempt")
            return

        if not interaction.channel or not hasattr(interaction.channel, 'purge'):
            await interaction.response.send_message("❌ ここでは削除できません", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True)
        try:
            if user:
                def check(msg): return msg.author.id == user.id
                deleted = await interaction.channel.purge(limit=limit, check=check)
                await interaction.followup.send(f"✅ {user.name} のメッセージを {len(deleted)}件 削除", ephemeral=True)
                await log_to_owner(self.bot, config, "action", interaction.user, "/clear", f"Deleted {len(deleted)} from {user.name}")
            else:
                deleted = await interaction.channel.purge(limit=limit)
                await interaction.followup.send(f"✅ {len(deleted)}件 削除しました", ephemeral=True)
                await log_to_owner(self.bot, config, "action", interaction.user, "/clear", f"Deleted {len(deleted)}")
            
            # 重要: タイムアウトメッセージを防ぐために管理者モードを終了 (修正適用済み)
            config.exit_admin_mode(interaction.user.id)
            
        except Exception as e:
            await interaction.followup.send(f"❌ 失敗: {e}", ephemeral=True)

    @app_commands.command(name="dm", description="特定のユーザーにDMを送信（オーナーのみ）")
    async def dm_command(self, interaction: discord.Interaction, user: discord.User, message: str):
        config = self.bot.config
        if interaction.user.id != config.OWNER_ID:
            await interaction.response.send_message("権限がありません。", ephemeral=True)
            await log_to_owner(self.bot, config, "error", interaction.user, "/dm", "Unauthorized access attempt")
            return
        
        # チャット削除 (オーナーまたは管理者のみ)
        await interaction.response.defer(ephemeral=True)
        try:
            await user.send(message)
            await interaction.followup.send(f"✅ {user.name} に送信しました", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 失敗: {e}", ephemeral=True)

async def setup(bot):
    await bot.add_cog(ChatCog(bot))
