"""
草刈りロボットニュース自動LINE配信パイプライン

4つのAIエージェントが連携してニュースを収集・評価・要約・配信します。
- 収集Agent (Haiku): Google News RSSからニュース取得
- 評価Agent (Haiku): 関連性でフィルタリング
- 要約Agent (Haiku): LINE向けメッセージ作成
- 配信Agent (Haiku): LINE Messaging APIで送信
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


# ========== Google News URL → 実際の記事URL変換 ==========
def resolve_google_news_url(google_url: str) -> str:
    """Google NewsのリダイレクトURLを実際の記事URLに変換する"""
    try:
        result = new_decoderv1(google_url)
        if result and result.get("status"):
            return result["decoded_url"]
    except Exception as e:
        logger.warning(f"URL変換失敗: {e}")
    return google_url  # 失敗時は元のURLをそのまま返す


# ========== ツール定義 ==========
TOOLS_RSS = [
    {
        "name": "fetch_rss_news",
        "description": "Google News RSSから指定キーワードの日本語ニュースを取得する。記事URLは実際のリンク先に変換済み。",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "検索クエリ（日本語可）"},
                "max_items": {"type": "integer", "description": "最大取得件数（デフォルト15）"},
            },
            "required": ["query"],
        },
    }
]

TOOLS_LINE = [
    {
        "name": "send_line_message",
        "description": "LINE Messaging APIでメッセージを送信する（個人・グループ両対応）",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "送信するメッセージ本文"},
            },
            "required": ["message"],
        },
    }
]


# ========== ツール実行 ==========
def execute_tool(name: str, input_data: dict) -> str:
    if name == "fetch_rss_news":
        query = quote(input_data["query"])
        max_items = input_data.get("max_items", 15)
        url = f"https://news.google.com/rss/search?q={query}&hl=ja&gl=JP&ceid=JP:ja"
        feed = feedparser.parse(url)
        articles = []
        for entry in feed.entries[:max_items]:
            # Google NewsのリダイレクトURLを実際の記事URLに変換
            raw_link = entry.get("link", "")
            real_link = resolve_google_news_url(raw_link)
            articles.append({
                "title": entry.get("title", ""),
                "link": real_link,
                "summary": entry.get("summary", "")[:300],
                "published": entry.get("published", ""),
            })
        logger.info(f"RSS取得完了: {len(articles)}件（URL変換済み）")
        return json.dumps(articles, ensure_ascii=False)

    elif name == "send_line_message":
        token = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
        to_id = os.environ["LINE_TO_ID"]  # ユーザーID(U...) or グループID(C...)
        message_text = input_data["message"]

        # LINEメッセージは5000文字制限
        if len(message_text) > 5000:
            message_text = message_text[:4990] + "\n..."

        resp = requests.post(
            "https://api.line.me/v2/bot/message/push",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "to": to_id,
                "messages": [{"type": "text", "text": message_text}],
            },
        )
        if resp.status_code == 200:
            return "LINE送信成功"
        else:
            return f"LINE送信エラー: status={resp.status_code}, body={resp.text}"

    return f"未知のツール: {name}"


# ========== エージェントループ ==========
def run_agent(
    system_prompt: str,
    user_prompt: str,
    tools: list | None = None,
    model: str = "claude-haiku-4-5-20251001",
    max_iter: int = 5,
) -> str:
    messages = [{"role": "user", "content": user_prompt}]

    for _ in range(max_iter):
        kwargs = {
            "model": model,
            "max_tokens": 4096,
            "system": system_prompt,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools

        response = client.messages.create(**kwargs)

        if response.stop_reason != "tool_use":
            return "".join(b.text for b in response.content if hasattr(b, "text"))

        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                logger.info(f"ツール実行: {block.name}")
                result = execute_tool(block.name, block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })
        messages.append({"role": "user", "content": tool_results})

    return "最大イテレーション到達"


# ========== メインパイプライン ==========
def main():
    today = datetime.now().strftime("%Y年%m月%d日")

    # === Step 1: 収集Agent ===
    logger.info("=" * 50)
    logger.info("[Step 1/4] ニュース収集Agent起動")
    raw_news = run_agent(
        system_prompt=(
            "あなたはニュース収集エージェントです。"
            "fetch_rss_newsツールを使い、草刈りロボット・草刈り自動化に関するニュースを収集してください。"
            "必ずツールを呼び出して結果を返してください。"
        ),
        user_prompt=(
            f"今日は{today}です。以下のキーワードでニュースを取得してください：\n"
            "「草刈りロボット OR 自動草刈り OR ロボット芝刈り機 OR 除草ロボット OR 草刈り機 ロボット」\n"
            "最大15件取得してください。"
        ),
        tools=TOOLS_RSS,
        model="claude-haiku-4-5-20251001",
    )
    logger.info("収集Agent完了")

    # === Step 2: 評価Agent ===
    logger.info("=" * 50)
    logger.info("[Step 2/4] 評価Agent起動")
    filtered_news = run_agent(
        system_prompt=(
            "あなたはニュース評価エージェントです。\n"
            "草刈りロボット・草刈り自動化・自律走行型草刈り機・ロボット芝刈り機に直接関連する記事のみを選んでください。\n"
            "関連性の低い記事（単なる農業ニュース、草刈りと無関係なロボットニュース等）は除外してください。\n"
            "関連性の高い順に最大5件に絞り、以下の形式で出力してください：\n\n"
            "タイトル: ...\nURL: ...\n概要: ...\n関連度: 5段階\n\n"
            "【重要】URLは提供されたlinkフィールドの値をそのまま正確に出力してください。改変しないでください。\n"
            "関連する記事が1件もない場合は「関連ニュースなし」と出力してください。"
        ),
        user_prompt=f"以下の記事を評価・フィルタリングしてください：\n\n{raw_news}",
        model="claude-haiku-4-5-20251001",
    )
    logger.info("評価Agent完了")

    # === Step 3: 要約Agent ===
    logger.info("=" * 50)
    logger.info("[Step 3/4] 要約Agent起動")
    summary = run_agent(
        system_prompt=(
            f"あなたはプロのニュース編集者です。\n"
            f"草刈りロボット関連ニュースをLINEグループ配信用に要約してください。\n\n"
            f"【出力フォーマット】\n"
            f"🌿 草刈りロボット最新ニュース\n"
            f"（{today}）\n\n"
            f"■ タイトル\n"
            f"要約（1-2文、簡潔に）\n"
            f"🔗 URL\n\n"
            f"---\n"
            f"（記事ごとに繰り返し）\n\n"
            f"【重要ルール】\n"
            f"- URLは提供された元のURLをそのまま正確にコピーしてください\n"
            f"- URLを省略・改変・短縮しないでください\n"
            f"- URLは必ず https:// から始まる完全な形で記載してください\n"
            f"- LINEでタップしてそのまま記事が読めるようにしてください\n\n"
            f"※ 関連ニュースがない場合は以下を出力：\n"
            f"🌿 草刈りロボット最新ニュース\n"
            f"（{today}）\n\n"
            f"本日の草刈りロボット関連ニュースはありませんでした。\n"
            f"明日もチェックします！"
        ),
        user_prompt=f"以下のフィルタリング済みニュースを要約してください：\n\n{filtered_news}",
        model="claude-haiku-4-5-20251001",
    )
    logger.info("要約Agent完了")

    # === Step 4: 配信Agent ===
    logger.info("=" * 50)
    logger.info("[Step 4/4] 配信Agent起動")
    result = run_agent(
        system_prompt=(
            "あなたはメッセージ配信エージェントです。"
            "渡されたメッセージをそのままsend_line_messageツールで送信してください。"
            "メッセージ内容は一切変更しないでください。URLも含めそのまま送信してください。"
        ),
        user_prompt=f"以下のメッセージをLINEに送信してください：\n\n{summary}",
        tools=TOOLS_LINE,
        model="claude-haiku-4-5-20251001",
    )

    logger.info("=" * 50)
    logger.info(f"パイプライン完了: {result}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error(f"パイプラインエラー: {e}", exc_info=True)
        sys.exit(1)
