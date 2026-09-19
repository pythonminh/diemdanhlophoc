from flask import Flask, render_template, request, redirect, url_for, flash
import sqlite3, os, re
from datetime import date
from io import BytesIO
import csv

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret")
DB_PATH = os.environ.get("DATABASE_PATH", "diemdanh.db")

def db():
    conn=sqlite3.connect(DB_PATH)
    conn.row_factory=sqlite3.Row
    return conn

def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS classes(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, school_year TEXT DEFAULT '', homeroom_teacher TEXT DEFAULT '', teacher_phone TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS students(id INTEGER PRIMARY KEY, class_id INTEGER NOT NULL, student_code TEXT NOT NULL, name TEXT NOT NULL, birth_date TEXT DEFAULT '', gender TEXT DEFAULT '', note TEXT DEFAULT '', phone TEXT DEFAULT '', parent_name TEXT DEFAULT '', parent_phone TEXT DEFAULT '', FOREIGN KEY(class_id) REFERENCES classes(id), UNIQUE(class_id,student_code));
        CREATE TABLE IF NOT EXISTS attendance(id INTEGER PRIMARY KEY, student_id INTEGER NOT NULL, day TEXT NOT NULL, status TEXT NOT NULL, note TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS student_events(id INTEGER PRIMARY KEY, student_id INTEGER NOT NULL, day TEXT NOT NULL, event_type TEXT NOT NULL, points REAL DEFAULT 0, note TEXT DEFAULT '', subject TEXT DEFAULT '', lesson TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS lesson_plans(id INTEGER PRIMARY KEY, class_id INTEGER NOT NULL, subject TEXT NOT NULL, lesson TEXT NOT NULL, day TEXT DEFAULT '', period TEXT DEFAULT '', note TEXT DEFAULT '');
        """)
        # Migrate DB created by earlier versions without losing records.
        cols={r["name"] for r in c.execute("PRAGMA table_info(students)").fetchall()}
        for name,typ in [("birth_date","TEXT DEFAULT ''"),("gender","TEXT DEFAULT ''"),("note","TEXT DEFAULT ''"),("phone","TEXT DEFAULT ''"),("parent_name","TEXT DEFAULT ''"),("parent_phone","TEXT DEFAULT ''")]:
            if name not in cols: c.execute(f"ALTER TABLE students ADD COLUMN {name} {typ}")
# Danh mục tích chọn nhanh. Điểm mặc định có thể chỉnh trực tiếp trên giao diện sau này.
EVENT_DEFAULTS = {
    "Xung phong": 1.0,
    "Tích cực": 1.0,
    "Làm tốt lý thuyết": 1.0,
    "Làm chưa tốt lý thuyết": -0.5,
    "Làm tốt bài tập": 1.0,
    "Làm chưa tốt bài tập": -0.5,
    "Chưa làm được bài tập": -1.0,
    "Làm tốt bài áp dụng": 1.0,
    "Làm tốt bài nâng cao": 1.5,
    "Nói chuyện": -1.0,
    "Làm ồn": -1.0,
    "Không làm bài": -1.0,
    "Không ghi bài": -1.0,
    "Chưa làm bài tập": -1.0,
    "Vô lễ": -2.0,
    "Bị phạt": -1.0,
    "Khác": 0.0,
}

init_db()

def parse_tex_students(tex_text):
    """Parse table rows like: 1 & HS001 & NGUYỄN VĂN A & 10/04/2011 & Nam \\\\"""
    records=[]
    for raw in tex_text.splitlines():
        line=raw.strip()
        if not line or line.startswith("%") or "&" not in line:
            continue
        line=re.sub(r"(?<!\\)%.*$","",line).strip()
        line=re.sub(r"\\\\\s*$","",line).strip()
        # Drop LaTeX row decorations and commands.
        if not line or line.startswith("\\") or "textbf" in line.lower():
            continue
        cells=[re.sub(r"\\(?:textbf|textit|emph)\s*\{([^{}]*)\}",r"\1",x).strip() for x in line.split("&")]
        cells=[re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?", "", x).strip() for x in cells]
        cells=[x.replace("{","").replace("}","").replace("~"," ").strip() for x in cells]
        if len(cells)<3: continue
        # Expected columns: ordinal, student code, full name, birth date, gender, optional note.
        code=cells[1] if len(cells)>=4 and re.fullmatch(r"(?:HS|[A-Za-z]*\d+)[A-Za-z0-9_-]*",cells[1],re.I) else ""
        if not code:
            # Also support tables without ordinal: code, name, date, gender.
            code=cells[0] if re.fullmatch(r"(?:HS|[A-Za-z]*\d+)[A-Za-z0-9_-]*",cells[0],re.I) else ""
            if code: cells=[cells[0],*cells[1:]]
        if not code: continue
        if len(cells)>=4 and cells[0].isdigit():
            name=cells[2]
            birth=cells[3] if len(cells)>3 else ""
            gender=cells[4] if len(cells)>4 else ""
            note=cells[5] if len(cells)>5 else ""
        else:
            name=cells[1] if len(cells)>1 else ""
            birth=cells[2] if len(cells)>2 else ""
            gender=cells[3] if len(cells)>3 else ""
            note=cells[4] if len(cells)>4 else ""
        if not name or name.lower() in ("họ và tên","ho va ten"): continue
        records.append({"student_code":code,"name":name,"birth_date":birth,"gender":gender,"note":note})
    # Keep first occurrence of each code.
    unique={}
    for r in records: unique[r["student_code"]]=r
    return list(unique.values())

@app.route("/")
def index():
    with db() as c: classes=c.execute("SELECT * FROM classes ORDER BY name").fetchall()
    return render_template("index.html", classes=classes)

@app.post("/class/add")
def add_class():
    name=request.form.get("name","").strip()
    if not name: flash("Nhập tên lớp."); return redirect(url_for("index"))
    try:
        with db() as c: c.execute("INSERT INTO classes(name) VALUES(?)",(name,))
        flash("Đã tạo lớp.")
    except sqlite3.IntegrityError: flash("Lớp đã tồn tại.")
    return redirect(url_for("index"))

@app.route("/class/<int:cid>")
def class_page(cid):
    with db() as c:
        cl=c.execute("SELECT * FROM classes WHERE id=?",(cid,)).fetchone()
        if not cl: return "Không tìm thấy lớp",404
        students=c.execute("SELECT * FROM students WHERE class_id=? ORDER BY student_code",(cid,)).fetchall()
        # Add an at-a-glance summary of event notes and net points per student.
        student_summaries={}
        for st in students:
            total=c.execute("SELECT COALESCE(SUM(points),0) FROM student_events WHERE student_id=?",(st["id"],)).fetchone()[0]
            recent=c.execute("SELECT day,event_type,points,note,subject,lesson FROM student_events WHERE student_id=? ORDER BY day DESC,id DESC LIMIT 3",(st["id"],)).fetchall()
            all_events=c.execute("SELECT day,event_type,points,note,subject,lesson FROM student_events WHERE student_id=? ORDER BY day DESC,id DESC",(st["id"],)).fetchall()
            attendance=c.execute("SELECT day,status,note FROM attendance WHERE student_id=? ORDER BY day DESC,id DESC",(st["id"],)).fetchall()
            student_summaries[st["id"]]={"total_points":total,"recent_events":recent,"all_events":all_events,"attendance":attendance}
        plans=c.execute("SELECT * FROM lesson_plans WHERE class_id=? ORDER BY day DESC,id DESC",(cid,)).fetchall()
    return render_template("class.html", cl=cl, students=students, student_summaries=student_summaries, plans=plans, today=date.today().isoformat(), event_defaults=EVENT_DEFAULTS)


@app.post("/class/<int:cid>/bulk-record")
def bulk_record(cid):
    day=request.form.get("day") or date.today().isoformat()
    subject=request.form.get("subject","").strip()
    lesson=request.form.get("lesson","").strip()
    saved_att=saved_events=0
    with db() as c:
        if not c.execute("SELECT id FROM classes WHERE id=?",(cid,)).fetchone(): return "Không tìm thấy lớp",404
        students=c.execute("SELECT id FROM students WHERE class_id=?",(cid,)).fetchall()
        for st in students:
            sid=st["id"]
            status=request.form.get(f"attendance_{sid}","").strip()
            note=request.form.get(f"note_{sid}","").strip()
            if status:
                c.execute("INSERT INTO attendance(student_id,day,status,note) VALUES(?,?,?,?)",(sid,day,status,note))
                saved_att+=1
            for packed in request.form.getlist(f"events_{sid}"):
                try: event_type,points_s=packed.rsplit("|",1); points=float(points_s)
                except Exception: event_type,points=packed,0
                custom=request.form.get(f"points_{sid}_{re.sub(r'[^A-Za-z0-9]+','_',event_type)}")
                if custom not in (None,""):
                    try: points=float(custom)
                    except ValueError: pass
                c.execute("""INSERT INTO student_events(student_id,day,event_type,points,note,subject,lesson)
                             VALUES(?,?,?,?,?,?,?)""",(sid,day,event_type,points,note,subject,lesson))
                saved_events+=1
    flash(f"Đã lưu: {saved_att} lượt điểm danh, {saved_events} hoạt động/vi phạm. Có thể xem lại trong hồ sơ từng học sinh.")
    return redirect(url_for("class_page",cid=cid))

@app.post("/class/<int:cid>/import-tex")
def import_tex(cid):
    upload=request.files.get("tex_file")
    if not upload or not upload.filename.lower().endswith(".tex"):
        flash("Chọn một tệp .tex hợp lệ.")
        return redirect(url_for("class_page",cid=cid))
    raw=upload.read()
    try: content=raw.decode("utf-8-sig")
    except UnicodeDecodeError: content=raw.decode("utf-8",errors="replace")
    parsed=parse_tex_students(content)
    if not parsed:
        flash("Không tìm thấy dòng học sinh. Kiểm tra định dạng bảng trong tệp .tex.")
        return redirect(url_for("class_page",cid=cid))
    added=updated=0
    with db() as c:
        if not c.execute("SELECT id FROM classes WHERE id=?",(cid,)).fetchone(): return "Không tìm thấy lớp",404
        for s in parsed:
            old=c.execute("SELECT id FROM students WHERE class_id=? AND student_code=?",(cid,s["student_code"])).fetchone()
            if old:
                c.execute("""UPDATE students SET name=?,birth_date=?,gender=?,note=? WHERE id=?""",
                          (s["name"],s["birth_date"],s["gender"],s["note"],old["id"]))
                updated+=1
            else:
                c.execute("""INSERT INTO students(class_id,student_code,name,birth_date,gender,note) VALUES(?,?,?,?,?,?)""",
                          (cid,s["student_code"],s["name"],s["birth_date"],s["gender"],s["note"]))
                added+=1
    flash(f"Đọc xong {len(parsed)} học sinh: thêm {added}, cập nhật {updated}. Lịch sử đã có được giữ nguyên theo mã học sinh.")
    return redirect(url_for("class_page",cid=cid))

@app.post("/class/<int:cid>/student/add")
def add_student(cid):
    code=request.form.get("student_code","").strip(); name=request.form.get("name","").strip()
    if not code or not name:
        flash("Cần nhập mã học sinh và họ tên."); return redirect(url_for("class_page",cid=cid))
    try:
        with db() as c:
            if not c.execute("SELECT id FROM classes WHERE id=?",(cid,)).fetchone(): return "Không tìm thấy lớp",404
            c.execute("""INSERT INTO students(class_id,student_code,name,birth_date,gender,phone,parent_name,parent_phone)
                         VALUES(?,?,?,?,?,?,?,?)""",(cid,code,name,request.form.get("birth_date",""),request.form.get("gender",""),request.form.get("phone","").strip(),request.form.get("parent_name","").strip(),request.form.get("parent_phone","").strip()))
        flash("Đã thêm học sinh.")
    except sqlite3.IntegrityError: flash("Mã học sinh này đã có trong lớp.")
    return redirect(url_for("class_page",cid=cid))

@app.post("/student/<int:sid>/edit")
def edit_student(sid):
    name=request.form.get("name","").strip(); code=request.form.get("student_code","").strip()
    if not name or not code:
        flash("Mã học sinh và họ tên không được để trống."); return redirect(url_for("student_page",sid=sid))
    with db() as c:
        row=c.execute("SELECT class_id FROM students WHERE id=?",(sid,)).fetchone()
        if not row: return "Không tìm thấy học sinh",404
        try:
            c.execute("""UPDATE students SET student_code=?,name=?,birth_date=?,gender=?,note=?,phone=?,parent_name=?,parent_phone=? WHERE id=?""",
             (code,name,request.form.get("birth_date",""),request.form.get("gender",""),request.form.get("note",""),request.form.get("phone","").strip(),request.form.get("parent_name","").strip(),request.form.get("parent_phone","").strip(),sid))
            flash("Đã cập nhật hồ sơ. Lịch sử vẫn được giữ nguyên.")
        except sqlite3.IntegrityError: flash("Mã học sinh bị trùng trong lớp.")
    return redirect(url_for("student_page",sid=sid))

@app.route("/student/<int:sid>")
def student_page(sid):
    with db() as c:
        s=c.execute("SELECT students.*,classes.name AS class_name FROM students JOIN classes ON classes.id=students.class_id WHERE students.id=?",(sid,)).fetchone()
        if not s: return "Không tìm thấy học sinh",404
        events=c.execute("SELECT * FROM student_events WHERE student_id=? ORDER BY day DESC,id DESC",(sid,)).fetchall()
        attendance=c.execute("SELECT * FROM attendance WHERE student_id=? ORDER BY day DESC,id DESC",(sid,)).fetchall()
    return render_template("student.html",s=s,events=events,attendance=attendance,today=date.today().isoformat())

@app.post("/student/<int:sid>/event")
def add_event(sid):
    try: points=float(request.form.get("points") or 0)
    except ValueError: points=0
    with db() as c:
        if not c.execute("SELECT id FROM students WHERE id=?",(sid,)).fetchone(): return "Không tìm thấy học sinh",404
        c.execute("INSERT INTO student_events(student_id,day,event_type,points,note,subject,lesson) VALUES(?,?,?,?,?,?,?)",
        (sid,request.form.get("day") or date.today().isoformat(),request.form.get("event_type","").strip(),points,request.form.get("note","").strip(),request.form.get("subject","").strip(),request.form.get("lesson","").strip()))
    flash("Đã ghi nhận sự kiện."); return redirect(url_for("student_page",sid=sid))

@app.post("/class/<int:cid>/plan")
def add_plan(cid):
    with db() as c:
        c.execute("INSERT INTO lesson_plans(class_id,subject,lesson,day,period,note) VALUES(?,?,?,?,?,?)",
        (cid,request.form.get("subject","").strip(),request.form.get("lesson","").strip(),request.form.get("day",""),request.form.get("period","").strip(),request.form.get("note","").strip()))
    flash("Đã lưu kế hoạch bài dạy."); return redirect(url_for("class_page",cid=cid))

@app.post("/class/<int:cid>/settings")
def settings(cid):
    with db() as c:
        c.execute("UPDATE classes SET school_year=?,homeroom_teacher=?,teacher_phone=? WHERE id=?",
        (request.form.get("school_year",""),request.form.get("homeroom_teacher",""),request.form.get("teacher_phone",""),cid))
    flash("Đã lưu thông tin lớp."); return redirect(url_for("class_page",cid=cid))

@app.post("/student/<int:sid>/attendance")
def mark_attendance(sid):
    with db() as c:
        c.execute("INSERT INTO attendance(student_id,day,status,note) VALUES(?,?,?,?)",
        (sid,request.form.get("day") or date.today().isoformat(),request.form.get("status",""),request.form.get("note","").strip()))
    flash("Đã ghi nhận điểm danh."); return redirect(url_for("student_page",sid=sid))

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT",5000)))
