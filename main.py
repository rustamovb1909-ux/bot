import asyncio
import copy
import json
import os
import random
import re
import time
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    WebAppInfo,
)
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from datetime import datetime, timezone, timedelta

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ─────────────────────────── SOZLAMALAR ──────────────────────────

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise ValueError("BOT_TOKEN o'rnatilmagan!")

WEBAPP_URL = os.getenv("WEBAPP_URL", "https://your-app.onrender.com/webapp")

DATA_FILE = "data/users.json"
os.makedirs("temp", exist_ok=True)
os.makedirs("data", exist_ok=True)

UZBEKISTAN_TZ = timezone(timedelta(hours=5))
SUPPORTED_EXT = (".docx", ".doc", ".txt", ".xlsx", ".pdf")

bot = Bot(token=TOKEN)
dp = Dispatcher()


# ─────────────────────────── VAQT ────────────────────────────────

def uz_time():
    return datetime.now(UZBEKISTAN_TZ)

def uz_time_str():
    return uz_time().strftime("%d.%m.%Y %H:%M")


# ─────────────────────────── PERSISTENCE ─────────────────────────

def load_users() -> dict:
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"⚠️ Yuklash xatoligi: {e}")
    return {}

def save_users(data: dict):
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        print(f"⚠️ Saqlash xatoligi: {e}")

users: dict = load_users()
user_messages: dict = {}

def get_user(uid: int) -> dict:
    key = str(uid)
    if key not in users:
        users[key] = {
            "first_visit": uz_time_str(),
            "total_tests": 0,
            "total_correct": 0,
            "results": [],
            "uploaded_docs": [],
        }
        save_users(users)
    return users[key]

def save_user(uid: int):
    save_users(users)


# ─────────────────────────── XABAR BOSHQARUVI ────────────────────

async def safe_delete(chat_id: int, mid: int):
    try:
        await bot.delete_message(chat_id, mid)
    except Exception:
        pass

async def clean_chat(uid: int, chat_id: int):
    for mid in user_messages.get(uid, []):
        await safe_delete(chat_id, mid)
    user_messages[uid] = []

async def track(uid: int, mid: int):
    user_messages.setdefault(uid, [])
    if mid not in user_messages[uid]:
        user_messages[uid].append(mid)

def cleanup_temp(max_days=3):
    if not os.path.exists("temp"):
        return
    cutoff = time.time() - max_days * 86400
    for name in os.listdir("temp"):
        path = os.path.join("temp", name)
        try:
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.remove(path)
        except Exception:
            pass


# ─────────────────────────── KLAVIATURA ──────────────────────────

def main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(
                text="🚀 Testni boshlash",
                web_app=WebAppInfo(url=WEBAPP_URL)
            )],
            [
                KeyboardButton(text="📊 Natijalarim"),
                KeyboardButton(text="🆘 Yordam"),
            ],
        ],
        resize_keyboard=True,
    )

def result_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📋 Batafsil", callback_data="details"),
            InlineKeyboardButton(text="📊 Tarix", callback_data="history"),
        ],
        [InlineKeyboardButton(text="🌐 Qayta boshlash", web_app=WebAppInfo(url=WEBAPP_URL))],
    ])


# ─────────────────────────── PARSERLAR ───────────────────────────

def _read_txt(path: str) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp1251", "windows-1251", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, LookupError):
            pass
    return ""

def _norm(text: str, limit: int = 100) -> str:
    t = str(text).strip()
    return (t[: limit - 1] + "…") if len(t) > limit else t

def _parse_hash(lines: list) -> list:
    questions, current_q, correct, opts, state_ = [], None, None, [], "idle"

    def flush():
        nonlocal current_q, correct, opts, state_
        if current_q and correct and len(opts) >= 2:
            all_opts = opts[:4]
            while len(all_opts) < 4:
                all_opts.append(f"Variant {len(all_opts) + 1}")
            if correct not in all_opts:
                all_opts.insert(0, correct)
                all_opts = all_opts[:4]
            questions.append({"question": current_q, "options": all_opts, "answer": correct})
        current_q, correct, opts, state_ = None, None, [], "idle"

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#") or line.startswith("?"):
            flush()
            q = line[1:].strip().rstrip("?").strip()
            if q:
                current_q = q
            else:
                state_ = "need_q"
        elif state_ == "need_q":
            current_q = line.rstrip("?").strip()
            state_ = "idle"
        elif line.startswith("+"):
            ans = line[1:].strip()
            if not ans:
                state_ = "need_correct"
            elif current_q:
                correct = ans
                if ans not in opts:
                    opts.append(ans)
                state_ = "idle"
        elif state_ == "need_correct":
            correct = line
            if line not in opts:
                opts.append(line)
            state_ = "idle"
        elif line.startswith("-"):
            ans = line[1:].strip()
            if current_q and ans and ans not in opts:
                opts.append(ans)

    flush()
    return questions

def _parse_abcd(lines: list) -> list:
    questions, current_q, opts_d, correct_l = [], None, {}, None

    def flush():
        nonlocal current_q, opts_d, correct_l
        if current_q and opts_d and correct_l:
            ul = correct_l.upper()
            if ul in opts_d:
                ans = opts_d[ul]
                options = list(opts_d.values())[:4]
                while len(options) < 4:
                    options.append(f"Variant {len(options) + 1}")
                questions.append({"question": current_q, "options": options, "answer": ans})
        current_q, opts_d, correct_l = None, {}, None

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^(\d+)[.)]\s+(.+)$", line)
        if m:
            flush()
            current_q = m.group(2).strip()
            continue
        m = re.match(r"^([A-Da-d])[.)]\s+(.+)$", line)
        if m:
            opts_d[m.group(1).upper()] = m.group(2).strip()
            continue
        m = re.match(r"^(?:Javob|To'g'ri\s*javob|Answer|Ans)[:\s]*([A-Da-d])", line, re.IGNORECASE)
        if m:
            correct_l = m.group(1).upper()

    flush()
    return questions

def _parse_pipe(lines: list) -> list:
    result = []
    for line in lines:
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 5 and parts[0]:
            result.append({"question": parts[0], "options": parts[1:5], "answer": parts[1]})
    return result

def parse_txt(path: str) -> list:
    content = _read_txt(path)
    if not content:
        return []
    lines = content.splitlines()
    ne = [l.strip() for l in lines if l.strip()]
    if not ne:
        return []

    has_hash = any(l.startswith("#") for l in ne)
    has_plus = any(l.startswith("+") for l in ne)
    has_pipe = any("|" in l and l.count("|") >= 4 for l in ne)
    has_num = any(re.match(r"^\d+[.)]\s+", l) for l in ne)
    has_abcd = any(re.match(r"^[A-Da-d][.)]\s+", l) for l in ne)

    if (has_hash) and has_plus:
        r = _parse_hash(lines)
        if r:
            return r
    if has_num and has_abcd:
        r = _parse_abcd(lines)
        if r:
            return r
    if has_pipe:
        r = _parse_pipe(ne)
        if r:
            return r
    for fn in [_parse_hash, _parse_abcd]:
        r = fn(lines)
        if r:
            return r
    return _parse_pipe(ne)

def parse_docx(path: str) -> list:
    try:
        from docx import Document
        doc = Document(path)
    except Exception:
        return []

    questions = []

    def add_q(q, opts, ans):
        if q and len(opts) >= 2 and ans in opts:
            o = opts[:4]
            while len(o) < 4:
                o.append(f"Variant {len(o) + 1}")
            questions.append({"question": q, "options": o, "answer": ans})

    for table in doc.tables:
        rows = []
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            text = " ".join(dict.fromkeys(c for c in cells if c))
            if text:
                rows.append(text)

        if not rows:
            continue

        if len(rows) == 5:
            add_q(rows[0], rows[1:5], rows[1])
        elif len(rows) == 4:
            add_q(rows[0], rows[1:4] + ["Variant D"], rows[1])
        elif len(rows) > 5:
            for row in table.rows:
                cells = list(dict.fromkeys([c.text.strip() for c in row.cells if c.text.strip()]))
                if len(cells) >= 5:
                    add_q(cells[0], cells[1:5], cells[1])
                elif len(cells) == 3:
                    add_q(cells[0], cells[1:] + ["Variant C", "Variant D"], cells[1])

    if not questions:
        lines = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        for fn in [_parse_hash, _parse_abcd]:
            r = fn(lines)
            if r:
                return r

    return questions

def parse_xlsx(path: str) -> list:
    try:
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        questions = []
        for sheet in wb.worksheets:
            for row in sheet.iter_rows(values_only=True):
                cells = [str(c).strip() for c in row if c is not None and str(c).strip()]
                if len(cells) >= 5:
                    questions.append({"question": cells[0], "options": cells[1:5], "answer": cells[1]})
        wb.close()
        return questions
    except Exception:
        return []

def parse_pdf(path: str) -> list:
    try:
        import pdfplumber
        lines = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    lines.extend(text.splitlines())
        lines = [l.strip() for l in lines if l.strip()]
        for fn in [_parse_hash, _parse_abcd, _parse_pipe]:
            r = fn(lines)
            if r:
                return r
        return []
    except Exception:
        return []


def convert_doc_to_docx(doc_path: str, docx_path: str) -> str:
    import shutil
    import subprocess

    soffice = (
        shutil.which("soffice")
        or shutil.which("libreoffice")
        or shutil.which("soffice.bin")
    )
    if not soffice:
        raise RuntimeError("LibreOffice serverda topilmadi")

    out_dir = os.path.dirname(docx_path) or "."
    try:
        result = subprocess.run(
            [soffice, "--headless", "--norestore", "--convert-to", "docx", "--outdir", out_dir, doc_path],
            capture_output=True,
            timeout=90,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("Konversiya vaqti tugadi (90s)")

    auto_out = os.path.join(out_dir, os.path.splitext(os.path.basename(doc_path))[0] + ".docx")
    if not os.path.exists(auto_out):
        err = result.stderr.decode(errors="ignore")[:200] if result.stderr else "noma'lum xato"
        raise RuntimeError(f"Konversiya muvaffaqiyatsiz: {err}")

    if auto_out != docx_path:
        os.rename(auto_out, docx_path)
    return docx_path


# ─────────────────────────── FSM ─────────────────────────────────

class TestState(StatesGroup):
    choosing_count = State()
    testing = State()


# ─────────────────────────── TEST YORDAMCHILARI ──────────────────

def clear_session(uid: int):
    key = str(uid)
    if key not in users:
        return
    for k in ["selected_questions", "total_test", "current_index", "score",
               "answers", "waiting_for_skip", "current_poll_message_id",
               "current_poll_id", "current_question_index", "current_answer_recorded",
               "test_start_time"]:
        users[key].pop(k, None)

def grade_info(pct: float):
    if pct >= 90:
        return "A'lo", "🏆"
    elif pct >= 75:
        return "Yaxshi", "🎉"
    elif pct >= 60:
        return "Qoniqarli", "👍"
    else:
        return "Qoniqarsiz", "📚"

def count_kb(total: int):
    presets = sorted({n for n in [5, 10, 15, 20, 25, 30, 40, 50] if n <= total} | {total})
    rows, row = [], []
    for i, c in enumerate(presets):
        label = f"Hammasi ({c})" if c == total else f"{c} ta"
        row.append(InlineKeyboardButton(text=label, callback_data=f"cnt_{c}"))
        if len(row) == 3 or i == len(presets) - 1:
            rows.append(row)
            row = []
    rows.append([
        InlineKeyboardButton(text="🎲 Tasodifiy", callback_data="cnt_rand"),
        InlineKeyboardButton(text="✍️ O'zim", callback_data="cnt_custom"),
    ])
    rows.append([InlineKeyboardButton(text="❌ Bekor", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def poll_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭  Keyingisi", callback_data="next_q")],
        [
            InlineKeyboardButton(text="💡 Javob", callback_data="hint"),
            InlineKeyboardButton(text="⏹  Yakunlash", callback_data="end_test"),
        ],
    ])


# ─────────────────────────── WEB SERVER ──────────────────────────

async def serve_webapp(request):
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            return web.Response(text=f.read(), content_type="text/html")
    except FileNotFoundError:
        return web.Response(text="Web App topilmadi", status=404)

async def health(request):
    return web.Response(
        text=json.dumps({"status": "ok", "time": uz_time_str()}),
        content_type="application/json",
    )

def _cors_headers():
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }

async def api_files(request):
    uid = request.match_info.get("uid")
    user_data = users.get(str(uid), {})
    docs = user_data.get("uploaded_docs", [])

    out = []
    for d in docs:
        out.append({
            "file_name": d.get("file_name"),
            "uploaded_at": d.get("uploaded_at"),
            "questions": d.get("questions", []),
        })

    return web.Response(
        text=json.dumps({"files": out}, ensure_ascii=False),
        content_type="application/json",
        headers=_cors_headers(),
    )

async def api_results(request):
    uid = request.match_info.get("uid")
    user_data = users.get(str(uid), {})
    results = user_data.get("results", [])

    return web.Response(
        text=json.dumps({"results": results}, ensure_ascii=False, default=str),
        content_type="application/json",
        headers=_cors_headers(),
    )

async def api_options(request):
    return web.Response(headers=_cors_headers())


# ─────────────────────────── BOT HANDLERLAR ──────────────────────

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    uid = message.from_user.id
    await clean_chat(uid, message.chat.id)
    get_user(uid)
    name = message.from_user.first_name

    msg = await message.answer(
        f"👋 Salom, <b>{name}</b>!\n\n"
        "🎯 <b>Test Master</b> — savollardan test yasab, bilimingizni sinab ko'ring.\n\n"
        "📎 Fayl yuboring <b>yoki</b> tugmani bosib Web App oching:",
        parse_mode="HTML",
        reply_markup=main_kb(),
    )
    await track(uid, msg.message_id)


@dp.message(F.text == "📊 Natijalarim")
async def cmd_results(message: Message):
    uid = message.from_user.id
    await clean_chat(uid, message.chat.id)
    results = users.get(str(uid), {}).get("results", [])

    if not results:
        msg = await message.answer(
            "📊 Hali natija yo'q.\n\n🌐 Test boshlang!",
            reply_markup=main_kb(),
        )
        await track(uid, msg.message_id)
        return

    avg = sum(r["percentage"] for r in results) / len(results)
    best = max(results, key=lambda x: x["percentage"])
    total_tests = len(results)

    lines = []
    for r in results[-8:]:
        grade, em = grade_info(r["percentage"])
        bar = "█" * int(r["percentage"] / 10) + "░" * (10 - int(r["percentage"] / 10))
        lines.append(f"{em} <code>{bar}</code> {r['percentage']:.0f}% — {r['date']}")

    text = (
        f"📊 <b>Natijalarim</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"Jami: <b>{total_tests} ta test</b>  ·  O'rtacha: <b>{avg:.0f}%</b>\n"
        f"🏆 Eng yaxshi: <b>{best['percentage']:.0f}%</b> ({best['date']})\n\n"
        + "\n".join(lines)
    )
    msg = await message.answer(text, parse_mode="HTML", reply_markup=main_kb())
    await track(uid, msg.message_id)


@dp.message(F.text == "🆘 Yordam")
async def cmd_help(message: Message):
    uid = message.from_user.id
    await clean_chat(uid, message.chat.id)
    msg = await message.answer(
        "🆘 <b>Yordam</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>Foydalanish:</b>\n"
        "1. Fayl yuboring yoki 🚀 tugmasini bosing\n"
        "2. Test sonini tanlang\n"
        "3. Javob bering → ⏭ bosing\n\n"
        "<b>TXT format (#):</b>\n"
        "<code># Savol\n+ To'g'ri javob\n- Noto'g'ri 1\n- Noto'g'ri 2\n- Noto'g'ri 3</code>\n\n"
        "<b>TXT format (A/B/C/D):</b>\n"
        "<code>1. Savol\nA) To'g'ri\nB) Noto'g'ri\nC) Noto'g'ri\nD) Noto'g'ri\nJavob: A</code>\n\n"
        "<b>DOCX/XLSX:</b> 5 ustunli jadval\n"
        "<i>(1: Savol, 2–5: Variantlar)</i>\n\n"
        "💬 Murojaat: @Rustamov_v1",
        parse_mode="HTML",
        reply_markup=main_kb(),
    )
    await track(uid, msg.message_id)


@dp.message(F.document)
async def handle_doc(message: Message, state: FSMContext):
    uid = message.from_user.id
    await clean_chat(uid, message.chat.id)
    doc = message.document
    fname = (doc.file_name or "fayl").lower()

    ext = next((e for e in SUPPORTED_EXT if fname.endswith(e)), None)
    if not ext:
        msg = await message.answer(
            "❌ Qo'llab-quvvatlanmaydi.\n\n"
            "✅ Qabul qilinadi: <code>.txt .docx .xlsx .pdf</code>",
            parse_mode="HTML",
            reply_markup=main_kb(),
        )
        await track(uid, msg.message_id)
        return

    loading = await message.answer("⏳ Tahlil qilinmoqda...")
    await track(uid, loading.message_id)

    save_path = None
    try:
        cleanup_temp()
        tg_file = await bot.get_file(doc.file_id)
        downloaded = await bot.download_file(tg_file.file_path)
        save_path = os.path.join("temp", f"u{uid}_{int(time.time())}{ext}")
        with open(save_path, "wb") as f:
            f.write(downloaded.read())

        parse_ext = ext
        if ext == ".doc":
            converted = save_path.replace(".doc", "_conv.docx")
            try:
                await asyncio.get_event_loop().run_in_executor(
                    None, convert_doc_to_docx, save_path, converted
                )
                save_path = converted
                parse_ext = ".docx"
            except RuntimeError as e:
                await clean_chat(uid, message.chat.id)
                msg = await message.answer(
                    f"❌ <b>.doc faylni o'qib bo'lmadi</b>\n\n"
                    f"<code>{str(e)[:200]}</code>\n\n"
                    f"💡 Iltimos, faylni <b>.docx</b> formatga saqlab qaytadan yuboring.",
                    parse_mode="HTML",
                    reply_markup=main_kb(),
                )
                await track(uid, msg.message_id)
                return

        parsers = {".txt": parse_txt, ".docx": parse_docx, ".xlsx": parse_xlsx, ".pdf": parse_pdf}
        questions = parsers.get(parse_ext, lambda p: [])(save_path)

        await clean_chat(uid, message.chat.id)

        if not questions:
            msg = await message.answer(
                "❌ <b>Savol topilmadi.</b>\n\n"
                "Fayl formatini tekshiring. Yordam: /start",
                parse_mode="HTML",
                reply_markup=main_kb(),
            )
            await track(uid, msg.message_id)
            return

        ud = get_user(uid)
        docs = ud.get("uploaded_docs", [])
        docs.append({
            "file_name": doc.file_name,
            "file_path": save_path,
            "questions": questions,
            "uploaded_at": uz_time_str(),
        })
        if len(docs) > 20:
            docs = docs[-20:]

        ud.update({
            "questions": questions,
            "file_name": doc.file_name,
            "uploaded_docs": docs,
        })
        save_user(uid)
        await state.set_state(TestState.choosing_count)

        msg = await message.answer(
            f"✅ <b>{doc.file_name}</b>\n"
            f"📚 <b>{len(questions)} ta savol</b> topildi\n\n"
            f"Nechta savoldan test qilasiz?",
            parse_mode="HTML",
            reply_markup=count_kb(len(questions)),
        )
        await track(uid, msg.message_id)

    except Exception as e:
        await clean_chat(uid, message.chat.id)
        msg = await message.answer(
            f"❌ Xatolik: <code>{str(e)[:200]}</code>",
            parse_mode="HTML",
            reply_markup=main_kb(),
        )
        await track(uid, msg.message_id)


@dp.callback_query(F.data.startswith("cnt_"))
async def cb_count(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    key = str(uid)
    if key not in users or "questions" not in users[key]:
        await callback.answer("❌ Avval fayl yuklang!", show_alert=True)
        return

    val = callback.data[4:]
    total = len(users[key]["questions"])

    if val == "rand":
        count = random.randint(min(5, total), min(50, total))
    elif val == "custom":
        try:
            await callback.message.edit_text(
                "✍️ Nechta savol? Raqam yuboring:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="❌ Bekor", callback_data="cancel")]
                ]),
            )
        except Exception:
            pass
        await callback.answer()
        return
    else:
        try:
            count = int(val)
        except ValueError:
            await callback.answer("❌ Xatolik", show_alert=True)
            return

    if count < 1 or count > total:
        count = min(count, total)

    await safe_delete(callback.message.chat.id, callback.message.message_id)
    await start_test(callback.message, uid, count, state)
    await callback.answer()

@dp.message(TestState.choosing_count)
async def cb_custom_count(message: Message, state: FSMContext):
    uid = message.from_user.id
    key = str(uid)
    if key not in users or "questions" not in users[key]:
        await state.clear()
        return
    try:
        count = int(message.text.strip())
        total = len(users[key]["questions"])
        if count < 1 or count > total:
            msg = await message.answer(f"❌ 1 dan {total} gacha kiriting:")
            await track(uid, msg.message_id)
            return
        await clean_chat(uid, message.chat.id)
        await start_test(message, uid, count, state)
    except (ValueError, TypeError):
        msg = await message.answer("❌ Faqat raqam kiriting:")
        await track(uid, msg.message_id)

@dp.callback_query(F.data == "cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    uid = callback.from_user.id
    clear_session(uid)
    await safe_delete(callback.message.chat.id, callback.message.message_id)
    await clean_chat(uid, callback.message.chat.id)
    msg = await bot.send_message(
        callback.message.chat.id,
        "❌ Bekor qilindi.",
        reply_markup=main_kb(),
    )
    await track(uid, msg.message_id)
    await callback.answer()


async def start_test(message: Message, uid: int, count: int, state: FSMContext):
    await clean_chat(uid, message.chat.id)
    key = str(uid)
    pool = copy.deepcopy(users[key]["questions"])
    random.shuffle(pool)
    selected = pool[:count]
    for q in selected:
        random.shuffle(q["options"])

    users[key].update({
        "selected_questions": selected,
        "total_test": count,
        "current_index": 0,
        "score": 0,
        "answers": [],
        "waiting_for_skip": False,
        "current_answer_recorded": False,
        "current_poll_message_id": None,
        "current_poll_id": None,
        "current_question_index": 0,
        "test_start_time": uz_time_str(),
    })
    await state.set_state(TestState.testing)

    msg = await message.answer(
        f"🚀 <b>Test boshlandi</b> — {count} ta savol\n"
        f"<i>Har javobdan so'ng ⏭ bosing</i>",
        parse_mode="HTML",
    )
    await track(uid, msg.message_id)
    await asyncio.sleep(0.8)
    await clean_chat(uid, message.chat.id)
    await send_poll(message.chat.id, uid)

async def send_poll(chat_id: int, uid: int):
    key = str(uid)
    data = users.get(key)
    if not data:
        return

    idx = data.get("current_index", 0)
    selected = data.get("selected_questions", [])
    if idx >= len(selected):
        return

    qd = selected[idx]
    opts = [_norm(o) for o in qd["options"]]
    ans = _norm(qd["answer"])

    try:
        correct_id = opts.index(ans)
    except ValueError:
        correct_id = 0
        if opts:
            opts[0] = ans

    q_text = str(qd["question"]).strip()[:300]
    opts = [o[:99] for o in opts]

    prev_id = data.get("current_poll_message_id")
    if prev_id:
        await safe_delete(chat_id, prev_id)
    await clean_chat(uid, chat_id)

    try:
        pm = await bot.send_poll(
            chat_id=chat_id,
            question=f"{idx + 1}/{data['total_test']}  {q_text}",
            options=opts,
            type="quiz",
            correct_option_id=correct_id,
            explanation=f"✅ To'g'ri: {ans[:200]}",
            is_anonymous=False,
            reply_markup=poll_kb(),
        )
        data.update({
            "current_poll_id": pm.poll.id,
            "current_question_index": idx,
            "current_poll_message_id": pm.message_id,
            "waiting_for_skip": True,
            "current_answer_recorded": False,
        })
    except Exception as e:
        print(f"Poll xatoligi: {e}")
        em = await bot.send_message(chat_id, "⚠️ Savol yuborishda xatolik. ⏭ bosing.", reply_markup=poll_kb())
        data["current_poll_message_id"] = em.message_id
        data["waiting_for_skip"] = True

@dp.poll_answer()
async def on_answer(poll_answer):
    uid = poll_answer.user.id
    key = str(uid)
    data = users.get(key)
    if not data or "selected_questions" not in data:
        return
    if poll_answer.poll_id != data.get("current_poll_id"):
        return
    if data.get("current_answer_recorded") or not poll_answer.option_ids:
        return

    cidx = data.get("current_question_index", 0)
    selected = data.get("selected_questions", [])
    if cidx >= len(selected):
        return

    qd = selected[cidx]
    try:
        chosen = qd["options"][poll_answer.option_ids[0]]
    except IndexError:
        return

    correct = chosen == qd["answer"]
    data.setdefault("answers", []).append({
        "question": qd["question"],
        "user_answer": chosen,
        "correct_answer": qd["answer"],
        "is_correct": correct,
    })
    if correct:
        data["score"] = data.get("score", 0) + 1
    data["current_answer_recorded"] = True

@dp.callback_query(F.data == "hint")
async def cb_hint(callback: CallbackQuery):
    uid = callback.from_user.id
    key = str(uid)
    data = users.get(key)
    if not data or "selected_questions" not in data:
        await callback.answer("❌ Test topilmadi", show_alert=True)
        return
    cidx = data.get("current_question_index", 0)
    selected = data.get("selected_questions", [])
    if cidx >= len(selected):
        await callback.answer("❌ Savol topilmadi", show_alert=True)
        return
    await callback.answer(f"✅ To'g'ri:\n{selected[cidx]['answer'][:200]}", show_alert=True)

@dp.callback_query(F.data == "next_q")
async def cb_next(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    key = str(uid)
    data = users.get(key)
    if not data:
        await callback.answer("❌ Test topilmadi", show_alert=True)
        return
    if not data.get("waiting_for_skip"):
        await callback.answer("⏳ Avval javob bering!", show_alert=True)
        return

    cidx = data.get("current_question_index", 0)
    selected = data.get("selected_questions", [])
    if not data.get("current_answer_recorded") and cidx < len(selected):
        data.setdefault("answers", []).append({
            "question": selected[cidx]["question"],
            "user_answer": "O'tkazib yuborildi",
            "correct_answer": selected[cidx]["answer"],
            "is_correct": False,
        })

    data["waiting_for_skip"] = False
    data["current_answer_recorded"] = False
    data["current_index"] = data.get("current_index", 0) + 1

    if data["current_index"] >= data.get("total_test", 0):
        await clean_chat(uid, callback.message.chat.id)
        await safe_delete(callback.message.chat.id, callback.message.message_id)
        await show_result(callback.message.chat.id, uid)
        await state.clear()
        await callback.answer("✅ Yakunlandi!")
        return

    await callback.answer()
    await clean_chat(uid, callback.message.chat.id)
    await send_poll(callback.message.chat.id, uid)

@dp.callback_query(F.data == "end_test")
async def cb_end(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    key = str(uid)
    data = users.get(key)
    if not data:
        await callback.answer("❌ Test topilmadi", show_alert=True)
        return

    cidx = data.get("current_question_index", 0)
    answers = data.get("answers", [])
    selected = data.get("selected_questions", [])
    if len(answers) <= cidx < len(selected):
        answers.append({
            "question": selected[cidx]["question"],
            "user_answer": "Yakunlandi",
            "correct_answer": selected[cidx]["answer"],
            "is_correct": False,
        })
        data["answers"] = answers

    await clean_chat(uid, callback.message.chat.id)
    await safe_delete(callback.message.chat.id, callback.message.message_id)
    await show_result(callback.message.chat.id, uid, stopped=True)
    await state.clear()
    await callback.answer("⏹ Yakunlandi")


async def show_result(chat_id: int, uid: int, stopped: bool = False):
    key = str(uid)
    data = users.get(key, {})
    score = data.get("score", 0)
    total = data.get("total_test", 0)
    answered = len(data.get("answers", []))
    pct = (score / total * 100) if total else 0
    grade, emoji = grade_info(pct)

    time_str = ""
    start_str = data.get("test_start_time")
    if start_str:
        try:
            start = datetime.strptime(start_str, "%d.%m.%Y %H:%M").replace(tzinfo=UZBEKISTAN_TZ)
            diff = int((uz_time() - start).total_seconds())
            m, s = diff // 60, diff % 60
            time_str = f"  ·  ⏱ {m}:{s:02d}"
        except Exception:
            pass

    filled = int(pct / 10)
    bar = "█" * filled + "░" * (10 - filled)

    text = (
        f"{emoji} <b>{grade}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"<code>{bar}</code>  <b>{pct:.0f}%</b>{time_str}\n\n"
        f"✅ To'g'ri:    <b>{score}</b>\n"
        f"❌ Noto'g'ri:  <b>{max(0, total - score)}</b>\n"
        f"📝 Jami:       <b>{total}</b>\n"
    )
    if stopped:
        text += f"\n<i>⚠️ Test to'xtatildi ({answered}/{total} javob berildi)</i>"
    else:
        text += f"\n<i>🎊 Test yakunlandi!</i>"

    data["total_tests"] = data.get("total_tests", 0) + 1
    data["total_correct"] = data.get("total_correct", 0) + score
    data.setdefault("results", []).append({
        "date": uz_time_str(),
        "total": total,
        "score": score,
        "percentage": pct,
        "grade": grade,
    })
    if len(data["results"]) > 100:
        data["results"] = data["results"][-100:]
    save_user(uid)

    msg = await bot.send_message(
        chat_id, text, parse_mode="HTML", reply_markup=result_kb()
    )
    await track(uid, msg.message_id)

@dp.callback_query(F.data == "details")
async def cb_details(callback: CallbackQuery):
    uid = callback.from_user.id
    answers = users.get(str(uid), {}).get("answers", [])
    if not answers:
        await callback.answer("❌ Javoblar yo'q", show_alert=True)
        return

    text = "📋 <b>Batafsil</b>\n━━━━━━━━━━━━━━━━━━━\n\n"
    for i, a in enumerate(answers, 1):
        icon = "✅" if a["is_correct"] else "❌"
        q = a["question"][:55] + ("…" if len(a["question"]) > 55 else "")
        text += f"<b>{i}.</b> {q}\n   {icon} {a['user_answer']}"
        if not a["is_correct"]:
            text += f"   →   ✅ {a['correct_answer']}"
        text += "\n\n"

        if len(text) > 3500:
            msg = await callback.message.answer(text, parse_mode="HTML")
            await track(uid, msg.message_id)
            text = ""

    if text.strip():
        msg = await callback.message.answer(text, parse_mode="HTML")
        await track(uid, msg.message_id)
    await callback.answer()

@dp.callback_query(F.data == "history")
async def cb_history(callback: CallbackQuery):
    uid = callback.from_user.id
    results = users.get(str(uid), {}).get("results", [])
    if not results:
        await callback.answer("❌ Natijalar yo'q", show_alert=True)
        return

    text = "📊 <b>So'nggi natijalar</b>\n━━━━━━━━━━━━━━━━━━━\n\n"
    for r in results[-10:]:
        grade, em = grade_info(r["percentage"])
        bar = "█" * int(r["percentage"] / 10) + "░" * (10 - int(r["percentage"] / 10))
        text += (
            f"{em} <code>{bar}</code> <b>{r['percentage']:.0f}%</b>\n"
            f"   {r['score']}/{r['total']} — {r['date']}\n\n"
        )

    msg = await callback.message.answer(text, parse_mode="HTML", reply_markup=main_kb())
    await track(uid, msg.message_id)
    await callback.answer()


@dp.message(F.web_app_data)
async def web_app_data_handler(message: Message):
    uid = message.from_user.id
    try:
        data = json.loads(message.web_app_data.data)
        action = data.get("action")

        if action == "test_completed":
            score = data.get("score", 0)
            total = data.get("total", 0)
            pct = (score / total * 100) if total else 0
            grade, emoji = grade_info(pct)
            filled = int(pct / 10)
            bar = "█" * filled + "░" * (10 - filled)

            ud = get_user(uid)
            ud["total_tests"] = ud.get("total_tests", 0) + 1
            ud["total_correct"] = ud.get("total_correct", 0) + score
            ud.setdefault("results", []).append({
                "date": uz_time_str(), "total": total,
                "score": score, "percentage": pct, "grade": grade,
            })
            if len(ud["results"]) > 100:
                ud["results"] = ud["results"][-100:]
            save_user(uid)

            msg = await message.answer(
                f"{emoji} <b>Web App natijasi</b>\n\n"
                f"<code>{bar}</code>  <b>{pct:.0f}%</b>\n\n"
                f"✅ {score}  ❌ {total - score}  📝 {total}",
                parse_mode="HTML",
                reply_markup=main_kb(),
            )
            await track(uid, msg.message_id)

    except Exception as e:
        print(f"⚠️ Web app data: {e}")


# ─────────────────────────── MAIN ────────────────────────────────

async def main():
    print("🚀 Bot ishga tushdi")
    print(f"🌐 Web App: {WEBAPP_URL}")
    cleanup_temp()
    
    # Web serverni ishga tushirish
    app = web.Application()
    app.router.add_get("/", serve_webapp)
    app.router.add_get("/health", health)
    app.router.add_get("/webapp", serve_webapp)
    app.router.add_get("/webapp/", serve_webapp)
    app.router.add_get("/api/files/{uid}", api_files)
    app.router.add_get("/api/results/{uid}", api_results)
    app.router.add_route("OPTIONS", "/api/files/{uid}", api_options)
    app.router.add_route("OPTIONS", "/api/results/{uid}", api_options)
    
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"🌐 Web server: port {port}")
    
    # Botni polling bilan ishga tushirish
    await dp.start_polling(
        bot,
        allowed_updates=["message", "callback_query", "poll_answer"],
    )

if __name__ == "__main__":
    asyncio.run(main())
