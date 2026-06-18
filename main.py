import os
import json
import sqlite3
import asyncio
import datetime
import tempfile
import shutil
import random
from pathlib import Path
from threading import Thread

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes
)
import mammoth
from bs4 import BeautifulSoup

# ==================== KONFIGURATSIYA ====================
TOKEN = os.getenv("TOKEN")
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://your-app.onrender.com")

if not TOKEN:
    raise ValueError("TOKEN muhit o'zgaruvchisi o'rnatilmagan!")

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

# ==================== DATABASE ====================
DB_PATH = "test_bot.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        file_name TEXT,
        file_path TEXT,
        file_size INTEGER,
        original_name TEXT,
        uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(user_id)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS test_questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        file_id INTEGER,
        question_text TEXT,
        option_a TEXT,
        option_b TEXT,
        option_c TEXT,
        option_d TEXT,
        correct_answer TEXT,
        FOREIGN KEY (file_id) REFERENCES files(id)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS test_results (
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
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS user_settings (
        user_id INTEGER PRIMARY KEY,
        default_test_count INTEGER DEFAULT 10,
        FOREIGN KEY (user_id) REFERENCES users(user_id)
    )''')
    conn.commit()
    conn.close()

init_db()

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ==================== BOT HANDLERS ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = get_db()
    c = conn.cursor()
    c.execute('''INSERT OR IGNORE INTO users (user_id, username, first_name, last_name)
                 VALUES (?, ?, ?, ?)''',
              (user.id, user.username, user.first_name, user.last_name))
    conn.commit()
    conn.close()

    webapp_url = WEBAPP_URL.rstrip('/')
    keyboard = [
        [InlineKeyboardButton("🌐 Web App ni ochish", web_app=WebAppInfo(url=webapp_url))],
        [InlineKeyboardButton("📤 Test fayl yuklash", callback_data="upload_file")],
        [InlineKeyboardButton("📊 Mening testlarim", callback_data="my_tests")],
        [InlineKeyboardButton("📈 Natijalarim", callback_data="my_results")],
        [InlineKeyboardButton("💡 Yordam", callback_data="help")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"👋 Assalomu alaykum, {user.first_name}!\n\n"
        "Men test tizimi botiman. Siz test fayllarni yuklab, ular ustida test topshirishingiz mumkin.\n\n"
        "📁 Qo'llab-quvvatlanadigan formatlar: TXT, DOCX\n"
        "⚠️ DOC format qo'llab-quvvatlanmaydi (faqat DOCX).\n\n"
        "🖥️ Web App orqali ham ishlashingiz mumkin!",
        reply_markup=reply_markup
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Yordam*\n\n"
        "1. 'Test fayl yuklash' - test faylini yuklang (TXT, DOCX)\n"
        "2. Fayl yuklangandan so'ng, test boshlash mumkin\n"
        "3. Har bir test uchun nechta savol olishni sozlashingiz mumkin\n"
        "4. Natijalar saqlanadi va istalgan vaqt ko'rish mumkin\n\n"
        "📝 *Test fayl formatlari:*\n"
        "• Savol va 4 ta variant bo'lishi kerak\n"
        "• HTML jadval formatida yoki matn formatida bo'lishi mumkin",
        parse_mode="Markdown"
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "upload_file":
        await query.edit_message_text(
            "📤 *Test faylini yuklang*\n\n"
            "Faylni yuboring (TXT, DOCX).\n\n"
            "⚠️ Eslatma: .DOC formatdagi fayllar qo'llab-quvvatlanmaydi!",
            parse_mode="Markdown"
        )
        context.user_data['waiting_for_file'] = True

    elif data == "my_tests":
        await show_user_tests(query)

    elif data == "my_results":
        await show_user_results(query)

    elif data == "help":
        await query.edit_message_text(
            "📖 *Yordam*\n\n"
            "1. 'Test fayl yuklash' - test faylini yuklang (TXT, DOCX)\n"
            "2. Fayl yuklangandan so'ng, test boshlash mumkin\n"
            "3. Har bir test uchun nechta savol olishni sozlashingiz mumkin\n"
            "4. Natijalar saqlanadi va istalgan vaqt ko'rish mumkin\n\n"
            "🌐 Web App: " + WEBAPP_URL,
            parse_mode="Markdown"
        )

    elif data == "back_to_main":
        await show_main_menu(query)

    elif data.startswith("test_"):
        file_id = int(data.split("_")[1])
        await start_test(query, context, file_id)

    elif data.startswith("delete_file_"):
        file_id = int(data.split("_")[2])
        await delete_file(query, file_id)

    elif data.startswith("answer_"):
        await handle_answer(query, context, data)

    elif data == "skip_question":
        context.user_data['test_skipped'] = context.user_data.get('test_skipped', 0) + 1
        context.user_data['test_current'] = context.user_data.get('test_current', 0) + 1
        await show_question(query, context)

    elif data == "finish_test":
        await finish_test(query, context)

    elif data == "convert_doc":
        await query.edit_message_text(
            "📤 *DOC faylni yuboring*\n\n"
            "Men uni .DOCX formatga o'tkazib beraman.\n\n"
            "⚠️ Eslatma: Bu jarayon bir necha soniya vaqt olishi mumkin.",
            parse_mode="Markdown"
        )
        context.user_data['waiting_for_doc_file'] = True

async def show_main_menu(query):
    webapp_url = WEBAPP_URL.rstrip('/')
    keyboard = [
        [InlineKeyboardButton("🌐 Web App ni ochish", web_app=WebAppInfo(url=webapp_url))],
        [InlineKeyboardButton("📤 Test fayl yuklash", callback_data="upload_file")],
        [InlineKeyboardButton("📊 Mening testlarim", callback_data="my_tests")],
        [InlineKeyboardButton("📈 Natijalarim", callback_data="my_results")],
        [InlineKeyboardButton("💡 Yordam", callback_data="help")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        "👋 *Asosiy menyu*\n\nTest tizimiga xush kelibsiz!",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

async def show_user_tests(query):
    user_id = query.from_user.id
    conn = get_db()
    c = conn.cursor()
    files = c.execute('''SELECT id, file_name, uploaded_at,
                         (SELECT COUNT(*) FROM test_questions WHERE file_id = files.id) as question_count
                         FROM files WHERE user_id = ? ORDER BY uploaded_at DESC''',
                      (user_id,)).fetchall()
    conn.close()

    if not files:
        keyboard = [[InlineKeyboardButton("🔙 Ortga", callback_data="back_to_main")]]
        await query.edit_message_text(
            "📂 Siz hali hech qanday test fayl yuklamagansiz.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    text = "📂 *Mening testlarim*\n\n"
    keyboard = []
    for f in files[:5]:
        text += f"📄 {f['file_name']} — {f['question_count']} savol\n"
        keyboard.append([
            InlineKeyboardButton(f"▶️ {f['file_name'][:20]}", callback_data=f"test_{f['id']}")
        ])
    keyboard.append([InlineKeyboardButton("🔙 Ortga", callback_data="back_to_main")])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def show_user_results(query):
    user_id = query.from_user.id
    conn = get_db()
    c = conn.cursor()
    results = c.execute('''SELECT tr.*, f.file_name
                          FROM test_results tr
                          JOIN files f ON tr.file_id = f.id
                          WHERE tr.user_id = ?
                          ORDER BY tr.test_date DESC LIMIT 10''',
                       (user_id,)).fetchall()
    conn.close()

    keyboard = [[InlineKeyboardButton("🔙 Ortga", callback_data="back_to_main")]]
    if not results:
        await query.edit_message_text(
            "📊 Siz hali hech qanday test natijasiga ega emassiz.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    text = "📊 *Oxirgi 10 ta natija*\n\n"
    for r in results:
        date = r['test_date'][:16] if r['test_date'] else "N/A"
        text += f"📄 {r['file_name'][:30]}\n"
        text += f"   📅 {date}\n"
        text += f"   ✅ {r['correct_answers']} | ❌ {r['wrong_answers']} | ⏭️ {r['skipped_answers']}\n"
        text += f"   📊 Ball: {r['score']}%\n\n"
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def start_test(query, context, file_id):
    user_id = query.from_user.id
    conn = get_db()
    c = conn.cursor()
    settings = c.execute('SELECT default_test_count FROM user_settings WHERE user_id = ?',
                        (user_id,)).fetchone()
    default_count = settings['default_test_count'] if settings else 10
    questions = c.execute('SELECT * FROM test_questions WHERE file_id = ?', (file_id,)).fetchall()
    conn.close()

    if not questions:
        await query.edit_message_text("❌ Bu faylda savollar topilmadi.")
        return

    questions = list(questions)
    total = len(questions)
    if default_count > 0 and default_count < total:
        questions = random.sample(questions, default_count)

    context.user_data['test_questions'] = questions
    context.user_data['test_file_id'] = file_id
    context.user_data['test_current'] = 0
    context.user_data['test_answers'] = {}
    context.user_data['test_correct'] = 0
    context.user_data['test_wrong'] = 0
    context.user_data['test_skipped'] = 0
    await show_question(query, context)

async def show_question(query, context):
    questions = context.user_data.get('test_questions', [])
    current = context.user_data.get('test_current', 0)
    if current >= len(questions):
        await finish_test(query, context)
        return

    q = questions[current]
    text = f"📝 *Savol {current + 1}/{len(questions)}*\n\n"
    text += f"{q['question_text']}\n\n"
    options = [
        ('A', q['option_a']),
        ('B', q['option_b']),
        ('C', q['option_c']),
        ('D', q['option_d'])
    ]
    keyboard = []
    for letter, opt_text in options:
        if opt_text and opt_text.strip():
            keyboard.append([InlineKeyboardButton(f"{letter}) {opt_text[:40]}", callback_data=f"answer_{letter}")])
    keyboard.append([InlineKeyboardButton("⏭️ O'tkazib yuborish", callback_data="skip_question")])
    keyboard.append([InlineKeyboardButton("📊 Yakunlash", callback_data="finish_test")])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_answer(query, context, data):
    letter = data.split("_")[1]
    current = context.user_data.get('test_current', 0)
    questions = context.user_data.get('test_questions', [])
    if current < len(questions):
        q = questions[current]
        if letter == q['correct_answer']:
            context.user_data['test_correct'] = context.user_data.get('test_correct', 0) + 1
        else:
            context.user_data['test_wrong'] = context.user_data.get('test_wrong', 0) + 1
        context.user_data['test_answers'][current] = letter
        context.user_data['test_current'] = current + 1
        await show_question(query, context)

async def finish_test(query, context):
    correct = context.user_data.get('test_correct', 0)
    wrong = context.user_data.get('test_wrong', 0)
    skipped = context.user_data.get('test_skipped', 0)
    total = len(context.user_data.get('test_questions', []))
    if total == 0:
        await query.edit_message_text("❌ Xatolik yuz berdi.")
        return

    score = int((correct / total) * 100)
    user_id = query.from_user.id
    file_id = context.user_data.get('test_file_id')
    conn = get_db()
    c = conn.cursor()
    c.execute('''INSERT INTO test_results
                 (user_id, file_id, total_questions, correct_answers, wrong_answers, skipped_answers, score)
                 VALUES (?, ?, ?, ?, ?, ?, ?)''',
              (user_id, file_id, total, correct, wrong, skipped, score))
    conn.commit()
    conn.close()

    text = "📊 *Test yakunlandi!*\n\n"
    text += f"✅ To'g'ri: {correct}\n"
    text += f"❌ Noto'g'ri: {wrong}\n"
    text += f"⏭️ O'tkazib yuborilgan: {skipped}\n"
    text += f"📊 Natija: {score}%\n\n"
    if score >= 80:
        text += "🌟 Ajoyib natija! Siz juda zo'rsiz! 🎉"
    elif score >= 60:
        text += "👍 Yaxshi natija! Bir oz ko'proq mashq qiling!"
    elif score >= 40:
        text += "📚 O'rtacha natija. Ko'proq o'rganing!"
    else:
        text += "💪 Yaxshilanish uchun joy bor. Harakat qiling!"
    keyboard = [[InlineKeyboardButton("🔙 Asosiy menyu", callback_data="back_to_main")]]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def delete_file(query, file_id):
    user_id = query.from_user.id
    conn = get_db()
    c = conn.cursor()
    row = c.execute('SELECT file_path FROM files WHERE id = ? AND user_id = ?', (file_id, user_id)).fetchone()
    if row and row['file_path'] and os.path.exists(row['file_path']):
        try:
            os.unlink(row['file_path'])
        except:
            pass
    c.execute('DELETE FROM test_questions WHERE file_id = ?', (file_id,))
    c.execute('DELETE FROM files WHERE id = ? AND user_id = ?', (file_id, user_id))
    conn.commit()
    conn.close()
    await query.answer("✅ Fayl o'chirildi")
    await show_user_tests(query)

# ------------------- FILE HANDLERS ---------------------

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get('waiting_for_file'):
        return

    document = update.message.document
    if not document:
        await update.message.reply_text("❌ Iltimos, fayl yuboring.")
        return

    file_name = document.file_name
    file_size = document.file_size
    ext = os.path.splitext(file_name)[1].lower()

    if ext == '.doc':
        await update.message.reply_text(
            "❌ .DOC format qo'llab-quvvatlanmaydi!\n\n"
            "Iltimos, faylni Microsoft Word yoki LibreOffice da ochib, "
            "📁 'Saqlash, nomi bilan' (Save As) → .DOCX formatda saqlang "
            "va qayta yuboring.\n\n"
            "Yoki faylni menga yuborib, men uni .DOCX ga o'tkazib beraman.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📤 Faylni menga yuborish", callback_data="convert_doc")],
                [InlineKeyboardButton("🔙 Ortga", callback_data="back_to_main")]
            ])
        )
        context.user_data['waiting_for_doc'] = True
        return

    if ext not in ['.txt', '.docx']:
        await update.message.reply_text(
            f"❌ {ext} format qo'llab-quvvatlanmaydi.\n"
            "Qo'llab-quvvatlanadigan formatlar: TXT, DOCX"
        )
        return

    processing_msg = await update.message.reply_text("⏳ Fayl yuklanmoqda...")
    file = await context.bot.get_file(document.file_id)
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp_file:
        await file.download_to_drive(tmp_file.name)
        tmp_path = tmp_file.name

    try:
        questions = []
        if ext == '.txt':
            with open(tmp_path, 'r', encoding='utf-8') as f:
                content = f.read()
            questions = parse_text_content(content)
        else:  # DOCX
            with open(tmp_path, 'rb') as f:
                result = mammoth.convert_to_html(f)
                html = result.value
            questions = parse_html_content(html)

        if not questions:
            await processing_msg.edit_text(
                "❌ Fayldan savollar topilmadi.\n\n"
                "Fayl quyidagi formatda bo'lishi kerak:\n"
                "Savol matni\n"
                "A) variant 1\n"
                "B) variant 2\n"
                "C) variant 3\n"
                "D) variant 4\n\n"
                "Yoki HTML jadval formatida."
            )
            os.unlink(tmp_path)
            context.user_data['waiting_for_file'] = False
            return

        upload_dir = Path("uploads")
        upload_dir.mkdir(exist_ok=True)
        unique_name = f"{update.effective_user.id}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_{file_name}"
        file_path = upload_dir / unique_name
        shutil.move(tmp_path, str(file_path))

        user_id = update.effective_user.id
        conn = get_db()
        c = conn.cursor()
        c.execute('''INSERT INTO files (user_id, file_name, file_path, file_size, original_name)
                     VALUES (?, ?, ?, ?, ?)''',
                  (user_id, unique_name, str(file_path), file_size, file_name))
        db_file_id = c.lastrowid
        for q in questions:
            opts = q['options']
            while len(opts) < 4:
                opts.append('')
            c.execute('''INSERT INTO test_questions
                         (file_id, question_text, option_a, option_b, option_c, option_d, correct_answer)
                         VALUES (?, ?, ?, ?, ?, ?, ?)''',
                      (db_file_id, q['text'], opts[0], opts[1], opts[2], opts[3], q['correct']))
        conn.commit()
        conn.close()

        await processing_msg.edit_text(
            f"✅ *Fayl muvaffaqiyatli yuklandi!*\n\n"
            f"📄 {file_name}\n"
            f"📊 {len(questions)} ta savol\n"
            f"📁 O'lcham: {file_size // 1024} KB\n\n"
            "Mening testlarim bo'limiga o'tib, test boshlashingiz mumkin.",
            parse_mode="Markdown"
        )
        context.user_data['waiting_for_file'] = False

    except Exception as e:
        await processing_msg.edit_text(f"❌ Xatolik yuz berdi: {str(e)}")
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        context.user_data['waiting_for_file'] = False

# ------------------- DOC HANDLER ---------------------

async def handle_doc_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Qabul qilingan DOC faylni qayta ishlash (uni DOCX ga o'tkazish)"""
    if not context.user_data.get('waiting_for_doc_file'):
        return

    document = update.message.document
    if not document:
        await update.message.reply_text("❌ Iltimos, fayl yuboring.")
        return

    file_name = document.file_name
    ext = os.path.splitext(file_name)[1].lower()
    if ext != '.doc':
        await update.message.reply_text("❌ Iltimos, .DOC formatdagi fayl yuboring.")
        return

    processing_msg = await update.message.reply_text("⏳ DOC fayl qayta ishlanmoqda...")

    try:
        file = await context.bot.get_file(document.file_id)
        with tempfile.NamedTemporaryFile(delete=False, suffix='.doc') as tmp_file:
            await file.download_to_drive(tmp_file.name)
            doc_path = tmp_file.name

        # DOC ni DOCX ga o'tkazish (bu yerda siz libreoffice yoki unoconv ishlatishingiz mumkin)
        # Renderda libreoffice o'rnatilgan bo'lishi kerak. Biz oddiy usulda foydalanuvchiga qo'lda aylantirishni tavsiya qilamiz.
        # Agar sizda libreoffice bo'lsa, quyidagi kabi:
        # import subprocess
        # subprocess.run(['libreoffice', '--headless', '--convert-to', 'docx', doc_path, '--outdir', '/tmp'])
        # converted_path = doc_path.replace('.doc', '.docx')
        # Keyin o'sha faylni o'qib, savollarni ajratib olish mumkin.

        # Hozircha faqat qo'lda aylantirishni tavsiya qilamiz:
        await processing_msg.edit_text(
            "⚠️ Hozircha DOC ni DOCX ga avtomatik o'tkazish imkoniyati cheklangan.\n\n"
            "Iltimos, faylni Microsoft Word yoki LibreOffice da ochib:\n"
            "📁 File → Save As → .DOCX formatda saqlang\n\n"
            "So'ng qayta yuboring.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Ortga", callback_data="back_to_main")]
            ])
        )
        os.unlink(doc_path)
        context.user_data['waiting_for_doc_file'] = False

    except Exception as e:
        await processing_msg.edit_text(f"❌ Xatolik: {str(e)}")
        context.user_data['waiting_for_doc_file'] = False

# ------------------- PARSERS ---------------------

def parse_text_content(text):
    lines = text.strip().split('\n')
    questions = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line and ('?' in line or len(line) > 20):
            q_text = line
            options = []
            j = i + 1
            while j < len(lines) and len(options) < 4:
                opt = lines[j].strip()
                if opt and len(opt) > 1 and opt[0].upper() in 'ABCD' and len(opt) > 1 and opt[1] in ').':
                    options.append(opt[2:].strip() if len(opt) > 2 else '')
                elif opt and len(opt) > 1:
                    options.append(opt)
                j += 1
                if len(options) >= 4:
                    break
            if len(options) == 4:
                questions.append({
                    'text': q_text,
                    'options': options,
                    'correct': 'A'
                })
                i = j
            else:
                i += 1
        else:
            i += 1
    return questions

def parse_html_content(html):
    soup = BeautifulSoup(html, 'html.parser')
    questions = []
    tables = soup.find_all('table')
    for table in tables:
        rows = table.find_all('tr')
        for row in rows:
            cells = row.find_all(['td', 'th'])
            if len(cells) >= 5:
                q_text = cells[0].get_text().strip()
                opts = []
                for i in range(1, min(5, len(cells))):
                    opt = cells[i].get_text().strip()
                    if opt:
                        opts.append(opt)
                if len(opts) == 4 and q_text:
                    questions.append({
                        'text': q_text,
                        'options': opts,
                        'correct': 'A'
                    })
    if not questions:
        text = soup.get_text()
        questions = parse_text_content(text)
    return questions

# ==================== FLASK ROUTES ====================

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/webhook', methods=['POST'])
def webhook():
    json_data = request.get_json(force=True)
    if json_data and bot_app:
        update = Update.de_json(json_data, bot_app.bot)
        asyncio.run_coroutine_threadsafe(
            bot_app.process_update(update),
            bot_loop
        )
    return "ok", 200

@app.route('/api/files/<int:user_id>')
def get_user_files(user_id):
    try:
        conn = get_db()
        c = conn.cursor()
        files = c.execute('''SELECT id, file_name, uploaded_at,
                             (SELECT COUNT(*) FROM test_questions WHERE file_id = files.id) as question_count
                             FROM files WHERE user_id = ? ORDER BY uploaded_at DESC''',
                          (user_id,)).fetchall()
        conn.close()
        result = [dict(f) for f in files]
        return jsonify({'files': result})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/results/<int:user_id>')
def get_user_results(user_id):
    try:
        conn = get_db()
        c = conn.cursor()
        results = c.execute('''SELECT tr.*, f.file_name
                              FROM test_results tr
                              JOIN files f ON tr.file_id = f.id
                              WHERE tr.user_id = ?
                              ORDER BY tr.test_date DESC''',
                           (user_id,)).fetchall()
        conn.close()
        result = [dict(r) for r in results]
        return jsonify({'results': result})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ==================== BOT SETUP ====================

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
    bot_app.add_handler(MessageHandler(filters.Document.ALL, handle_doc_file))
    bot_app.add_handler(CallbackQueryHandler(button_handler))

    async def setup():
        await bot_app.initialize()
        webhook_url = WEBAPP_URL.rstrip('/') + '/webhook'
        await bot_app.bot.set_webhook(webhook_url)
        await bot_app.start()
        print(f"✅ Webhook o'rnatildi: {webhook_url}")

    bot_loop.run_until_complete(setup())
    bot_loop.run_forever()

# ==================== MAIN ====================

if __name__ == '__main__':
    if not TOKEN:
        raise ValueError("TOKEN muhit o'zgaruvchisi o'rnatilmagan!")
    bot_thread = Thread(target=run_bot, daemon=True)
    bot_thread.start()
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
