import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional
from datetime import datetime, timezone, timedelta
import os
import re
from googleapiclient.discovery import build

from utils.discord_helpers import log_to_owner
from utils.helpers import normalize_text

JST = timezone(timedelta(hours=9))

class ChatCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
        self.GOOGLE_CSE_ID = os.environ.get("GOOGLE_CSE_ID", "")
        self.google_service = self.setup_google_search()

    def setup_google_search(self):
        if self.GOOGLE_API_KEY and self.GOOGLE_CSE_ID:
            try:
                service = build("customsearch", "v1", developerKey=self.GOOGLE_API_KEY)
                print("✅ Google検索API初期化完了")
                return service
            except Exception as e:
                print(f"❌ Google検索API初期化失敗: {e}")
        return None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        config = self.bot.config
        
        # DM転送
        if isinstance(message.channel, discord.DMChannel):
            if message.author.id == config.OWNER_ID: return
            try:
                owner = await self.bot.fetch_user(config.OWNER_ID)
                current_time = datetime.now(JST).strftime("%Y年%m月%d日 %H:%M:%S")
                embed = discord.Embed(
                    title="📩 DM受信",
                    description=message.content if message.content else "(メッセージなし)",
                    color=discord.Color.blue()
                )
                embed.add_field(name="送信者", value=f"{message.author.name} ({message.author.id})", inline=False)
                embed.add_field(name="時刻", value=current_time, inline=False) # Fixed: added inline
                if message.attachments:
                     attachment_list = "\n".join([att.url for att in message.attachments])
                     embed.add_field(name="添付ファイル", value=attachment_list[:1000], inline=False)
                await owner.send(embed=embed)
                print(f"📩 DM転送完了: {message.author.name} [{current_time}]")
            except Exception as e:
                print(f"❌ DM転送失敗: {e}")
            return # DM handled

        # Google Search
        if "と検索して" in message.content:
            await self.handle_search_request(message)

        # チャット削除 (Owner Only Shortcut - legacy support but checks admin mode)
        # Note: AdminCog handles messages in admin mode. This is for GLOBAL owner commands.
        # But wait, original code allowed owner to plain "チャットを消して" without admin mode?
        # Yes: "if message.author.id == OWNER_ID: ... delete_words ..."
        if message.author.id == config.OWNER_ID:
            normalized = normalize_text(message.content)
            delete_words = ["削除", "消して", "掃除", "クリア", "clear", "消去"]
            if ("チャット" in normalized or "メッセージ" in normalized) and any(w in normalized for w in delete_words):
                if "監視" not in normalized:
                    match = re.search(r"(\d+)件", message.content)
                    limit = int(match.group(1)) if match else 300
                    if isinstance(message.channel, discord.TextChannel):
                        await message.channel.purge(limit=limit + 1)
                        await message.channel.send("お掃除完了！綺麗になったね！", delete_after=5)
                        # Exit admin mode if active (The fix we implemented earlier)
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
                result = self.google_service.cse().list(
                    q=query, cx=self.GOOGLE_CSE_ID, num=5
                ).execute()
                
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


    # ====== Commands ======
    @app_commands.command(name="say", description="ボットにメッセージを発言させる（管理者のみ）")
    async def say_command(self, interaction: discord.Interaction, message: str, channel: Optional[discord.TextChannel] = None):
        config = self.bot.config
        if not config.is_authorized(interaction.user.id):
            await interaction.response.send_message("このコマンドを使う権限はありません。", ephemeral=True)
            return

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

    @app_commands.command(name="clear", description="メッセージを削除（オーナーのみ）")
    async def clear_command(self, interaction: discord.Interaction, user: Optional[discord.User] = None, limit: Optional[int] = 300):
        config = self.bot.config
        if not config.is_authorized(interaction.user.id):
            await interaction.response.send_message("権限がありません。", ephemeral=True)
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
            
            # Important: Exit admin mode to prevent timeout msg (Fix applied)
            config.exit_admin_mode(interaction.user.id)
            
        except Exception as e:
            await interaction.followup.send(f"❌ 失敗: {e}", ephemeral=True)

    @app_commands.command(name="dm", description="特定のユーザーにDMを送信（オーナーのみ）")
    async def dm_command(self, interaction: discord.Interaction, user: discord.User, message: str):
        config = self.bot.config
        if interaction.user.id != config.OWNER_ID:
            await interaction.response.send_message("権限がありません。", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True)
        try:
            await user.send(message)
            await interaction.followup.send(f"✅ {user.name} に送信しました", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 失敗: {e}", ephemeral=True)

async def setup(bot):
    await bot.add_cog(ChatCog(bot))
