def normalize_text(s):
    """Matnni tozalash"""
    if not s:
        return ''
    s = s.strip()
    s = s.replace('\xa0', ' ').replace(' ', ' ')
    s = ' '.join(s.split())  # Ko'p bo'sh joylarni bitta qilish
    return s


def extract_correct_marker(text):
    """Variant matnidan to'g'ri javob belgisini ajratish
    Belgilar: +, *, ✓, ✔, ✅, [to'g'ri], (to'g'ri), yoki birinchi variant
    Qaytaradi: (tozalangan_matn, to_g'ri_mi)
    """
    text = normalize_text(text)
    if not text:
        return '', False

    is_correct = False
    clean = text

    # + bilan boshlangan
    if clean.startswith('+'):
        is_correct = True
        clean = clean[1:].strip()
    # * bilan boshlangan
    elif clean.startswith('*'):
        is_correct = True
        clean = clean[1:].strip()
    # ✓ yoki ✔ bilan boshlangan
    elif clean.startswith('✓') or clean.startswith('✔') or clean.startswith('✅'):
        is_correct = True
        clean = clean[1:].strip()
    # [to'g'ri] yoki (to'g'ri) yoki [+] yoki (*) kabi markerlar
    elif clean.startswith('[+]') or clean.startswith('[*]'):
        is_correct = True
        clean = clean[3:].strip()
    elif clean.startswith('[to') and 'gri' in clean.lower():
        is_correct = True
        clean = clean.split(']', 1)[-1].strip() if ']' in clean else clean
    elif clean.startswith('(to') and 'gri' in clean.lower():
        is_correct = True
        clean = clean.split(')', 1)[-1].strip() if ')' in clean else clean
    elif ' - to\'g\'ri' in clean.lower():
        is_correct = True
        clean = re.sub(r'\s*-\s*to.?g.?ri', '', clean, flags=re.IGNORECASE).strip()
    elif ' - togri' in clean.lower():
        is_correct = True
        clean = re.sub(r'\s*-\s*togri', '', clean, flags=re.IGNORECASE).strip()

    return clean, is_correct


def strip_option_marker(text):
    """Variantlardan A), B), 1), -, • kabi markerlarni olib tashlash"""
    text = normalize_text(text)
    if not text:
        return ''

    # A) Boshqarish ... yoki A. Boshqarish ...
    m = re.match(r'^[A-Da-d][\.\)]\s*(.+)$', text)
    if m:
        return m.group(1).strip()

    # 1) Boshqarish ... yoki 1. Boshqarish ...
    m = re.match(r'^\d{1,2}[\.\)]\s*(.+)$', text)
    if m:
        return m.group(1).strip()

    # - Boshqarish ... yoki • Boshqarish ...
    m = re.match(r'^[-•·▪▫◦○●]\s*(.+)$', text)
    if m:
        return m.group(1).strip()

    return text


def parse_text_content(text):
    """Matndan savollarni ajratish

    Qo'llab-quvvatlanadigan formatlar:

    FORMAT 1 — 5 ta ustunli jadval (| bilan):
    Savol|To'g'ri javob|Xato 1|Xato 2|Xato 3

    FORMAT 2 — Belgi bilan (to'g'ri javob + bilan):
    Menejment – bu
    + Boshqarish va rahbarlikni tashkil etish
    - Qo'yilgan maqsadga intilish
    - Boshqaruv haqidagi tasavvur
    - Samarali boshqaruv

    FORMAT 3 — Klassik (birinchi variant to'g'ri deb olinadi):
    Menejment – bu?
    - Boshqarish
    - Maqsadga intilish
    - Tasavvur
    - Samarali boshqaruv

    FORMAT 4 — A) B) C) D):
    1. Menejment – bu?
    A) Boshqarish
    B) Maqsadga intilish
    C) Tasavvur
    D) Samarali boshqaruv
    """
    lines = [l for l in text.split('\n')]
    lines = [l for l in lines if normalize_text(l)]  # Bo'sh qatorlarni tashlash
    questions = []

    # ━━━ FORMAT 1: 5 ta ustunli (| yoki tab) ━━━
    for line in lines:
        if '|' in line:
            parts = [p.strip() for p in line.split('|')]
            parts = [p for p in parts if p]
        elif '\t' in line:
            parts = [p.strip() for p in line.split('\t')]
            parts = [p for p in parts if p]
        else:
            continue

        if len(parts) >= 5:
            q_text = normalize_text(parts[0])
            correct = normalize_text(parts[1])
            wrong = [normalize_text(p) for p in parts[2:5] if normalize_text(p)]
            if q_text and correct:
                all_opts = [correct] + wrong[:3]
                while len(all_opts) < 4:
                    all_opts.append('')
                questions.append({
                    'text': q_text,
                    'options': all_opts[:4],
                    'correct': 'A'
                })

    if questions:
        return questions

    # ━━━ FORMAT 2-4: Savol + variantlar bloki ━━━
    # Savol va variantlarni aniqlash
    blocks = []
    current_q = None
    current_opts = []

    for line in lines:
        line_clean = normalize_text(line)
        if not line_clean:
            continue

        # Bu variant ehtimoli?
        # Belgilar: +, *, ✓, ✔, ✅, -, •, ·, ▪, A), A., 1), 1., [variant]
        is_option = False
        option_text = line_clean

        # +, *, ✓ bilan boshlangan variantlar
        if line_clean[0] in '+*✓✔✅':
            is_option = True
            option_text = line_clean[1:].strip()
        # -, •, ·, ▪ bilan boshlangan
        elif line_clean[0] in '-•·▪▫◦○●':
            is_option = True
            option_text = line_clean[1:].strip()
        # A) B) C) D)
        elif re.match(r'^[A-Da-d][\.\)]\s', line_clean):
            is_option = True
            option_text = re.sub(r'^[A-Da-d][\.\)]\s*', '', line_clean)
        # 1) 2) 3) 4) yoki 1. 2. 3. 4.
        elif re.match(r'^\d{1,2}[\.\)]\s', line_clean):
            is_option = True
            option_text = re.sub(r'^\d{1,2}[\.\)]\s*', '', line_clean)

        if is_option:
            current_opts.append(line_clean)  # Asl satrni saqlaymiz (marker bilan)
        else:
            # Yangi savol — avvalgisini saqlash
            if current_q and current_opts:
                blocks.append((current_q, current_opts))
            current_q = line_clean
            current_opts = []

    # Oxirgi blokni qo'shish
    if current_q and current_opts:
        blocks.append((current_q, current_opts))

    # Har bir blokni qayta ishlash
    for q_text, opts_lines in blocks:
        options = []
        correct_idx = 0  # Default: birinchi variant to'g'ri

        found_explicit_correct = False

        for idx, opt_line in enumerate(opts_lines):
            # To'g'ri javob belgisini tekshirish
            clean_opt, is_correct = extract_correct_marker(opt_line)
            clean_opt = strip_option_marker(clean_opt)

            if not clean_opt:
                continue

            options.append(clean_opt)

            if is_correct and not found_explicit_correct:
                correct_idx = len(options) - 1
                found_explicit_correct = True

        # Savol matnidan raqam va belgilarni tozalash
        q_text = re.sub(r'^\d{1,3}[\.\)]\s*', '', q_text)
        q_text = q_text.strip()

        if len(options) >= 2 and q_text:
            # 4 tagacha to'ldiramiz
            while len(options) < 4:
                options.append('')
            questions.append({
                'text': q_text,
                'options': options[:4],
                'correct': chr(ord('A') + correct_idx) if correct_idx < 4 else 'A'
            })

    return questions


def parse_html_content(html):
    """DOCX dan olingan HTML dan savollarni ajratish

    Qo'llab-quvvatlanadigan formatlar:
    - 5 ta ustunli jadval: [Savol | To'g'ri | Xato 1 | Xato 2 | Xato 3]
    - 2 ta ustunli jadval: [Savol | To'g'ri javob] (faqat to'g'ri javob, xatolar yo'q)
    - Matn bloki (DOCX ichida paragraf ko'rinishida)
    """
    soup = BeautifulSoup(html, 'html.parser')
    questions = []

    # ━━━ JADVAL FORMATI ━━━
    tables = soup.find_all('table')

    for table in tables:
        rows = table.find_all('tr')
        if len(rows) < 1:
            continue

        for row in rows:
            cells = row.find_all(['td', 'th'])

            if len(cells) >= 5:
                # 5 ta ustunli format
                q_text = normalize_text(cells[0].get_text())
                correct = normalize_text(cells[1].get_text())
                wrong = []
                for i in range(2, min(5, len(cells))):
                    txt = normalize_text(cells[i].get_text())
                    if txt:
                        wrong.append(txt)

                if q_text and correct and len(wrong) >= 1:
                    all_opts = [correct] + wrong[:3]
                    while len(all_opts) < 4:
                        all_opts.append('')
                    questions.append({
                        'text': q_text,
                        'options': all_opts[:4],
                        'correct': 'A'
                    })

            elif len(cells) == 2:
                # 2 ta ustunli: savol + to'g'ri javob
                # Xatolarni keyingi qatordan olamiz, yoki default yaratamiz
                q_text = normalize_text(cells[0].get_text())
                correct = normalize_text(cells[1].get_text())

                if q_text and correct and len(q_text) > 3 and len(correct) > 1:
                    # Faqat to'g'ri javob bor, xatolarni 3 ta bo'sh qo'yamiz
                    # (yoki keyingi qatorlardan olish mumkin)
                    all_opts = [correct, '', '', '']
                    questions.append({
                        'text': q_text,
                        'options': all_opts,
                        'correct': 'A'
                    })

    if questions:
        return questions

    # ━━━ MATN FORMATI ━━━
    # DOCX dagi barcha matnni olamiz va parse_text_content ga beramiz
    text = soup.get_text('\n')
    return parse_text_content(text)


