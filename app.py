from flask import Flask, render_template, request, redirect, url_for, flash
import sqlite3, os, re
from datetime import date

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
        CREATE TABLE IF NOT EXISTS students(id INTEGER PRIMARY KEY, class_id INTEGER NOT NULL, student_code TEXT NOT NULL, name TEXT NOT NULL, phone TEXT DEFAULT '', parent_name TEXT DEFAULT '', parent_phone TEXT DEFAULT '', FOREIGN KEY(class_id) REFERENCES classes(id), UNIQUE(class_id,student_code));
        CREATE TABLE IF NOT EXISTS attendance(id INTEGER PRIMARY KEY, student_id INTEGER NOT NULL, day TEXT NOT NULL, status TEXT NOT NULL, note TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS student_events(id INTEGER PRIMARY KEY, student_id INTEGER NOT NULL, day TEXT NOT NULL, event_type TEXT NOT NULL, points REAL DEFAULT 0, note TEXT DEFAULT '', subject TEXT DEFAULT '', lesson TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS lesson_plans(id INTEGER PRIMARY KEY, class_id INTEGER NOT NULL, subject TEXT NOT NULL, lesson TEXT NOT NULL, day TEXT DEFAULT '', period TEXT DEFAULT '', note TEXT DEFAULT '');
        """)
init_db()

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
        plans=c.execute("SELECT * FROM lesson_plans WHERE class_id=? ORDER BY day DESC,id DESC",(cid,)).fetchall()
    return render_template("class.html", cl=cl, students=students, plans=plans, today=date.today().isoformat())

@app.post("/class/<int:cid>/student/add")
def add_student(cid):
    code=request.form.get("student_code","").strip()
    name=request.form.get("name","").strip()
    if not code or not name:
        flash("Cần nhập mã học sinh và họ tên.")
        return redirect(url_for("class_page",cid=cid))
    try:
        with db() as c:
            exists=c.execute("SELECT id FROM classes WHERE id=?",(cid,)).fetchone()
            if not exists: return "Không tìm thấy lớp",404
            c.execute("""INSERT INTO students(class_id,student_code,name,phone,parent_name,parent_phone)
                         VALUES(?,?,?,?,?,?)""",(cid,code,name,request.form.get("phone","").strip(),request.form.get("parent_name","").strip(),request.form.get("parent_phone","").strip()))
        flash("Đã thêm học sinh.")
    except sqlite3.IntegrityError:
        flash("Mã học sinh này đã có trong lớp. Hãy mở hồ sơ để chỉnh sửa.")
    return redirect(url_for("class_page",cid=cid))

@app.post("/student/<int:sid>/edit")
def edit_student(sid):
    name=request.form.get("name","").strip()
    code=request.form.get("student_code","").strip()
    if not name or not code:
        flash("Mã học sinh và họ tên không được để trống.")
        return redirect(url_for("student_page",sid=sid))
    with db() as c:
        row=c.execute("SELECT class_id FROM students WHERE id=?",(sid,)).fetchone()
        if not row: return "Không tìm thấy học sinh",404
        try:
            c.execute("""UPDATE students SET student_code=?,name=?,phone=?,parent_name=?,parent_phone=? WHERE id=?""",
                      (code,name,request.form.get("phone","").strip(),request.form.get("parent_name","").strip(),request.form.get("parent_phone","").strip(),sid))
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
    with db() as c:
        if not c.execute("SELECT id FROM students WHERE id=?",(sid,)).fetchone(): return "Không tìm thấy học sinh",404
        c.execute("INSERT INTO student_events(student_id,day,event_type,points,note,subject,lesson) VALUES(?,?,?,?,?,?,?)",
        (sid,request.form.get("day") or date.today().isoformat(),request.form.get("event_type","").strip(),float(request.form.get("points") or 0),request.form.get("note","").strip(),request.form.get("subject","").strip(),request.form.get("lesson","").strip()))
    flash("Đã ghi nhận sự kiện.")
    return redirect(url_for("student_page",sid=sid))

@app.post("/class/<int:cid>/plan")
def add_plan(cid):
    with db() as c:
        c.execute("INSERT INTO lesson_plans(class_id,subject,lesson,day,period,note) VALUES(?,?,?,?,?,?)",
        (cid,request.form.get("subject","").strip(),request.form.get("lesson","").strip(),request.form.get("day",""),request.form.get("period","").strip(),request.form.get("note","").strip()))
    flash("Đã lưu kế hoạch bài dạy.")
    return redirect(url_for("class_page",cid=cid))

@app.post("/class/<int:cid>/settings")
def settings(cid):
    with db() as c:
        c.execute("UPDATE classes SET school_year=?,homeroom_teacher=?,teacher_phone=? WHERE id=?",
        (request.form.get("school_year",""),request.form.get("homeroom_teacher",""),request.form.get("teacher_phone",""),cid))
    flash("Đã lưu thông tin lớp.")
    return redirect(url_for("class_page",cid=cid))

@app.post("/student/<int:sid>/attendance")
def mark_attendance(sid):
    with db() as c:
        c.execute("INSERT INTO attendance(student_id,day,status,note) VALUES(?,?,?,?)",
        (sid,request.form.get("day") or date.today().isoformat(),request.form.get("status",""),request.form.get("note","").strip()))
    flash("Đã ghi nhận điểm danh.")
    return redirect(url_for("student_page",sid=sid))

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT",5000)))
