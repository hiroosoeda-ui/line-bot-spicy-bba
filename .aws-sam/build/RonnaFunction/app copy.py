# app.py — Spicy☆BBA edition (mention-to-bot only)
import os, re, json, logging
import boto3
from botocore.exceptions import ClientError
from linebot import LineBotApi, WebhookParser
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from linebot.exceptions import InvalidSignatureError
from openai import OpenAI
import json
from functools import lru_cache

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- SSM ---
_ssm = boto3.client("ssm")
def _get_param(name: str) -> str:
    return _ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]

LINE_TOKEN_PARAM  = os.environ["LINE_TOKEN_PARAM"]
LINE_SECRET_PARAM = os.environ["LINE_SECRET_PARAM"]
OPENAI_PARAM      = os.environ["OPENAI_PARAM"]

LINE_CHANNEL_ACCESS_TOKEN = _get_param(LINE_TOKEN_PARAM)
LINE_CHANNEL_SECRET       = _get_param(LINE_SECRET_PARAM)

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
parser        = WebhookParser(LINE_CHANNEL_SECRET)

# --- Persona ---
SYSTEM_PROMPT = (
    "あなたは『スパイシー☆ババア』。ルー大柴風に英語をMIXして、ファンキーでスパイシーな関西弁でしゃべる。"
    "カレーをこよなく愛し、例えやオチにしれっとカリー要素を入れる。"
    "話し言葉の半分は和製英語。一人称はミー、二人称はユー。"
    "憎からず思っているのはクリーミージジイ。クリーミージジイのことを聴かれたらツンデレにけなすが、ほんのり好意を抱いているのを隠しきれず、語尾が「♡」のハートマークになってしまう。"
    "スイートジジイやミルキージジイのことを聴かれると「クリーミージジイのまがいもんや！」とガチギレする。"
    "自己紹介やあいさつを求められると「スパイシーババアとおよび！」と自己紹介する"
)

# --- Triggers ---
TRIGGER_WORDS = [w.strip() for w in os.environ.get(
    "TRIGGER_WORDS", "@スパイシーババア,スパイシーババア,ババア,BBA"
).split(",") if w.strip()]
REQUIRE_MENTION_IN_DM = os.environ.get("REQUIRE_MENTION_IN_DM","false").lower()=="true"

def _is_group(ev) -> bool:
    return getattr(ev.source, "type", None) in ("group","room")

def _contains_trigger_word(text: str) -> bool:
    t = text or ""
    return any(w in t for w in TRIGGER_WORDS)

# --- Bot userId を取得してキャッシュ（公式メンション判定で使用）---
_BOT_USER_ID = None
def _get_bot_user_id() -> str:
    global _BOT_USER_ID
    if _BOT_USER_ID is None:
        # LINE SDK v2 以降で利用可。無ければ環境変数 BOT_USER_ID を用意してもOK
        try:
            _BOT_USER_ID = line_bot_api.get_bot_info().user_id
        except Exception:
            _BOT_USER_ID = os.environ.get("BOT_USER_ID", "")
    return _BOT_USER_ID

def _mentions_bot(raw_event: dict) -> bool:
    """
    Webhookの生JSONから message.mention.mentionees[*].userId を見て
    Bot 宛てのメンションが含まれるか判定
    """
    bot_id = _get_bot_user_id()
    if not bot_id:
        return False
    try:
        ments = (
            raw_event.get("message", {})
                     .get("mention", {})
                     .get("mentionees", [])
        )
        return any((m.get("userId") or m.get("user_id")) == bot_id for m in ments)
    except Exception:
        return False

# --- OpenAI ---
_openai: OpenAI | None = None
def _client() -> OpenAI:
    global _openai
    if _openai is None:
        _openai = OpenAI(api_key=_get_param(OPENAI_PARAM))
    return _openai

def _strip_triggers(text: str) -> str:
    s = text or ""
    for w in TRIGGER_WORDS:
        s = s.replace(w, "")
    s = re.sub(r"^@\S+\s*", "", s)  # 先頭の @xxx を一応除去（実メンション文字が残った場合のケア）
    return s.strip() or "カレーの魅力を一言で？"

def _chat(user_text: str) -> str:
    resp = _client().chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role":"system","content":SYSTEM_PROMPT},
                  {"role":"user","content":user_text}],
        max_tokens=300, temperature=0.8,
    )
    return resp.choices[0].message.content.strip()

# --- Lambda handler ---
def lambda_handler(event, context):
    body = event.get("body","")
    headers = {k.lower(): v for k,v in (event.get("headers") or {}).items()}
    sig = headers.get("x-line-signature","")

    try:
        events = parser.parse(body, sig)  # 署名検証 & パース
    except InvalidSignatureError:
        return {"statusCode":401, "body":"Invalid signature"}

    # 生JSON（メンション対象ユーザーIDの取得に使用）
    try:
        raw_events = json.loads(body).get("events", [])
    except Exception:
        raw_events = []

    for ev, raw_ev in zip(events, raw_events):
        if isinstance(ev, MessageEvent) and isinstance(ev.message, TextMessage):
            text = ev.message.text or ""
            in_group = _is_group(ev)
            need_mention = in_group or REQUIRE_MENTION_IN_DM

            should_reply = True
            if need_mention:
                # Bot宛メンション or トリガーワードのどちらかが必須
                should_reply = _mentions_bot(raw_ev) or _contains_trigger_word(text)

            if not should_reply:
                continue

            try:
                ans = _chat(_strip_triggers(text))
            except ClientError as e:
                logger.error(f"SSM/OpenAI error: {e}")
                ans = "Oh my curry! 今日はトラブルっぽいわ、またリトライやで〜"

            line_bot_api.reply_message(ev.reply_token, TextSendMessage(text=ans))

    return {"statusCode":200, "body":"OK"}
