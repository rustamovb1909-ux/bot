import os
import json
import sqlite3
import datetime
import tempfile
import shutil
import random
import asyncio
import signal
from pathlib import Path
from threading import Thread
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# Aiogram imports
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, 
    InlineKeyboardButton, WebAppInfo
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder

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
    print("✅ Database initialized")

init_db()

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ==================== FSM STATES ====================
class TestStates(StatesGroup):
    waiting_for_file = State()
    taking_test = State()

# ==================== BOT ====================
storage = MemoryStorage()
bot = Bot(token=TOKEN)
dp = Dispatcher(storage=storage)

# ==================== KEYBOARDS ====================
def get_main_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text="🌐 Web App ni ochish",
        web_app=WebAppInfo(url=WEBAPP_URL.rstrip('/'))
    ))
    builder.row(InlineKeyboardButton(
        text="📤 Test fayl yuklash",
        callback_data="upload_file"
    ))
    builder.row(InlineKeyboardButton(
        text="📊 Mening testlarim",
        callback_data="my_tests"
    ))
    builder.row(InlineKeyboardButton(
        text="📈 Natijalarim",
        callback_data="my_results"
    ))
    builder.row(InlineKeyboardButton(
        text="💡 Yordam",
        callback_data="help"
    ))
    return builder.as_markup()

# ==================== HANDLERS ====================

@dp.message(Command("start"))
async def start_command(message: Message):
    """Start command"""
    user = message.from_user
    
    conn = get_db()
    c = conn.cursor()
    c.execute('''INSERT OR IGNORE INTO users (user_id, username, first_name, last_name)
                 VALUES (?, ?, ?, ?)''',
              (user.id, user.username, user.first_name, user.last_name))
    conn.commit()
    conn.close()
    
    await message.answer(
        f"👋 Assalomu alaykum, {user.first_name}!\n\n"
        "Men test tizimi botiman. Siz test fayllarni yuklab, ular ustida test topshirishingiz mumkin.\n\n"
        "📁 Qo'llab-quvvatlanadigan formatlar: TXT, DOCX\n"
        "⚠️ DOC format qo'llab-quvvatlanmaydi (faqat DOCX).\n\n"
        "🖥️ Web App orqali ham ishlashingiz mumkin!",
        reply_markup=get_main_keyboard()
    )

@dp.message(Command("help"))
async def help_command(message: Message):
    """Help command"""
    await message.answer(
        "📖 *Yordam*\n\n"
        "1. 'Test fayl yuklash' - test faylini yuklang (TXT, DOCX)\n"
        "2. Fayl yuklangandan so'ng, test boshlash mumkin\n"
        "3. Har bir test uchun nechta savol olishni sozlashingiz mumkin\n"
        "4. Natijalar saqlanadi va istalgan vaqt ko'rish mumkin",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )

@dp.callback_query(F.data == "upload_file")
async def upload_file_callback(callback: CallbackQuery, state: FSMContext):
    """Upload file button handler"""
    await callback.answer()
    await state.set_state(TestStates.waiting_for_file)
    await callback.message.edit_text(
        "📤 *Test faylini yuklang*\n\n"
        "Faylni yuboring (TXT, DOCX).\n\n"
        "⚠️ Eslatma: .DOC formatdagi fayllar qo'llab-quvvatlanmaydi!\n\n"
        "🔙 Ortga qaytish uchun /start ni bosing",
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "my_tests")
async def my_tests_callback(callback: CallbackQuery):
    """My tests button handler"""
    await callback.answer()
    await show_user_tests(callback.message, callback.from_user.id)

@dp.callback_query(F.data == "my_results")
async def my_results_callback(callback: CallbackQuery):
    """My results button handler"""
    await callback.answer()
    await show_user_results(callback.message, callback.from_user.id)

@dp.callback_query(F.data == "help")
async def help_callback(callback: CallbackQuery):
    """Help button handler"""
    await callback.answer()
    await callback.message.edit_text(
        "📖 *Yordam*\n\n"
        "1. 'Test fayl yuklash' - test faylini yuklang (TXT, DOCX)\n"
        "2. Fayl yuklangandan so'ng, test boshlash mumkin\n"
        "3. Har bir test uchun nechta savol olishni sozlashingiz mumkin\n"
        "4. Natijalar saqlanadi va istalgan vaqt ko'rish mumkin",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )

@dp.callback_query(F.data == "back_to_main")
async def back_to_main_callback(callback: CallbackQuery, state: FSMContext):
    """Back to main menu"""
    await callback.answer()
    await state.clear()
    await callback.message.edit_text(
        "👋 *Asosiy menyu*\n\nTest tizimiga xush kelibsiz!",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )

@dp.callback_query(F.data.startswith("test_"))
async def start_test_callback(callback: CallbackQuery, state: FSMContext):
    """Start test from button"""
    await callback.answer()
    file_id = int(callback.data.split("_")[1])
    await start_test(callback.message, callback.from_user.id, file_id, state)

@dp.callback_query(F.data.startswith("delete_file_"))
async def delete_file_callback(callback: CallbackQuery):
    """Delete file"""
    await callback.answer()
    file_id = int(callback.data.split("_")[2])
    await delete_file(callback, file_id)

@dp.callback_query(F.data.startswith("answer_"))
async def answer_callback(callback: CallbackQuery, state: FSMContext):
    """Handle answer selection"""
    await callback.answer()
    letter = callback.data.split("_")[1]
    await handle_answer(callback, letter, state)

@dp.callback_query(F.data == "skip_question")
async def skip_question_callback(callback: CallbackQuery, state: FSMContext):
    """Skip question"""
    await callback.answer()
    data = await state.get_data()
    skipped = data.get('test_skipped', 0) + 1
    current = data.get('test_current', 0) + 1
    await state.update_data(test_skipped=skipped, test_current=current)
    await show_question(callback.message, state)

@dp.callback_query(F.data == "finish_test")
async def finish_test_callback(callback: CallbackQuery, state: FSMContext):
    """Finish test"""
    await callback.answer()
    await finish_test(callback.message, state)

@dp.message(TestStates.waiting_for_file, F.document)
async def handle_file(message: Message, state: FSMContext):
    """Handle file upload"""
    document = message.document
    if not document:
        await message.answer("❌ Iltimos, fayl yuboring.")
        return
    
    file_name = document.file_name
    file_size = document.file_size
    ext = os.path.splitext(file_name)[1].lower()
    
    if ext == '.doc':
        await message.answer(
            "❌ .DOC format qo'llab-quvvatlanmaydi!\n\n"
            "Iltimos, faylni .DOCX formatga o'tkazib qayta yuboring."
        )
        return
    
    if ext not in ['.txt', '.docx']:
        await message.answer(
            f"❌ {ext} format qo'llab-quvvatlanmaydi.\n"
            "Qo'llab-quvvatlanadigan formatlar: TXT, DOCX"
        )
        return
    
    processing_msg = await message.answer("⏳ Fayl yuklanmoqda...")
    
    file = await bot.get_file(document.file_id)
    
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp_file:
        await bot.download_file(file.file_path, tmp_file.name)
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
                "D) variant 4"
            )
            os.unlink(tmp_path)
            return
        
        upload_dir = Path("uploads")
        upload_dir.mkdir(exist_ok=True)
        
        unique_name = f"{message.from_user.id}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_{file_name}"
        file_path = upload_dir / unique_name
        shutil.move(tmp_path, str(file_path))
        
        user_id = message.from_user.id
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
            parse_mode="Markdown",
            reply_markup=get_main_keyboard()
        )
        
        await state.clear()
        
    except Exception as e:
        await processing_msg.edit_text(f"❌ Xatolik yuz berdi: {str(e)}")
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

@dp.message(TestStates.waiting_for_file)
async def handle_invalid_file(message: Message):
    """Handle invalid file"""
    await message.answer(
        "❌ Iltimos, TXT yoki DOCX formatdagi fayl yuboring.\n\n"
        "🔙 Ortga qaytish uchun /start ni bosing"
    )

# ==================== HELPER FUNCTIONS ====================

async def show_user_tests(message, user_id):
    """Show user's tests"""
    conn = get_db()
    c = conn.cursor()
    files = c.execute('''SELECT id, file_name, uploaded_at,
                         (SELECT COUNT(*) FROM test_questions WHERE file_id = files.id) as question_count
                         FROM files WHERE user_id = ? ORDER BY uploaded_at DESC''',
                      (user_id,)).fetchall()
    conn.close()
    
    if not files:
        await message.edit_text(
            "📂 Siz hali hech qanday test fayl yuklamagansiz.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Ortga", callback_data="back_to_main")]
            ])
        )
        return
    
    text = "📂 *Mening testlarim*\n\n"
    builder = InlineKeyboardBuilder()
    
    for f in files[:5]:
        text += f"📄 {f['file_name']} — {f['question_count']} savol\n"
        builder.row(InlineKeyboardButton(
            text=f"▶️ {f['file_name'][:20]}",
            callback_data=f"test_{f['id']}"
        ))
    
    builder.row(InlineKeyboardButton(text="🔙 Ortga", callback_data="back_to_main"))
    await message.edit_text(text, parse_mode="Markdown", reply_markup=builder.as_markup())

async def show_user_results(message, user_id):
    """Show user results"""
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
        await message.edit_text(
            "📊 Siz hali hech qanday test natijasiga ega emassiz.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Ortga", callback_data="back_to_main")]
            ])
        )
        return
    
    text = "📊 *Oxirgi 10 ta natija*\n\n"
    for r in results:
        date = r['test_date'][:16] if r['test_date'] else "N/A"
        text += f"📄 {r['file_name'][:30]}\n"
        text += f"   📅 {date}\n"
        text += f"   ✅ {r['correct_answers']} | ❌ {r['wrong_answers']} | ⏭️ {r['skipped_answers']}\n"
        text += f"   📊 Ball: {r['score']}%\n\n"
    
    await message.edit_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Ortga", callback_data="back_to_main")]
        ])
    )

async def start_test(message, user_id, file_id, state):
    """Start test"""
    conn = get_db()
    c = conn.cursor()
    settings = c.execute('SELECT default_test_count FROM user_settings WHERE user_id = ?',
                        (user_id,)).fetchone()
    default_count = settings['default_test_count'] if settings else 10
    
    questions = c.execute('SELECT * FROM test_questions WHERE file_id = ?', (file_id,)).fetchall()
    conn.close()
    
    if not questions:
        await message.edit_text("❌ Bu faylda savollar topilmadi.")
        return
    
    questions = list(questions)
    total = len(questions)
    
    if default_count > 0 and default_count < total:
        questions = random.sample(questions, default_count)
    
    await state.update_data(
        test_questions=questions,
        test_file_id=file_id,
        test_current=0,
        test_answers={},
        test_correct=0,
        test_wrong=0,
        test_skipped=0
    )
    
    await show_question(message, state)

async def show_question(message, state):
    """Show current question"""
    data = await state.get_data()
    questions = data.get('test_questions', [])
    current = data.get('test_current', 0)
    
    if current >= len(questions):
        await finish_test(message, state)
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
    
    builder = InlineKeyboardBuilder()
    for letter, opt_text in options:
        if opt_text and opt_text.strip():
            builder.row(InlineKeyboardButton(
                text=f"{letter}) {opt_text[:40]}",
                callback_data=f"answer_{letter}"
            ))
    
    builder.row(InlineKeyboardButton(
        text="⏭️ O'tkazib yuborish",
        callback_data="skip_question"
    ))
    builder.row(InlineKeyboardButton(
        text="📊 Yakunlash",
        callback_data="finish_test"
    ))
    
    await message.edit_text(text, parse_mode="Markdown", reply_markup=builder.as_markup())

async def handle_answer(callback, letter, state):
    """Handle answer selection"""
    data = await state.get_data()
    current = data.get('test_current', 0)
    questions = data.get('test_questions', [])
    
    if current < len(questions):
        q = questions[current]
        if letter == q['correct_answer']:
            await state.update_data(test_correct=data.get('test_correct', 0) + 1)
        else:
            await state.update_data(test_wrong=data.get('test_wrong', 0) + 1)
        
        answers = data.get('test_answers', {})
        answers[current] = letter
        await state.update_data(test_answers=answers, test_current=current + 1)
        
        await show_question(callback.message, state)

async def finish_test(message, state):
    """Finish test"""
    data = await state.get_data()
    correct = data.get('test_correct', 0)
    wrong = data.get('test_wrong', 0)
    skipped = data.get('test_skipped', 0)
    questions = data.get('test_questions', [])
    total = len(questions)
    file_id = data.get('test_file_id')
    
    if total == 0:
        await message.edit_text("❌ Xatolik yuz berdi.")
        return
    
    score = int((correct / total) * 100)
    
    user_id = message.chat.id
    
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
    
    await state.clear()
    await message.edit_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Asosiy menyu", callback_data="back_to_main")]
        ])
    )

async def delete_file(callback, file_id):
    """Delete file"""
    user_id = callback.from_user.id
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
    
    await show_user_tests(callback.message, user_id)

def parse_text_content(text):
    """Parse text content to questions"""
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
    """Parse HTML content to questions"""
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
        
        result = []
        for f in files:
            result.append({
                'id': f['id'],
                'file_name': f['file_name'],
                'uploaded_at': f['uploaded_at'],
                'question_count': f['question_count']
            })
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
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ==================== MAIN ====================

async def main():
    """Main function to run bot with polling"""
    try:
        # Webhookni o'chirish (polling mode)
        await bot.delete_webhook(drop_pending_updates=True)
        print("✅ Webhook o'chirildi")
        
        # Botni polling mode da ishga tushirish - signal handlersiz
        print("🚀 Bot polling mode da ishga tushmoqda...")
        
        # Signal handler xatosini oldini olish uchun
        # Aiogram 3 da polling uchun skip_updates=True va signal handlersiz
        await dp.start_polling(
    bot,
    skip_updates=True,
    handle_signals=False
)
    except Exception as e:
        print(f"❌ Bot xatosi: {e}")
        raise

def run_bot():
    """Run bot in separate thread"""
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"❌ Bot ishga tushmadi: {e}")

if __name__ == '__main__':
    # Bot ni alohida threadda ishga tushirish
    bot_thread = Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    # Flask ni ishga tushirish
    port = int(os.environ.get('PORT', 10000))
    print(f"🌐 Flask server running on port: {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
