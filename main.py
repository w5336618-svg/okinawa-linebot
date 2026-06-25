import os
import hmac
import hashlib
import base64
import json
import re
import threading
from datetime import datetime, timezone, timedelta
from flask import Flask, request, abort
import anthropic
import requests

app = Flask(__name__)

LINE_CHANNEL_SECRET = os.environ.get('LINE_CHANNEL_SECRET', '')
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN', '')
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
OWNER_USER_ID = 'U1de725e610e28c4102411a93cf234726'
TWN = timezone(timedelta(hours=8))

# 今日對話紀錄 [{id, fan_id, fan_msg, bot_reply}]
daily_log = []
log_date = datetime.now(TWN).date()
log_lock = threading.Lock()

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
    save_learning(examples[-30:])


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

關於準確性（非常重要）：
- 標題沒寫到的細節（價格、地址、營業時間）你不知道，絕對不要編造
- 遇到細節問題，請誠實說不確定，並推薦相關影片讓粉絲自行確認
- 不確定的事情，永遠誠實說不知道

你擅長的主題：
- 沖繩機票、租車、交通攻略
- 沖繩美食推薦（在地、平價、特色）
- 沖繩住宿（民宿、飯店、包棟）
- 沖繩景點（南部、中部、北部、離島）
- 沖繩購物、藥妝、伴手禮
- 沖繩旅遊省錢技巧
- 幾天幾夜行程規劃

如果有人問和沖繩無關的問題，請禮貌引導回沖繩旅遊主題。

以下是住幾天拍過的 781 支 IG 影片清單（格式：標題 → 連結）：
{KNOWLEDGE}"""


def verify_signature(body: bytes, signature: str) -> bool:
    h = hmac.new(LINE_CHANNEL_SECRET.encode('utf-8'), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(h).decode('utf-8'), signature)


def reply_message(reply_token: str, text: str):
    requests.post('https://api.line.me/v2/bot/message/reply',
        headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {LINE_CHANNEL_ACCESS_TOKEN}'},
        json={'replyToken': reply_token, 'messages': [{'type': 'text', 'text': text}]})


def push_message(user_id: str, text: str):
    requests.post('https://api.line.me/v2/bot/message/push',
        headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {LINE_CHANNEL_ACCESS_TOKEN}'},
        json={'to': user_id, 'messages': [{'type': 'text', 'text': text}]})


def ask_claude(user_message: str) -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    examples = build_examples_prompt()
    system = SYSTEM_PROMPT + ('\n\n' + examples if examples else '')
    msg = client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=500,
        system=system,
        messages=[{'role': 'user', 'content': user_message}]
    )
    return msg.content[0].text


def get_summary_text():
    with log_lock:
        if not daily_log:
            return '今天還沒有粉絲傳訊息 😊'
        lines = [f'📊 今日回覆摘要（共 {len(daily_log)} 則）\n']
        for entry in daily_log[-20:]:
            lines.append(f'【#{entry["id"]}】粉絲：{entry["fan_msg"][:30]}')
            lines.append(f'Bot：{entry["bot_reply"][:50]}')
            lines.append(f'→ 如需補充請回「補充內容#{entry["id"]}」\n')
        return '\n'.join(lines)


def reset_daily_log():
    global daily_log, log_date
    with log_lock:
        daily_log = []
        log_date = datetime.now(TWN).date()


def schedule_daily_summary():
    now = datetime.now(TWN)
    tomorrow_6am = now.replace(hour=6, minute=0, second=0, microsecond=0) + timedelta(days=1)
    if now.hour < 6:
        tomorrow_6am -= timedelta(days=1)
    delay = (tomorrow_6am - now).total_seconds()

    def send_and_reschedule():
        push_message(OWNER_USER_ID, get_summary_text())
        reset_daily_log()
        schedule_daily_summary()

    t = threading.Timer(delay, send_and_reschedule)
    t.daemon = True
    t.start()


schedule_daily_summary()


@app.route('/webhook', methods=['POST'])
def webhook():
    global daily_log
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
            reply_message(reply_token, f'你的 LINE User ID：\n{user_id}')
            continue

        # 你本人的指令
        if user_id == OWNER_USER_ID:
            if user_text == '/summary':
                reply_message(reply_token, get_summary_text())
                continue
            # 補充修正：補充內容#1
            edit_match = re.match(r'^(.+)#(\d+)$', user_text, re.DOTALL)
            if edit_match:
                new_reply = edit_match.group(1).strip()
                pid = edit_match.group(2)
                with log_lock:
                    entry = next((e for e in daily_log if str(e['id']) == pid), None)
                if entry:
                    push_message(entry['fan_id'], f'補充說明：{new_reply}')
                    add_learning(entry['fan_msg'], new_reply)
                    reply_message(reply_token, f'✅ 已補充說明給粉絲，並記錄學習 📚')
                else:
                    reply_message(reply_token, f'找不到 #{pid}')
                continue

        # 粉絲訊息：直接回覆，記錄到今日 log
        try:
            bot_reply = ask_claude(user_text)
        except Exception as e:
            print(f"[error] {e}", flush=True)
            reply_message(reply_token, '抱歉，目前系統忙碌中，請稍後再試 🙏')
            continue

        reply_message(reply_token, bot_reply)

        with log_lock:
            pid = len(daily_log) + 1
            daily_log.append({'id': pid, 'fan_id': user_id, 'fan_msg': user_text, 'bot_reply': bot_reply})

    return 'OK'


@app.route('/')
def index():
    return '住幾天沖繩 AI Bot 運行中 ✈️'


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
