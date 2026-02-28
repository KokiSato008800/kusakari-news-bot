"""
LINEグループIDを取得するための一時的なWebhookサーバー（標準ライブラリのみ）

使い方:
1. python3 get_group_id.py を実行
2. 別ターミナルで ngrok http 8080 を実行
3. LINE DevelopersコンソールでWebhook URLを設定（ngrokのURL + /callback）
4. Botをグループに招待 or グループでメッセージ送信
5. ターミナルにグループIDが表示される
"""

import json
from http.server import HTTPServer, BaseHTTPRequestHandler


class WebhookHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Webhook server is running")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK")

        try:
            data = json.loads(body)
            events = data.get("events", [])
            for event in events:
                source = event.get("source", {})
                source_type = source.get("type", "")
                event_type = event.get("type", "")

                if source_type == "group":
                    gid = source.get("groupId", "")
                    uid = source.get("userId", "")
                    print("\n" + "=" * 60)
                    print(f"  グループID: {gid}")
                    print(f"  ユーザーID: {uid}")
                    print(f"  イベント: {event_type}")
                    print("=" * 60)
                    print(f"\n  .env に設定: LINE_TO_ID={gid}\n")

                elif source_type == "user":
                    uid = source.get("userId", "")
                    print("\n" + "=" * 60)
                    print(f"  ユーザーID: {uid}")
                    print(f"  イベント: {event_type}")
                    print("=" * 60)
                    print(f"\n  .env に設定: LINE_TO_ID={uid}\n")
        except Exception as e:
            print(f"解析エラー: {e}")

    def log_message(self, format, *args):
        pass  # アクセスログを抑制


if __name__ == "__main__":
    port = 8080
    server = HTTPServer(("", port), WebhookHandler)
    print(f"\nWebhookサーバー起動: http://localhost:{port}")
    print("=" * 50)
    print("手順:")
    print("  1. 別ターミナルで: ngrok http 8080")
    print("  2. ngrokのURLをコピー")
    print("  3. LINE Developers → Messaging API設定")
    print("     → Webhook URL: ngrokのURL/callback")
    print("     → Webhookの利用: ON")
    print("  4. Botをグループに招待 or メッセージ送信")
    print("=" * 50 + "\n")
    server.serve_forever()
