    async def check_and_update_rate_limit(self, user_id: int) -> tuple[bool, Optional[str]]:
        """レート制限をチェックし、問題なければ履歴を更新する。制限にかかった場合はエラ文字列を返す。"""
        config = self.bot.config
        now = datetime.now(JST).timestamp()

        # 履歴の読み込みとクリーンアップ (24時間以上前を削除)
        history = self.scan_history.get(user_id, [])
        history = [ts for ts in history if now - ts < 86400]

        # 1. 24時間制限チェック
        if len(history) >= config.RATELIMIT_24H:
            # 履歴がある場合のみ残り時間を計算
            if history:
                wait_sec = history[0] + 86400 - now
                hours = int(wait_sec // 3600)
                minutes = int((wait_sec % 3600) // 60)
                return (False, (
                    "✖エラーが発生しました：エラーコード006\n"
                    "短期間に大量のリクエストを検知しました。\n"
                    f"このbotは過去24時間で{config.RATELIMIT_24H}件まで画像を処理することができます。\n"
                    f"{hours}時間{minutes}分後に再度お試しください。"
                ))
            else:
                return (False, "✖エラーが発生しました：エラーコード006\n現在、画像スキャンは制限されています。")

        # 2. 1時間制限チェック
        one_hour_history = [ts for ts in history if now - ts < 3600]
        if len(one_hour_history) >= config.RATELIMIT_1H:
            if one_hour_history:
                wait_sec = one_hour_history[0] + 3600 - now
                minutes = int(wait_sec // 60)
                if minutes < 1: minutes = 1
                return (False, (
                    "✖エラーが発生しました：エラーコード005\n"
                    "短期間に大量のリクエストを検知しました。\n"
                    f"このbotは過去1時間で{config.RATELIMIT_1H}件まで画像を処理することができます。\n"
                    f"また、過去24時間で{config.RATELIMIT_24H}件まで画像を処理することができます。\n"
                    f"{minutes}分後に再度お試しください。"
                ))
            else:
                 return (False, "✖エラーが発生しました：エラーコード005\n現在、画像スキャンは1時間制限されています。")

        # 履歴を更新して保存
        history.append(now)
        self.scan_history[user_id] = history
        self.save_scan_history()
        
        return (True, None)
