#!/usr/bin/env python3
# -*- coding: utf-8 -*-
 
import os
import sys
import json
import sqlite3
import logging
import asyncio
import datetime
from html import escape
 
from aiohttp import web
 
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
 
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("personal-bot")
 
def _require_env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        print(f"ОШИБКА ЗАПУСКА: не задана переменная {name}", flush=True)
        sys.exit(1)
    return v
 
BOT_TOKEN = _require_env("BOT_TOKEN")
OWNER_ID_RAW = _require_env("OWNER_ID")
try:
    OWNER_ID = int(OWNER_ID_RAW)
except ValueError:
    print("ОШИБКА: OWNER_ID должен быть числом:", OWNER_ID_RAW, flush=True)
    sys.exit(1)
 
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "change-me")
WEBHOOK_BASE = os.environ.get("WEBHOOK_BASE", "").rstrip("/")
PORT = int(os.environ.get("PORT", "8080"))
DB_PATH = os.environ.get("DB_PATH", "tasks.db")
 
# ── CORS MIDDLEWARE ──
@web.middleware
async def cors_middleware(request, handler):
    if request.method == "OPTIONS":
        return web.Response(
            status=200,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, X-Secret",
            },
        )
    response = await handler(request)
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Secret"
    return response
 
# ── DATABASE ──
def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn
 
def db_init() -> None:
    with db_connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                details TEXT,
                source TEXT DEFAULT 'manual',
                status TEXT DEFAULT 'open',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                done_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
 
def db_add_task(title: str, details: str = "", source: str = "manual") -> int:
    with db_connect() as conn:
        cur = conn.execute(
            "INSERT INTO tasks (title, details, source) VALUES (?, ?, ?)",
            (title, details, source),
        )
        conn.commit()
        return cur.lastrowid
 
def db_list_open() -> list:
    with db_connect() as conn:
        return list(conn.execute("SELECT * FROM tasks WHERE status='open' ORDER BY id DESC"))
 
def db_mark_done(task_id: int) -> bool:
    with db_connect() as conn:
        cur = conn.execute(
            "UPDATE tasks SET status='done', done_at=CURRENT_TIMESTAMP WHERE id=? AND status='open'",
            (task_id,),
        )
        conn.commit()
        return cur.rowcount > 0
 
def db_add_note(text: str) -> int:
    with db_connect() as conn:
        cur = conn.execute("INSERT INTO notes (text) VALUES (?)", (text,))
        conn.commit()
        return cur.lastrowid
 
def db_list_notes(limit: int = 20) -> list:
    with db_connect() as conn:
        return list(conn.execute("SELECT * FROM notes ORDER BY id DESC LIMIT ?", (limit,)))
 
# ── TELEGRAM HANDLERS ──
def _is_owner(update: Update) -> bool:
    u = update.effective_user
    return u is not None and u.id == OWNER_ID
 
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        await update.message.reply_text("Это личный бот.")
        return
    await update.message.reply_text(
        "Готов.\n\n"
        "Команды:\n"
        "/list — открытые задачи\n"
        "/add <текст> — добавить задачу\n"
        "/done <id> — закрыть задачу\n"
        "/notes — последние заметки\n"
        "/note <текст> — записать мысль\n"
    )
 
async def cmd_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        return
    rows = db_list_open()
    if not rows:
        await update.message.reply_text("Открытых задач нет.")
        return
    lines = ["<b>Открытые задачи:</b>", ""]
    for r in rows:
        lines.append(f"#{r['id']} — {escape(r['title'])}")
        if r["details"]:
            lines.append(f"   <i>{escape(r['details'])}</i>")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")
 
async def cmd_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        return
    text = " ".join(ctx.args).strip()
    if not text:
        await update.message.reply_text("Формат: /add <текст задачи>")
        return
    tid = db_add_task(text, source="telegram")
    await update.message.reply_text(f"Записал #{tid}: {text}")
 
async def cmd_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        return
    if not ctx.args:
        await update.message.reply_text("Формат: /done <id>")
        return
    try:
        tid = int(ctx.args[0])
    except ValueError:
        await update.message.reply_text("id должен быть числом.")
        return
    if db_mark_done(tid):
        await update.message.reply_text(f"Закрыл #{tid}.")
    else:
        await update.message.reply_text(f"#{tid} не найдена или уже закрыта.")
 
async def cmd_note(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        return
    text = " ".join(ctx.args).strip()
    if not text:
        await update.message.reply_text("Формат: /note <текст>")
        return
    nid = db_add_note(text)
    await update.message.reply_text(f"Записал заметку #{nid}.")
 
async def cmd_notes(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        return
    rows = db_list_notes()
    if not rows:
        await update.message.reply_text("Заметок пока нет.")
        return
    lines = ["<b>Последние заметки:</b>", ""]
    for r in rows:
        lines.append(f"#{r['id']} — {escape(r['text'])}")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")
 
async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        return
    text = (update.message.text or "").strip()
    if not text:
        return
    nid = db_add_note(text)
    await update.message.reply_text(f"Записал как заметку #{nid}.")
 
async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q:
        return
    await q.answer()
    if q.from_user.id != OWNER_ID:
        return
    data = q.data or ""
    if data.startswith("done:"):
        try:
            tid = int(data.split(":", 1)[1])
        except ValueError:
            return
        if db_mark_done(tid):
            await q.edit_message_text(f"Закрыл #{tid}. ✓")
        else:
            await q.edit_message_text(f"#{tid} уже закрыта.")
 
# ── WEB HANDLERS ──
async def handle_root(request: web.Request) -> web.Response:
    return web.Response(text="Personal bot is alive.")
 
async def handle_telegram_webhook(request: web.Request) -> web.Response:
    app: Application = request.app["tg_app"]
    try:
        data = await request.json()
    except Exception as e:
        log.warning("Не могу распарсить апдейт: %s", e)
        return web.Response(status=400, text="bad json")
    update = Update.de_json(data, app.bot)
    await app.process_update(update)
    return web.Response(text="ok")
 
async def handle_task_post(request: web.Request) -> web.Response:
    secret = request.headers.get("X-Secret", "")
    if secret != WEBHOOK_SECRET:
        return web.Response(status=403, text="forbidden")
    try:
        data = await request.json()
    except Exception:
        return web.Response(status=400, text="bad json")
    title = (data.get("title") or "").strip()
    details = (data.get("details") or "").strip()
    source = (data.get("source") or "external").strip()
    if not title:
        return web.Response(status=400, text="title is required")
    tid = db_add_task(title, details, source)
 
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Закрыть ✓", callback_data=f"done:{tid}")]]
    )
    msg = f"<b>Новая задача #{tid}</b>\n{escape(title)}"
    if details:
        msg += f"\n\n<i>{escape(details)}</i>"
    msg += f"\n\n<code>источник: {escape(source)}</code>"
 
    tg_app: Application = request.app["tg_app"]
    try:
        await tg_app.bot.send_message(
            chat_id=OWNER_ID, text=msg, parse_mode="HTML", reply_markup=kb
        )
    except Exception as e:
        log.exception("Не смог отправить в Telegram: %s", e)
        return web.Response(status=500, text="telegram send failed")
    return web.json_response({"ok": True, "task_id": tid})
 
FORM_HTML = """<!doctype html>
<html lang="ru"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Задача в бот</title>
<style>
body{font-family:-apple-system,sans-serif;background:#0b0b0c;color:#eee;max-width:560px;margin:40px auto;padding:0 20px}
h1{font-weight:500;font-size:22px;margin:0 0 24px}
label{display:block;margin:14px 0 6px;color:#aaa;font-size:13px}
input,textarea{width:100%;background:#17171a;color:#eee;border:1px solid #2a2a2e;border-radius:8px;padding:10px 12px;font-size:15px;font-family:inherit;box-sizing:border-box}
textarea{min-height:120px;resize:vertical}
button{margin-top:20px;width:100%;background:#fff;color:#000;border:0;border-radius:8px;padding:12px;font-size:15px;font-weight:500;cursor:pointer}
.ok{color:#7dd87d;margin-top:14px;font-size:14px}
.err{color:#ff7a7a;margin-top:14px;font-size:14px}
</style></head><body>
<h1>Задача в личный бот</h1>
<form id="f">
  <label>Секрет</label>
  <input id="secret" type="password" autocomplete="off">
  <label>Задача</label>
  <input id="title" required>
  <label>Детали</label>
  <textarea id="details"></textarea>
  <label>Источник</label>
  <input id="source" value="form">
  <button type="submit">Отправить в Telegram</button>
  <div id="out"></div>
</form>
<script>
const f=document.getElementById('f'),out=document.getElementById('out');
f.addEventListener('submit',async e=>{
  e.preventDefault();out.textContent='Отправляю…';out.className='';
  try{
    const r=await fetch('/task',{method:'POST',
      headers:{'Content-Type':'application/json','X-Secret':document.getElementById('secret').value},
      body:JSON.stringify({title:document.getElementById('title').value,
        details:document.getElementById('details').value,
        source:document.getElementById('source').value||'form'})});
    if(r.ok){const j=await r.json();out.textContent='Отправлено. ID='+j.task_id;out.className='ok';
      document.getElementById('title').value='';document.getElementById('details').value='';}
    else{out.textContent='Ошибка '+r.status+': '+await r.text();out.className='err';}
  }catch(err){out.textContent='Ошибка: '+err.message;out.className='err';}
});
</script></body></html>
"""
 
async def handle_form(request: web.Request) -> web.Response:
    return web.Response(text=FORM_HTML, content_type="text/html")
 
# ── DAILY DIGEST ──
async def daily_digest(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    rows = db_list_open()
    if not rows:
        text = "Доброе утро. Открытых задач нет."
    else:
        lines = ["Доброе утро. Открытые задачи:", ""]
        for r in rows[:15]:
            lines.append(f"#{r['id']} — {r['title']}")
        text = "\n".join(lines)
    try:
        await ctx.bot.send_message(chat_id=OWNER_ID, text=text)
    except Exception as e:
        log.exception("daily_digest failed: %s", e)
 
# ── STARTUP / SHUTDOWN ──
async def on_startup(app: web.Application) -> None:
    tg_app: Application = app["tg_app"]
    await tg_app.initialize()
    await tg_app.start()
    if WEBHOOK_BASE:
        webhook_url = f"{WEBHOOK_BASE}/{BOT_TOKEN}"
        await tg_app.bot.set_webhook(
            url=webhook_url,
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )
        log.info("Webhook: %s", webhook_url)
    if tg_app.job_queue is not None:
        tg_app.job_queue.run_daily(
            daily_digest,
            time=datetime.time(hour=4, minute=0),
        )
        log.info("Утренний дайджест в 07:00 МСК.")
 
async def on_cleanup(app: web.Application) -> None:
    tg_app: Application = app["tg_app"]
    await tg_app.stop()
    await tg_app.shutdown()
 
def build_telegram_app() -> Application:
    tg_app = Application.builder().token(BOT_TOKEN).build()
    tg_app.add_handler(CommandHandler("start", cmd_start))
    tg_app.add_handler(CommandHandler("list", cmd_list))
    tg_app.add_handler(CommandHandler("add", cmd_add))
    tg_app.add_handler(CommandHandler("done", cmd_done))
    tg_app.add_handler(CommandHandler("note", cmd_note))
    tg_app.add_handler(CommandHandler("notes", cmd_notes))
    tg_app.add_handler(CallbackQueryHandler(on_callback))
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return tg_app
 
def main() -> None:
    db_init()
    tg_app = build_telegram_app()
    web_app = web.Application(middlewares=[cors_middleware])
    web_app["tg_app"] = tg_app
    web_app.router.add_get("/", handle_root)
    web_app.router.add_get("/form", handle_form)
    web_app.router.add_post("/task", handle_task_post)
    web_app.router.add_options("/task", handle_task_post)
    web_app.router.add_post(f"/{BOT_TOKEN}", handle_telegram_webhook)
    web_app.on_startup.append(on_startup)
    web_app.on_cleanup.append(on_cleanup)
    log.info("Старт на 0.0.0.0:%d", PORT)
    web.run_app(web_app, host="0.0.0.0", port=PORT)
 
if __name__ == "__main__":
    main()
 
