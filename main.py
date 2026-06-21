"""
Imtihon platformasi — Telegram bot + WebApp
- Telefon raqam bilan ro'yxatdan o'tish
- .txt, .docx, .doc fayllarni qabul qilish (.doc avtomatik .docx ga o'tkaziladi)
- 5 ta ustunli jadval yoki matn formatidagi testlarni parse qilish
- Natijalarni saqlash va ko'rsatish
"""
import os
import re
import json
import sqlite3
import asyncio
import datetime
import tempfile
import shutil
import random
import subprocess
from pathlib import Path
from threading import Thread

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    WebAppInfo, ReplyKeyboardMarkup, KeyboardButton, BotCommand
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)
import mammoth
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# KONFIGURATSIYA
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TOKEN = os.getenv("BOT_TOKEN")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
WEBAPP_URL = RENDER_EXTERNAL_URL or os.getenv("WEBAPP_URL") or "https://example.com"

if not TOKEN:
    raise ValueError("BOT_TOKEN o'rnatilmagan! .env faylga BOT_TOKEN=qabul qilingan_token qo'ying")

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DOIMIY DISK (Render.com uchun)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DATA_DIR = os.getenv("DATA_DIR", "/var/data" if os.path.isdir("/var/data") else ".")
DATA_DIR = Path(DATA_DIR)
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = os.getenv("DB_PATH", str(DATA_DIR / "test_bot.db"))
UPLOADS_DIR = Path(os.getenv("UPLOADS_DIR", str(DATA_DIR / "uploads")))
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

print(f"DATA_DIR: {DATA_DIR}", flush=True)
print(f"DB_PATH: {DB_PATH}", flush=True)
print(f"UPLOADS_DIR: {UPLOADS_DIR}", flush=True)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MA'LUMOTLAR BAZASI
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def init_db():
    """Bazani yaratish va migratsiya"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            phone_number TEXT,
            registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            file_name TEXT,
            file_path TEXT,
            file_size INTEGER,
            original_name TEXT,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );
        CREATE TABLE IF NOT EXISTS test_questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER,
            question_text TEXT,
            option_a TEXT,
            option_b TEXT,
            option_c TEXT,
            option_d TEXT,
            correct_answer TEXT,
            FOREIGN KEY (file_id) REFERENCES files(id)
        );
        CREATE TABLE IF NOT EXISTS test_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            file_id INTEGER,
            total_questions INTEGER,
            correct_answers INTEGER,
            wrong_answers INTEGER,
            skipped_answers INTEGER,
            score INTEGER,
            test_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id),
            FOREIGN KEY (file_id) REFERENCES files(id)
        );
    ''')

    c.execute("PRAGMA table_info(users)")
    cols = [row[1] for row in c.fetchall()]
    if 'phone_number' not in cols:
        try:
            c.execute("ALTER TABLE users ADD COLUMN phone_number TEXT")
            print("phone_number ustuni qo'shildi", flush=True)
        except Exception as e:
            print(f"Migratsiya xatosi: {e}", flush=True)

    c.execute("CREATE INDEX IF NOT EXISTS idx_users_phone ON users(phone_number)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_files_user ON files(user_id)")

    conn.commit()
    conn.close()


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


init_db()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PARSER FUNKSIYALARI (avvalgidek)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def normalize_text(s):
    if not s:
        return ''
    s = str(s).strip()
    s = s.replace('\xa0', ' ').replace(' ', ' ')
    s = ' '.join(s.split())
    return s


def extract_correct_marker(text):
    text = normalize_text(text)
    if not text:
        return '', False
    is_correct = False
    clean = text
    if clean.startswith('+'):
        is_correct, clean = True, clean[1:].strip()
    elif clean.startswith('*'):
        is_correct, clean = True, clean[1:].strip()
    elif clean[0] in '✓✔✅':
        is_correct, clean = True, clean[1:].strip()
    elif clean.startswith('[+]') or clean.startswith('[*]'):
        is_correct, clean = True, clean[3:].strip()
    elif clean.lower().startswith('[to') and 'gri' in clean.lower():
        is_correct = True
        clean = clean.split(']', 1)[-1].strip() if ']' in clean else clean
    elif clean.lower().startswith('(to') and 'gri' in clean.lower():
        is_correct = True
        clean = clean.split(')', 1)[-1].strip() if ')' in clean else clean
    return clean, is_correct


def strip_option_marker(text):
    text = normalize_text(text)
    if not text:
        return ''
    m = re.match(r'^[A-Da-d][\.\)]\s*(.+)$', text)
    if m:
        return m.group(1).strip()
    m = re.match(r'^\d{1,2}[\.\)]\s*(.+)$', text)
    if m:
        return m.group(1).strip()
    m = re.match(r'^[-•·▪▫◦○●]\s*(.+)$', text)
    if m:
        return m.group(1).strip()
    return text


def is_variant_marker(line):
    if not line:
        return False
    if line[0] in '+*✓✔✅':
        return True
    if line[0] in '-•·▪▫◦○●':
        return True
    if re.match(r'^[A-Da-d][\.\)]\s*\S', line):
        return True
    return False


def parse_text_content(text):
    if not text:
        return []
    lines = [normalize_text(l) for l in text.split('\n') if normalize_text(l)]
    questions = []

    # Format 1: 5 ustunli
    format1_found = False
    for line in lines:
        parts = None
        for sep in ['|', '\t', ';']:
            if sep in line:
                raw = [normalize_text(p) for p in line.split(sep)]
                raw = [p for p in raw if p]
                if len(raw) >= 5:
                    parts = raw
                    break
        if not parts:
            continue
        if len(parts) >= 5:
            q_text, correct = parts[0], parts[1]
            wrong = parts[2:5]
            if q_text and correct:
                all_opts = [correct] + wrong[:3]
                while len(all_opts) < 4:
                    all_opts.append('')
                questions.append({
                    'text': q_text,
                    'options': all_opts[:4],
                    'correct': 'A',
                })
                format1_found = True
    if format1_found:
        return questions

    # Format 2-4
    classified = []
    for idx, line in enumerate(lines):
        if is_variant_marker(line):
            classified.append((line, True))
        else:
            classified.append((line, False))

    fixed = []
    for idx, (line, is_opt) in enumerate(classified):
        if is_opt:
            fixed.append((line, True))
            continue
        next_count = sum(1 for j in range(idx + 1, min(idx + 7, len(classified))) if classified[j][1])
        if next_count >= 2:
            fixed.append((line, False))
        elif '?' in line or len(line) > 50:
            fixed.append((line, False))
        else:
            fixed.append((line, True))

    blocks = []
    current_q = None
    current_opts = []
    for line_clean, is_option in fixed:
        if is_option:
            current_opts.append(line_clean)
        else:
            if current_opts:
                blocks.append((current_q, current_opts))
                current_opts = []
            current_q = line_clean
    if current_q is not None and current_opts:
        blocks.append((current_q, current_opts))

    for q_text, opts_lines in blocks:
        if not q_text:
            continue
        options = []
        correct_idx = 0
        found_explicit = False
        for opt_line in opts_lines:
            clean_opt, is_correct = extract_correct_marker(opt_line)
            clean_opt = strip_option_marker(clean_opt)
            if not clean_opt:
                continue
            options.append(clean_opt)
            if is_correct and not found_explicit:
                correct_idx = len(options) - 1
                found_explicit = True
        q_text = re.sub(r'^\d{1,3}[\.\)]\s*', '', q_text).strip()
        if len(options) >= 2 and q_text:
            while len(options) < 4:
                options.append('')
            questions.append({
                'text': q_text,
                'options': options[:4],
                'correct': chr(ord('A') + correct_idx) if correct_idx < 4 else 'A',
            })
    return questions


def parse_html_content(html):
    if not html:
        return []
    soup = BeautifulSoup(html, 'html.parser')
    questions = []
    tables = soup.find_all('table')

    for table in tables:
        rows = table.find_all('tr')
        if not rows:
            continue

        cells_per_row = [row.find_all(['td', 'th']) for row in rows]

        # Format A: 1 ustunli jadval — har bir jadvalda 1 savol + 2..5 javob
        # (Bu BuxDU test formatiga mos: birinchi qator=savol, qolganlar=variantlar)
        if all(len(c) == 1 for c in cells_per_row) and 3 <= len(rows) <= 8:
            texts = [normalize_text(r[0].get_text()) for r in cells_per_row]
            texts = [t for t in texts if t]
            if len(texts) >= 3:
                q_text = texts[0]
                opts = texts[1:]
                while len(opts) < 4:
                    opts.append('')
                questions.append({
                    'text': q_text,
                    'options': opts[:4],
                    'correct': 'A',
                })
            continue

        # Format B: 5 ta yoki undan ko'p ustunli jadval (Savol|To'g'ri|Xato1|Xato2|Xato3)
        for row, cells in zip(rows, cells_per_row):
            if len(cells) >= 5:
                q_text = normalize_text(cells[0].get_text())
                correct = normalize_text(cells[1].get_text())
                wrong = [normalize_text(cells[i].get_text()) for i in range(2, min(5, len(cells)))]
                wrong = [w for w in wrong if w]
                if q_text and correct and wrong:
                    all_opts = [correct] + wrong[:3]
                    while len(all_opts) < 4:
                        all_opts.append('')
                    questions.append({
                        'text': q_text,
                        'options': all_opts[:4],
                        'correct': 'A',
                    })

    # Jadvallardan savol topilmasa, matn rejimiga o'tamiz
    if not questions:
        text = soup.get_text('\n')
        questions = parse_text_content(text)

    return questions


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TELEGRAM BOT HANDLERLARI (o'zgarishsiz)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def md_escape(text):
    if not text:
        return ''
    bad_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '|', '{', '}']
    for ch in bad_chars:
        text = text.replace(ch, ' ')
    return text


def html_escape(text):
    if not text:
        return ''
    text = str(text)
    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    return text


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute(
            'INSERT OR IGNORE INTO users (user_id, username, first_name, last_name) VALUES (?, ?, ?, ?)',
            (user.id, user.username, user.first_name, user.last_name),
        )
        conn.commit()
        row = c.execute('SELECT phone_number FROM users WHERE user_id = ?', (user.id,)).fetchone()
        phone = (row['phone_number'] if row else None) or ''
        conn.close()
    except Exception as e:
        print(f"start DB error: {e}", flush=True)
        phone = None
    if not phone:
        kb = ReplyKeyboardMarkup(
            [[KeyboardButton("📱 Telefon raqamni ulashish", request_contact=True)]],
            resize_keyboard=True,
            one_time_keyboard=True,
        )
        safe_name = html_escape(user.first_name or 'foydalanuvchi')
        await update.message.reply_text(
            f"👋 Assalomu alaykum, <b>{safe_name}</b>!\n\n"
            "📌 Imtihon platformasidan foydalanish uchun avval telefon raqamingizni ulashing.\n\n"
            "Quyidagi tugmani bosing:",
            parse_mode="HTML",
            reply_markup=kb,
        )
    else:
        await send_main_menu(update, user.first_name)


async def send_main_menu(update, first_name):
    safe_name = html_escape(first_name or 'foydalanuvchi')
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Imtihon platformasi", web_app=WebAppInfo(url=WEBAPP_URL.rstrip('/')))],
        [InlineKeyboardButton("📤 Test fayl yuklash", callback_data="upload_file")],
        [InlineKeyboardButton("📊 Natijalarim", callback_data="my_results")],
        [InlineKeyboardButton("📚 Mening testlarim", callback_data="my_tests")],
        [InlineKeyboardButton("💡 Yordam", callback_data="help")],
    ])
    await update.message.reply_text(
        f"✅ Xush kelibsiz, <b>{safe_name}</b>!\n\nQuyidagilardan birini tanlang:",
        parse_mode="HTML",
        reply_markup=kb,
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 <b>Yordam</b>\n\n"
        "1️⃣ <b>Test yuklash</b>: botga <code>.txt</code>, <code>.docx</code> yoki <code>.doc</code> fayl yuboring\n"
        "2️⃣ <code>.doc</code> fayl avtomatik <code>.docx</code> ga o'tkaziladi\n"
        "3️⃣ Test natijalari saqlanadi va istalgan vaqt ko'riladi\n\n"
        "📋 <b>Test formati:</b>\n"
        "• 5 ta ustunli jadval (Word yoki Excel)\n"
        "• Yoki matn ko'rinishida (<code>-</code> belgi bilan)",
        parse_mode="HTML",
    )


async def contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    contact = update.message.contact if update.message else None
    if not contact or not user:
        return
    if contact.user_id and contact.user_id != user.id:
        await update.message.reply_text("❌ Iltimos, o'z telefon raqamingizni ulashing.")
        return
    phone = contact.phone_number
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute('SELECT 1 FROM users WHERE user_id = ?', (user.id,))
        exists = c.fetchone()
        if exists:
            c.execute('UPDATE users SET phone_number = ? WHERE user_id = ?', (phone, user.id))
        else:
            c.execute(
                'INSERT INTO users (user_id, username, first_name, last_name, phone_number) VALUES (?, ?, ?, ?, ?)',
                (user.id, user.username, user.first_name, user.last_name, phone),
            )
        conn.commit()
        conn.close()
        print(f"Telefon raqam saqlandi: user={user.id}, phone={phone}", flush=True)
    except Exception as e:
        print(f"contact_handler DB error: {e}", flush=True)
        await update.message.reply_text("❌ Telefon raqamni saqlashda xatolik. Qayta urinib ko'ring.")
        return
    remove_kb = ReplyKeyboardMarkup([[]], resize_keyboard=True)
    await update.message.reply_text(
        f"✅ Telefon raqam saqlandi: {phone}\n\nEndi botdan to'liq foydalanishingiz mumkin!",
        reply_markup=remove_kb,
    )
    await send_main_menu(update, user.first_name)


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    if data == "upload_file":
        await query.edit_message_text(
            "📤 <b>Test faylini yuboring</b>\n\n"
            "✅ <code>.txt</code> — matn fayli\n"
            "✅ <code>.docx</code> — zamonaviy Word formati\n"
            "✅ <code>.doc</code> — eski format (avtomatik <code>.docx</code> ga o'tkaziladi)\n\n"
            "📋 <b>Test formati:</b>\n"
            "Variant 1 (5 ta ustunli jadval):\n"
            "<code>Savol|To'g'ri|Xato1|Xato2|Xato3</code>\n\n"
            "Variant 2 (matn ko'rinishida):\n"
            "<pre>"
            "Menejment – bu\n"
            "- Boshqarish (to'g'ri)\n"
            "- Maqsadga intilish\n"
            "- Tasavvur\n"
            "- Samarali boshqaruv"
            "</pre>",
            parse_mode="HTML",
        )
        context.user_data['waiting_for_file'] = True
    elif data == "my_tests":
        await show_user_tests(query)
    elif data == "my_results":
        await show_user_results(query)
    elif data == "help":
        await query.edit_message_text(
            "📖 <b>Yordam</b>\n\n"
            "1️⃣ <code>.txt</code>, <code>.docx</code> yoki <code>.doc</code> fayl yuboring\n"
            "2️⃣ Bot testlarni ajratib saqlaydi\n"
            "3️⃣ WebApp'da test topshiring\n"
            "4️⃣ Natijalar saqlanadi",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Orqaga", callback_data="back_to_main")
            ]]),
        )
    elif data == "back_to_main":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🌐 Imtihon platformasi", web_app=WebAppInfo(url=WEBAPP_URL.rstrip('/')))],
            [InlineKeyboardButton("📤 Test fayl yuklash", callback_data="upload_file")],
            [InlineKeyboardButton("📊 Natijalarim", callback_data="my_results")],
            [InlineKeyboardButton("📚 Mening testlarim", callback_data="my_tests")],
            [InlineKeyboardButton("💡 Yordam", callback_data="help")],
        ])
        await query.edit_message_text("👋 Asosiy menyu:", reply_markup=kb)


async def show_user_tests(query):
    user_id = query.from_user.id
    conn = get_db()
    c = conn.cursor()
    files = c.execute(
        '''SELECT id, original_name, uploaded_at,
           (SELECT COUNT(*) FROM test_questions WHERE file_id = files.id) as cnt
           FROM files WHERE user_id = ? ORDER BY uploaded_at DESC''',
        (user_id,),
    ).fetchall()
    conn.close()
    kb_back = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="back_to_main")]])
    if not files:
        await query.edit_message_text("📂 Siz hali hech qanday test yuklamagansiz.", reply_markup=kb_back)
        return
    text = "📚 <b>Mening testlarim:</b>\n\n"
    for i, f in enumerate(files[:20], 1):
        date = (f['uploaded_at'] or '')[:10]
        name = md_escape(f['original_name'] or 'Nomsiz')
        text += f"{i}. <b>{name}</b> — {f['cnt']} ta savol\n   📅 {date}\n\n"
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb_back)


async def show_user_results(query):
    user_id = query.from_user.id
    conn = get_db()
    c = conn.cursor()
    rows = c.execute(
        '''SELECT tr.*, f.file_name
           FROM test_results tr
           JOIN files f ON tr.file_id = f.id
           WHERE tr.user_id = ?
           ORDER BY tr.test_date DESC LIMIT 10''',
        (user_id,),
    ).fetchall()
    conn.close()
    kb_back = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="back_to_main")]])
    if not rows:
        await query.edit_message_text("📊 Hali natijalar yo'q. Avval test topshiring.", reply_markup=kb_back)
        return
    text = "📊 <b>Oxirgi 10 ta natija:</b>\n\n"
    for r in rows:
        date = (r['test_date'] or '')[:16]
        emoji = "🌟" if r['score'] >= 80 else "👍" if r['score'] >= 60 else "📚" if r['score'] >= 40 else "💪"
        fname = md_escape(r['file_name'] or '')
        text += (
            f"{emoji} <b>{fname}</b>\n"
            f"   📅 {date}\n"
            f"   ✅ {r['correct_answers']} | ❌ {r['wrong_answers']} | ⏭️ {r['skipped_answers']}\n"
            f"   📊 Ball: <b>{r['score']}%</b>\n\n"
        )
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb_back)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FAYL QAYTA ISHLASH (bot uchun)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def convert_doc_to_docx(doc_path):
    try:
        out_dir = tempfile.mkdtemp()
        cmd = ['libreoffice', '--headless', '--convert-to', 'docx', '--outdir', out_dir, doc_path]
        result = subprocess.run(cmd, check=True, timeout=120, capture_output=True, text=True)
        base = os.path.basename(doc_path)
        docx_name = os.path.splitext(base)[0] + '.docx'
        docx_path = os.path.join(out_dir, docx_name)
        if os.path.exists(docx_path):
            return docx_path, out_dir
        return None, out_dir
    except FileNotFoundError:
        print("❌ LibreOffice topilmadi", flush=True)
        return None, None
    except subprocess.TimeoutExpired:
        print("❌ LibreOffice timeout", flush=True)
        return None, None
    except Exception as e:
        print(f"❌ Conversion error: {e}", flush=True)
        return None, None


async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get('waiting_for_file'):
        await update.message.reply_text(
            "❗ Avval '📤 Test fayl yuklash' tugmasini bosing.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📤 Test fayl yuklash", callback_data="upload_file")
            ]]),
        )
        return
    msg = update.message
    if not msg or not msg.document:
        await update.message.reply_text("❌ Iltimos, fayl yuboring.")
        return
    doc = msg.document
    file_name = doc.file_name or "test.txt"
    file_size = doc.file_size or 0
    ext = os.path.splitext(file_name)[1].lower()
    if ext not in ('.txt', '.docx', '.doc'):
        await update.message.reply_text(
            f"❌ '{ext}' formati qo'llab-quvvatlanmaydi.\nFaqat .txt, .docx, .doc fayllar."
        )
        return
    if file_size > 20 * 1024 * 1024:
        await update.message.reply_text("❌ Fayl 20 MB dan oshmasligi kerak.")
        return
    processing_msg = await update.message.reply_text("⏳ Fayl qayta ishlanmoqda...")
    try:
        tg_file = await context.bot.get_file(doc.file_id)
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            await tg_file.download_to_drive(tmp.name)
            tmp_path = tmp.name
    except Exception as e:
        print(f"❌ Download error: {e}", flush=True)
        await processing_msg.edit_text("❌ Yuklab olishda xatolik. Qayta urinib ko'ring.")
        return
    converted_path = None
    convert_dir = None
    try:
        if ext == '.doc':
            await processing_msg.edit_text("🔄 .doc → .docx konvertatsiya...")
            converted_path, convert_dir = convert_doc_to_docx(tmp_path)
            if not converted_path:
                await processing_msg.edit_text(
                    "❌ .doc faylni .docx ga o'tkazib bo'lmadi.\n\n"
                    "Iltimos, faylni Microsoft Word yoki LibreOffice'da oching:\n"
                    "1. <b>Fayl → Saqlash, nomi bilan</b>\n"
                    "2. Format: <b>Word (.docx)</b>\n"
                    "3. Saqlang va qayta yuboring",
                    parse_mode="HTML",
                )
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                return
        read_path = converted_path or tmp_path
        read_ext = '.docx' if converted_path else ext
        await processing_msg.edit_text("📖 Savollar ajratilmoqda...")
        if read_ext == '.txt':
            with open(read_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            questions = parse_text_content(content)
        else:
            with open(read_path, 'rb') as f:
                result = mammoth.convert_to_html(f)
                html = result.value
            questions = parse_html_content(html)
        if not questions:
            await processing_msg.edit_text(
                "❌ Fayldan savollar topilmadi.\n\n"
                "<b>To'g'ri format:</b>\n"
                "1. 5 ta ustunli jadval (Word)\n"
                "2. Yoki matn:\n"
                "<pre>"
                "Menejment – bu\n"
                "- Boshqarish\n"
                "- Maqsadga intilish\n"
                "- Tasavvur\n"
                "- Samarali boshqaruv"
                "</pre>",
                parse_mode="HTML",
            )
            return
        upload_dir = UPLOADS_DIR
        upload_dir.mkdir(parents=True, exist_ok=True)
        save_name = file_name[:-4] + '.docx' if ext == '.doc' else file_name
        unique_name = f"{update.effective_user.id}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_{save_name}"
        file_path = upload_dir / unique_name
        if converted_path and os.path.exists(converted_path):
            shutil.move(converted_path, str(file_path))
            if convert_dir and os.path.exists(convert_dir):
                shutil.rmtree(convert_dir, ignore_errors=True)
        else:
            if os.path.exists(tmp_path):
                shutil.move(tmp_path, str(file_path))
        user_id = update.effective_user.id
        conn = get_db()
        c = conn.cursor()
        c.execute(
            '''INSERT INTO files (user_id, file_name, file_path, file_size, original_name)
               VALUES (?, ?, ?, ?, ?)''',
            (user_id, unique_name, str(file_path), file_size, save_name),
        )
        db_file_id = c.lastrowid
        for q in questions:
            opts = (q['options'] + ['', '', '', ''])[:4]
            c.execute(
                '''INSERT INTO test_questions
                   (file_id, question_text, option_a, option_b, option_c, option_d, correct_answer)
                   VALUES (?, ?, ?, ?, ?, ?, ?)''',
                (db_file_id, q['text'], opts[0], opts[1], opts[2], opts[3], q['correct']),
            )
        conn.commit()
        conn.close()
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🌐 Testni boshlash", web_app=WebAppInfo(url=WEBAPP_URL.rstrip('/')))],
            [InlineKeyboardButton("🔙 Asosiy menyu", callback_data="back_to_main")],
        ])
        await processing_msg.edit_text(
            f"✅ <b>Fayl saqlandi!</b>\n\n"
            f"📄 {md_escape(save_name)}\n"
            f"📊 {len(questions)} ta savol topildi\n\n"
            f"Test topshirish uchun quyidagi tugmani bosing:",
            parse_mode="HTML",
            reply_markup=kb,
        )
        context.user_data['waiting_for_file'] = False
    except Exception as e:
        print(f"❌ handle_file error: {e}", flush=True)
        import traceback
        traceback.print_exc()
        safe_err = md_escape(str(e)[:200])
        try:
            await processing_msg.edit_text(f"❌ Xatolik: {safe_err}")
        except Exception:
            await processing_msg.edit_text(f"❌ Xatolik yuz berdi. Qayta urinib ko'ring.")
        for p in [tmp_path, converted_path]:
            if p and os.path.exists(p):
                try:
                    os.unlink(p)
                except OSError:
                    pass
        if convert_dir and os.path.exists(convert_dir):
            shutil.rmtree(convert_dir, ignore_errors=True)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FLASK API ROUTES (YANGILANGAN)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@app.route('/')
def index():
    return send_from_directory('.', 'index.html')


@app.route('/ping')
def ping():
    return "ok", 200


@app.route('/healthz')
def healthz():
    return jsonify({"status": "ok"}), 200


# YANGI: /api/upload — faylni qabul qilish va parse qilish
@app.route('/api/upload', methods=['POST'])
def api_upload():
    """WebApp dan fayl yuklash"""
    try:
        # userId ni form yoki JSON dan olish
        user_id = request.form.get('userId')
        if not user_id:
            # Agar form da bo'lmasa, JSON dan tekshiramiz
            data = request.get_json(silent=True) or {}
            user_id = data.get('userId')
        if not user_id:
            return jsonify({'error': 'userId kerak'}), 400
        try:
            user_id = int(user_id)
        except ValueError:
            return jsonify({'error': 'userId noto‘g‘ri'}), 400

        if 'file' not in request.files:
            return jsonify({'error': 'Fayl yuborilmadi'}), 400
        file = request.files['file']
        if not file.filename:
            return jsonify({'error': 'Fayl tanlanmagan'}), 400

        # Faylni vaqtinchalik saqlaymiz
        file_name = file.filename
        ext = os.path.splitext(file_name)[1].lower()
        if ext not in ('.txt', '.docx', '.doc'):
            return jsonify({'error': f"'{ext}' formati qo'llab-quvvatlanmaydi. Faqat .txt, .docx, .doc"}), 400

        # Faylni diskka vaqtinchalik yozamiz
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            file.save(tmp.name)
            tmp_path = tmp.name

        converted_path = None
        convert_dir = None

        # .doc -> .docx konvertatsiya
        if ext == '.doc':
            converted_path, convert_dir = convert_doc_to_docx(tmp_path)
            if not converted_path:
                return jsonify({'error': ".doc faylni o'qib bo'lmadi. DOCX formatida yuboring."}), 400

        read_path = converted_path or tmp_path
        read_ext = '.docx' if converted_path else ext

        # Savollarni ajratish
        if read_ext == '.txt':
            with open(read_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            questions = parse_text_content(content)
        else:
            with open(read_path, 'rb') as f:
                result = mammoth.convert_to_html(f)
                html = result.value
            questions = parse_html_content(html)

        if not questions:
            # Tozalash (savol topilmasa)
            for p in [tmp_path, converted_path]:
                if p and os.path.exists(p):
                    try:
                        os.unlink(p)
                    except OSError:
                        pass
            if convert_dir and os.path.exists(convert_dir):
                shutil.rmtree(convert_dir, ignore_errors=True)
            return jsonify({'error': "Fayldan savollar topilmadi. Formatni tekshiring."}), 400

        # Faylni doimiy saqlash
        save_name = file_name[:-4] + '.docx' if ext == '.doc' else file_name
        unique_name = f"{user_id}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_{save_name}"
        file_path = UPLOADS_DIR / unique_name

        # Asl faylni (konvert qilingan yoki original) ko'chirish — tozalashdan OLDIN
        src = converted_path if converted_path else tmp_path
        if src and os.path.exists(src):
            shutil.copy2(src, str(file_path))

        # Endi tozalash
        for p in [tmp_path, converted_path]:
            if p and os.path.exists(p):
                try:
                    os.unlink(p)
                except OSError:
                    pass
        if convert_dir and os.path.exists(convert_dir):
            shutil.rmtree(convert_dir, ignore_errors=True)

        file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0

        conn = get_db()
        c = conn.cursor()
        c.execute(
            '''INSERT INTO files (user_id, file_name, file_path, file_size, original_name)
               VALUES (?, ?, ?, ?, ?)''',
            (user_id, unique_name, str(file_path), file_size, save_name),
        )
        db_file_id = c.lastrowid

        for q in questions:
            opts = (q['options'] + ['', '', '', ''])[:4]
            c.execute(
                '''INSERT INTO test_questions
                   (file_id, question_text, option_a, option_b, option_c, option_d, correct_answer)
                   VALUES (?, ?, ?, ?, ?, ?, ?)''',
                (db_file_id, q['text'], opts[0], opts[1], opts[2], opts[3], q['correct']),
            )
        conn.commit()
        conn.close()

        return jsonify({
            'ok': True,
            'file_id': db_file_id,
            'questions_count': len(questions),
            'original_name': file_name,
        })
    except Exception as e:
        print(f"❌ api_upload error: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# YANGI: /api/tests — GET, userId query parametr
@app.route('/api/tests')
def api_user_tests():
    try:
        user_id = request.args.get('userId')
        if not user_id:
            return jsonify({'error': 'userId kerak'}), 400
        try:
            user_id = int(user_id)
        except ValueError:
            return jsonify({'error': 'userId noto‘g‘ri'}), 400

        conn = get_db()
        c = conn.cursor()
        rows = c.execute(
            '''SELECT id, original_name as name, uploaded_at as date,
               (SELECT COUNT(*) FROM test_questions WHERE file_id = files.id) as questions
               FROM files WHERE user_id = ? ORDER BY uploaded_at DESC''',
            (user_id,),
        ).fetchall()
        conn.close()
        return jsonify({'tests': [dict(r) for r in rows]})
    except Exception as e:
        print(f"❌ api/tests error: {e}", flush=True)
        return jsonify({'tests': [], 'error': str(e)}), 200


# YANGI: /api/results — GET, userId query parametr
@app.route('/api/results')
def api_user_results():
    try:
        user_id = request.args.get('userId')
        if not user_id:
            return jsonify({'error': 'userId kerak'}), 400
        try:
            user_id = int(user_id)
        except ValueError:
            return jsonify({'error': 'userId noto‘g‘ri'}), 400

        conn = get_db()
        c = conn.cursor()
        rows = c.execute(
            '''SELECT tr.*, f.file_name
               FROM test_results tr
               JOIN files f ON tr.file_id = f.id
               WHERE tr.user_id = ?
               ORDER BY tr.test_date DESC LIMIT 20''',
            (user_id,),
        ).fetchall()
        conn.close()
        return jsonify({'results': [dict(r) for r in rows]})
    except Exception as e:
        print(f"❌ api/results error: {e}", flush=True)
        return jsonify({'results': [], 'error': str(e)}), 200


# /api/test/<file_id> — userId query parametr tekshiruvi qo'shildi (ixtiyoriy)
@app.route('/api/test/<int:file_id>')
def api_test_questions(file_id):
    try:
        user_id = request.args.get('userId')
        if not user_id:
            return jsonify({'error': 'userId kerak'}), 400
        try:
            user_id = int(user_id)
        except ValueError:
            return jsonify({'error': 'userId noto‘g‘ri'}), 400

        conn = get_db()
        c = conn.cursor()
        # Fayl foydalanuvchiga tegishli ekanligini tekshiramiz
        file_row = c.execute(
            'SELECT id FROM files WHERE id = ? AND user_id = ?',
            (file_id, user_id),
        ).fetchone()
        if not file_row:
            conn.close()
            return jsonify({'error': 'Test topilmadi'}), 404

        rows = c.execute(
            '''SELECT question_text, option_a, option_b, option_c, option_d, correct_answer
               FROM test_questions WHERE file_id = ?''',
            (file_id,),
        ).fetchall()
        conn.close()

        questions = []
        for r in rows:
            opts = [r['option_a'] or '', r['option_b'] or '', r['option_c'] or '', r['option_d'] or '']
            non_empty = [(i, o) for i, o in enumerate(opts) if o and o.strip()]
            if len(non_empty) < 2:
                continue
            correct_letter = (r['correct_answer'] or 'A').strip().upper()
            try:
                original_correct_idx = ord(correct_letter[0]) - ord('A')
            except (TypeError, ValueError, IndexError):
                original_correct_idx = 0
            if original_correct_idx < 0 or original_correct_idx > 3:
                original_correct_idx = 0
            new_correct_idx = None
            correct_text = None
            for new_idx, (orig_idx, txt) in enumerate(non_empty):
                if orig_idx == original_correct_idx:
                    new_correct_idx = new_idx
                    correct_text = txt
                    break
            if new_correct_idx is None:
                new_correct_idx = 0
                correct_text = non_empty[0][1]
            cleaned_opts = [txt for _, txt in non_empty]
            questions.append({
                'text': r['question_text'],
                'options': cleaned_opts,
                'correct': new_correct_idx,
                'correct_text': correct_text,
            })
        return jsonify({'questions': questions})
    except Exception as e:
        print(f"❌ api/test error: {e}", flush=True)
        return jsonify({'questions': [], 'error': str(e)}), 200


# /api/delete_test/<file_id> — testni o'chirish
@app.route('/api/delete_test/<int:file_id>', methods=['DELETE'])
def api_delete_test(file_id):
    try:
        user_id = request.args.get('userId')
        if not user_id:
            return jsonify({'error': 'userId kerak'}), 400

        # user_id int yoki string bolishi mumkin
        try:
            user_id_int = int(user_id)
        except (ValueError, TypeError):
            user_id_int = None

        conn = get_db()
        c = conn.cursor()

        # Avval int sifatida qidiramiz, topilmasa string sifatida
        file_row = None
        if user_id_int is not None:
            file_row = c.execute(
                'SELECT id, file_path FROM files WHERE id = ? AND user_id = ?',
                (file_id, user_id_int),
            ).fetchone()
        if not file_row:
            file_row = c.execute(
                'SELECT id, file_path FROM files WHERE id = ? AND CAST(user_id AS TEXT) = ?',
                (file_id, str(user_id)),
            ).fetchone()
        if not file_row:
            conn.close()
            return jsonify({'error': 'Test topilmadi yoki ruxsat yoq'}), 404

        file_path = file_row['file_path']

        # Avval bog'liq ma'lumotlarni o'chiramiz
        c.execute('DELETE FROM test_results WHERE file_id = ?', (file_id,))
        c.execute('DELETE FROM test_questions WHERE file_id = ?', (file_id,))
        c.execute('DELETE FROM files WHERE id = ?', (file_id,))
        conn.commit()
        conn.close()

        # Diskdan faylni o'chiramiz (mavjud bo'lsa)
        if file_path and os.path.exists(file_path):
            try:
                os.unlink(file_path)
            except OSError as e:
                print(f"Fayl o'chirishda xatolik: {e}", flush=True)

        return jsonify({'ok': True})
    except Exception as e:
        print(f"❌ api/delete_test error: {e}", flush=True)
        return jsonify({'error': str(e)}), 500


# /api/save_result — allaqachon mavjud, userId body dan olinadi (o'zgarmaydi)
@app.route('/api/save_result', methods=['POST'])
def api_save_result():
    try:
        data = request.get_json(silent=True) or {}
        user_id = data.get('user_id')
        file_id = data.get('file_id')
        if not user_id or not file_id:
            return jsonify({'error': 'user_id and file_id required'}), 400

        conn = get_db()
        c = conn.cursor()
        c.execute(
            '''INSERT INTO test_results
               (user_id, file_id, total_questions, correct_answers, wrong_answers, skipped_answers, score)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (
                user_id,
                file_id,
                int(data.get('total_questions', 0)),
                int(data.get('correct_answers', 0)),
                int(data.get('wrong_answers', 0)),
                int(data.get('skipped_answers', 0)),
                int(data.get('score', 0)),
            ),
        )
        conn.commit()
        conn.close()
        return jsonify({'ok': True})
    except Exception as e:
        print(f"❌ api/save_result error: {e}", flush=True)
        return jsonify({'error': str(e)}), 500


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# WEBHOOK
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        json_data = request.get_json(force=True, silent=True)
        if json_data and bot_app and bot_loop:
            update = Update.de_json(json_data, bot_app.bot)
            asyncio.run_coroutine_threadsafe(
                bot_app.process_update(update),
                bot_loop,
            )
        return "ok", 200
    except Exception as e:
        print(f"❌ Webhook error: {e}", flush=True)
        return "error", 500


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# BOT ISHGA TUSHIRISH
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
bot_app = None
bot_loop = None


def run_bot():
    global bot_app, bot_loop
    bot_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(bot_loop)

    bot_app = Application.builder().token(TOKEN).build()
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CommandHandler("help", help_command))
    bot_app.add_handler(MessageHandler(filters.Document.ALL, handle_file))
    bot_app.add_handler(MessageHandler(filters.CONTACT, contact_handler))
    bot_app.add_handler(CallbackQueryHandler(button_handler))

    async def setup():
        try:
            await bot_app.initialize()
            await bot_app.bot.set_my_commands([
                BotCommand("start", "Botni ishga tushirish"),
                BotCommand("help", "Yordam"),
            ])
            webhook_url = None
            if RENDER_EXTERNAL_URL:
                webhook_url = RENDER_EXTERNAL_URL.rstrip('/') + '/webhook'
            elif os.getenv("WEBHOOK_URL"):
                webhook_url = os.getenv("WEBHOOK_URL").rstrip('/') + '/webhook'
            if webhook_url:
                await bot_app.bot.delete_webhook(drop_pending_updates=True)
                await bot_app.bot.set_webhook(
                    url=webhook_url,
                    allowed_updates=["message", "callback_query"],
                )
                print(f"✅ Webhook: {webhook_url}", flush=True)
                await bot_app.start()
            else:
                await bot_app.bot.delete_webhook(drop_pending_updates=True)
                await bot_app.start()
                await bot_app.updater.start_polling(drop_pending_updates=True)
                print("✅ Polling rejimida", flush=True)
            print("✅ Bot tayyor!", flush=True)
        except Exception as e:
            print(f"❌ Bot setup error: {e}", flush=True)
            import traceback
            traceback.print_exc()

    bot_loop.run_until_complete(setup())
    bot_loop.run_forever()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MAIN
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
if __name__ == '__main__':
    print(f"🚀 Bot ishga tushmoqda...", flush=True)
    print(f"📡 WebApp: {WEBAPP_URL}", flush=True)

    bot_thread = Thread(target=run_bot, daemon=True)
    bot_thread.start()

    port = int(os.environ.get('PORT', 10000))
    print(f"🌐 Flask port: {port}", flush=True)

    try:
        app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
    except Exception as e:
        print(f"Flask error: {e}", flush=True)
