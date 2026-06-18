import os
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

# ==================== KONFIGURATSIYA ====================
TOKEN = os.getenv("BOT_TOKEN")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
WEBAPP_URL = RENDER_EXTERNAL_URL or os.getenv("WEBAPP_URL", "https://example.com")

if not TOKEN:
    raise ValueError("BOT_TOKEN o'rnatilmagan! .env faylga BOT_TOKEN=qabul qilingan_token qo'ying")

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

# ==================== DATABASE ====================
DB_PATH = os.getenv("DB_PATH", "test_bot.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        phone_number TEXT,
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
    conn.commit()
    conn.close()
    print("✅ Database initialized", flush=True)

init_db()

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ==================== BOT HANDLERS ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("📩 /start", flush=True)
    user = update.effective_user

    conn = get_db()
    c = conn.cursor()
    c.execute('''INSERT OR IGNORE INTO users (user_id, username, first_name, last_name)
                 VALUES (?, ?, ?, ?)''',
              (user.id, user.username, user.first_name, user.last_name))
    conn.commit()

    # Avval foydalanuvchida telefon raqam bor-yo'qligini tekshiramiz
    row = c.execute('SELECT phone_number FROM users WHERE user_id = ?', (user.id,)).fetchone()
    conn.close()

    if not row or not row['phone_number']:
        # Telefon raqam so'raymiz
        contact_keyboard = ReplyKeyboardMarkup(
            [[KeyboardButton("📱 Telefon raqamni ulashish", request_contact=True)]],
            resize_keyboard=True, one_time_keyboard=True
        )
        await update.message.reply_text(
            f"👋 Assalomu alaykum, {user.first_name}!\n\n"
            "📌 Imtihon platformasidan foydalanish uchun avval telefon raqamingizni ulashing.",
            reply_markup=contact_keyboard
        )
    else:
        # Telefon raqam bor — asosiy menyu
        await send_main_menu(update, user.first_name)


async def send_main_menu(update, first_name):
    """Asosiy menyu — telefon raqam berilgandan keyin"""
    inline_keyboard = [
        [InlineKeyboardButton("🌐 Imtihon platformasiga o'tish", web_app=WebAppInfo(url=WEBAPP_URL.rstrip('/')))],
        [InlineKeyboardButton("📤 Test fayl yuklash", callback_data="upload_file")],
        [InlineKeyboardButton("📊 Natijalarim", callback_data="my_results")],
        [InlineKeyboardButton("💡 Yordam", callback_data="help")],
    ]
    await update.message.reply_text(
        f"✅ Rahmat, {first_name}!\n\n"
        "Quyidagilardan birini tanlang:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard)
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Yordam*\n\n"
        "1️⃣ *Test yuklash*: botga .txt, .docx yoki .doc fayl yuboring\n"
        "   • .doc yuborsangiz, bot avtomatik .docx ga o'tkazib qaytaradi\n"
        "2️⃣ *Test topshirish*: 'Imtihon platformasiga o'tish' tugmasini bosing\n"
        "3️⃣ *Natijalar*: 'Natijalarim' orqali ko'ring\n\n"
        "⚠️ Eski .doc formatini qo'llab bo'lmaydi, .docx ga o'tkazing",
        parse_mode="Markdown"
    )


async def contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact = update.message.contact
    user = update.effective_user

    if contact and contact.user_id == user.id:
        # Faqat o'zining kontaktini qabul qilamiz
        phone = contact.phone_number
        conn = get_db()
        c = conn.cursor()
        c.execute('UPDATE users SET phone_number = ? WHERE user_id = ?', (phone, user.id))
        conn.commit()
        conn.close()

        # Reply keyboardni olib tashlaymiz
        remove_keyboard = ReplyKeyboardMarkup([[]], resize_keyboard=True)
        await update.message.reply_text(
            f"✅ Telefon raqam saqlandi: {phone}",
            reply_markup=remove_keyboard
        )
        # Asosiy menyu
        await send_main_menu(update, user.first_name)
    else:
        await update.message.reply_text("❌ Iltimos, o'z telefon raqamingizni ulashing.")


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "upload_file":
        await query.edit_message_text(
            "📤 *Test faylini yuboring*\n\n"
            "Qo'llab-quvvatlanadigan formatlar:\n"
            "• `.txt` — matn fayli\n"
            "• `.docx` — zamonaviy Word formati\n"
            "• `.doc` — eski format (avtomatik .docx ga o'tkaziladi)\n\n"
            "⚠️ Eski .doc fayl yuborganingizda, bot uni qayta ishlab, "
            ".docx formatda qaytaradi. Keyin qayta yuklashingiz mumkin.",
            parse_mode="Markdown"
        )
        context.user_data['waiting_for_file'] = True

    elif data == "my_results":
        await show_user_results(query)

    elif data == "help":
        await query.edit_message_text(
            "📖 *Yordam*\n\n"
            "1️⃣ Test yuklash: .txt, .docx yoki .doc fayl yuboring\n"
            "2️⃣ .doc fayl avtomatik .docx ga o'tkaziladi\n"
            "3️⃣ Imtihon platformasida test topshiring\n"
            "4️⃣ Natijalaringiz saqlanadi",
            parse_mode="Markdown"
        )

    elif data == "back_to_main":
        keyboard = [
            [InlineKeyboardButton("🌐 Imtihon platformasiga o'tish", web_app=WebAppInfo(url=WEBAPP_URL.rstrip('/')))],
            [InlineKeyboardButton("📤 Test fayl yuklash", callback_data="upload_file")],
            [InlineKeyboardButton("📊 Natijalarim", callback_data="my_results")],
            [InlineKeyboardButton("💡 Yordam", callback_data="help")],
        ]
        await query.edit_message_text("👋 Asosiy menyu:", reply_markup=InlineKeyboardMarkup(keyboard))


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
            "📊 Siz hali hech qanday test topshirmagansiz.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    text = "📊 *Oxirgi 10 ta natija:*\n\n"
    for r in results:
        date = r['test_date'][:16] if r['test_date'] else "N/A"
        text += f"📄 {r['file_name']}\n"
        text += f"   📅 {date}\n"
        text += f"   ✅ {r['correct_answers']} | ❌ {r['wrong_answers']} | ⏭️ {r['skipped_answers']}\n"
        text += f"   📊 Ball: *{r['score']}%*\n\n"

    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


# ==================== FILE HANDLING ====================

async def convert_doc_to_docx(doc_path):
    """LibreOffice yordamida .doc -> .docx konvertatsiya"""
    try:
        out_dir = tempfile.mkdtemp()
        cmd = [
            'libreoffice', '--headless', '--convert-to', 'docx',
            '--outdir', out_dir, doc_path
        ]
        result = subprocess.run(cmd, check=True, timeout=120, capture_output=True, text=True)
        base = os.path.basename(doc_path).replace('.doc', '.docx')
        docx_path = os.path.join(out_dir, base)
        if os.path.exists(docx_path):
            print(f"✅ .doc -> .docx converted: {docx_path}", flush=True)
            return docx_path, out_dir
        print(f"⚠️ .docx not found after conversion. stdout: {result.stdout}", flush=True)
        return None, out_dir
    except subprocess.TimeoutExpired:
        print("❌ LibreOffice timeout", flush=True)
        return None, None
    except FileNotFoundError:
        print("❌ libreoffice topilmadi. Docker image'ga o'rnatilgan bo'lishi kerak", flush=True)
        return None, None
    except Exception as e:
        print(f"❌ Conversion error: {e}", flush=True)
        return None, None


async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get('waiting_for_file'):
        await update.message.reply_text("❗ Avval 'Test fayl yuklash' tugmasini bosing.")
        return

    document = update.message.document
    if not document:
        await update.message.reply_text("❌ Iltimos, fayl yuboring.")
        return

    file_name = document.file_name or "test"
    file_size = document.file_size or 0
    ext = os.path.splitext(file_name)[1].lower()

    # Ruxsat berilgan formatlar
    if ext not in ['.txt', '.docx', '.doc']:
        await update.message.reply_text(
            f"❌ '{ext}' formati qo'llab-quvvatlanmaydi.\n"
            "Faqat .txt, .docx yoki .doc fayl yuboring."
        )
        return

    # 20 MB dan katta fayllarni rad etamiz
    if file_size > 20 * 1024 * 1024:
        await update.message.reply_text("❌ Fayl hajmi 20 MB dan oshmasligi kerak.")
        return

    processing_msg = await update.message.reply_text("⏳ Fayl qayta ishlanmoqda...")

    # Faylni yuklab olish
    try:
        tg_file = await context.bot.get_file(document.file_id)
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp_file:
            await tg_file.download_to_drive(tmp_file.name)
            tmp_path = tmp_file.name
    except Exception as e:
        print(f"❌ Download error: {e}", flush=True)
        await processing_msg.edit_text("❌ Faylni yuklab bo'lmadi. Qayta urinib ko'ring.")
        return

    converted_path = None
    convert_dir = None

    try:
        # .doc -> .docx konvertatsiya
        if ext == '.doc':
            await processing_msg.edit_text("🔄 .doc -> .docx konvertatsiya...")
            docx_path, convert_dir = await convert_doc_to_docx(tmp_path)
            if not docx_path:
                await processing_msg.edit_text(
                    "❌ .doc faylni .docx ga o'tkazib bo'lmadi.\n\n"
                    "Iltimos, faylni o'zingiz Microsoft Word yoki LibreOffice'da oching:\n"
                    "1. *Fayl → Saqlash, nomi bilan* (Save As)\n"
                    "2. Format: *Word Document (.docx)*\n"
                    "3. Saqlang va menga qayta yuboring",
                    parse_mode="Markdown"
                )
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                return
            converted_path = docx_path

        # Savollarni ajratib olish
        read_path = converted_path or tmp_path
        read_ext = '.docx' if converted_path else ext

        await processing_msg.edit_text("📖 Savollar ajratilmoqda...")

        if read_ext == '.txt':
            with open(read_path, 'r', encoding='utf-8') as f:
                content = f.read()
            questions = parse_text_content(content)
        else:
            with open(read_path, 'rb') as f:
                result = mammoth.convert_to_html(f)
                html = result.value
            questions = parse_html_content(html)

        # read_path endi kerak emas, tozalaymiz (lekin keyin saqlash uchun kerak bo'ladi)
        # Saqlash logikasi pastda

        if not questions:
            await processing_msg.edit_text(
                "❌ Fayldan savollar topilmadi.\n\n"
                "Fayl formati:\n"
                "1-qator: Savol matni\n"
                "2-5 qatorlar: A) B) C) D) variantlar\n"
                "... yoki jadval ko'rinishida (har bir qator 1 savol)",
                parse_mode="Markdown"
            )
            return

        # Yuklangan faylni saqlash
        upload_dir = Path("uploads")
        upload_dir.mkdir(exist_ok=True)

        # .doc bo'lsa .docx ga o'zgartiramiz nomini
        save_name = file_name
        if ext == '.doc':
            save_name = file_name[:-4] + '.docx'

        unique_name = f"{update.effective_user.id}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_{save_name}"
        file_path = upload_dir / unique_name

        # Saqlash: .doc -> .docx (konvert qilingan), boshqalar o'z holicha
        if converted_path and os.path.exists(converted_path):
            shutil.move(converted_path, str(file_path))
            if convert_dir and os.path.exists(convert_dir):
                shutil.rmtree(convert_dir, ignore_errors=True)
        else:
            if os.path.exists(tmp_path):
                shutil.move(tmp_path, str(file_path))

        # Database ga yozish
        user_id = update.effective_user.id
        conn = get_db()
        c = conn.cursor()
        c.execute('''INSERT INTO files (user_id, file_name, file_path, file_size, original_name)
                     VALUES (?, ?, ?, ?, ?)''',
                  (user_id, unique_name, str(file_path), file_size, save_name))
        db_file_id = c.lastrowid

        for q in questions:
            opts = q['options'] + [''] * (4 - len(q['options']))
            opts = opts[:4]
            c.execute('''INSERT INTO test_questions
                         (file_id, question_text, option_a, option_b, option_c, option_d, correct_answer)
                         VALUES (?, ?, ?, ?, ?, ?, ?)''',
                      (db_file_id, q['text'], opts[0], opts[1], opts[2], opts[3], q['correct']))

        conn.commit()
        conn.close()

        await processing_msg.edit_text(
            f"✅ *Fayl muvaffaqiyatli saqlandi!*\n\n"
            f"📄 {save_name}\n"
            f"📊 {len(questions)} ta savol\n\n"
            f"Test topshirish uchun 'Imtihon platformasiga o'tish' tugmasini bosing.",
            parse_mode="Markdown"
        )
        context.user_data['waiting_for_file'] = False

    except Exception as e:
        print(f"❌ handle_file error: {e}", flush=True)
        import traceback
        traceback.print_exc()
        await processing_msg.edit_text(f"❌ Xatolik: {str(e)[:200]}")
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        if converted_path and os.path.exists(converted_path):
            os.unlink(converted_path)
        if convert_dir and os.path.exists(convert_dir):
            shutil.rmtree(convert_dir, ignore_errors=True)


# ==================== PARSERS ====================

def parse_text_content(text):
    """Matndan savollarni ajratish"""
    lines = [l.strip() for l in text.strip().split('\n') if l.strip()]
    questions = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # Savol ehtimoli: "?" bor yoki uzun matn
        is_q = '?' in line or (len(line) > 30 and not line[0].isdigit() == False)
        # Raqam bilan boshlangan savollar: "1. Savol matni..."
        if line and (line[0].isdigit() and ('.' in line[:5] or ')' in line[:5])):
            # "1. Savol matni" yoki "1) Savol matni" formatda
            parts = line.split(maxsplit=1)
            if len(parts) == 2 and len(parts[1]) > 5:
                line = parts[1]
                is_q = True

        if is_q:
            q_text = line.lstrip('0123456789.-) ').strip()
            options = []
            j = i + 1
            while j < len(lines) and len(options) < 4:
                opt = lines[j]
                # A) variant matni
                if opt and len(opt) > 2 and opt[0].upper() in 'ABCD' and opt[1] in ').':
                    clean = opt[2:].strip() if len(opt) > 2 else ''
                    if clean:
                        options.append(clean)
                # Oddiy variant (harf bilan boshlanmagan)
                elif opt and len(options) < 4 and not opt[0].isdigit():
                    options.append(opt)
                j += 1
                if len(options) >= 4:
                    break
            if len(options) >= 2:
                questions.append({
                    'text': q_text,
                    'options': options[:4],
                    'correct': 'A'
                })
                i = j
                continue
        i += 1
    return questions


def parse_html_content(html):
    """DOCX dan olingan HTML dan savollarni ajratish"""
    soup = BeautifulSoup(html, 'html.parser')
    questions = []

    # Avval jadvallarni tekshiramiz
    tables = soup.find_all('table')
    for table in tables:
        rows = table.find_all('tr')
        for row in rows:
            cells = row.find_all(['td', 'th'])
            if len(cells) >= 5:
                q_text = cells[0].get_text().strip()
                opts = []
                for i in range(1, min(5, len(cells))):
                    txt = cells[i].get_text().strip()
                    if txt:
                        # "A) variant" yoki "A. variant" formatini tozalash
                        clean = txt
                        if len(clean) > 2 and clean[0].upper() in 'ABCD' and clean[1] in ').':
                            clean = clean[2:].strip()
                        if clean:
                            opts.append(clean)
                if len(opts) >= 2 and q_text:
                    # 4 ta variantga to'ldiramiz
                    while len(opts) < 4:
                        opts.append('')
                    questions.append({
                        'text': q_text,
                        'options': opts[:4],
                        'correct': 'A'
                    })

    if not questions:
        # Matndan parse qilamiz
        text = soup.get_text()
        questions = parse_text_content(text)

    return questions


# ==================== FLASK ROUTES ====================

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')


@app.route('/ping')
def ping():
    return "ok", 200


@app.route('/healthz')
def healthz():
    return jsonify({"status": "ok"}), 200


@app.route('/api/tests/<int:user_id>')
def get_user_tests(user_id):
    """Foydalanuvchining barcha testlarini qaytarish"""
    try:
        conn = get_db()
        c = conn.cursor()
        files = c.execute('''SELECT id, original_name as name, uploaded_at as date,
                             (SELECT COUNT(*) FROM test_questions WHERE file_id = files.id) as questions
                             FROM files WHERE user_id = ? ORDER BY uploaded_at DESC''',
                          (user_id,)).fetchall()
        conn.close()
        return jsonify({'tests': [dict(f) for f in files]})
    except Exception as e:
        print(f"❌ get_user_tests error: {e}", flush=True)
        return jsonify({'error': str(e), 'tests': []}), 500


@app.route('/api/test/<int:file_id>')
def get_test_questions(file_id):
    """Test savollarini qaytarish"""
    try:
        conn = get_db()
        c = conn.cursor()
        rows = c.execute('''SELECT question_text, option_a, option_b, option_c, option_d, correct_answer
                            FROM test_questions WHERE file_id = ?''', (file_id,)).fetchall()
        conn.close()

        questions = []
        for r in rows:
            opts = [r['option_a'], r['option_b'], r['option_c'], r['option_d']]
            opts = [o for o in opts if o and o.strip()]
            if len(opts) < 2:
                continue
            correct_letter = r['correct_answer']
            correct_idx = ord(correct_letter) - ord('A') if correct_letter else 0
            if correct_idx >= len(opts):
                correct_idx = 0
            questions.append({
                'text': r['question_text'],
                'options': opts,
                'correct': opts[correct_idx]
            })
        return jsonify({'questions': questions})
    except Exception as e:
        print(f"❌ get_test_questions error: {e}", flush=True)
        return jsonify({'error': str(e), 'questions': []}), 500


@app.route('/api/save_result', methods=['POST'])
def save_result():
    """Test natijasini saqlash"""
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        file_id = data.get('file_id')
        total = data.get('total_questions', 0)
        correct = data.get('correct_answers', 0)
        wrong = data.get('wrong_answers', 0)
        skipped = data.get('skipped_answers', 0)
        score = data.get('score', 0)

        if not user_id or not file_id:
            return jsonify({'error': 'user_id va file_id kerak'}), 400

        conn = get_db()
        c = conn.cursor()
        c.execute('''INSERT INTO test_results
                     (user_id, file_id, total_questions, correct_answers, wrong_answers, skipped_answers, score)
                     VALUES (?, ?, ?, ?, ?, ?, ?)''',
                  (user_id, file_id, total, correct, wrong, skipped, score))
        conn.commit()
        conn.close()
        return jsonify({'ok': True})
    except Exception as e:
        print(f"❌ save_result error: {e}", flush=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/results/<int:user_id>')
def get_user_results(user_id):
    """Foydalanuvchi natijalari"""
    try:
        conn = get_db()
        c = conn.cursor()
        results = c.execute('''SELECT tr.*, f.file_name
                              FROM test_results tr
                              JOIN files f ON tr.file_id = f.id
                              WHERE tr.user_id = ?
                              ORDER BY tr.test_date DESC LIMIT 20''',
                           (user_id,)).fetchall()
        conn.close()
        return jsonify({'results': [dict(r) for r in results]})
    except Exception as e:
        return jsonify({'error': str(e), 'results': []}), 500


@app.route('/webhook', methods=['POST'])
def webhook():
    print("📨 Webhook POST", flush=True)
    try:
        json_data = request.get_json(force=True)
        if json_data and bot_app and bot_loop:
            update = Update.de_json(json_data, bot_app.bot)
            asyncio.run_coroutine_threadsafe(
                bot_app.process_update(update),
                bot_loop
            )
        return "ok", 200
    except Exception as e:
        print(f"❌ Webhook error: {e}", flush=True)
        return "error", 500


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
    bot_app.add_handler(MessageHandler(filters.CONTACT, contact_handler))
    bot_app.add_handler(CallbackQueryHandler(button_handler))

    async def setup():
        try:
            await bot_app.initialize()

            # Bot komandalari
            await bot_app.bot.set_my_commands([
                BotCommand("start", "Botni ishga tushirish"),
                BotCommand("help", "Yordam"),
            ])

            # Webhook yoki polling
            webhook_url = None
            if RENDER_EXTERNAL_URL:
                webhook_url = RENDER_EXTERNAL_URL.rstrip('/') + '/webhook'
            elif os.getenv("WEBHOOK_URL"):
                webhook_url = os.getenv("WEBHOOK_URL").rstrip('/') + '/webhook'

            if webhook_url:
                await bot_app.bot.delete_webhook(drop_pending_updates=True)
                await bot_app.bot.set_webhook(url=webhook_url, allowed_updates=["message", "callback_query"])
                print(f"✅ Webhook: {webhook_url}", flush=True)
                # Webhook bilan start() — Updater'siz
                await bot_app.start()
            else:
                # Lokal: polling
                print("⚠️ Webhook URL yo'q, polling rejimida", flush=True)
                await bot_app.bot.delete_webhook(drop_pending_updates=True)
                await bot_app.start()
                await bot_app.updater.start_polling(drop_pending_updates=True)

            print("✅ Bot tayyor", flush=True)

        except Exception as e:
            print(f"❌ BOT SETUP ERROR: {e}", flush=True)
            import traceback
            traceback.print_exc()

    bot_loop.run_until_complete(setup())
    # Webhook rejimida run_forever() — updater yo'q
    if not (RENDER_EXTERNAL_URL or os.getenv("WEBHOOK_URL")):
        bot_loop.run_forever()
    else:
        bot_loop.run_forever()


# ==================== MAIN ====================

if __name__ == '__main__':
    print(f"🚀 Starting bot...", flush=True)
    print(f"📡 WebApp URL: {WEBAPP_URL}", flush=True)

    bot_thread = Thread(target=run_bot, daemon=True)
    bot_thread.start()

    port = int(os.environ.get('PORT', 10000))
    print(f"🌐 Flask port: {port}", flush=True)
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
