#!/usr/bin/env python3
"""Документы для Telegram-бота: PDF-копии для чтения и подписание подписью __ИМЯ_Р__.
Запускает только код бота (Claude в боте команд не имеет). Нужны: LibreOffice (soffice), pymupdf, python-docx."""
import os, re, shutil, subprocess, tempfile, time

SIG = "/root/.secrets/podpis.png"          # прозрачный PNG подписи, вне синхронизации, права 600
CONVERT_EXT = {".doc", ".docx", ".rtf", ".odt", ".xls", ".xlsx", ".ods", ".ppt", ".pptx", ".odp"}
SIGN_EXT = {".doc", ".docx", ".rtf", ".odt", ".pdf"}
COPY_DAYS = 7


def soffice(src, fmt, outdir):
    """Конвертация LibreOffice; свой профиль на каждый запуск, чтобы запуски не мешали друг другу."""
    prof = tempfile.mkdtemp(prefix="lo_")
    try:
        subprocess.run(["soffice", f"-env:UserInstallation=file://{prof}", "--headless",
                        "--convert-to", fmt, "--outdir", outdir, src],
                       capture_output=True, timeout=180, check=True)
    finally:
        shutil.rmtree(prof, ignore_errors=True)
    out = os.path.join(outdir, os.path.splitext(os.path.basename(src))[0] + "." + fmt.split(":")[0])
    if not os.path.exists(out):
        raise RuntimeError("LibreOffice не создал файл")
    return out


def unique(path):
    stem, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(path):
        path = f"{stem} ({n}){ext}"
        n += 1
    return path


def reading_copy(path, copies_dir):
    """PDF-копия офисного файла, чтобы Claude мог его прочитать. Копии старше 7 дней удаляются."""
    if os.path.splitext(path)[1].lower() not in CONVERT_EXT:
        return None
    os.makedirs(copies_dir, exist_ok=True)
    now = time.time()
    for f in os.listdir(copies_dir):
        p = os.path.join(copies_dir, f)
        if os.path.isfile(p) and now - os.path.getmtime(p) > COPY_DAYS * 86400:
            os.remove(p)
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "src" + os.path.splitext(path)[1].lower())
        shutil.copy(path, src)
        out = soffice(src, "pdf", td)
        dest = unique(os.path.join(copies_dir, os.path.basename(path) + ".pdf"))
        shutil.move(out, dest)
    return dest


def _paragraphs(d):
    def walk(c):
        for p in c.paragraphs:
            yield p
        for t in c.tables:
            for row in t.rows:
                for cell in row.cells:
                    yield from walk(cell)
    yield from walk(d)
    for s in d.sections:
        for part in (s.header, s.footer):
            yield from walk(part)


def _pattern(old):
    """Число подчёркиваний и пробелов в бланке может не совпадать с тем, что видно в PDF: ___ = любая линия, пробелы — любые."""
    out = ""
    for tok in re.findall(r"_{2,}|\s+|.", old):
        out += r"_{2,}" if tok.startswith("__") else r"\s*" if tok.isspace() else re.escape(tok)
    return re.compile(out)


def replace_in_docx(path, pairs):
    """Замены текста. Если текст разбит на куски с разным форматом — абзац собирается в первый кусок."""
    import docx
    d = docx.Document(path)
    report = []
    for old, new in pairs:
        pat, n = _pattern(old), 0
        for p in _paragraphs(d):
            if not pat.search(p.text):
                continue
            hit = False
            for r in p.runs:
                if pat.search(r.text):
                    r.text = pat.sub(lambda m: new, r.text)
                    hit = True
            if not hit:
                full = pat.sub(lambda m: new, p.text)
                for i, r in enumerate(p.runs):
                    r.text = full if i == 0 else ""
            n += 1
        report.append((old, new, n))
    d.save(path)
    return report


def place_signature(pdf_in, pdf_out, near, preview_png):
    """Подпись на линию из подчёркиваний слева от текста near (последнее вхождение в документе).
    Нет линии — слева от near, если есть место, иначе над ним. Подпись не залезает на строку выше."""
    import pymupdf
    doc = pymupdf.open(pdf_in)
    hits = [(i, r) for i, pg in enumerate(doc) for r in pg.search_for(near)]
    if not hits:
        raise ValueError(f"не нашёл в документе текст «{near}»")
    i, a = hits[-1]
    pg = doc[i]
    words = pg.get_text("words")
    line = None
    for w in words:
        r = pymupdf.Rect(w[:4])
        if re.match(r"_{4,}", w[4]) and abs(r.y1 - a.y1) < 4 and r.x0 < a.x0 and a.x0 - r.x1 < 60:
            if line is None or r.x1 > line.x1:
                line = r
    sw, sh = pymupdf.Pixmap(SIG).width, pymupdf.Pixmap(SIG).height
    if line:
        w = min(110, max(60, line.width))
        x0, y1 = line.x0 + (line.width - w) / 2, line.y1 + 4
    elif a.x0 - 40 > 110:
        w = 100
        x0, y1 = a.x0 - w - 6, a.y1 + 4
    else:
        w = 100
        x0, y1 = a.x0, a.y0 - 1
    h = w * sh / sw
    above = max((pymupdf.Rect(t[:4]).y1 for t in words
                 if pymupdf.Rect(t[:4]).y1 < y1 - 10 and pymupdf.Rect(t[:4]).x1 > x0 and pymupdf.Rect(t[:4]).x0 < x0 + w),
                default=0)
    if y1 - h < above - 4:  # уменьшаем, чтобы не закрыть текст выше (касание на 4 pt допустимо)
        cx = x0 + w / 2
        h = max(y1 - above + 4, 30)
        w = h * sw / sh
        x0 = cx - w / 2
    pg.insert_image(pymupdf.Rect(x0, y1 - h, x0 + w, y1), filename=SIG, overlay=True)
    doc.save(pdf_out, garbage=3, deflate=True)
    pg.get_pixmap(dpi=110).save(preview_png)
    return i + 1


def sign(src, out_dir, near, pairs, preview_dir):
    """Вписать текст (только Word-форматы), собрать PDF, поставить подпись.
    Возвращает (pdf, docx или None, превью, номер страницы, отчёт о заменах)."""
    ext = os.path.splitext(src)[1].lower()
    if ext not in SIGN_EXT:
        raise ValueError(f"формат {ext} не подписываю: нужен .doc, .docx, .rtf, .odt или .pdf")
    if not os.path.exists(SIG):
        raise RuntimeError("на сервере нет файла подписи")
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(preview_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(src))[0]
    with tempfile.TemporaryDirectory() as td:
        work = os.path.join(td, "doc" + ext)
        shutil.copy(src, work)
        if ext in (".doc", ".rtf", ".odt"):
            work, ext = soffice(work, "docx", td), ".docx"
        report, filled = [], None
        if pairs:
            if ext != ".docx":
                raise ValueError("в PDF текст не вписываю — пришлите документ в Word")
            report = replace_in_docx(work, pairs)
            filled = unique(os.path.join(out_dir, base + " (заполнен).docx"))
            shutil.copy(work, filled)
        pdf = soffice(work, "pdf", td) if ext == ".docx" else work
        out = unique(os.path.join(out_dir, base + " (подписан).pdf"))
        preview = os.path.join(preview_dir, f"sign_{int(time.time())}.png")
        page = place_signature(pdf, out, near, preview)
    return out, filled, preview, page, report
