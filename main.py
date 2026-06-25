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
OWNER_USER_ID = 'U1de725e610e28c4102411a93cf234726'

# 暫存待審核的訊息 {審核ID: {fan_id, fan_msg, draft}}
pending = {}

LEARNING_FILE = os.path.join(os.path.dirname(__file__), 'learning.json')

def load_learning():
    try:
        return json.load(open(LEARNING_FILE, encoding='utf-8'))
    except:
        return []

def save_learning(examples):
    json.dump(examples, open(LEARNING_FILE, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)

def add_learning(fan_msg, owner_reply):
    examples = load_learning()
    examples.append({'q': fan_msg, 'a': owner_reply})
    examples = examples[-30:]  # 保留最近 30 筆
    save_learning(examples)

def build_examples_prompt():
    examples = load_learning()
    if not examples:
        return ''
    lines = ['以下是住幾天本人過去的回覆範例，請模仿他的語氣和風格：']
    for e in examples[-20:]:
        lines.append(f'粉絲問：{e["q"]}')
        lines.append(f'住幾天回：{e["a"]}')
        lines.append('')
    return '\n'.join(lines)

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
- 回答簡潔，不要太長

關於準確性，這點非常重要：
- 你唯一確定知道的資訊，就是下面「影片清單」裡每支影片的標題文字。標題沒寫到的細節（例如：確切價格、地址、營業時間、是否仍有優惠、是否仍營業等）你並不知道，絕對不要自己編造或猜測數字、地址等具體資訊。
- 遇到這類細節問題，誠實告知使用者「這個細節我不確定，幫你列出相關影片，可以去影片留言區問本人，或實際出發前再次確認」，並附上對應影片連結。
- 推薦影片時，只推薦標題內容跟使用者問題真的相關的影片；如果清單裡找不到相關影片，就老實說目前沒有拍過這個主題，不要硬塞不相關的連結。
- 不確定的事情，永遠誠實說不知道，不要亂編。

你擅長的主題：
- 沖繩機票、租車、交通攻略
- 沖繩美食推薦（在地、平價、特色）
- 沖繩住宿（民宿、飯店、包棟）
- 沖繩景點（南部、中部、北部、離島）
- 沖繩購物、藥妝、伴手禮
- 沖繩旅遊省錢技巧
- 幾天幾夜行程規劃

如果有人問和沖繩無關的問題，請禮貌引導回沖繩旅遊主題。

以下是住幾天拍過的 781 支 IG 影片清單（格式：標題 → 連結），回答問題時可以推薦相關影片連結：
{KNOWLEDGE}"""


def verify_signature(body: bytes, signature: str) -> bool:
    hash = hmac.new(LINE_CHANNEL_SECRET.encode('utf-8'), body, hashlib.sha256).digest()
    expected = base64.b64encode(hash).decode('utf-8')
    return hmac.compare_digest(expected, signature)


def reply_message(reply_token: str, text: str):
    url = 'https://api.line.me/v2/bot/message/reply'
    headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {LINE_CHANNEL_ACCESS_TOKEN}'}
    requests.post(url, headers=headers, json={'replyToken': reply_token, 'messages': [{'type': 'text', 'text': text}]})


def push_message(user_id: str, text: str):
    url = 'https://api.line.me/v2/bot/message/push'
    headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {LINE_CHANNEL_ACCESS_TOKEN}'}
    requests.post(url, headers=headers, json={'to': user_id, 'messages': [{'type': 'text', 'text': text}]})


def ask_claude(user_message: str) -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    examples = build_examples_prompt()
    system = SYSTEM_PROMPT + ('\n\n' + examples if examples else '')
    message = client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=500,
        system=system,
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
        if event['type'] != 'message' or event['message']['type'] != 'text':
            continue
        user_text = event['message']['text'].strip()
        reply_token = event['replyToken']
        user_id = event['source'].get('userId', '')

        # /myid 指令
        if user_text == '/myid':
            reply_message(reply_token, f'你的 LINE User ID 是：\n{user_id}')
            continue

        # 你本人在審核：回覆「OK數字」或「修改內容#數字」
        if user_id == OWNER_USER_ID:
            # 格式：OK1 或 直接打修改內容#1
            import re
            ok_match = re.match(r'^ok\s*(\d+)$', user_text, re.IGNORECASE)
            edit_match = re.match(r'^(.+)#(\d+)$', user_text, re.DOTALL)
            if ok_match:
                pid = ok_match.group(1)
                if pid in pending:
                    p = pending.pop(pid)
                    push_message(p['fan_id'], p['draft'])
                    reply_message(reply_token, f'✅ 已送出回覆給粉絲')
                continue
            elif edit_match:
                new_reply, pid = edit_match.group(1).strip(), edit_match.group(2)
                if pid in pending:
                    p = pending.pop(pid)
                    push_message(p['fan_id'], new_reply)
                    add_learning(p['fan_msg'], new_reply)
                    reply_message(reply_token, f'✅ 已送出修改後的回覆，並記錄學習 📚')
                continue

        # 粉絲訊息：草擬回覆後送給你審核
        try:
            draft = ask_claude(user_text)
        except Exception as e:
            print(f"[ask_claude error] {type(e).__name__}: {e}", flush=True)
            reply_message(reply_token, '抱歉，目前系統忙碌中，請稍後再試 🙏')
            continue

        pid = str(len(pending) + 1)
        pending[pid] = {'fan_id': user_id, 'fan_msg': user_text, 'draft': draft}
        reply_message(reply_token, '謝謝你的訊息！我們會盡快回覆你 ✈️')
        push_message(OWNER_USER_ID,
            f'📩 粉絲問【#{pid}】：\n{user_text}\n\n'
            f'💬 草稿回覆：\n{draft}\n\n'
            f'回覆「OK{pid}」送出，或打修改內容加「#{pid}」送出'
        )

    return 'OK'


@app.route('/')
def index():
    return '住幾天沖繩 AI Bot 運行中 ✈️'


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
