import os
import json
import sqlite3
import asyncio
import datetime
import subprocess
import tempfile
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import mammoth
from docx import Document
import pythoncom
from win32com import client as win32  # Windows uchun, Linux uchun python-docx yetarli

# ==================== KONFIGURATSIYA ====================
TOKEN = os.getenv("TOKEN")  # Bot tokenini o'zgartiring
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://YOUR_DOMAIN/webapp")  # Render URL ni qo'ying

app = Flask(__name__)

# ==================== DATABASE ====================
DB_PATH = "test_bot.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Foydalanuvchilar jadvali
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        phone_number TEXT,
        registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    # Fayllar jadvali
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
    
    # Test savollari jadvali
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
    
    # Test natijalari jadvali
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
    
    # Foydalanuvchi sozlamalari (test soni va hokazo)
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
    
    # Foydalanuvchini bazaga qo'shish
    conn = get_db()
    c = conn.cursor()
    c.execute('''INSERT OR IGNORE INTO users (user_id, username, first_name, last_name)
                 VALUES (?, ?, ?, ?)''',
              (user.id, user.username, user.first_name, user.last_name))
    conn.commit()
    conn.close()
    
    keyboard = [
        [InlineKeyboardButton("📤 Test fayl yuklash", callback_data="upload_file")],
        [InlineKeyboardButton("📊 Mening testlarim", callback_data="my_tests")],
        [InlineKeyboardButton("📈 Natijalarim", callback_data="my_results")],
        [InlineKeyboardButton("⚙️ Sozlamalar", callback_data="settings")],
        [InlineKeyboardButton("💡 Yordam", callback_data="help")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"👋 Assalomu alaykum, {user.first_name}!\n\n"
        "Men test tizimi botiman. Siz test fayllarni yuklab, ular ustida test topshirishingiz mumkin.\n\n"
        "📁 Qo'llab-quvvatlanadigan formatlar: TXT, DOCX\n"
        "⚠️ Agar DOC formatda bo'lsa, avtomatik DOCX ga o'giriladi.",
        reply_markup=reply_markup
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 **Yordam**\n\n"
        "1. 'Test fayl yuklash' - test faylini yuklang (TXT, DOCX)\n"
        "2. Fayl yuklangandan so'ng, test boshlash mumkin\n"
        "3. Har bir test uchun nechta savol olishni sozlashingiz mumkin\n"
        "4. Natijalar saqlanadi va istalgan vaqt ko'rish mumkin\n\n"
        "📝 **Test fayl formatlari:**\n"
        "• Savol va 4 ta variant bo'lishi kerak\n"
        "• HTML jadval formatida yoki matn formatida bo'lishi mumkin",
        parse_mode="Markdown"
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "upload_file":
        await query.edit_message_text(
            "📤 **Test faylini yuklang**\n\n"
            "Faylni yuboring (TXT, DOCX).\n"
            "DOC formatdagi fayllar avtomatik DOCX ga o'giriladi.",
            parse_mode="Markdown"
        )
        context.user_data['waiting_for_file'] = True
        
    elif query.data == "my_tests":
        await show_user_tests(query)
        
    elif query.data == "my_results":
        await show_user_results(query)
        
    elif query.data == "settings":
        await show_settings(query, context)
        
    elif query.data == "help":
        await query.edit_message_text(
            "📖 **Yordam**\n\n"
            "1. 'Test fayl yuklash' - test faylini yuklang (TXT, DOCX)\n"
            "2. Fayl yuklangandan so'ng, test boshlash mumkin\n"
            "3. Har bir test uchun nechta savol olishni sozlashingiz mumkin\n"
            "4. Natijalar saqlanadi va istalgan vaqt ko'rish mumkin\n\n"
            "📝 **Test fayl formatlari:**\n"
            "• Savol va 4 ta variant bo'lishi kerak\n"
            "• HTML jadval formatida yoki matn formatida bo'lishi mumkin",
            parse_mode="Markdown"
        )
    
    elif query.data.startswith("test_"):
        file_id = int(query.data.split("_")[1])
        await start_test(query, context, file_id)
    
    elif query.data.startswith("set_count_"):
        count = int(query.data.split("_")[2])
        context.user_data['test_count'] = count
        await query.edit_message_text(f"✅ Testlar soni {count} ga o'rnatildi")
        
    elif query.data.startswith("delete_file_"):
        file_id = int(query.data.split("_")[2])
        await delete_file(query, context, file_id)

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
        await query.edit_message_text(
            "📂 Siz hali hech qanday test fayl yuklamagansiz.\n\n"
            "Birinchi test faylingizni yuklash uchun 'Test fayl yuklash' tugmasini bosing."
        )
        return
    
    text = "📂 **Mening testlarim**\n\n"
    keyboard = []
    
    for f in files:
        text += f"📄 {f['file_name']} - {f['question_count']} savol\n"
        keyboard.append([
            InlineKeyboardButton(f"▶️ Test boshlash", callback_data=f"test_{f['id']}"),
            InlineKeyboardButton(f"🗑️ O'chirish", callback_data=f"delete_file_{f['id']}")
        ])
    
    keyboard.append([InlineKeyboardButton("🔙 Ortga", callback_data="back_to_main")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=reply_markup)

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
    
    if not results:
        await query.edit_message_text(
            "📊 Siz hali hech qanday test natijasiga ega emassiz.\n\n"
            "Test topshirish uchun avval test fayl yuklang."
        )
        return
    
    text = "📊 **Oxirgi 10 ta natija**\n\n"
    for r in results:
        date = r['test_date'][:16]
        text += f"📄 {r['file_name']}\n"
        text += f"   📅 {date}\n"
        text += f"   ✅ {r['correct_answers']} to'g'ri | ❌ {r['wrong_answers']} xato | ⏭️ {r['skipped_answers']} o'tkazib yuborilgan\n"
        text += f"   📊 Ball: {r['score']}%\n\n"
    
    keyboard = [[InlineKeyboardButton("🔙 Ortga", callback_data="back_to_main")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=reply_markup)

async def show_settings(query, context):
    user_id = query.from_user.id
    conn = get_db()
    c = conn.cursor()
    settings = c.execute('SELECT default_test_count FROM user_settings WHERE user_id = ?', 
                        (user_id,)).fetchone()
    conn.close()
    
    current_count = settings['default_test_count'] if settings else 10
    
    keyboard = [
        [InlineKeyboardButton(f"10 ta savol {'✅' if current_count == 10 else ''}", 
                             callback_data=f"set_count_10")],
        [InlineKeyboardButton(f"25 ta savol {'✅' if current_count == 25 else ''}", 
                             callback_data=f"set_count_25")],
        [InlineKeyboardButton(f"50 ta savol {'✅' if current_count == 50 else ''}", 
                             callback_data=f"set_count_50")],
        [InlineKeyboardButton(f"Barcha savollar {'✅' if current_count == 0 else ''}", 
                             callback_data=f"set_count_0")],
        [InlineKeyboardButton("🔙 Ortga", callback_data="back_to_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"⚙️ **Sozlamalar**\n\n"
        f"Joriy testlar soni: {current_count if current_count > 0 else 'Barchasi'}\n\n"
        f"Test boshlaganda nechta savol olishni tanlang:",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

async def start_test(query, context, file_id):
    user_id = query.from_user.id
    
    # Foydalanuvchi sozlamalarini olish
    conn = get_db()
    c = conn.cursor()
    settings = c.execute('SELECT default_test_count FROM user_settings WHERE user_id = ?', 
                        (user_id,)).fetchone()
    default_count = settings['default_test_count'] if settings else 10
    
    # Test savollarini olish
    questions = c.execute('''SELECT * FROM test_questions WHERE file_id = ?''', 
                         (file_id,)).fetchall()
    conn.close()
    
    if not questions:
        await query.edit_message_text("❌ Bu faylda savollar topilmadi.")
        return
    
    total = len(questions)
    if default_count > 0 and default_count < total:
        import random
        questions = random.sample(questions, default_count)
        total = default_count
    
    # Test ma'lumotlarini context ga saqlash
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
    answers = context.user_data.get('test_answers', {})
    answered = answers.get(current)
    
    text = f"📝 **Savol {current + 1}/{len(questions)}**\n\n"
    text += f"{q['question_text']}\n\n"
    
    options = [
        ('A', q['option_a']),
        ('B', q['option_b']),
        ('C', q['option_c']),
        ('D', q['option_d'])
    ]
    
    keyboard = []
    for letter, text_opt in options:
        if text_opt:
            checked = " ✅" if answered == letter else ""
            keyboard.append([InlineKeyboardButton(f"{letter}) {text_opt}{checked}", 
                           callback_data=f"answer_{letter}")])
    
    keyboard.append([InlineKeyboardButton("⏭️ O'tkazib yuborish", callback_data="skip_question")])
    keyboard.append([InlineKeyboardButton("📊 Yakunlash", callback_data="finish_test")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=reply_markup)

async def handle_test_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "skip_question":
        current = context.user_data.get('test_current', 0)
        context.user_data['test_skipped'] = context.user_data.get('test_skipped', 0) + 1
        context.user_data['test_current'] = current + 1
        await show_question(query, context)
        return
    
    if query.data == "finish_test":
        await finish_test(query, context)
        return
    
    if query.data.startswith("answer_"):
        letter = query.data.split("_")[1]
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
    
    score = int((correct / total) * 100) if total > 0 else 0
    
    # Natijalarni bazaga saqlash
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
    
    text = "📊 **Test yakunlandi!**\n\n"
    text += f"✅ To'g'ri: {correct}\n"
    text += f"❌ Noto'g'ri: {wrong}\n"
    text += f"⏭️ O'tkazib yuborilgan: {skipped}\n"
    text += f"📊 Natija: {score}%\n\n"
    
    if score >= 80:
        text += "🌟 Ajoyib natija! Siz juda zo'rsiz!"
    elif score >= 60:
        text += "👍 Yaxshi natija! Bir oz ko'proq mashq qiling!"
    elif score >= 40:
        text += "📚 O'rtacha natija. Ko'proq o'rganing!"
    else:
        text += "💪 Yaxshilanish uchun joy bor. Harakat qiling!"
    
    keyboard = [[InlineKeyboardButton("🔙 Asosiy menyu", callback_data="back_to_main")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=reply_markup)

async def delete_file(query, context, file_id):
    user_id = query.from_user.id
    
    conn = get_db()
    c = conn.cursor()
    c.execute('DELETE FROM files WHERE id = ? AND user_id = ?', (file_id, user_id))
    c.execute('DELETE FROM test_questions WHERE file_id = ?', (file_id,))
    conn.commit()
    conn.close()
    
    await query.answer("✅ Fayl o'chirildi")
    await show_user_tests(query)

async def back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("📤 Test fayl yuklash", callback_data="upload_file")],
        [InlineKeyboardButton("📊 Mening testlarim", callback_data="my_tests")],
        [InlineKeyboardButton("📈 Natijalarim", callback_data="my_results")],
        [InlineKeyboardButton("⚙️ Sozlamalar", callback_data="settings")],
        [InlineKeyboardButton("💡 Yordam", callback_data="help")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "👋 **Asosiy menyu**\n\n"
        "Test tizimiga xush kelibsiz!",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get('waiting_for_file'):
        return
    
    document = update.message.document
    if not document:
        await update.message.reply_text("❌ Iltimos, fayl yuboring.")
        return
    
    file_name = document.file_name
    file_size = document.file_size
    
    # Fayl formatini tekshirish
    valid_extensions = ['.txt', '.docx', '.doc']
    ext = os.path.splitext(file_name)[1].lower()
    
    if ext not in valid_extensions:
        await update.message.reply_text(
            f"❌ {ext} format qo'llab-quvvatlanmaydi.\n"
            "Qo'llab-quvvatlanadigan formatlar: TXT, DOCX"
        )
        return
    
    # Faylni yuklab olish
    file = await context.bot.get_file(document.file_id)
    
    # Vaqtinchalik fayl yaratish
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp_file:
        await file.download_to_drive(tmp_file.name)
        tmp_path = tmp_file.name
    
    try:
        # DOC formatni DOCX ga o'tkazish
        if ext == '.doc':
            docx_path = tmp_path + 'x'
            try:
                # Windows uchun win32com
                pythoncom.CoInitialize()
                word = win32.Dispatch("Word.Application")
                word.Visible = False
                doc = word.Documents.Open(tmp_path)
                doc.SaveAs(docx_path, 16)  # 16 = docx format
                doc.Close()
                word.Quit()
                os.unlink(tmp_path)
                tmp_path = docx_path
                file_name = file_name.replace('.doc', '.docx')
            except Exception as e:
                await update.message.reply_text(f"❌ DOC ni DOCX ga o'tkazishda xatolik: {str(e)}")
                os.unlink(tmp_path)
                return
        
        # Savollarni parslash
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
            await update.message.reply_text(
                "❌ Fayldan savollar topilmadi.\n"
                "Fayl quyidagi formatda bo'lishi kerak:\n"
                "Savol matni\n"
                "A) variant 1\n"
                "B) variant 2\n"
                "C) variant 3\n"
                "D) variant 4"
            )
            os.unlink(tmp_path)
            return
        
        # Faylni serverda saqlash (yoki cloud)
        upload_dir = Path("uploads")
        upload_dir.mkdir(exist_ok=True)
        
        unique_name = f"{update.effective_user.id}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_{file_name}"
        file_path = upload_dir / unique_name
        
        import shutil
        shutil.move(tmp_path, file_path)
        
        # Bazaga yozish
        user_id = update.effective_user.id
        conn = get_db()
        c = conn.cursor()
        
        c.execute('''INSERT INTO files (user_id, file_name, file_path, file_size, original_name)
                     VALUES (?, ?, ?, ?, ?)''',
                  (user_id, unique_name, str(file_path), file_size, file_name))
        
        file_id = c.lastrowid
        
        # Savollarni bazaga yozish
        for q in questions:
            c.execute('''INSERT INTO test_questions 
                         (file_id, question_text, option_a, option_b, option_c, option_d, correct_answer)
                         VALUES (?, ?, ?, ?, ?, ?, ?)''',
                      (file_id, q['text'], q['options'][0], q['options'][1], 
                       q['options'][2], q['options'][3], q['correct']))
        
        conn.commit()
        conn.close()
        
        await update.message.reply_text(
            f"✅ **Fayl muvaffaqiyatli yuklandi!**\n\n"
            f"📄 {file_name}\n"
            f"📊 {len(questions)} ta savol\n"
            f"📁 O'lcham: {file_size // 1024} KB\n\n"
            f"Mening testlarim bo'limiga o'tib, test boshlashingiz mumkin.",
            parse_mode="Markdown"
        )
        
        context.user_data['waiting_for_file'] = False
        
    except Exception as e:
        await update.message.reply_text(f"❌ Xatolik yuz berdi: {str(e)}")
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

def parse_text_content(text):
    """Matn formatidan savollarni parslash"""
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
                if opt and (opt[0].upper() in 'ABCD' and opt[1] in ').'):
                    options.append(opt[3:].strip())
                elif opt and len(opt) > 1:
                    options.append(opt)
                j += 1
                if len(options) >= 4:
                    break
            
            if len(options) == 4:
                questions.append({
                    'text': q_text,
                    'options': options,
                    'correct': 'A'  # Birinchi variant to'g'ri deb hisoblanadi
                })
                i = j
            else:
                i += 1
        else:
            i += 1
    
    return questions

def parse_html_content(html):
    """HTML jadvaldan savollarni parslash"""
    from bs4 import BeautifulSoup
    
    soup = BeautifulSoup(html, 'html.parser')
    questions = []
    
    # Jadvalni topish
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
                
                if len(opts) == 4:
                    questions.append({
                        'text': q_text,
                        'options': opts,
                        'correct': 'A'
                    })
    
    # Agar jadval bo'lmasa, matn sifatida parslash
    if not questions:
        text = soup.get_text()
        questions = parse_text_content(text)
    
    return questions

# ==================== FLASK ROUTES ====================

@app.route('/')
def index():
    return "✅ Test bot ishlayapti!"

@app.route('/webhook', methods=['POST'])
def webhook():
    """Telegram webhook"""
    json_data = request.get_json()
    if json_data:
        update = Update.de_json(json_data, bot_app.bot)
        bot_app.process_update(update)
    return "ok", 200

@app.route('/api/files/<int:user_id>')
def get_user_files(user_id):
    """API: Foydalanuvchi fayllarini olish"""
    conn = get_db()
    c = conn.cursor()
    files = c.execute('''SELECT id, file_name, uploaded_at, 
                         (SELECT COUNT(*) FROM test_questions WHERE file_id = files.id) as question_count
                         FROM files WHERE user_id = ? ORDER BY uploaded_at DESC''', 
                      (user_id,)).fetchall()
    conn.close()
    
    result = []
    for f in files:
        result.append({
            'id': f['id'],
            'file_name': f['file_name'],
            'uploaded_at': f['uploaded_at'],
            'question_count': f['question_count']
        })
    
    return jsonify({'files': result})

@app.route('/api/results/<int:user_id>')
def get_user_results(user_id):
    """API: Foydalanuvchi natijalarini olish"""
    conn = get_db()
    c = conn.cursor()
    results = c.execute('''SELECT tr.*, f.file_name 
                          FROM test_results tr 
                          JOIN files f ON tr.file_id = f.id 
                          WHERE tr.user_id = ? 
                          ORDER BY tr.test_date DESC''', 
                       (user_id,)).fetchall()
    conn.close()
    
    result = []
    for r in results:
        result.append({
            'id': r['id'],
            'file_name': r['file_name'],
            'total_questions': r['total_questions'],
            'correct_answers': r['correct_answers'],
            'wrong_answers': r['wrong_answers'],
            'skipped_answers': r['skipped_answers'],
            'score': r['score'],
            'test_date': r['test_date']
        })
    
    return jsonify({'results': result})

# ==================== BOT SETUP ====================

# Bot application ni yaratish
bot_app = Application.builder().token(TOKEN).build()

# Handlers
bot_app.add_handler(CommandHandler("start", start))
bot_app.add_handler(CommandHandler("help", help_command))
bot_app.add_handler(CallbackQueryHandler(button_handler, pattern="^(upload_file|my_tests|my_results|settings|help|back_to_main)$"))
bot_app.add_handler(CallbackQueryHandler(handle_test_answer, pattern="^(answer_|skip_question|finish_test)"))
bot_app.add_handler(CallbackQueryHandler(back_to_main, pattern="^back_to_main$"))
bot_app.add_handler(MessageHandler(filters.Document.ALL, handle_file))

# ==================== MAIN ====================

if __name__ == '__main__':
    import threading
    
    # Webhook ni sozlash
    bot_app.bot.set_webhook(WEBHOOK_URL)
    
    # Flask ni ishga tushirish
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)