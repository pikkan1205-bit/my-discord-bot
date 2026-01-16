import json
import os
import shutil
from typing import Set, Dict, List
from datetime import datetime, timezone, timedelta

# 日本時間のタイムゾーン
JST = timezone(timedelta(hours=9))

class ConfigManager:
    def __init__(self):
        # Constants
        self.CONFIG_FILE = "vcblock_config.json"
        self.PLAYER_NAMES_FILE = "player_names.json"
        
        # Admin Mode Settings
        self.ADMIN_MODE_TIMEOUT = 120  # 2分（秒）
        
        # State
        self.OWNER_ID = int(os.environ.get("OWNER_ID", "0"))
        self.ADMIN_IDS: Set[int] = set()
        self.BLOCKED_USERS: Set[int] = set()
        self.TARGET_VC_IDS: Set[int] = set()
        self.vc_block_enabled: bool = True
        self.AUTO_PING_CHANNEL_ID: int = int(os.environ.get("AUTO_PING_CHANNEL_ID", "0"))
        
        # BrawlStars Data
        self.player_names: Dict = {}
        self.player_register_count: Dict = {}
        
        # Admin Mode State {user_id: timestamp}
        self.admin_mode_users: Dict = {}
        
        # Initial Load
        self.load_config()
        self.load_player_names()
        self.load_env_initials()
        
        # Validation
        self.validate_settings()

    def load_env_initials(self):
        """Load initial values from environment variables if set"""
        # 初期対象ユーザー（カンマ区切りで複数指定可能）
        blocked_str = os.environ.get("INITIAL_BLOCKED_USERS", "")
        if blocked_str and not self.BLOCKED_USERS: # only if empty
            try:
                self.BLOCKED_USERS = set(int(x.strip()) for x in blocked_str.split(",") if x.strip())
                print(f"📋 環境変数から初期ブロックユーザー読み込み: {len(self.BLOCKED_USERS)}人")
            except ValueError:
                pass

        # 初期対象VC（カンマ区切りで複数指定可能）
        vc_str = os.environ.get("INITIAL_TARGET_VCS", "")
        if vc_str and not self.TARGET_VC_IDS:
             try:
                self.TARGET_VC_IDS = set(int(x.strip()) for x in vc_str.split(",") if x.strip())
                print(f"📋 環境変数から初期対象VC読み込み: {len(self.TARGET_VC_IDS)}個")
             except ValueError:
                pass


    def save_config(self):
        """設定をJSONファイルに保存"""
        config = {
            "admin_ids": list(self.ADMIN_IDS),
            "blocked_users": list(self.BLOCKED_USERS),
            "target_vc_ids": list(self.TARGET_VC_IDS),
            "vc_block_enabled": self.vc_block_enabled,
            "auto_ping_channel_id": self.AUTO_PING_CHANNEL_ID
        }
        try:
            temp_file = f"{self.CONFIG_FILE}.tmp"
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_file, self.CONFIG_FILE)
            print(f"💾 設定を保存しました")
        except Exception as e:
            print(f"❌ 設定保存エラー: {e}")

    def load_config(self):
        """JSONファイルから設定を読み込む"""
        try:
            if os.path.exists(self.CONFIG_FILE):
                with open(self.CONFIG_FILE, "r", encoding="utf-8") as f:
                    config = json.load(f)
                self.ADMIN_IDS = set(config.get("admin_ids", []))
                self.BLOCKED_USERS = set(config.get("blocked_users", []))
                self.TARGET_VC_IDS = set(config.get("target_vc_ids", []))
                self.vc_block_enabled = config.get("vc_block_enabled", True)
                self.AUTO_PING_CHANNEL_ID = config.get("auto_ping_channel_id", 0)
                print(f"📂 設定を読み込みました")
            else:
                print(f"⚠️ 設定ファイルが見つかりません。初期値を使用します")
                self.save_config()
        except json.JSONDecodeError as e:
            print(f"❌ 設定ファイルが破損しています: {e}")
            if os.path.exists(self.CONFIG_FILE):
                shutil.copy(self.CONFIG_FILE, f"{self.CONFIG_FILE}.backup")
            self.save_config()
        except Exception as e:
            print(f"❌ 設定の読み込みに失敗しました: {e}")

    def save_player_names(self):
        """プレイヤー名をJSONに保存"""
        try:
            data = {
                'players': self.player_names,
                'counts': self.player_register_count
            }
            temp_file = f"{self.PLAYER_NAMES_FILE}.tmp"
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_file, self.PLAYER_NAMES_FILE)
            print(f"💾 プレイヤー名を保存しました")
        except Exception as e:
            print(f"❌ プレイヤー名保存エラー: {e}")

    def load_player_names(self):
        """プレイヤー名をJSONから読み込み"""
        try:
            if os.path.exists(self.PLAYER_NAMES_FILE):
                with open(self.PLAYER_NAMES_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                
                if isinstance(data, dict) and 'players' in data:
                    self.player_names = data.get('players', {})
                    self.player_register_count = data.get('counts', {})
                else:
                    self.player_names = data
                    self.player_register_count = {}
                
                print(f"📂 プレイヤー名を読み込みました: {len(self.player_names)}人")
            else:
                self.player_names = {}
                self.player_register_count = {}
        except Exception as e:
            print(f"❌ プレイヤー名読み込みエラー: {e}")
            self.player_names = {}
            self.player_register_count = {}

    def validate_settings(self):
        """設定項目の整合性チェック"""
        if self.OWNER_ID == 0:
            print("⚠️ 警告: OWNER_ID が設定されていません。環境変数を確認してください。")
        if not self.CONFIG_FILE:
             print("❌ エラー: CONFIG_FILE が定義されていません。")
             
    def is_authorized(self, user_id: int) -> bool:
        """ユーザーがオーナーまたは管理者かチェック"""
        return user_id == self.OWNER_ID or user_id in self.ADMIN_IDS

    # ====== 管理者モード管理 ======
    def is_in_admin_mode(self, user_id: int) -> bool:
        """ユーザーが管理者モード中かチェック"""
        if user_id not in self.admin_mode_users:
            return False
        last_activity = self.admin_mode_users[user_id]
        if (datetime.now(JST) - last_activity).total_seconds() > self.ADMIN_MODE_TIMEOUT:
            del self.admin_mode_users[user_id]
            return False
        return True

    def enter_admin_mode(self, user_id: int):
        """管理者モードに入る"""
        self.admin_mode_users[user_id] = datetime.now(JST)

    def update_admin_mode(self, user_id: int):
        """管理者モードのタイムスタンプを更新"""
        self.admin_mode_users[user_id] = datetime.now(JST)

    def exit_admin_mode(self, user_id: int):
        """管理者モードから抜ける"""
        if user_id in self.admin_mode_users:
            del self.admin_mode_users[user_id]
