"""
草刈りロボットニュース自動LINE配信パイプライン

4つのステップでニュースを収集・評価・要約・配信:
- Step 1 (収集): Google News RSSから直接取得 + URL変換
- Step 2 (評価Agent): Claude Haikuで関連性フィルタリング
- Step 3 (要約Agent): Claude Haikuで要約メッセージ作成
- Step 4 (配信): LINE Messaging APIで直接送信
"""

import os
import json
import logging
import sys
import feedparser
import requests
import anthropic
from datetime import datetime
from urllib.parse import quote
from dotenv import load_dotenv
from googlenewsdecoder import new_decoderv1

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

client = anthropic.Anthropic()


# ========== Step 1: ニュース収集（直接実行） ==========
def fetch_news() -> list[dict]:
    """Google News RSSからニュースを取得し、URLを実記事に変換する"""
    query = quote("草刈りロボット OR 自動草刈り OR ロボット芝刈り機 OR 除草ロボット OR 草刈り機 ロボット")
    url = f"https://news.google.com/rss/search?q={query}&hl=ja&gl=JP&ceid=JP:ja"
    feed = feedparser.parse(url)

    articles = []
    for entry in feed.entries[:15]:
        raw_link = entry.get("link", "")
        try:
            result = new_decoderv1(raw_link)
            real_link = result["decoded_url"] if result and result.get("status") else raw_link
        except Exception:
            real_link = raw_link

        articles.append({
            "title": entry.get("title", ""),
            "link": real_link,
            "published": entry.get("published", ""),
        })

    logger.info(f"RSS取得完了: {len(articles)}件（URL変換済み）")
    return articles


# ========== Step 2: 評価Agent ==========
def evaluate_news(articles: list[dict]) -> list[dict]:
    """Claude Haikuで関連性を評価し、上位5件を返す"""
    articles_json = json.dumps(articles, ensure_ascii=False, indent=2)

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4096,
        system=(
            "あなたはニュース評価エージェントです。\n"
            "JSON形式の記事リストを受け取り、草刈りロボット・草刈り自動化・ロボット芝刈り機に"
            "直接関連する記事のみを選び、関連度の高い順に最大5件返してください。\n"
            "無関係な記事（一般的な農業ニュース等）は除外してください。\n\n"
            "【出力形式】必ず以下のJSON配列のみを出力してください。説明文は不要です：\n"
            '[{"title": "...", "link": "...", "relevance": 5}, ...]'
        ),
        messages=[{"role": "user", "content": f"以下の記事を評価してください：\n\n{articles_json}"}],
    )

    result_text = "".join(b.text for b in response.content if hasattr(b, "text"))

    # JSONを抽出
    try:
        # ```json ... ``` で囲まれている場合に対応
        import re
        json_match = re.search(r'\[.*\]', result_text, re.DOTALL)
        if json_match:
            evaluated = json.loads(json_match.group())
        else:
            evaluated = json.loads(result_text)
    except json.JSONDecodeError:
        logger.warning(f"評価結果のJSON解析失敗。元の記事を使用します。")
        logger.warning(f"Agent出力: {result_text[:500]}")
        return articles[:5]

    # 元の記事データからURLを確実にマッチさせる
    title_to_link = {a["title"]: a["link"] for a in articles}
    for item in evaluated:
        if item.get("title") in title_to_link:
            item["link"] = title_to_link[item["title"]]

    logger.info(f"評価完了: {len(evaluated)}件選出")
    return evaluated[:5]


# ========== Step 3: 要約Agent ==========
def summarize_news(articles: list[dict], today: str) -> str:
    """Claude Haikuで要約メッセージを作成する。URLは直接埋め込む。"""
    if not articles:
        return (
            f"🌿 草刈りロボット最新ニュース\n"
            f"（{today}）\n\n"
            f"本日の草刈りロボット関連ニュースはありませんでした。\n"
            f"明日もチェックします！"
        )

    # 記事データをテキスト形式で渡す（URLを明示的に含む）
    articles_text = ""
    for i, a in enumerate(articles, 1):
        articles_text += f"記事{i}:\n"
        articles_text += f"  タイトル: {a['title']}\n"
        articles_text += f"  URL: {a['link']}\n\n"

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4096,
        system=(
            f"あなたはプロのニュース編集者です。\n"
            f"記事情報を受け取り、LINEグループ向けの要約メッセージを作成してください。\n\n"
            f"【出力フォーマット（これに厳密に従ってください）】\n"
            f"🌿 草刈りロボット最新ニュース\n"
            f"（{today}）\n\n"
            f"■ 記事タイトル\n"
            f"タイトルから推測できる内容を1文で簡潔に説明\n"
            f"🔗 記事のURL\n\n"
            f"---\n\n"
            f"（次の記事...）\n\n"
            f"【絶対ルール】\n"
            f"- 各記事のURLは入力データの「URL:」の値をそのままコピーすること\n"
            f"- URLを1文字も変えないこと\n"
            f"- 🔗 の後にURLをそのまま置くこと（マークダウン記法は使わない）"
        ),
        messages=[{"role": "user", "content": f"以下の記事を要約してください：\n\n{articles_text}"}],
    )

    summary = "".join(b.text for b in response.content if hasattr(b, "text"))

    # フォールバック: AgentがURLを正しく含めなかった場合、手動で組み立てる
    for a in articles:
        if a["link"] not in summary:
            logger.warning(f"URLが要約に含まれていません。手動組み立てに切り替えます。")
            return build_message_manually(articles, today)

    logger.info("要約完了")
    return summary


def build_message_manually(articles: list[dict], today: str) -> str:
    """URLが欠落した場合のフォールバック: 手動でメッセージを組み立てる"""
    logger.info("手動メッセージ組み立て実行")
    lines = [f"🌿 草刈りロボット最新ニュース", f"（{today}）", ""]
    for a in articles:
        title = a["title"].split(" - ")[0] if " - " in a["title"] else a["title"]
        source = a["title"].split(" - ")[-1] if " - " in a["title"] else ""
        lines.append(f"■ {title}")
        if source:
            lines.append(f"（{source}）")
        lines.append(f"🔗 {a['link']}")
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines).rstrip("\n---\n")


# ========== Step 4: LINE送信（直接実行） ==========
def send_to_line(message: str) -> bool:
    """LINE Messaging APIでプッシュメッセージを送信する"""
    token = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
    to_id = os.environ["LINE_TO_ID"]

    if len(message) > 5000:
        message = message[:4990] + "\n..."

    resp = requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={
            "to": to_id,
            "messages": [{"type": "text", "text": message}],
        },
    )
    if resp.status_code == 200:
        logger.info("LINE送信成功")
        return True
    else:
        logger.error(f"LINE送信エラー: status={resp.status_code}, body={resp.text}")
        return False


# ========== メインパイプライン ==========
def main():
    today = datetime.now().strftime("%Y年%m月%d日")

    # Step 1: 収集
    logger.info("=" * 50)
    logger.info("[Step 1/4] ニュース収集")
    articles = fetch_news()

    # Step 2: 評価
    logger.info("=" * 50)
    logger.info("[Step 2/4] ニュース評価Agent")
    filtered = evaluate_news(articles)
    logger.info(f"選出記事: {[a['title'][:30] for a in filtered]}")

    # Step 3: 要約
    logger.info("=" * 50)
    logger.info("[Step 3/4] 要約Agent")
    message = summarize_news(filtered, today)
    logger.info(f"メッセージ長: {len(message)}文字")

    # Step 4: 配信
    logger.info("=" * 50)
    logger.info("[Step 4/4] LINE配信")
    send_to_line(message)

    logger.info("=" * 50)
    logger.info("パイプライン完了")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error(f"パイプラインエラー: {e}", exc_info=True)
        sys.exit(1)
