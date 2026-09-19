from flask import Flask, render_template, request, redirect, url_for, flash, send_file
import sqlite3, os, re, csv
from datetime import date, datetime
from io import BytesIO
from openpyxl import Workbook, load_workbook
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret")
DB_PATH = os.environ.get("DATABASE_PATH", "diemdanh.db")

# Loại hoạt động và điểm mặc định cho giao diện ghi nhận nhanh.
EVENT_DEFAULTS = {
    "Xung phong": 1,
    "Tích cực": 1,
    "Hoàn thành tốt": 1,
    "Giúp đỡ bạn": 1,
    "Nói chuyện": -1,
    "Làm ồn": -1,
    "Chưa làm bài tập": -1,
    "Không làm bài": -2,
    "Không ghi bài": -1,
    "Vô lễ": -3,
    "Điện thoại trong giờ": -2,
    "Không chuẩn bị bài": -1,
    "Bị phạt": -1,
    "Khác": 0,
}

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS classes(
            id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL,
            school_year TEXT DEFAULT '', homeroom_teacher TEXT DEFAULT '',
            teacher_phone TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS students(
            id INTEGER PRIMARY KEY, class_id INTEGER NOT NULL,
            student_code TEXT NOT NULL, name TEXT NOT NULL,
            birth_date TEXT DEFAULT '', gender TEXT DEFAULT '',
            note TEXT DEFAULT '', phone TEXT DEFAULT '',
            parent_name TEXT DEFAULT '', parent_phone TEXT DEFAULT '',
            FOREIGN KEY(class_id) REFERENCES classes(id),
            UNIQUE(class_id, student_code)
        );
        CREATE TABLE IF NOT EXISTS attendance(
            id INTEGER PRIMARY KEY, student_id INTEGER NOT NULL,
            day TEXT NOT NULL, status TEXT NOT NULL, note TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS student_events(
            id INTEGER PRIMARY KEY, student_id INTEGER NOT NULL,
            day TEXT NOT NULL, event_type TEXT NOT NULL,
            points REAL DEFAULT 0, note TEXT DEFAULT '',
            subject TEXT DEFAULT '', lesson TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS lesson_plans(
            id INTEGER PRIMARY KEY, class_id INTEGER NOT NULL,
            subject TEXT NOT NULL, lesson TEXT NOT NULL,
            day TEXT DEFAULT '', period TEXT DEFAULT '', note TEXT DEFAULT ''
        );
        """)
        cols = {r["name"] for r in c.execute("PRAGMA table_info(students)").fetchall()}
        for name, typ in [
            ("birth_date", "TEXT DEFAULT ''"), ("gender", "TEXT DEFAULT ''"),
            ("note", "TEXT DEFAULT ''"), ("phone", "TEXT DEFAULT ''"),
            ("parent_name", "TEXT DEFAULT ''"), ("parent_phone", "TEXT DEFAULT ''")
        ]:
            if name not in cols:
                c.execute(f"ALTER TABLE students ADD COLUMN {name} {typ}")

init_db()

def parse_date(value):
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y")
    s = str(value).strip()
    if not s:
        return ""
    # Preserve common date formats, normalize ISO date to dd/mm/yyyy.
    try:
        if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", s):
            return datetime.strptime(s, "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        pass
    return s

def clean_cell(value):
    if value is None:
        return ""
    s = str(value).strip()
    s = re.sub(r"\\(?:textbf|textit|emph)\s*\{([^{}]*)\}", r"\1", s)
    s = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?", "", s)
    return s.replace("{", "").replace("}", "").replace("~", " ").strip()

def parse_tex_students(tex_text):
    records = []
    for raw in tex_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("%") or "&" not in line:
            continue
        line = re.sub(r"(?<!\\)%.*$", "", line).strip()
        line = re.sub(r"\\\\\s*$", "", line).strip()
        if not line or line.startswith("\\") or "textbf" in line.lower():
            continue
        cells = [clean_cell(x) for x in line.split("&")]
        if len(cells) < 3:
            continue
        if len(cells) >= 4 and cells[0].isdigit():
            code, name = cells[1], cells[2]
            birth = cells[3] if len(cells) > 3 else ""
            gender = cells[4] if len(cells) > 4 else ""
            note = cells[5] if len(cells) > 5 else ""
        else:
            code, name = cells[0], cells[1]
            birth = cells[2] if len(cells) > 2 else ""
            gender = cells[3] if len(cells) > 3 else ""
            note = cells[4] if len(cells) > 4 else ""
        if not re.fullmatch(r"(?:HS|[A-Za-z]*\d+)[A-Za-z0-9_-]*", code, re.I):
            continue
        if not name or name.lower() in ("họ và tên", "ho va ten"):
            continue
        records.append({
            "student_code": code, "name": name, "birth_date": birth,
            "gender": gender, "phone": "", "parent_name": "",
            "parent_phone": "", "note": note
        })
    return dedupe_students(records)

def dedupe_students(records):
    result = {}
    for row in records:
        code = str(row.get("student_code", "")).strip()
        name = str(row.get("name", "")).strip()
        if code and name:
            row["student_code"] = code
            row["name"] = name
            result[code] = row
    return list(result.values())

def parse_pasted_students(raw):
    """Dòng nhập: Mã HS [TAB] Họ tên [TAB] Ngày sinh [TAB] Giới tính ..."""
    records = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "&" in line and ("\\\\" in line or "HS" in line):
            records.extend(parse_tex_students(line))
            continue
        if "\t" in line:
            cells = [x.strip() for x in line.split("\t")]
        elif "|" in line:
            cells = [x.strip() for x in line.split("|")]
        elif ";" in line:
            cells = [x.strip() for x in line.split(";")]
        else:
            # Space-delimited fallback: infer code as first token and keep
            # remaining name as a whole; optional columns should be tab-separated.
            cells = re.split(r"\s{2,}", line)
            if len(cells) < 2:
                cells = line.split(None, 1)
        if not cells:
            continue
        # Skip header row.
        if cells[0].strip().lower() in ("mã hs", "mã học sinh", "student_code"):
            continue
        # Accept optional ordinal in first column.
        if len(cells) >= 3 and cells[0].isdigit():
            cells = cells[1:]
        if len(cells) < 2:
            continue
        code, name = cells[0].strip(), cells[1].strip()
        if not code or not name:
            continue
        records.append({
            "student_code": code, "name": name,
            "birth_date": parse_date(cells[2]) if len(cells) > 2 else "",
            "gender": cells[3] if len(cells) > 3 else "",
            "phone": cells[4] if len(cells) > 4 else "",
            "parent_name": cells[5] if len(cells) > 5 else "",
            "parent_phone": cells[6] if len(cells) > 6 else "",
            "note": cells[7] if len(cells) > 7 else ""
        })
    return dedupe_students(records)

def upsert_students(cid, records):
    added = updated = 0
    with db() as c:
        if not c.execute("SELECT id FROM classes WHERE id=?", (cid,)).fetchone():
            return None
        for s in dedupe_students(records):
            old = c.execute(
                "SELECT id FROM students WHERE class_id=? AND student_code=?",
                (cid, s["student_code"])
            ).fetchone()
            values = (
                s.get("name", ""), parse_date(s.get("birth_date", "")),
                s.get("gender", ""), s.get("note", ""),
                s.get("phone", ""), s.get("parent_name", ""),
                s.get("parent_phone", "")
            )
            if old:
                c.execute("""UPDATE students SET name=?,birth_date=?,gender=?,
                    note=?,phone=?,parent_name=?,parent_phone=? WHERE id=?""",
                    (*values, old["id"]))
                updated += 1
            else:
                c.execute("""INSERT INTO students
                    (class_id,student_code,name,birth_date,gender,note,phone,parent_name,parent_phone)
                    VALUES(?,?,?,?,?,?,?,?,?)""",
                    (cid, s["student_code"], *values))
                added += 1
    return added, updated

@app.route("/")
def index():
    with db() as c:
        classes = c.execute("SELECT * FROM classes ORDER BY name").fetchall()
    return render_template("index.html", classes=classes)

@app.post("/class/add")
def add_class():
    name = request.form.get("name", "").strip()
    if not name:
        flash("Nhập tên lớp.")
        return redirect(url_for("index"))
    try:
        with db() as c:
            c.execute("INSERT INTO classes(name) VALUES(?)", (name,))
        flash("Đã tạo lớp.")
    except sqlite3.IntegrityError:
        flash("Lớp đã tồn tại.")
    return redirect(url_for("index"))

@app.route("/class/<int:cid>")
def class_page(cid):
    with db() as c:
        cl = c.execute("SELECT * FROM classes WHERE id=?", (cid,)).fetchone()
        if not cl:
            return "Không tìm thấy lớp", 404
        students = c.execute("SELECT * FROM students WHERE class_id=? ORDER BY student_code", (cid,)).fetchall()
        student_summaries = {}
        for st in students:
            sid = st["id"]
            total = c.execute("SELECT COALESCE(SUM(points),0) FROM student_events WHERE student_id=?", (sid,)).fetchone()[0]
            recent = c.execute("""SELECT day,event_type,points,note,subject,lesson
                FROM student_events WHERE student_id=? ORDER BY day DESC,id DESC LIMIT 3""", (sid,)).fetchall()
            all_events = c.execute("""SELECT day,event_type,points,note,subject,lesson
                FROM student_events WHERE student_id=? ORDER BY day DESC,id DESC""", (sid,)).fetchall()
            attendance = c.execute("SELECT day,status,note FROM attendance WHERE student_id=? ORDER BY day DESC,id DESC", (sid,)).fetchall()
            student_summaries[sid] = {"total_points": total, "recent_events": recent, "all_events": all_events, "attendance": attendance}
        plans = c.execute("SELECT * FROM lesson_plans WHERE class_id=? ORDER BY day DESC,id DESC", (cid,)).fetchall()
    return render_template("class.html", cl=cl, students=students,
        student_summaries=student_summaries, plans=plans,
        today=date.today().isoformat(), event_defaults=EVENT_DEFAULTS)

@app.post("/class/<int:cid>/bulk-record")
def bulk_record(cid):
    day = request.form.get("day") or date.today().isoformat()
    subject = request.form.get("subject", "").strip()
    lesson = request.form.get("lesson", "").strip()
    saved_att = saved_events = 0
    with db() as c:
        if not c.execute("SELECT id FROM classes WHERE id=?", (cid,)).fetchone():
            return "Không tìm thấy lớp", 404
        students = c.execute("SELECT id FROM students WHERE class_id=?", (cid,)).fetchall()
        for st in students:
            sid = st["id"]
            status = request.form.get(f"attendance_{sid}", "").strip()
            note = request.form.get(f"note_{sid}", "").strip()
            if status:
                c.execute("INSERT INTO attendance(student_id,day,status,note) VALUES(?,?,?,?)", (sid, day, status, note))
                saved_att += 1
            for packed in request.form.getlist(f"events_{sid}"):
                try:
                    event_type, points_s = packed.rsplit("|", 1)
                    points = float(points_s)
                except Exception:
                    event_type, points = packed, 0
                key = re.sub(r"[^A-Za-z0-9]+", "_", event_type)
                custom = request.form.get(f"points_{sid}_{key}")
                if custom not in (None, ""):
                    try:
                        points = float(custom)
                    except ValueError:
                        pass
                c.execute("""INSERT INTO student_events(student_id,day,event_type,points,note,subject,lesson)
                    VALUES(?,?,?,?,?,?,?)""", (sid, day, event_type, points, note, subject, lesson))
                saved_events += 1
    flash(f"Đã lưu: {saved_att} lượt điểm danh, {saved_events} hoạt động/vi phạm.")
    return redirect(url_for("class_page", cid=cid))

@app.post("/class/<int:cid>/import-text")
def import_text(cid):
    # Hỗ trợ nhiều tên trường form để tương thích các phiên bản giao diện.
    raw = ""
    for key in ("student_text", "students_text", "text_data", "raw_text", "text", "students"):
        if request.form.get(key):
            raw = request.form.get(key, "")
            break
    records = parse_pasted_students(raw)
    if not records:
        flash("Chưa đọc được dòng nào. Mỗi dòng cần có Mã HS và Họ tên; nên ngăn cột bằng phím Tab.")
        return redirect(url_for("class_page", cid=cid))
    result = upsert_students(cid, records)
    if result is None:
        return "Không tìm thấy lớp", 404
    added, updated = result
    flash(f"Đã đọc {len(records)} dòng: thêm {added}, cập nhật {updated}. Lịch sử học sinh được giữ theo mã HS.")
    return redirect(url_for("class_page", cid=cid))

@app.post("/class/<int:cid>/import-xlsx")
def import_xlsx(cid):
    upload = request.files.get("xlsx_file") or request.files.get("excel_file") or request.files.get("file")
    if not upload or not upload.filename.lower().endswith((".xlsx", ".xlsm")):
        flash("Vui lòng chọn file Excel .xlsx hợp lệ.")
        return redirect(url_for("class_page", cid=cid))
    try:
        wb = load_workbook(upload, data_only=True, read_only=True)
        ws = wb["DanhSach"] if "DanhSach" in wb.sheetnames else wb.active
        rows = ws.iter_rows(values_only=True)
        headers = [str(x or "").strip().lower() for x in next(rows, ())]
        aliases = {
            "student_code": ("mã học sinh", "mã hs", "ma hoc sinh", "ma hs", "student_code"),
            "name": ("họ và tên", "họ tên", "ho va ten", "ho ten", "name"),
            "birth_date": ("ngày sinh", "ngay sinh", "birth_date"),
            "gender": ("giới tính", "gioi tinh", "gender"),
            "phone": ("điện thoại hs", "điện thoại học sinh", "sđt hs", "phone"),
            "parent_name": ("họ tên phụ huynh", "tên phụ huynh", "parent_name"),
            "parent_phone": ("điện thoại phụ huynh", "sđt phụ huynh", "parent_phone"),
            "note": ("ghi chú", "note")
        }
        indexes = {}
        for field, names in aliases.items():
            for i, h in enumerate(headers):
                if h in names:
                    indexes[field] = i
                    break
        if "student_code" not in indexes or "name" not in indexes:
            flash("File Excel cần có hai cột bắt buộc: Mã học sinh và Họ và tên.")
            return redirect(url_for("class_page", cid=cid))
        records = []
        for row in rows:
            if not row or not any(v is not None and str(v).strip() for v in row):
                continue
            rec = {}
            for field, idx in indexes.items():
                rec[field] = row[idx] if idx < len(row) else ""
            rec["student_code"] = str(rec.get("student_code") or "").strip()
            rec["name"] = str(rec.get("name") or "").strip()
            rec["birth_date"] = parse_date(rec.get("birth_date"))
            for field in ("gender", "phone", "parent_name", "parent_phone", "note"):
                rec[field] = str(rec.get(field) or "").strip()
            if rec["student_code"] and rec["name"]:
                records.append(rec)
        wb.close()
    except Exception as exc:
        app.logger.exception("Excel import failed")
        flash(f"Không đọc được file Excel: {exc}")
        return redirect(url_for("class_page", cid=cid))
    if not records:
        flash("File Excel không có dòng học sinh hợp lệ.")
        return redirect(url_for("class_page", cid=cid))
    result = upsert_students(cid, records)
    if result is None:
        return "Không tìm thấy lớp", 404
    added, updated = result
    flash(f"Excel: đọc {len(dedupe_students(records))} dòng, thêm {added}, cập nhật {updated}; lịch sử được giữ theo mã HS.")
    return redirect(url_for("class_page", cid=cid))

@app.get("/class/<int:cid>/students-template.xlsx")
def download_students_template(cid):
    with db() as c:
        if not c.execute("SELECT id FROM classes WHERE id=?", (cid,)).fetchone():
            return "Không tìm thấy lớp", 404
    wb = Workbook()
    ws = wb.active
    ws.title = "DanhSach"
    ws.append(["Mã học sinh", "Họ và tên", "Ngày sinh", "Giới tính",
               "Điện thoại HS", "Họ tên phụ huynh", "Điện thoại phụ huynh", "Ghi chú"])
    ws.append(["HS001", "NGUYỄN VĂN A", "10/04/2011", "Nam", "", "", "", ""])
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = "A1:H2"
    for col, width in enumerate([18, 28, 16, 14, 20, 28, 22, 35], 1):
        ws.column_dimensions[chr(64 + col)].width = width
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return send_file(output, as_attachment=True,
        download_name=f"mau_danh_sach_hoc_sinh_lop_{cid}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@app.post("/class/<int:cid>/import-tex")
def import_tex(cid):
    upload = request.files.get("tex_file")
    if not upload or not upload.filename.lower().endswith(".tex"):
        flash("Chọn một tệp .tex hợp lệ.")
        return redirect(url_for("class_page", cid=cid))
    raw = upload.read()
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        content = raw.decode("utf-8", errors="replace")
    parsed = parse_tex_students(content)
    if not parsed:
        flash("Không tìm thấy dòng học sinh. Kiểm tra định dạng bảng trong tệp .tex.")
        return redirect(url_for("class_page", cid=cid))
    result = upsert_students(cid, parsed)
    if result is None:
        return "Không tìm thấy lớp", 404
    added, updated = result
    flash(f"Đọc xong {len(parsed)} học sinh: thêm {added}, cập nhật {updated}. Lịch sử đã có được giữ nguyên.")
    return redirect(url_for("class_page", cid=cid))

@app.post("/class/<int:cid>/student/add")
def add_student(cid):
    code = request.form.get("student_code", "").strip()
    name = request.form.get("name", "").strip()
    if not code or not name:
        flash("Cần nhập mã học sinh và họ tên.")
        return redirect(url_for("class_page", cid=cid))
    try:
        with db() as c:
            if not c.execute("SELECT id FROM classes WHERE id=?", (cid,)).fetchone():
                return "Không tìm thấy lớp", 404
            c.execute("""INSERT INTO students(class_id,student_code,name,birth_date,gender,phone,parent_name,parent_phone)
                VALUES(?,?,?,?,?,?,?,?)""", (cid, code, name, request.form.get("birth_date", ""),
                request.form.get("gender", ""), request.form.get("phone", "").strip(),
                request.form.get("parent_name", "").strip(), request.form.get("parent_phone", "").strip()))
        flash("Đã thêm học sinh.")
    except sqlite3.IntegrityError:
        flash("Mã học sinh này đã có trong lớp.")
    return redirect(url_for("class_page", cid=cid))

@app.post("/student/<int:sid>/edit")
def edit_student(sid):
    name = request.form.get("name", "").strip()
    code = request.form.get("student_code", "").strip()
    if not name or not code:
        flash("Mã học sinh và họ tên không được để trống.")
        return redirect(url_for("student_page", sid=sid))
    with db() as c:
        row = c.execute("SELECT class_id FROM students WHERE id=?", (sid,)).fetchone()
        if not row:
            return "Không tìm thấy học sinh", 404
        try:
            c.execute("""UPDATE students SET student_code=?,name=?,birth_date=?,gender=?,note=?,phone=?,parent_name=?,parent_phone=? WHERE id=?""",
                (code, name, request.form.get("birth_date", ""), request.form.get("gender", ""),
                 request.form.get("note", ""), request.form.get("phone", "").strip(),
                 request.form.get("parent_name", "").strip(), request.form.get("parent_phone", "").strip(), sid))
            flash("Đã cập nhật hồ sơ. Lịch sử vẫn được giữ nguyên.")
        except sqlite3.IntegrityError:
            flash("Mã học sinh bị trùng trong lớp.")
    return redirect(url_for("student_page", sid=sid))

@app.route("/student/<int:sid>")
def student_page(sid):
    with db() as c:
        s = c.execute("""SELECT students.*,classes.name AS class_name
            FROM students JOIN classes ON classes.id=students.class_id WHERE students.id=?""", (sid,)).fetchone()
        if not s:
            return "Không tìm thấy học sinh", 404
        events = c.execute("SELECT * FROM student_events WHERE student_id=? ORDER BY day DESC,id DESC", (sid,)).fetchall()
        attendance = c.execute("SELECT * FROM attendance WHERE student_id=? ORDER BY day DESC,id DESC", (sid,)).fetchall()
    return render_template("student.html", s=s, events=events, attendance=attendance, today=date.today().isoformat())

@app.post("/student/<int:sid>/event")
def add_event(sid):
    try:
        points = float(request.form.get("points") or 0)
    except ValueError:
        points = 0
    event_type = request.form.get("event_type", "").strip()
    if not event_type:
        flash("Chọn loại hoạt động/vi phạm.")
        return redirect(url_for("student_page", sid=sid))
    with db() as c:
        if not c.execute("SELECT id FROM students WHERE id=?", (sid,)).fetchone():
            return "Không tìm thấy học sinh", 404
        c.execute("""INSERT INTO student_events(student_id,day,event_type,points,note,subject,lesson)
            VALUES(?,?,?,?,?,?,?)""", (sid, request.form.get("day") or date.today().isoformat(),
            event_type, points, request.form.get("note", "").strip(),
            request.form.get("subject", "").strip(), request.form.get("lesson", "").strip()))
    flash("Đã ghi nhận sự kiện.")
    return redirect(url_for("student_page", sid=sid))

@app.post("/student/<int:sid>/attendance")
def mark_attendance(sid):
    with db() as c:
        if not c.execute("SELECT id FROM students WHERE id=?", (sid,)).fetchone():
            return "Không tìm thấy học sinh", 404
        c.execute("INSERT INTO attendance(student_id,day,status,note) VALUES(?,?,?,?)",
            (sid, request.form.get("day") or date.today().isoformat(),
             request.form.get("status", ""), request.form.get("note", "").strip()))
    flash("Đã ghi nhận điểm danh.")
    return redirect(url_for("student_page", sid=sid))

@app.post("/class/<int:cid>/plan")
def add_plan(cid):
    with db() as c:
        if not c.execute("SELECT id FROM classes WHERE id=?", (cid,)).fetchone():
            return "Không tìm thấy lớp", 404
        c.execute("INSERT INTO lesson_plans(class_id,subject,lesson,day,period,note) VALUES(?,?,?,?,?,?)",
            (cid, request.form.get("subject", "").strip(), request.form.get("lesson", "").strip(),
             request.form.get("day", ""), request.form.get("period", "").strip(), request.form.get("note", "").strip()))
    flash("Đã lưu kế hoạch bài dạy.")
    return redirect(url_for("class_page", cid=cid))

@app.post("/class/<int:cid>/settings")
def settings(cid):
    with db() as c:
        c.execute("UPDATE classes SET school_year=?,homeroom_teacher=?,teacher_phone=? WHERE id=?",
            (request.form.get("school_year", ""), request.form.get("homeroom_teacher", ""),
             request.form.get("teacher_phone", ""), cid))
    flash("Đã lưu thông tin lớp.")
    return redirect(url_for("class_page", cid=cid))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
