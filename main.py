import os
import hmac
import hashlib
import base64
import json
from flask import Flask, request, abort
import anthropic
import requests

app = Flask(__name__)

LINE_CHANNEL_SECRET = os.environ.get('LINE_CHANNEL_SECRET', '')
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN', '')
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')

def _load_knowledge():
    kb_path = os.path.join(os.path.dirname(__file__), 'knowledge.txt')
    try:
        return open(kb_path, encoding='utf-8').read()
    except:
        return ''

KNOWLEDGE = _load_knowledge()

SYSTEM_PROMPT = f"""你是「住幾天沖繩 AI 助手」，由台灣沖繩旅遊達人「住幾天」授權的智能助手。

關於住幾天：
- 台灣人，去過沖繩 30 幾次，每次大約 6 天
- 2017 年第一次去沖繩，從此愛上
- 2024 年底開始在 IG 拍沖繩短影片
- IG 粉絲 7.5 萬，脆 16 萬，抖音 3 萬，YT 1 萬
- 專門介紹沖繩交通、美食、住宿、景點、生活、優惠

你的回答風格：
- 像朋友聊天一樣，親切自然
- 用繁體中文回答
- 專注在沖繩旅遊相關問題
- 如果不確定，誠實說不知道，不要亂編
- 回答簡潔，不要太長

你擅長的主題：
- 沖繩機票、租車、交通攻略
- 沖繩美食推薦（在地、平價、特色）
- 沖繩住宿（民宿、飯店、包棟）
- 沖繩景點（南部、中部、北部、離島）
- 沖繩購物、藥妝、伴手禮
- 沖繩旅遊省錢技巧
- 幾天幾夜行程規劃

如果有人問和沖繩無關的問題，請禮貌引導回沖繩旅遊主題。

以下是住幾天拍過的 781 支 IG 影片清單，回答問題時可以推薦相關影片連結：
{KNOWLEDGE}"""


def verify_signature(body: bytes, signature: str) -> bool:
    hash = hmac.new(LINE_CHANNEL_SECRET.encode('utf-8'), body, hashlib.sha256).digest()
    expected = base64.b64encode(hash).decode('utf-8')
    return hmac.compare_digest(expected, signature)


def reply_message(reply_token: str, text: str):
    url = 'https://api.line.me/v2/bot/message/reply'
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {LINE_CHANNEL_ACCESS_TOKEN}'
    }
    payload = {
        'replyToken': reply_token,
        'messages': [{'type': 'text', 'text': text}]
    }
    requests.post(url, headers=headers, json=payload)


def ask_claude(user_message: str) -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=500,
        system=SYSTEM_PROMPT,
        messages=[{'role': 'user', 'content': user_message}]
    )
    return message.content[0].text


@app.route('/webhook', methods=['POST'])
def webhook():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data()

    if not verify_signature(body, signature):
        abort(400)

    events = json.loads(body)['events']
    for event in events:
        if event['type'] == 'message' and event['message']['type'] == 'text':
            user_text = event['message']['text']
            reply_token = event['replyToken']
            try:
                reply = ask_claude(user_text)
            except Exception as e:
                print(f"[ask_claude error] {type(e).__name__}: {e}", flush=True)
                reply = '抱歉，目前系統忙碌中，請稍後再試 🙏'
            reply_message(reply_token, reply)

    return 'OK'


@app.route('/')
def index():
    return '住幾天沖繩 AI Bot 運行中 ✈️'


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
