from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file
import sqlite3, os, re, hashlib, hmac, json, base64, urllib.request, urllib.error
from datetime import timedelta
from datetime import date, datetime
from io import BytesIO
import csv
from openpyxl import Workbook, load_workbook

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "1") == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)
# Resolve a writable SQLite path. Free Render has no /var/data disk → must not crash.
def _path_writable(path):
    parent = os.path.dirname(os.path.abspath(path)) or "."
    try:
        os.makedirs(parent, exist_ok=True)
        probe = os.path.join(parent, ".diemdanh_write_probe")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        return True
    except OSError:
        return False

def resolve_db_path():
    candidates = []
    env = (os.environ.get("DATABASE_PATH") or "").strip()
    if env:
        candidates.append(env)
    if os.environ.get("RENDER") == "true":
        candidates.append("/var/data/diemdanh.db")
    here = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(here, "diemdanh.db"))
    candidates.append("diemdanh.db")
    for path in candidates:
        if _path_writable(path):
            return path
    return "diemdanh.db"

DB_PATH = resolve_db_path()

def ensure_db_dir():
    parent = os.path.dirname(os.path.abspath(DB_PATH))
    if parent:
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError:
            pass

def db_ephemeral_warning():
    """Warn on Render when DB is not on a real Persistent Disk."""
    if os.environ.get("RENDER") != "true":
        return False
    path = os.path.abspath(DB_PATH).replace("\\", "/")
    return not (path.startswith("/var/data") and _persistent_disk_mounted())

def _persistent_disk_mounted():
    try:
        if not os.path.isdir("/var/data"):
            return False
        with open("/proc/mounts", "r", encoding="utf-8", errors="ignore") as f:
            return any(" /var/data " in line for line in f)
    except OSError:
        return False

ensure_db_dir()

def db():
    ensure_db_dir()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def password_hash(password, salt=None):
    salt = salt or os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 240000).hex()
    return salt, digest

def verify_password(password, salt, digest):
    return hmac.compare_digest(password_hash(password, salt)[1], digest)

@app.before_request
def require_edit_password_for_mutations():
    if request.method == "POST" and request.endpoint != "login" and not session.get("can_edit"):
        flash("Cần đăng nhập bằng mật khẩu chỉnh sửa để thao tác.")
        return redirect(url_for("login", next=request.path))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form.get("password", "")
        with db() as c:
            saltrow=c.execute("SELECT value FROM app_settings WHERE key='edit_password_salt'").fetchone()
            hashrow=c.execute("SELECT value FROM app_settings WHERE key='edit_password_hash'").fetchone()
        if saltrow and hashrow and verify_password(password, saltrow["value"], hashrow["value"]):
            session.clear()
            session["can_edit"] = True
            session.permanent = request.form.get("remember") == "yes"
            flash("Đã mở quyền chỉnh sửa. " + ("Thiết bị này sẽ ghi nhớ đăng nhập tối đa 30 ngày." if session.permanent else "Chỉ giữ đăng nhập trong phiên hiện tại."))
            next_url = request.form.get("next", "")
            # Only allow local relative paths to prevent open redirects.
            if not next_url.startswith("/") or next_url.startswith("//"):
                next_url = url_for("index")
            return redirect(next_url)
        flash("Mật khẩu không đúng.")
    return render_template("login.html", next=request.args.get("next", ""))

@app.post("/logout")
def logout():
    session.clear()
    flash("Đã thoát chế độ chỉnh sửa; hiện chỉ xem.")
    return redirect(request.referrer or url_for("index"))

@app.route("/change-password", methods=["GET", "POST"])
def change_password():
    if not session.get("can_edit"):
        return redirect(url_for("login", next=url_for("change_password")))
    if request.method == "POST":
        new=request.form.get("new_password","")
        confirm=request.form.get("confirm_password","")
        if len(new)<10: flash("Mật khẩu mới phải có ít nhất 10 ký tự.")
        elif new!=confirm: flash("Hai mật khẩu nhập lại không khớp.")
        else:
            salt,digest=password_hash(new)
            with db() as c:
                c.execute("INSERT OR REPLACE INTO app_settings(key,value) VALUES('edit_password_salt',?)",(salt,))
                c.execute("INSERT OR REPLACE INTO app_settings(key,value) VALUES('edit_password_hash',?)",(digest,))
            flash("Đã đổi mật khẩu chỉnh sửa.")
            return redirect(url_for("index"))
    return render_template("change_password.html")

def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS classes(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, school_year TEXT DEFAULT '', homeroom_teacher TEXT DEFAULT '', teacher_phone TEXT DEFAULT '', layout_rows INTEGER DEFAULT 6, layout_cols INTEGER DEFAULT 7);
        CREATE TABLE IF NOT EXISTS students(id INTEGER PRIMARY KEY, class_id INTEGER NOT NULL, student_code TEXT NOT NULL, name TEXT NOT NULL, birth_date TEXT DEFAULT '', gender TEXT DEFAULT '', note TEXT DEFAULT '', phone TEXT DEFAULT '', parent_name TEXT DEFAULT '', parent_phone TEXT DEFAULT '', team TEXT DEFAULT '', seat_row INTEGER, seat_col INTEGER, FOREIGN KEY(class_id) REFERENCES classes(id), UNIQUE(class_id,student_code));
        CREATE TABLE IF NOT EXISTS attendance(id INTEGER PRIMARY KEY, student_id INTEGER NOT NULL, day TEXT NOT NULL, status TEXT NOT NULL, note TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS student_events(id INTEGER PRIMARY KEY, student_id INTEGER NOT NULL, day TEXT NOT NULL, event_type TEXT NOT NULL, points REAL DEFAULT 0, note TEXT DEFAULT '', subject TEXT DEFAULT '', lesson TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS lesson_plans(id INTEGER PRIMARY KEY, class_id INTEGER NOT NULL, subject TEXT NOT NULL, lesson TEXT NOT NULL, day TEXT DEFAULT '', period TEXT DEFAULT '', note TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS app_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        if not c.execute("SELECT 1 FROM app_settings WHERE key='edit_password_hash'").fetchone():
            salt, digest = password_hash(os.environ.get("EDIT_PASSWORD", "DoiMatKhauNgay123!"))
            c.execute("INSERT INTO app_settings(key,value) VALUES('edit_password_salt',?)",(salt,))
            c.execute("INSERT INTO app_settings(key,value) VALUES('edit_password_hash',?)",(digest,))
        # Migrate DB created by earlier versions without losing records.
        cols={r["name"] for r in c.execute("PRAGMA table_info(students)").fetchall()}
        for name,typ in [("birth_date","TEXT DEFAULT ''"),("gender","TEXT DEFAULT ''"),("note","TEXT DEFAULT ''"),("phone","TEXT DEFAULT ''"),("parent_name","TEXT DEFAULT ''"),("parent_phone","TEXT DEFAULT ''"),("team","TEXT DEFAULT ''"),("seat_row","INTEGER"),("seat_col","INTEGER")]:
            if name not in cols: c.execute(f"ALTER TABLE students ADD COLUMN {name} {typ}")
        ccols={r["name"] for r in c.execute("PRAGMA table_info(classes)").fetchall()}
        for name,typ in [("layout_rows","INTEGER DEFAULT 6"),("layout_cols","INTEGER DEFAULT 7")]:
            if name not in ccols: c.execute(f"ALTER TABLE classes ADD COLUMN {name} {typ}")
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

def normalize_team(value):
    """Accept '1', 'Tổ 1', 'to 2' → canonical '1' (empty if blank)."""
    s=_cell(value)
    if not s: return ""
    m=re.search(r"(\d+)", s)
    return m.group(1) if m else s

def parse_int_cell(value):
    s=_cell(value)
    if not s: return None
    m=re.search(r"(\d+)", s)
    if not m: return None
    try: return int(m.group(1))
    except ValueError: return None

def parse_team_marker(line):
    """Detect '% Tổ 1' or '\\section*{Tổ 2}' style markers."""
    raw=line.strip()
    if raw.startswith("%"):
        m=re.search(r"t[oôố]\s*(\d+)", raw, re.I)
        if m: return m.group(1)
    m=re.search(r"\\(?:section|subsection|paragraph)\*?\{[^}]*t[oôố]\s*(\d+)", raw, re.I)
    if m: return m.group(1)
    m=re.match(r"^\s*t[oôố]\s*(\d+)\s*$", raw, re.I)
    if m: return m.group(1)
    return None

def parse_tex_students(tex_text):
    """Parse LaTeX table rows; optional team via section markers or Tổ/Hàng/Cột columns.

    Supported row shapes (after optional STT):
      Mã & Tên & NS & GT
      Mã & Tên & NS & GT & Tổ
      Mã & Tên & NS & GT & Tổ & Hàng & Cột
    Markers like `% Tổ 1` or `\\section*{Tổ 1}` set the current team for following rows.
    """
    records=[]
    current_team=""
    for raw in tex_text.splitlines():
        line=raw.strip()
        if not line: continue
        marker=parse_team_marker(line)
        if marker:
            current_team=marker
            continue
        if line.startswith("%") or "&" not in line:
            continue
        line=re.sub(r"(?<!\\)%.*$","",line).strip()
        line=re.sub(r"\\\\\s*$","",line).strip()
        if not line or line.startswith("\\") or "textbf" in line.lower():
            continue
        cells=[re.sub(r"\\(?:textbf|textit|emph)\s*\{([^{}]*)\}",r"\1",x).strip() for x in line.split("&")]
        cells=[re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?", "", x).strip() for x in cells]
        cells=[x.replace("{","").replace("}","").replace("~"," ").strip() for x in cells]
        if len(cells)<3: continue
        code=cells[1] if len(cells)>=4 and re.fullmatch(r"(?:HS|[A-Za-z]*\d+)[A-Za-z0-9_-]*",cells[1],re.I) else ""
        if not code:
            code=cells[0] if re.fullmatch(r"(?:HS|[A-Za-z]*\d+)[A-Za-z0-9_-]*",cells[0],re.I) else ""
            if code: cells=[cells[0],*cells[1:]]
        if not code: continue
        if len(cells)>=4 and cells[0].isdigit():
            name=cells[2]
            birth=cells[3] if len(cells)>3 else ""
            gender=cells[4] if len(cells)>4 else ""
            rest=cells[5:]
        else:
            name=cells[1] if len(cells)>1 else ""
            birth=cells[2] if len(cells)>2 else ""
            gender=cells[3] if len(cells)>3 else ""
            rest=cells[4:]
        if not name or name.lower() in ("họ và tên","ho va ten"): continue
        team, seat_row, seat_col, note = current_team, None, None, ""
        # rest may be: note | team | team,row,col | team,row,col,note
        if len(rest)>=3 and parse_int_cell(rest[1]) is not None and parse_int_cell(rest[2]) is not None:
            team=normalize_team(rest[0]) or team
            seat_row=parse_int_cell(rest[1])
            seat_col=parse_int_cell(rest[2])
            note=rest[3] if len(rest)>3 else ""
        elif rest and (
            re.match(r"(?i)^t[oôố]?\s*\d+$", rest[0] or "")
            or (rest[0].isdigit() and len(rest[0])<=2)
        ):
            team=normalize_team(rest[0]) or team
            note=rest[1] if len(rest)>1 else ""
        elif rest:
            note=" ".join(rest)
        records.append({
            "student_code":code,"name":name,"birth_date":birth,"gender":gender,"note":note,
            "team":team,"seat_row":seat_row,"seat_col":seat_col,
        })
    unique={}
    for r in records: unique[r["student_code"]]=r
    return list(unique.values())

STUDENT_IMPORT_HEADERS = [
    "Mã HS", "Họ và tên", "Ngày sinh", "Giới tính",
    "Điện thoại HS", "Họ tên phụ huynh", "Điện thoại phụ huynh", "Ghi chú",
    "Tổ", "Hàng", "Cột",
]

def _cell(v):
    if v is None: return ""
    return str(v).strip()

def _student_record(parts):
    """Map columns: code, name, birth, gender, phone, parent, parent_phone, note, team, row, col."""
    parts=[_cell(x) for x in parts]
    while len(parts)<11: parts.append("")
    code,name=parts[0],parts[1]
    if not code or not name: return None
    if name.lower() in ("họ và tên","ho va ten"): return None
    return {
        "student_code":code, "name":name, "birth_date":parts[2], "gender":parts[3],
        "phone":parts[4], "parent_name":parts[5], "parent_phone":parts[6], "note":parts[7],
        "team":normalize_team(parts[8]),
        "seat_row":parse_int_cell(parts[9]), "seat_col":parse_int_cell(parts[10]),
    }

def parse_text_students(text):
    """Parse pasted lines: code, name, birth, gender, phone, parent, parent_phone, note (tab/comma)."""
    records=[]
    for raw in (text or "").splitlines():
        line=raw.strip()
        if not line or line.startswith("#"): continue
        if "\t" in line: parts=line.split("\t")
        elif ";" in line: parts=line.split(";")
        else: parts=next(csv.reader([line]))
        rec=_student_record(parts)
        if rec: records.append(rec)
    unique={}
    for r in records: unique[r["student_code"]]=r
    return list(unique.values())

def parse_xlsx_students(file_bytes):
    wb=load_workbook(BytesIO(file_bytes), read_only=True, data_only=True)
    ws=wb.active
    records=[]
    for i,row in enumerate(ws.iter_rows(values_only=True)):
        if not row or all(v is None or str(v).strip()=="" for v in row): continue
        # Skip header row when first cell looks like a column title.
        if i==0 and _cell(row[0]).lower() in ("mã hs","ma hs","student_code","mã học sinh"):
            continue
        rec=_student_record(row)
        if rec: records.append(rec)
    unique={}
    for r in records: unique[r["student_code"]]=r
    return list(unique.values())

def upsert_students(cid, records):
    """Insert new or update existing students by student_code. Keeps history via stable id."""
    added=updated=0
    with db() as c:
        if not c.execute("SELECT id FROM classes WHERE id=?",(cid,)).fetchone():
            return None
        for s in records:
            old=c.execute("SELECT id FROM students WHERE class_id=? AND student_code=?",(cid,s["student_code"])).fetchone()
            team=normalize_team(s.get("team",""))
            seat_row=s.get("seat_row"); seat_col=s.get("seat_col")
            if old:
                c.execute("""UPDATE students SET name=?,birth_date=?,gender=?,note=? WHERE id=?""",
                          (s["name"],s["birth_date"],s["gender"],s["note"],old["id"]))
                if s.get("phone") or s.get("parent_name") or s.get("parent_phone"):
                    c.execute("""UPDATE students SET phone=?,parent_name=?,parent_phone=? WHERE id=?""",
                              (s.get("phone",""),s.get("parent_name",""),s.get("parent_phone",""),old["id"]))
                if team or seat_row is not None or seat_col is not None:
                    # Only overwrite seating when import provides team and/or seat.
                    sets=[]; vals=[]
                    if team: sets.append("team=?"); vals.append(team)
                    if seat_row is not None: sets.append("seat_row=?"); vals.append(seat_row)
                    if seat_col is not None: sets.append("seat_col=?"); vals.append(seat_col)
                    vals.append(old["id"])
                    c.execute(f"UPDATE students SET {', '.join(sets)} WHERE id=?", vals)
                updated+=1
            else:
                c.execute("""INSERT INTO students(class_id,student_code,name,birth_date,gender,note,phone,parent_name,parent_phone,team,seat_row,seat_col)
                             VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (cid,s["student_code"],s["name"],s["birth_date"],s["gender"],s["note"],
                           s.get("phone",""),s.get("parent_name",""),s.get("parent_phone",""),
                           team, seat_row, seat_col))
                added+=1
    return added, updated

def short_name(full_name):
    parts=[p for p in (full_name or "").split() if p]
    if not parts: return "?"
    if len(parts)==1: return parts[0][:10]
    return f"{parts[0][0]}. {parts[-1]}"[:14]

def build_seat_map(students, rows, cols):
    seat_map={}
    unseated=[]
    for st in students:
        r,c=st["seat_row"], st["seat_col"]
        if r and c and 1<=int(r)<=rows and 1<=int(c)<=cols:
            key=(int(r),int(c))
            if key not in seat_map:
                seat_map[key]=st
            else:
                unseated.append(st)
        else:
            unseated.append(st)
    return seat_map, unseated

DANH_SACH_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "danh-sach")
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
SNAPSHOT_PATH = os.path.join(DATA_DIR, "snapshot.json")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
GITHUB_REPO = os.environ.get("GITHUB_REPO", "pythonminh/diemdanhlophoc").strip()
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main").strip()

def _rows(c, sql, args=()):
    return [dict(r) for r in c.execute(sql, args).fetchall()]

def build_snapshot():
    with db() as c:
        return {
            "version": 1,
            "exported_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "classes": _rows(c, "SELECT * FROM classes ORDER BY id"),
            "students": _rows(c, "SELECT * FROM students ORDER BY id"),
            "attendance": _rows(c, "SELECT * FROM attendance ORDER BY id"),
            "student_events": _rows(c, "SELECT * FROM student_events ORDER BY id"),
            "lesson_plans": _rows(c, "SELECT * FROM lesson_plans ORDER BY id"),
            "app_settings": _rows(c, "SELECT * FROM app_settings ORDER BY key"),
        }

def save_snapshot_local(snapshot=None):
    os.makedirs(DATA_DIR, exist_ok=True)
    snap = snapshot or build_snapshot()
    with open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)
    return SNAPSHOT_PATH, snap

def import_snapshot(snap):
    """Replace operational tables from a snapshot (keeps schema)."""
    if not isinstance(snap, dict) or "classes" not in snap:
        raise ValueError("Snapshot không hợp lệ")
    with db() as c:
        c.executescript("""
            DELETE FROM attendance;
            DELETE FROM student_events;
            DELETE FROM lesson_plans;
            DELETE FROM students;
            DELETE FROM classes;
        """)
        for row in snap.get("classes") or []:
            c.execute(
                """INSERT INTO classes(id,name,school_year,homeroom_teacher,teacher_phone,layout_rows,layout_cols)
                   VALUES(?,?,?,?,?,?,?)""",
                (row.get("id"), row.get("name"), row.get("school_year",""), row.get("homeroom_teacher",""),
                 row.get("teacher_phone",""), row.get("layout_rows") or 6, row.get("layout_cols") or 7),
            )
        for row in snap.get("students") or []:
            c.execute(
                """INSERT INTO students(id,class_id,student_code,name,birth_date,gender,note,phone,parent_name,parent_phone,team,seat_row,seat_col)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (row.get("id"), row.get("class_id"), row.get("student_code"), row.get("name"),
                 row.get("birth_date",""), row.get("gender",""), row.get("note",""), row.get("phone",""),
                 row.get("parent_name",""), row.get("parent_phone",""), row.get("team",""),
                 row.get("seat_row"), row.get("seat_col")),
            )
        for row in snap.get("attendance") or []:
            c.execute(
                "INSERT INTO attendance(id,student_id,day,status,note) VALUES(?,?,?,?,?)",
                (row.get("id"), row.get("student_id"), row.get("day"), row.get("status"), row.get("note","")),
            )
        for row in snap.get("student_events") or []:
            c.execute(
                """INSERT INTO student_events(id,student_id,day,event_type,points,note,subject,lesson)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (row.get("id"), row.get("student_id"), row.get("day"), row.get("event_type"),
                 row.get("points") or 0, row.get("note",""), row.get("subject",""), row.get("lesson","")),
            )
        for row in snap.get("lesson_plans") or []:
            c.execute(
                """INSERT INTO lesson_plans(id,class_id,subject,lesson,day,period,note)
                   VALUES(?,?,?,?,?,?,?)""",
                (row.get("id"), row.get("class_id"), row.get("subject"), row.get("lesson"),
                 row.get("day",""), row.get("period",""), row.get("note","")),
            )
        if snap.get("app_settings"):
            for row in snap["app_settings"]:
                c.execute(
                    "INSERT OR REPLACE INTO app_settings(key,value) VALUES(?,?)",
                    (row.get("key"), row.get("value")),
                )
    return (
        len(snap.get("classes") or []),
        len(snap.get("students") or []),
        len(snap.get("attendance") or []),
        len(snap.get("student_events") or []),
    )

def load_snapshot_file(path=SNAPSHOT_PATH):
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def github_api(method, url_path, body=None):
    url = "https://api.github.com" + url_path
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "diemdanhlophoc")
    if GITHUB_TOKEN:
        req.add_header("Authorization", f"Bearer {GITHUB_TOKEN}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API {e.code}: {err[:300]}") from e

def push_snapshot_to_github(snap=None):
    if not GITHUB_TOKEN:
        raise RuntimeError("Chưa đặt GITHUB_TOKEN trên Render (Settings → Environment).")
    snap = snap or build_snapshot()
    content_b64 = base64.b64encode(json.dumps(snap, ensure_ascii=False, indent=2).encode("utf-8")).decode("ascii")
    api_path = f"/repos/{GITHUB_REPO}/contents/data/snapshot.json"
    sha = None
    try:
        existing = github_api("GET", f"{api_path}?ref={GITHUB_BRANCH}")
        sha = existing.get("sha")
    except RuntimeError:
        sha = None
    body = {
        "message": f"chore: lưu snapshot điểm danh {snap.get('exported_at','')}",
        "content": content_b64,
        "branch": GITHUB_BRANCH,
    }
    if sha:
        body["sha"] = sha
    result = github_api("PUT", api_path, body)
    save_snapshot_local(snap)  # keep local copy in container too
    return result.get("content", {}).get("html_url") or f"https://github.com/{GITHUB_REPO}/blob/{GITHUB_BRANCH}/data/snapshot.json"

def fetch_snapshot_from_github():
    """Read data/snapshot.json from GitHub (token optional for public repos)."""
    if GITHUB_TOKEN:
        api_path = f"/repos/{GITHUB_REPO}/contents/data/snapshot.json?ref={GITHUB_BRANCH}"
        meta = github_api("GET", api_path)
        raw = base64.b64decode(meta.get("content", "").replace("\n", "")).decode("utf-8")
        return json.loads(raw)
    raw_url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/data/snapshot.json"
    req = urllib.request.Request(raw_url, headers={"User-Agent": "diemdanhlophoc"})
    with urllib.request.urlopen(req, timeout=45) as resp:
        return json.loads(resp.read().decode("utf-8"))

def restore_data_if_empty():
    """Prefer full snapshot (GitHub/local), else danh-sach/*.tex roster only."""
    with db() as c:
        if c.execute("SELECT COUNT(*) FROM classes").fetchone()[0] > 0:
            return "keep"
    # 1) local file shipped with deploy
    snap = load_snapshot_file()
    if snap:
        import_snapshot(snap)
        return "local-snapshot"
    # 2) pull from GitHub
    try:
        snap = fetch_snapshot_from_github()
        if snap:
            import_snapshot(snap)
            save_snapshot_local(snap)
            return "github-snapshot"
    except Exception:
        pass
    # 3) roster-only from tex
    sync_classes_from_danh_sach(only_if_empty=True)
    return "tex"

def sync_classes_from_danh_sach(only_if_empty=False):
    """Create/update classes from danh-sach/*.tex (skip mau_*.tex). Roster comes back from git after wipe."""
    with db() as c:
        n=c.execute("SELECT COUNT(*) FROM classes").fetchone()[0]
        if only_if_empty and n>0:
            return 0, 0, 0
    if not os.path.isdir(DANH_SACH_DIR):
        return 0, 0, 0
    created=added=updated=0
    for fn in sorted(os.listdir(DANH_SACH_DIR)):
        if not fn.lower().endswith(".tex"): continue
        if fn.lower().startswith("mau_"): continue
        class_name=os.path.splitext(fn)[0].strip()
        if not class_name: continue
        path=os.path.join(DANH_SACH_DIR, fn)
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                content=f.read()
        except OSError:
            continue
        records=parse_tex_students(content)
        if not records: continue
        with db() as c:
            row=c.execute("SELECT id FROM classes WHERE name=?",(class_name,)).fetchone()
            if row:
                cid=row["id"]
            else:
                c.execute("INSERT INTO classes(name) VALUES(?)",(class_name,))
                cid=c.execute("SELECT id FROM classes WHERE name=?",(class_name,)).fetchone()["id"]
                created+=1
        for s in records:
            s.setdefault("phone",""); s.setdefault("parent_name",""); s.setdefault("parent_phone","")
        result=upsert_students(cid, records)
        if result:
            added+=result[0]; updated+=result[1]
    return created, added, updated

# Empty DB after Render wipe → restore from GitHub/local snapshot, else tex roster.
restore_data_if_empty()

def upsert_attendance(c, student_id, day, status, note=""):
    """One attendance row per student per day: update latest, drop older duplicates."""
    existing=c.execute(
        "SELECT id FROM attendance WHERE student_id=? AND day=? ORDER BY id DESC LIMIT 1",
        (student_id, day),
    ).fetchone()
    if existing:
        c.execute("UPDATE attendance SET status=?, note=? WHERE id=?", (status, note, existing["id"]))
        c.execute("DELETE FROM attendance WHERE student_id=? AND day=? AND id!=?", (student_id, day, existing["id"]))
        return "updated"
    c.execute(
        "INSERT INTO attendance(student_id,day,status,note) VALUES(?,?,?,?)",
        (student_id, day, status, note),
    )
    return "inserted"

@app.post("/restore-from-tex")
def restore_from_tex():
    created, added, updated = sync_classes_from_danh_sach(only_if_empty=False)
    flash(f"Đã đồng bộ từ danh-sach/*.tex: tạo {created} lớp, thêm {added} HS, cập nhật {updated} HS.")
    return redirect(url_for("index"))

@app.get("/snapshot.json")
def download_snapshot():
    path, _ = save_snapshot_local()
    return send_file(path, as_attachment=True, download_name="snapshot.json", mimetype="application/json")

@app.post("/snapshot/save-github")
def snapshot_save_github():
    try:
        url = push_snapshot_to_github()
        flash(f"Đã lưu toàn bộ dữ liệu lên GitHub: {url}")
    except Exception as e:
        flash(f"Không lưu được lên GitHub: {e}")
    return redirect(url_for("index"))

@app.post("/snapshot/load-github")
def snapshot_load_github():
    try:
        snap = fetch_snapshot_from_github()
        n = import_snapshot(snap)
        save_snapshot_local(snap)
        flash(f"Đã mở từ GitHub: {n[0]} lớp, {n[1]} HS, {n[2]} điểm danh, {n[3]} sự kiện.")
    except Exception as e:
        flash(f"Không mở được từ GitHub: {e}")
    return redirect(url_for("index"))

@app.post("/snapshot/import")
def snapshot_import_upload():
    upload = request.files.get("snapshot_file")
    if not upload or not upload.filename:
        flash("Chọn file snapshot.json.")
        return redirect(url_for("index"))
    try:
        snap = json.loads(upload.read().decode("utf-8"))
        n = import_snapshot(snap)
        save_snapshot_local(snap)
        flash(f"Đã nhập snapshot: {n[0]} lớp, {n[1]} HS, {n[2]} điểm danh, {n[3]} sự kiện.")
    except Exception as e:
        flash(f"File snapshot không hợp lệ: {e}")
    return redirect(url_for("index"))

@app.route("/")
def index():
    with db() as c: classes=c.execute("SELECT * FROM classes ORDER BY name").fetchall()
    return render_template(
        "index.html",
        classes=classes,
        db_path=DB_PATH,
        db_ephemeral=db_ephemeral_warning(),
        disk_ok=_persistent_disk_mounted() if os.environ.get("RENDER")=="true" else None,
        github_ready=bool(GITHUB_TOKEN),
        github_repo=GITHUB_REPO,
        has_local_snapshot=os.path.isfile(SNAPSHOT_PATH),
    )

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
        students=c.execute("""SELECT * FROM students WHERE class_id=?
            ORDER BY CASE WHEN team GLOB '[0-9]*' THEN CAST(team AS INTEGER) ELSE 999 END,
                     team, seat_row, seat_col, student_code""",(cid,)).fetchall()
        today_s=date.today().isoformat()
        student_summaries={}
        for st in students:
            total=c.execute("SELECT COALESCE(SUM(points),0) FROM student_events WHERE student_id=?",(st["id"],)).fetchone()[0]
            recent=c.execute("SELECT day,event_type,points,note,subject,lesson FROM student_events WHERE student_id=? ORDER BY day DESC,id DESC LIMIT 3",(st["id"],)).fetchall()
            all_events=c.execute("SELECT day,event_type,points,note,subject,lesson FROM student_events WHERE student_id=? ORDER BY day DESC,id DESC",(st["id"],)).fetchall()
            attendance=c.execute("SELECT day,status,note FROM attendance WHERE student_id=? ORDER BY day DESC,id DESC",(st["id"],)).fetchall()
            pos_n=sum(1 for e in all_events if (e["points"] or 0)>0)
            neg_n=sum(1 for e in all_events if (e["points"] or 0)<0)
            today_att=c.execute(
                "SELECT status FROM attendance WHERE student_id=? AND day=? ORDER BY id DESC LIMIT 1",
                (st["id"], today_s),
            ).fetchone()
            student_summaries[st["id"]]={
                "total_points":total,"recent_events":recent,"all_events":all_events,"attendance":attendance,
                "pos_n":pos_n,"neg_n":neg_n,
                "today_status": today_att["status"] if today_att else "",
            }
        plans=c.execute("SELECT * FROM lesson_plans WHERE class_id=? ORDER BY day DESC,id DESC",(cid,)).fetchall()
    rows=int(cl["layout_rows"] or 6); cols=int(cl["layout_cols"] or 7)
    seat_map, unseated=build_seat_map(students, rows, cols)
    return render_template(
        "class.html", cl=cl, students=students, student_summaries=student_summaries, plans=plans,
        today=date.today().isoformat(), event_defaults=EVENT_DEFAULTS,
        layout_rows=rows, layout_cols=cols, seat_map=seat_map, unseated=unseated, short_name=short_name,
    )


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
                upsert_attendance(c, sid, day, status, note)
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

@app.get("/class/<int:cid>/students-template.xlsx")
def students_template(cid):
    with db() as c:
        if not c.execute("SELECT id FROM classes WHERE id=?",(cid,)).fetchone():
            return "Không tìm thấy lớp",404
    wb=Workbook()
    ws=wb.active
    ws.title="Danh sách"
    ws.append(STUDENT_IMPORT_HEADERS)
    ws.append(["HS001","NGUYỄN VĂN A","10/04/2011","Nam","","","","","1","1","1"])
    buf=BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(
        buf,
        as_attachment=True,
        download_name="mau_danh_sach_hoc_sinh.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

LATEX_SEATING_TEMPLATE = r"""% Mẫu danh sách + chỗ ngồi theo tổ — dùng cho app điểm danh
% Quy ước: Hàng 1 gần bảng GV; Cột 1 từ TRÁI khi nhìn lên bảng.
% Có thể đánh dấu tổ bằng comment %% Tổ N hoặc cột Tổ / Hàng / Cột trên mỗi dòng.
% Định dạng dòng: STT & Mã HS & Họ tên & Ngày sinh & Giới tính & Tổ & Hàng & Cột \\

\section*{Tổ 1}
\begin{longtable}{|c|l|p{5.2cm}|c|c|c|c|c|}
\hline
STT & Mã HS & Họ và tên & Ngày sinh & GT & Tổ & Hàng & Cột \\
\hline
1 & HS001 & NGUYỄN VĂN A & 10/04/2011 & Nam & 1 & 1 & 1 \\
\hline
2 & HS002 & TRẦN THỊ B & 12/03/2011 & Nữ & 1 & 1 & 2 \\
\hline
\end{longtable}

\section*{Tổ 2}
\begin{longtable}{|c|l|p{5.2cm}|c|c|c|c|c|}
\hline
STT & Mã HS & Họ và tên & Ngày sinh & GT & Tổ & Hàng & Cột \\
\hline
1 & HS003 & LÊ VĂN C & 01/05/2011 & Nam & 2 & 2 & 1 \\
\hline
2 & HS004 & PHẠM THỊ D & 08/08/2011 & Nữ & 2 & 2 & 2 \\
\hline
\end{longtable}

% Cách ngắn — chỉ dùng marker tổ, không ghi cột Tổ/Hàng/Cột:
% Tổ 3
% 1 & HS005 & HOÀNG VĂN E & 20/01/2011 & Nam \\
"""

@app.get("/class/<int:cid>/seating-template.tex")
def seating_template(cid):
    with db() as c:
        if not c.execute("SELECT id FROM classes WHERE id=?",(cid,)).fetchone():
            return "Không tìm thấy lớp",404
    buf=BytesIO(LATEX_SEATING_TEMPLATE.encode("utf-8"))
    return send_file(buf, as_attachment=True, download_name="mau_so_do_theo_to.tex", mimetype="text/x-tex")

@app.post("/class/<int:cid>/seating")
def save_seating(cid):
    try: rows=max(1, min(20, int(request.form.get("layout_rows") or 6)))
    except ValueError: rows=6
    try: cols=max(1, min(20, int(request.form.get("layout_cols") or 7)))
    except ValueError: cols=7
    with db() as c:
        if not c.execute("SELECT id FROM classes WHERE id=?",(cid,)).fetchone():
            return "Không tìm thấy lớp",404
        c.execute("UPDATE classes SET layout_rows=?, layout_cols=? WHERE id=?", (rows, cols, cid))
        students=c.execute("SELECT id FROM students WHERE class_id=?",(cid,)).fetchall()
        # Clear seats that would collide: apply in two passes — first clear, then set.
        for st in students:
            sid=st["id"]
            team=normalize_team(request.form.get(f"team_{sid}",""))
            clear=request.form.get(f"clear_seat_{sid}")=="1"
            r=parse_int_cell(request.form.get(f"seat_row_{sid}",""))
            col=parse_int_cell(request.form.get(f"seat_col_{sid}",""))
            if clear:
                c.execute("UPDATE students SET team=?, seat_row=NULL, seat_col=NULL WHERE id=?", (team, sid))
            else:
                if r is not None and not (1<=r<=rows): r=None
                if col is not None and not (1<=col<=cols): col=None
                c.execute("UPDATE students SET team=?, seat_row=?, seat_col=? WHERE id=?", (team, r, col, sid))
    flash(f"Đã lưu sơ đồ lớp ({rows}×{cols}) và tổ/chỗ ngồi.")
    return redirect(url_for("class_page",cid=cid)+"#so-do-lop")

@app.post("/class/<int:cid>/import-xlsx")
def import_xlsx(cid):
    upload=request.files.get("xlsx_file")
    if not upload or not upload.filename.lower().endswith(".xlsx"):
        flash("Chọn một tệp .xlsx hợp lệ.")
        return redirect(url_for("class_page",cid=cid))
    try:
        parsed=parse_xlsx_students(upload.read())
    except Exception:
        flash("Không đọc được file Excel. Kiểm tra định dạng .xlsx.")
        return redirect(url_for("class_page",cid=cid))
    if not parsed:
        flash("Không tìm thấy dòng học sinh trong Excel.")
        return redirect(url_for("class_page",cid=cid))
    result=upsert_students(cid, parsed)
    if result is None: return "Không tìm thấy lớp",404
    added,updated=result
    flash(f"Excel: đọc {len(parsed)} học sinh — thêm {added}, cập nhật {updated}. Lịch sử được giữ theo mã HS.")
    return redirect(url_for("class_page",cid=cid))

@app.post("/class/<int:cid>/import-text")
def import_text(cid):
    parsed=parse_text_students(request.form.get("students_text",""))
    if not parsed:
        flash("Không tìm thấy dòng học sinh. Dùng tab hoặc dấu phẩy giữa các cột.")
        return redirect(url_for("class_page",cid=cid))
    result=upsert_students(cid, parsed)
    if result is None: return "Không tìm thấy lớp",404
    added,updated=result
    flash(f"Văn bản: đọc {len(parsed)} học sinh — thêm {added}, cập nhật {updated}. Lịch sử được giữ theo mã HS.")
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
    # LaTeX rows may omit phone/parent fields.
    for s in parsed:
        s.setdefault("phone",""); s.setdefault("parent_name",""); s.setdefault("parent_phone","")
    result=upsert_students(cid, parsed)
    if result is None: return "Không tìm thấy lớp",404
    added,updated=result
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
    team=normalize_team(request.form.get("team",""))
    seat_row=parse_int_cell(request.form.get("seat_row",""))
    seat_col=parse_int_cell(request.form.get("seat_col",""))
    with db() as c:
        row=c.execute("SELECT class_id FROM students WHERE id=?",(sid,)).fetchone()
        if not row: return "Không tìm thấy học sinh",404
        try:
            c.execute("""UPDATE students SET student_code=?,name=?,birth_date=?,gender=?,note=?,phone=?,parent_name=?,parent_phone=?,team=?,seat_row=?,seat_col=? WHERE id=?""",
             (code,name,request.form.get("birth_date",""),request.form.get("gender",""),request.form.get("note",""),request.form.get("phone","").strip(),request.form.get("parent_name","").strip(),request.form.get("parent_phone","").strip(),team,seat_row,seat_col,sid))
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
    day=request.form.get("day") or date.today().isoformat()
    status=request.form.get("status","").strip()
    note=request.form.get("note","").strip()
    if not status:
        flash("Chọn trạng thái điểm danh."); return redirect(url_for("student_page",sid=sid))
    with db() as c:
        if not c.execute("SELECT id FROM students WHERE id=?",(sid,)).fetchone():
            return "Không tìm thấy học sinh",404
        action=upsert_attendance(c, sid, day, status, note)
    flash("Đã cập nhật điểm danh trong ngày." if action=="updated" else "Đã ghi nhận điểm danh.")
    return redirect(url_for("student_page",sid=sid))

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT",5000)))
