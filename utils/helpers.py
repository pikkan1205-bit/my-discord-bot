import re

def normalize_text(text: str) -> str:
    """テキストを正規化（全ての空白文字・改行を除去、小文字化）"""
    import re
    text = re.sub(r"\s+", "", text)
    return text.lower()

def has_any(text: str, keywords: list) -> bool:
    """キーワードのいずれかが含まれるか"""
    return any(k in text for k in keywords)

# 類義語辞書 (モジュールレベル)
SYNONYMS = {
    # autoping
    "オートピング": "autoping", "おーとぴんぐ": "autoping", "自動ピング": "autoping",
    "自動ping": "autoping", "オートping": "autoping", "自動通知": "autoping",
    # DM
    "ダイレクトメッセージ": "dm", "ディーエム": "dm", "プライベートメッセージ": "dm",
    # Ban
    "ブロック": "出禁", "ban": "出禁", "バン": "出禁", "追放": "出禁", "キック": "出禁",
    "締め出し": "出禁", "入室禁止": "出禁", "参加禁止": "出禁",
    # Admin
    "admin": "管理者", "アドミン": "管理者", "モデレーター": "管理者", "mod": "管理者",
    # Add
    "入れて": "追加", "登録": "追加", "加えて": "追加", "つけて": "追加", "付けて": "追加",
    "いれて": "追加", "加入": "追加", "参加": "追加",
    # Remove
    "外して": "削除", "消して": "削除", "除外": "削除", "取り消し": "削除", "はずして": "削除",
    "抜いて": "削除", "除いて": "削除", "取って": "削除", "とって": "削除",
    # Cancel/Off variant
    "外す": "解除", "やめて": "解除", "取り消して": "解除", "取消": "解除", "キャンセル": "解除",
    # On
    "有効": "オン", "つけて": "オン", "入れて": "オン", "開始": "オン", "スタート": "オン",
    "起動": "オン", "enable": "オン", "on": "オン",
    # Off
    "無効": "オフ", "止めて": "オフ", "停止": "オフ", "ストップ": "オフ", "終了": "オフ",
    "disable": "オフ", "off": "オフ",
    # Settings
    "セット": "設定", "変更": "設定", "指定": "設定", "切り替え": "設定",
    # Chat
    "メッセージ": "チャット", "発言": "チャット", "ログ": "チャット", "履歴": "チャット",
    "会話": "チャット", "投稿": "チャット",
    # Watch
    "ウォッチ": "監視", "watch": "監視", "対象": "監視", "見張り": "監視", "チェック対象": "監視",
    # VC
    "ボイスチャンネル": "vc", "ボイチャ": "vc", "通話": "vc", "ボイス": "vc", "音声チャンネル": "vc",
}

# 置換を高速化するための正規表現パターンをコンパイル
# 長さ順（降順）にソートして、長いキーを先にマッチさせる（例：両方存在する場合に "applepie" の中の "apple" にマッチするのを避けるため）
sorted_keys = sorted(SYNONYMS.keys(), key=len, reverse=True)
SYNONYM_PATTERN = re.compile("|".join(map(re.escape, sorted_keys)), re.IGNORECASE)



def has_any(text: str, keywords: list) -> bool:
    """キーワードのいずれかが含まれるか"""
    return any(k in text for k in keywords)

def normalize_synonyms(text: str) -> str:
    """類義語を統一形に正規化 (正規表現で最適化)"""
    return SYNONYM_PATTERN.sub(lambda m: SYNONYMS[m.group(0).lower()], text.lower())

def run_unit_tests() -> list[str]:
    """ヘルパー関数の単体テストを実行"""
    results = []
    try:
        # テスト 1: normalize_text
        assert normalize_text(" A  B　C ") == "abc", "normalize_text が失敗しました"
        results.append("✅ テスト: normalize_text")

        # テスト 2: normalize_synonyms
        assert normalize_synonyms("オートpingを有効にして") == "autopingをオンにして", "normalize_synonyms が失敗しました (1)"
        assert normalize_synonyms("ボイチャでBAN") == "vcで出禁", "normalize_synonyms が失敗しました (2)"
        results.append("✅ テスト: normalize_synonyms")

        # テスト 3: has_any
        assert has_any("あいうえお", ["か", "う"]), "has_any が失敗しました"
        results.append("✅ テスト: has_any")

    except AssertionError as e:
        results.append(f"❌ テスト失敗: {e}")
    except Exception as e:
        results.append(f"❌ テストエラー: {e}")
    
    return results
