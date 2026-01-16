import discord
from datetime import datetime, timezone, timedelta
from typing import Union

# 日本時間のタイムゾーン
JST = timezone(timedelta(hours=9))

async def log_to_owner(bot, config, log_type: str, user: Union[discord.User, discord.Member], command: str, details: str = ""):
    """管理者アクションまたは権限エラーをオーナーにDMでログ通知"""
    try:
        owner = bot.get_user(config.OWNER_ID) or await bot.fetch_user(config.OWNER_ID)
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

async def send_error_to_owner(bot, config, error_type: str, error: Exception, context: str = ""):
    """エラーをオーナーにDMで通知"""
    try:
        owner = bot.get_user(config.OWNER_ID) or await bot.fetch_user(config.OWNER_ID)
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
