from flask import Flask, request, redirect, url_for, render_template, flash, send_file
from werkzeug.utils import secure_filename
import sqlite3, re, os, io
from datetime import date

app=Flask(__name__)
app.secret_key=os.environ.get("SECRET_KEY","change-me")
DB=os.environ.get("DATABASE_PATH","attendance.db")
UPLOAD=os.path.join(os.path.dirname(__file__),"uploads")
os.makedirs(UPLOAD,exist_ok=True)

def db():
    con=sqlite3.connect(DB)
    con.row_factory=sqlite3.Row
    return con

def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS classes(id INTEGER PRIMARY KEY, name TEXT UNIQUE, school_year TEXT);
        CREATE TABLE IF NOT EXISTS students(id INTEGER PRIMARY KEY, class_id INTEGER, stt TEXT, code TEXT, name TEXT, dob TEXT, note TEXT,
        UNIQUE(class_id,code), FOREIGN KEY(class_id) REFERENCES classes(id));
        CREATE TABLE IF NOT EXISTS attendance(id INTEGER PRIMARY KEY, student_id INTEGER, day TEXT, period TEXT, status TEXT,
        UNIQUE(student_id,day,period), FOREIGN KEY(student_id) REFERENCES students(id));
        """)
init_db()

def clean_tex(s):
    return s.replace(r'\&','&').replace(r'\%','%').replace(r'\_','_').strip()

def parse_longtable(src):
    # Read ordinary rows: STT & code & name & date & note \\
    rows=[]
    for line in src.splitlines():
        line=line.strip()
        if not line or line.startswith("%") or "&" not in line or r"\\" not in line:
            continue
        if any(x in line for x in (r"\multicolumn",r"\textbf",r"\endhead",r"\endfoot",r"\endlastfoot")):
            continue
        body=line.split(r"\\",1)[0].strip()
        cells=[clean_tex(x) for x in body.split("&")]
        if len(cells) < 5: continue
        stt,code,name,dob,note=cells[:5]
        if not stt.isdigit() or not re.fullmatch(r"HS[\w-]+",code,re.I):
            continue
        rows.append((stt,code,name,dob,note))
    return rows

@app.route("/")
def index():
    with db() as c:
        classes=c.execute("SELECT cl.*, COUNT(s.id) n FROM classes cl LEFT JOIN students s ON s.class_id=cl.id GROUP BY cl.id ORDER BY cl.name").fetchall()
    return render_template("index.html",classes=classes)

@app.route("/import",methods=["POST"])
def import_tex():
    files=request.files.getlist("files")
    imported=0
    with db() as c:
        for f in files:
            if not f or not f.filename: continue
            filename=secure_filename(f.filename)
            if not filename.lower().endswith(".tex"):
                flash(f"Bỏ qua {filename}: chỉ nhận file .tex"); continue
            content=f.read().decode("utf-8-sig",errors="replace")
            rows=parse_longtable(content)
            if not rows:
                flash(f"Không tìm thấy dòng học sinh hợp lệ trong {filename}"); continue
            class_name=os.path.splitext(filename)[0]
            c.execute("INSERT OR IGNORE INTO classes(name,school_year) VALUES(?,?)",(class_name,""))
            cid=c.execute("SELECT id FROM classes WHERE name=?",(class_name,)).fetchone()["id"]
            for row in rows:
                c.execute("""INSERT INTO students(class_id,stt,code,name,dob,note) VALUES(?,?,?,?,?,?)
                ON CONFLICT(class_id,code) DO UPDATE SET stt=excluded.stt,name=excluded.name,dob=excluded.dob,note=excluded.note""",(cid,*row))
            imported+=1
    flash(f"Đã nhập/cập nhật {imported} file.")
    return redirect(url_for("index"))

@app.route("/class/<int:cid>")
def class_view(cid):
    day=request.args.get("day",date.today().isoformat())
    period=request.args.get("period","1")
    with db() as c:
        cl=c.execute("SELECT * FROM classes WHERE id=?",(cid,)).fetchone()
        if not cl: return "Không tìm thấy lớp",404
        students=c.execute("""SELECT s.*, COALESCE(a.status,'Chưa điểm danh') status FROM students s
        LEFT JOIN attendance a ON a.student_id=s.id AND a.day=? AND a.period=?
        WHERE s.class_id=? ORDER BY CAST(s.stt AS INTEGER),s.id""",(day,period,cid)).fetchall()
    return render_template("class.html",cl=cl,students=students,day=day,period=period)

@app.route("/attendance/<int:cid>",methods=["POST"])
def save_attendance(cid):
    day=request.form.get("day",date.today().isoformat()); period=request.form.get("period","1")
    with db() as c:
        for key,status in request.form.items():
            if key.startswith("status_"):
                sid=int(key[7:])
                c.execute("""INSERT INTO attendance(student_id,day,period,status) VALUES(?,?,?,?)
                ON CONFLICT(student_id,day,period) DO UPDATE SET status=excluded.status""",(sid,day,period,status))
    flash("Đã lưu điểm danh.")
    return redirect(url_for("class_view",cid=cid,day=day,period=period))

@app.route("/student/<int:sid>/edit",methods=["POST"])
def edit_student(sid):
    with db() as c:
        c.execute("UPDATE students SET name=?,code=?,dob=?,note=? WHERE id=?",
                  (request.form["name"],request.form["code"],request.form["dob"],request.form["note"],sid))
        cid=c.execute("SELECT class_id FROM students WHERE id=?",(sid,)).fetchone()["class_id"]
    flash("Đã cập nhật học sinh.")
    return redirect(url_for("class_view",cid=cid))

@app.route("/class/<int:cid>/export")
def export_tex(cid):
    with db() as c:
        cl=c.execute("SELECT * FROM classes WHERE id=?",(cid,)).fetchone()
        students=c.execute("SELECT * FROM students WHERE class_id=? ORDER BY CAST(stt AS INTEGER)",(cid,)).fetchall()
    if not cl: return "Không tìm thấy lớp",404
    out=[r"\documentclass[12pt,a4paper]{book}",r"\usepackage[utf8]{inputenc}",r"\usepackage[T5]{fontenc}",r"\usepackage[vietnamese]{babel}",r"\usepackage{longtable,booktabs,array}",r"\begin{document}",r"\begin{center}\Large\textbf{DANH SÁCH HỌC SINH}\\",r"\textbf{Lớp:} "+cl["name"],r"\end{center}",r"\begin{longtable}{@{} c c p{6cm} c p{3.5cm} @{}}",r"\toprule STT & Mã HS & Họ và Tên & Ngày sinh & Ghi chú \\",r"\midrule"]
    for i,s in enumerate(students,1):
        vals=[str(i),s["code"],s["name"],s["dob"],s["note"] or ""]
        vals=[v.replace("&",r"\&") for v in vals]
        out.append(" & ".join(vals)+r" \\")
    out += [r"\bottomrule",r"\end{longtable}",r"\end{document}"]
    return send_file(io.BytesIO("\n".join(out).encode("utf-8")),as_attachment=True,download_name=cl["name"]+".tex",mimetype="text/plain; charset=utf-8")

if __name__=="__main__":
    app.run(debug=True)
