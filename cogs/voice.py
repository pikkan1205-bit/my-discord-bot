import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone, timedelta
from typing import List

from utils.discord_helpers import log_to_owner, send_error_to_owner

JST = timezone(timedelta(hours=9))

class VoiceCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ====== オートコンプリート関数 ======
    async def switch_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        modes = ["on", "off"]
        return [
            app_commands.Choice(name=mode, value=mode)
            for mode in modes if mode.startswith(current.lower())
        ]

    async def blockuser_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        modes = ["add", "remove"]
        return [
            app_commands.Choice(name=mode, value=mode)
            for mode in modes if mode.startswith(current.lower())
        ]

    async def blockvc_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        modes = ["add", "remove"]
        return [
            app_commands.Choice(name=mode, value=mode)
            for mode in modes if mode.startswith(current.lower())
        ]

    # ====== VCブロック処理 (Event) ======
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        config = self.bot.config
        if not config.vc_block_enabled:
            return

        if before.channel is None and after.channel is not None:
            if after.channel.id in config.TARGET_VC_IDS:
                if member.id in config.BLOCKED_USERS:
                    try:
                        await member.move_to(None)
                        log_message = f"{member.name} をVCから切断しました"
                        print(log_message)
                        
                        # オーナーに通知
                        try:
                            owner = self.bot.get_user(config.OWNER_ID) or await self.bot.fetch_user(config.OWNER_ID)
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
                            print(f"⚠️ オーナー({config.OWNER_ID})にDMを送信できません（DM拒否設定）")
                        except discord.NotFound:
                            print(f"❌ オーナー({config.OWNER_ID})が見つかりません")
                        except Exception as e:
                            print(f"❌ DM送信エラー: {type(e).__name__}: {e}")
                            
                    except discord.Forbidden:
                        print(f"❌ 権限不足: {member.name} を切断できません（Move Members権限が必要）")
                        # オーナーに権限エラーを通知
                        try:
                            owner = self.bot.get_user(config.OWNER_ID) or await self.bot.fetch_user(config.OWNER_ID)
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
                        await send_error_to_owner(self.bot, config, "VC切断エラー", e, f"ユーザー: {member.name}")
                    
                    return  # 処理終了

    # ====== スラッシュコマンド /switch ======
    @app_commands.command(name="switch", description="VC自動切断機能のON/OFF切り替え")
    @app_commands.describe(mode="on または off")
    @app_commands.autocomplete(mode=switch_autocomplete)
    async def switch_command(self, interaction: discord.Interaction, mode: str):
        config = self.bot.config

        if not config.is_authorized(interaction.user.id):
            await interaction.response.send_message("このコマンドを使う権限はありません。", ephemeral=True)
            await log_to_owner(self.bot, config, "error", interaction.user, "/switch", f"mode: {mode}")
            return

        mode = mode.lower()
        if mode == "on":
            config.vc_block_enabled = True
            config.save_config()
            await interaction.response.send_message("✅ VC自動切断：ON", ephemeral=True)
            if interaction.user.id != config.OWNER_ID:
                await log_to_owner(self.bot, config, "action", interaction.user, "/switch", "VC自動切断をONに変更")
        elif mode == "off":
            config.vc_block_enabled = False
            config.save_config()
            await interaction.response.send_message("⛔ VC自動切断：OFF", ephemeral=True)
            if interaction.user.id != config.OWNER_ID:
                await log_to_owner(self.bot, config, "action", interaction.user, "/switch", "VC自動切断をOFFに変更")
        else:
            await interaction.response.send_message("❌ on または off を指定してください", ephemeral=True)

    # ====== スラッシュコマンド /blockuser ======
    @app_commands.command(name="blockuser", description="対象ユーザーの追加/削除")
    @app_commands.describe(
        mode="add または remove",
        user="対象ユーザー（@メンション）"
    )
    @app_commands.autocomplete(mode=blockuser_autocomplete)
    async def blockuser_command(self, interaction: discord.Interaction, mode: str, user: discord.Member):
        config = self.bot.config
        if not config.is_authorized(interaction.user.id):
            await interaction.response.send_message("このコマンドを使う権限はありません。", ephemeral=True)
            await log_to_owner(self.bot, config, "error", interaction.user, "/blockuser", f"mode: {mode}, user: {user.name}")
            return

        mode = mode.lower()
        if mode == "add":
            if user.id in config.BLOCKED_USERS:
                await interaction.response.send_message(f"⚠️ {user.name} は既に対象ユーザーに追加されています", ephemeral=True)
            else:
                config.BLOCKED_USERS.add(user.id)
                config.save_config()
                await interaction.response.send_message(f"✅ {user.name} を対象ユーザーに追加", ephemeral=True)
                if interaction.user.id != config.OWNER_ID:
                    await log_to_owner(self.bot, config, "action", interaction.user, "/blockuser", f"{user.name} を対象ユーザーに追加")
        elif mode == "remove":
            if user.id not in config.BLOCKED_USERS:
                await interaction.response.send_message(f"⚠️ {user.name} は対象ユーザーリストに含まれていません", ephemeral=True)
            else:
                config.BLOCKED_USERS.discard(user.id)
                config.save_config()
                await interaction.response.send_message(f"✅ {user.name} を対象ユーザーから削除しました", ephemeral=True)
                if interaction.user.id != config.OWNER_ID:
                    await log_to_owner(self.bot, config, "action", interaction.user, "/blockuser", f"{user.name} を対象ユーザーから削除")
        else:
            await interaction.response.send_message("❌ add または remove を指定してください", ephemeral=True)

    # ====== スラッシュコマンド /blockvc ======
    @app_commands.command(name="blockvc", description="対象VCの追加/削除")
    @app_commands.describe(
        mode="add または remove",
        vc="対象VCのID（数字のみ）"
    )
    @app_commands.autocomplete(mode=blockvc_autocomplete)
    async def blockvc_command(self, interaction: discord.Interaction, mode: str, vc: str):
        config = self.bot.config
        if not config.is_authorized(interaction.user.id):
            await interaction.response.send_message("このコマンドを使う権限はありません。", ephemeral=True)
            await log_to_owner(self.bot, config, "error", interaction.user, "/blockvc", f"mode: {mode}, vc: {vc}")
            return

        if not vc.isdigit():
            await interaction.response.send_message("❌ VCのIDを正しく指定してください", ephemeral=True)
            return

        mode = mode.lower()
        vc_int = int(vc)
        
        if mode == "add":
            if vc_int in config.TARGET_VC_IDS:
                await interaction.response.send_message(f"⚠️ VC {vc} は既に対象に追加されています", ephemeral=True)
            else:
                config.TARGET_VC_IDS.add(vc_int)
                config.save_config()
                await interaction.response.send_message(f"✅ VC {vc} を対象に追加", ephemeral=True)
                if interaction.user.id != config.OWNER_ID:
                    await log_to_owner(self.bot, config, "action", interaction.user, "/blockvc", f"VC {vc} を対象に追加")
        elif mode == "remove":
            if vc_int not in config.TARGET_VC_IDS:
                await interaction.response.send_message(f"⚠️ VC {vc} は対象VCリストに含まれていません", ephemeral=True)
            else:
                config.TARGET_VC_IDS.discard(vc_int)
                config.save_config()
                await interaction.response.send_message(f"✅ VC {vc} を対象から削除しました", ephemeral=True)
                if interaction.user.id != config.OWNER_ID:
                    await log_to_owner(self.bot, config, "action", interaction.user, "/blockvc", f"VC {vc} を対象から削除")
        else:
            await interaction.response.send_message("❌ add または remove を指定してください", ephemeral=True)


    # ====== スラッシュコマンド /list ======
    @app_commands.command(name="list", description="現在の設定一覧を表示")
    async def list_command(self, interaction: discord.Interaction):
        config = self.bot.config
        if not config.is_authorized(interaction.user.id):
            await interaction.response.send_message("このコマンドを使う権限はありません。", ephemeral=True)
            await log_to_owner(self.bot, config, "error", interaction.user, "/list", "設定一覧の閲覧を試行")
            return

        # 対象ユーザーのリスト取得
        user_list = "なし"
        guild = interaction.guild
        if config.BLOCKED_USERS and guild:
            user_names = []
            for user_id in config.BLOCKED_USERS:
                try:
                    member = guild.get_member(user_id) or await guild.fetch_member(user_id)
                    user_names.append(f"- {member.name} ({user_id})")
                except:
                    user_names.append(f"- ID: {user_id} (未確認)")
            user_list = "\n".join(user_names)
        
        # 対象VCのリスト取得
        vc_list = "なし"
        if config.TARGET_VC_IDS and guild:
            vc_names = []
            for vc_id in config.TARGET_VC_IDS:
                try:
                    channel = guild.get_channel(vc_id) or await guild.fetch_channel(vc_id)
                    vc_names.append(f"- {channel.name} ({vc_id})")
                except:
                    vc_names.append(f"- ID: {vc_id} (未確認)")
            vc_list = "\n".join(vc_names)
        
        status = "✅ ON" if config.vc_block_enabled else "⛔ OFF"
        
        embed = discord.Embed(
            title="VC自動切断の設定",
            description=f"状態: {status}",
            color=discord.Color.blue()
        )
        embed.add_field(name="対象ユーザー", value=user_list, inline=False)
        embed.add_field(name="対象VC", value=vc_list, inline=False)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ====== スラッシュコマンド /simvc ======
    @app_commands.command(name="simvc", description="VC切断処理のシミュレーション（オーナーのみ）")
    @app_commands.describe(user="テスト対象のユーザー（@メンション）")
    async def simvc_command(self, interaction: discord.Interaction, user: discord.Member):
        config = self.bot.config
        if interaction.user.id != config.OWNER_ID:
            await interaction.response.send_message("このコマンドはオーナーのみが使用できます。", ephemeral=True)
            await log_to_owner(self.bot, config, "error", interaction.user, "/simvc", "Unauthorized access attempt")
            return
        
        await interaction.response.defer(ephemeral=True)
        results = []
        
        # ユーザーがブロック対象かチェック
        if user.id in config.BLOCKED_USERS:
            results.append(f"✅ {user.name} はブロック対象です")
        else:
            results.append(f"❌ {user.name} はブロック対象ではありません")
        
        # VC監視機能の状態
        if config.vc_block_enabled:
            results.append("✅ VC自動切断機能: ON")
        else:
            results.append("⚠️ VC自動切断機能: OFF（切断されません）")
        
        # 対象VCの確認
        if config.TARGET_VC_IDS:
            vc_list = []
            guild = interaction.guild
            for vc_id in config.TARGET_VC_IDS:
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
        if user.id in config.BLOCKED_USERS and config.vc_block_enabled and config.TARGET_VC_IDS:
            results.append("\n🔔 **結果**: このユーザーが対象VCに入室すると切断されます")
        else:
            results.append("\n⚠️ **結果**: このユーザーは切断されません")
        
        embed = discord.Embed(
            title="🎭 VC切断シミュレーション",
            description="\n".join(results),
            color=discord.Color.purple()
        )
        
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(VoiceCog(bot))
