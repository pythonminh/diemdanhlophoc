from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file, send_from_directory, abort
import sqlite3, os, re, hashlib, hmac, json, base64, urllib.request, urllib.error, unicodedata
from urllib.parse import urlparse
from datetime import timedelta
from datetime import date, datetime
from io import BytesIO
import csv
from openpyxl import Workbook, load_workbook
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "1") == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)
# OPEN_EDIT=1 (mặc định): ai cũng xem + sửa, không cần đăng nhập. Đặt OPEN_EDIT=0 để bật lại mật khẩu.
OPEN_EDIT = os.environ.get("OPEN_EDIT", "1").strip().lower() in ("1", "true", "yes", "on")
# Resolve a writable SQLite path. Free Render has no /var/data disk → must not crash.
def _path_writable(path):
    parent = os.path.dirname(os.path.abspath(path)) or "."
    try:
        os.makedirs(parent, exist_ok=True)
        probe = os.path.join(parent, ".diemdanh_write_probe")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        # Confirm SQLite can actually open the file (makedirs alone is not enough on Render).
        conn = sqlite3.connect(path)
        conn.execute("SELECT 1")
        conn.close()
        return True
    except (OSError, sqlite3.Error):
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
    # Chế độ mở: luôn cho sửa, không chặn POST.
    if OPEN_EDIT:
        session["can_edit"] = True
        return None
    if request.method == "POST" and request.endpoint != "login" and not session.get("can_edit"):
        flash("Cần đăng nhập bằng mật khẩu chỉnh sửa để thao tác.")
        # After login, send user back to the page they were viewing — never to a POST-only URL
        # (e.g. /class/10/seating), otherwise the browser GETs it and gets HTTP 405.
        next_url = None
        ref = request.referrer or ""
        if ref:
            try:
                parsed = urlparse(ref)
                if parsed.path and not parsed.path.startswith("//"):
                    next_url = parsed.path
                    if parsed.query:
                        next_url += "?" + parsed.query
                    if parsed.fragment:
                        next_url += "#" + parsed.fragment
            except Exception:
                next_url = None
        if not next_url:
            path = request.path or "/"
            for suffix in ("/seating", "/photos-bulk", "/bulk-record", "/import-xlsx", "/import-text", "/import-tex", "/save-tex-github", "/settings", "/plan", "/student/add"):
                if path.endswith(suffix):
                    next_url = path[: -len(suffix)] or "/"
                    if suffix == "/seating":
                        next_url += "#so-do-lop"
                    break
            # /class/<id>/photo/<sid>
            m = re.match(r"^(/class/\d+)/photo/\d+$", path)
            if m:
                next_url = m.group(1) + "#so-do-lop"
        if not next_url:
            next_url = url_for("index")
        return redirect(url_for("login", next=next_url))
    return None

@app.context_processor
def inject_auth_flags():
    return {"open_edit": OPEN_EDIT, "can_edit": OPEN_EDIT or bool(session.get("can_edit"))}

@app.route("/login", methods=["GET", "POST"])
def login():
    if OPEN_EDIT:
        session["can_edit"] = True
        flash("Ứng dụng đang mở sửa tự do — không cần đăng nhập.")
        return redirect(request.args.get("next") or url_for("index"))
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
            # Never bounce onto POST-only mutation URLs after login.
            if next_url.rstrip("/").endswith("/seating"):
                base = next_url.rstrip("/").rsplit("/seating", 1)[0]
                next_url = (base or "/") + "#so-do-lop"
                flash("Đã đăng nhập — xếp chỗ rồi bấm «Lưu sơ đồ» lại.")
            return redirect(next_url)
        flash("Mật khẩu không đúng.")
    return render_template("login.html", next=request.args.get("next", ""))

@app.post("/logout")
def logout():
    session.clear()
    if OPEN_EDIT:
        session["can_edit"] = True
        flash("Ứng dụng đang mở sửa tự do.")
    else:
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
        CREATE TABLE IF NOT EXISTS classes(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, school_year TEXT DEFAULT '', homeroom_teacher TEXT DEFAULT '', teacher_phone TEXT DEFAULT '', layout_rows INTEGER DEFAULT 6, layout_cols INTEGER DEFAULT 8);
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
        for name,typ in [("birth_date","TEXT DEFAULT ''"),("gender","TEXT DEFAULT ''"),("note","TEXT DEFAULT ''"),("phone","TEXT DEFAULT ''"),("parent_name","TEXT DEFAULT ''"),("parent_phone","TEXT DEFAULT ''"),("team","TEXT DEFAULT ''"),("seat_row","INTEGER"),("seat_col","INTEGER"),("avg_grade_10","TEXT DEFAULT ''"),("avg_grade_11","TEXT DEFAULT ''")]:
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

def normalize_avg_grade(value):
    """Keep empty or a clean decimal string like 8.5 / 8,50."""
    s=_cell(value).replace(",", ".")
    if not s: return ""
    m=re.search(r"-?\d+(?:\.\d+)?", s)
    if not m: return s
    try:
        n=float(m.group(0))
        if n == int(n): return str(int(n))
        return f"{n:.2f}".rstrip("0").rstrip(".")
    except ValueError:
        return s

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
    "Tổ", "Hàng", "Cột", "TB lớp 10", "TB lớp 11",
]

def _cell(v):
    if v is None: return ""
    return str(v).strip()

def _student_record(parts):
    """Map columns: code, name, birth, gender, phone, parent, parent_phone, note, team, row, col, avg10, avg11."""
    parts=[_cell(x) for x in parts]
    while len(parts)<13: parts.append("")
    code,name=parts[0],parts[1]
    if not code or not name: return None
    if name.lower() in ("họ và tên","ho va ten"): return None
    return {
        "student_code":code, "name":name, "birth_date":parts[2], "gender":parts[3],
        "phone":parts[4], "parent_name":parts[5], "parent_phone":parts[6], "note":parts[7],
        "team":normalize_team(parts[8]),
        "seat_row":parse_int_cell(parts[9]), "seat_col":parse_int_cell(parts[10]),
        "avg_grade_10":normalize_avg_grade(parts[11]),
        "avg_grade_11":normalize_avg_grade(parts[12]),
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
                if s.get("avg_grade_10") or s.get("avg_grade_11"):
                    sets=[]; vals=[]
                    if s.get("avg_grade_10"): sets.append("avg_grade_10=?"); vals.append(s.get("avg_grade_10",""))
                    if s.get("avg_grade_11"): sets.append("avg_grade_11=?"); vals.append(s.get("avg_grade_11",""))
                    vals.append(old["id"])
                    c.execute(f"UPDATE students SET {', '.join(sets)} WHERE id=?", vals)
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
                c.execute("""INSERT INTO students(class_id,student_code,name,birth_date,gender,note,phone,parent_name,parent_phone,team,seat_row,seat_col,avg_grade_10,avg_grade_11)
                             VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (cid,s["student_code"],s["name"],s["birth_date"],s["gender"],s["note"],
                           s.get("phone",""),s.get("parent_name",""),s.get("parent_phone",""),
                           team, seat_row, seat_col,
                           s.get("avg_grade_10",""), s.get("avg_grade_11","")))
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
ANH_LOP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "anh-lop")
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
SNAPSHOT_PATH = os.path.join(DATA_DIR, "snapshot.json")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip().strip('"').strip("'")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "pythonminh/diemdanhlophoc").strip()
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main").strip()
PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

# Folder name → tên hiển thị trong app
CLASS_DISPLAY_NAMES = {
    "LopTangCuong": "Lớp tăng cường",
    "NguyenVanDau": "Nguyễn Văn Đậu",
    "QuangTrung": "Quang Trung",
}
CLASS_FOLDER_FROM_NAME = {v: k for k, v in CLASS_DISPLAY_NAMES.items()}

def class_name_from_folder(folder_name):
    name = (folder_name or "").strip()
    if name.lower().startswith("class"):
        name = name[5:]
    return CLASS_DISPLAY_NAMES.get(name, name)

def fold_key(s):
    """Lowercase ASCII-ish key for matching photo filenames to names/codes."""
    s = unicodedata.normalize("NFD", str(s or ""))
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9]+", "", s.lower())

def class_photo_folder_name(class_name):
    name = (class_name or "").strip()
    return CLASS_FOLDER_FROM_NAME.get(name, name)

def class_photo_dir(class_name, create=False):
    folder = class_photo_folder_name(class_name)
    path = os.path.join(ANH_LOP_DIR, folder)
    # Prefer existing folder under anh-lop or danh-sach naming
    if not os.path.isdir(path):
        for cand in (folder, class_name, CLASS_FOLDER_FROM_NAME.get(class_name, "")):
            if not cand:
                continue
            alt = os.path.join(ANH_LOP_DIR, cand)
            if os.path.isdir(alt):
                return alt, cand
    if create:
        os.makedirs(path, exist_ok=True)
    return path, folder

def build_photo_index(photo_dir):
    """filename stem keys → absolute filename (basename)."""
    index = {}
    if not os.path.isdir(photo_dir):
        return index
    for fn in os.listdir(photo_dir):
        base, ext = os.path.splitext(fn)
        if ext.lower() not in PHOTO_EXTS:
            continue
        if fn.startswith("."):
            continue
        keys = {fold_key(base), base.strip().lower()}
        # HS019 → also 019 / 19
        m = re.match(r"^(?:hs|hv)?0*(\d+)$", fold_key(base), re.I)
        if m:
            keys.add(m.group(1))
            keys.add(m.group(1).zfill(3))
        for k in keys:
            if k and k not in index:
                index[k] = fn
    return index

def find_photo_filename(index, student_code, student_name):
    code = (student_code or "").strip()
    name = (student_name or "").strip()
    candidates = []
    if code:
        candidates += [fold_key(code), code.lower(), fold_key(code).lstrip("hs")]
        m = re.search(r"(\d+)", code)
        if m:
            candidates += [m.group(1), m.group(1).lstrip("0") or "0", m.group(1).zfill(3)]
    if name:
        candidates.append(fold_key(name))
        parts = [p for p in name.split() if p]
        if len(parts) >= 2:
            candidates.append(fold_key(parts[-1] + parts[0]))
            candidates.append(fold_key("".join(parts)))
    for key in candidates:
        if key and key in index:
            return index[key]
    return None

def student_photo_urls(class_name, students):
    """Map student id → URL path for photo (or empty)."""
    photo_dir, folder = class_photo_dir(class_name, create=False)
    index = build_photo_index(photo_dir)
    urls = {}
    for st in students:
        fn = find_photo_filename(index, st["student_code"], st["name"])
        if fn:
            urls[st["id"]] = url_for("serve_class_photo", folder=folder, filename=fn)
        else:
            urls[st["id"]] = ""
    return urls, folder, len(index)

def _read_tex(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return f.read()
    except OSError:
        return ""

def _pick_class_tex_files(folder_path):
    """Return (danh_sach_path, so_do_path) inside a class folder."""
    files = [f for f in os.listdir(folder_path) if f.lower().endswith(".tex")]
    danh = so = None
    for f in files:
        low = f.lower()
        if low.startswith("mau_"):
            continue
        if low in ("danh_sach.tex", "danhsach.tex") or low.startswith("danh_sach"):
            danh = f
        elif low in ("so_do.tex", "sodo.tex") or low.startswith("so_do"):
            so = f
    if not danh:
        for f in sorted(files):
            low = f.lower()
            if low.startswith("mau_") or low.startswith("so_do") or low.startswith("sodo"):
                continue
            danh = f
            break
    if not so:
        for f in sorted(files):
            low = f.lower()
            if low.startswith("so_do") or low.startswith("sodo"):
                so = f
                break
    return (
        os.path.join(folder_path, danh) if danh else None,
        os.path.join(folder_path, so) if so else None,
    )

def _ensure_class(class_name):
    """Create class row if missing. Returns (class_id, created_bool)."""
    with db() as c:
        row = c.execute("SELECT id FROM classes WHERE name=?", (class_name,)).fetchone()
        if row:
            return row["id"], False
        c.execute("INSERT INTO classes(name) VALUES(?)", (class_name,))
        cid = c.execute("SELECT id FROM classes WHERE name=?", (class_name,)).fetchone()["id"]
        return cid, True

def _upsert_tex_into_class(class_name, tex_path):
    content = _read_tex(tex_path)
    if not content.strip():
        return 0, 0, False
    records = parse_tex_students(content)
    cid, created = _ensure_class(class_name)
    if not records:
        return 0, 0, created
    for s in records:
        s.setdefault("phone", ""); s.setdefault("parent_name", ""); s.setdefault("parent_phone", "")
        s.setdefault("avg_grade_10", ""); s.setdefault("avg_grade_11", "")
    result = upsert_students(cid, records)
    added = result[0] if result else 0
    updated = result[1] if result else 0
    return added, updated, created

def sync_classes_from_danh_sach(only_if_empty=False):
    """Sync from danh-sach/<Lớp>/ — always create a class per folder (even if tex còn trống)."""
    with db() as c:
        n = c.execute("SELECT COUNT(*) FROM classes").fetchone()[0]
        if only_if_empty and n > 0:
            return 0, 0, 0
    if not os.path.isdir(DANH_SACH_DIR):
        return 0, 0, 0
    created = added = updated = 0

    for entry in sorted(os.listdir(DANH_SACH_DIR)):
        folder = os.path.join(DANH_SACH_DIR, entry)
        if not os.path.isdir(folder):
            continue
        if entry.startswith(".") or entry.lower() in ("mau", "_templates"):
            continue
        class_name = class_name_from_folder(entry)
        if not class_name:
            continue
        # Always register the class from folder name (empty roster is OK).
        _, was_new = _ensure_class(class_name)
        if was_new:
            created += 1
        danh_path, so_path = _pick_class_tex_files(folder)
        for path in (danh_path, so_path):
            if not path:
                continue
            a, u, _ = _upsert_tex_into_class(class_name, path)
            added += a
            updated += u

    for fn in sorted(os.listdir(DANH_SACH_DIR)):
        path = os.path.join(DANH_SACH_DIR, fn)
        if not os.path.isfile(path) or not fn.lower().endswith(".tex"):
            continue
        if fn.lower().startswith("mau_") or fn.lower().startswith("so_do"):
            continue
        class_name = os.path.splitext(fn)[0].strip()
        _, was_new = _ensure_class(class_name)
        if was_new:
            created += 1
        a, u, _ = _upsert_tex_into_class(class_name, path)
        added += a
        updated += u
    return created, added, updated

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
                 row.get("teacher_phone",""), row.get("layout_rows") or 6, row.get("layout_cols") or 8),
            )
        for row in snap.get("students") or []:
            c.execute(
                """INSERT INTO students(id,class_id,student_code,name,birth_date,gender,note,phone,parent_name,parent_phone,team,seat_row,seat_col,avg_grade_10,avg_grade_11)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (row.get("id"), row.get("class_id"), row.get("student_code"), row.get("name"),
                 row.get("birth_date",""), row.get("gender",""), row.get("note",""), row.get("phone",""),
                 row.get("parent_name",""), row.get("parent_phone",""), row.get("team",""),
                 row.get("seat_row"), row.get("seat_col"),
                 row.get("avg_grade_10",""), row.get("avg_grade_11","")),
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

def latex_escape(s):
    s = str(s or "")
    return (
        s.replace("\\", "\\textbackslash{}")
        .replace("&", "\\&")
        .replace("%", "\\%")
        .replace("$", "\\$")
        .replace("#", "\\#")
        .replace("_", "\\_")
        .replace("{", "\\{")
        .replace("}", "\\}")
    )

def class_tex_dir(class_name, create=False):
    """Resolve danh-sach/<folder>/ for a class display name."""
    folder = class_photo_folder_name(class_name)
    path = os.path.join(DANH_SACH_DIR, folder)
    if not os.path.isdir(path):
        for cand in (folder, class_name, CLASS_FOLDER_FROM_NAME.get(class_name, "")):
            if not cand:
                continue
            alt = os.path.join(DANH_SACH_DIR, cand)
            if os.path.isdir(alt):
                return alt, cand
    if create:
        os.makedirs(path, exist_ok=True)
    return path, folder

def _row_get(st, key, default=""):
    try:
        if hasattr(st, "keys") and key in st.keys():
            v = st[key]
            return default if v is None else v
    except Exception:
        pass
    if isinstance(st, dict):
        v = st.get(key, default)
        return default if v is None else v
    return default

def build_danh_sach_tex(class_name, students):
    lines = [
        f"% Danh sách học sinh — {class_name} (xuất từ app điểm danh)",
        f"% Cập nhật: {datetime.now().isoformat(timespec='seconds')}",
        r"\begin{longtable}{|c|l|p{6cm}|c|c|}",
        r"\hline",
        r"STT & Mã HS & Họ và tên & Ngày sinh & GT \\",
        r"\hline",
    ]
    for i, st in enumerate(students, 1):
        lines.append(
            f"{i} & {latex_escape(_row_get(st,'student_code'))} & {latex_escape(_row_get(st,'name'))} & "
            f"{latex_escape(_row_get(st,'birth_date'))} & {latex_escape(_row_get(st,'gender'))} \\\\"
        )
        lines.append(r"\hline")
    lines.append(r"\end{longtable}")
    lines.append("")
    return "\n".join(lines)

def build_so_do_tex(class_name, students):
    lines = [
        f"% Sơ đồ chỗ ngồi — {class_name} (xuất từ app điểm danh)",
        f"% Hàng 1 gần bảng; Cột 1 từ trái nhìn lên bảng",
        f"% Cập nhật: {datetime.now().isoformat(timespec='seconds')}",
        "",
    ]
    by_team = {}
    no_team = []
    for st in students:
        team = str(_row_get(st, "team") or "").strip()
        if team:
            by_team.setdefault(team, []).append(st)
        else:
            no_team.append(st)
    def team_sort_key(t):
        return (0, int(t)) if str(t).isdigit() else (1, str(t))
    ordered = sorted(by_team.keys(), key=team_sort_key)
    if no_team:
        by_team[""] = no_team
        ordered.append("")
    if not ordered:
        ordered = ["1"]
        by_team["1"] = []
    stt = 1
    for team in ordered:
        title = f"Tổ {team}" if team else "Chưa phân tổ"
        lines.append(f"\\section*{{{title}}}")
        lines.append(r"\begin{longtable}{|c|l|p{5cm}|c|c|c|c|c|}")
        lines.append(r"\hline")
        lines.append(r"STT & Mã HS & Họ và tên & Ngày sinh & GT & Tổ & Hàng & Cột \\")
        lines.append(r"\hline")
        for st in by_team.get(team, []):
            row = _row_get(st, "seat_row", None)
            col = _row_get(st, "seat_col", None)
            row_s = "" if row is None else str(row)
            col_s = "" if col is None else str(col)
            t_s = team or str(_row_get(st, "team") or "")
            lines.append(
                f"{stt} & {latex_escape(_row_get(st,'student_code'))} & {latex_escape(_row_get(st,'name'))} & "
                f"{latex_escape(_row_get(st,'birth_date'))} & {latex_escape(_row_get(st,'gender'))} & "
                f"{latex_escape(t_s)} & {latex_escape(row_s)} & {latex_escape(col_s)} \\\\"
            )
            lines.append(r"\hline")
            stt += 1
        lines.append(r"\end{longtable}")
        lines.append("")
    return "\n".join(lines)

def push_repo_text_file(rel_path, text, message):
    """Create or update a UTF-8 text file in the GitHub repo via Contents API."""
    if not GITHUB_TOKEN:
        raise RuntimeError("Chưa đặt GITHUB_TOKEN trên Render (Settings → Environment).")
    rel_path = rel_path.replace("\\", "/").lstrip("/")
    content_b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
    api_path = f"/repos/{GITHUB_REPO}/contents/{rel_path}"
    sha = None
    try:
        existing = github_api("GET", f"{api_path}?ref={GITHUB_BRANCH}")
        sha = existing.get("sha")
    except RuntimeError:
        sha = None
    body = {"message": message, "content": content_b64, "branch": GITHUB_BRANCH}
    if sha:
        body["sha"] = sha
    result = github_api("PUT", api_path, body)
    return result.get("content", {}).get("html_url") or f"https://github.com/{GITHUB_REPO}/blob/{GITHUB_BRANCH}/{rel_path}"

def export_class_tex_files(cid):
    """Write danh_sach.tex + so_do.tex for a class. Returns (folder, danh_text, so_text, n)."""
    with db() as c:
        cl = c.execute("SELECT * FROM classes WHERE id=?", (cid,)).fetchone()
        if not cl:
            raise RuntimeError("Không tìm thấy lớp")
        students = c.execute(
            """SELECT * FROM students WHERE class_id=?
               ORDER BY CASE WHEN team GLOB '[0-9]*' THEN CAST(team AS INTEGER) ELSE 999 END,
                        team, seat_row, seat_col, student_code""",
            (cid,),
        ).fetchall()
    class_name = cl["name"]
    folder_path, folder = class_tex_dir(class_name, create=True)
    danh = build_danh_sach_tex(class_name, students)
    so = build_so_do_tex(class_name, students)
    with open(os.path.join(folder_path, "danh_sach.tex"), "w", encoding="utf-8") as f:
        f.write(danh)
    with open(os.path.join(folder_path, "so_do.tex"), "w", encoding="utf-8") as f:
        f.write(so)
    return folder, danh, so, len(students)

def push_class_tex_to_github(cid):
    folder, danh, so, n = export_class_tex_files(cid)
    url1 = push_repo_text_file(
        f"danh-sach/{folder}/danh_sach.tex",
        danh,
        f"chore: cập nhật danh_sach.tex lớp {folder} ({n} HS)",
    )
    url2 = push_repo_text_file(
        f"danh-sach/{folder}/so_do.tex",
        so,
        f"chore: cập nhật so_do.tex lớp {folder}",
    )
    return folder, n, url1, url2

def persist_class_tex(cid, push_github=True):
    """Always write local .tex; push to GitHub when token exists and push_github=True."""
    folder, danh, so, n = export_class_tex_files(cid)
    note = f"Đã ghi danh-sach/{folder}/danh_sach.tex & so_do.tex ({n} HS)"
    if push_github and GITHUB_TOKEN:
        url1 = push_repo_text_file(
            f"danh-sach/{folder}/danh_sach.tex", danh, f"chore: cập nhật danh_sach.tex lớp {folder} ({n} HS)"
        )
        push_repo_text_file(
            f"danh-sach/{folder}/so_do.tex", so, f"chore: cập nhật so_do.tex lớp {folder}"
        )
        note += f" và đã đẩy lên GitHub"
        return note, url1
    if push_github and not GITHUB_TOKEN:
        note += " — chưa đẩy GitHub (thiếu GITHUB_TOKEN trên Render)"
    return note, None

def push_all_class_tex_to_github():
    """Export + push .tex for every class. Returns list of folder names pushed."""
    with db() as c:
        classes = c.execute("SELECT id, name FROM classes ORDER BY name").fetchall()
    pushed = []
    for cl in classes:
        folder, n, _, _ = push_class_tex_to_github(cl["id"])
        pushed.append(f"{folder}({n})")
    return pushed

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
    """Restore snapshot if DB empty, then always merge danh-sach/<lớp>/ folders."""
    with db() as c:
        empty = c.execute("SELECT COUNT(*) FROM classes").fetchone()[0] == 0
    source = "keep"
    if empty:
        snap = load_snapshot_file()
        if snap:
            import_snapshot(snap)
            source = "local-snapshot"
        else:
            try:
                snap = fetch_snapshot_from_github()
                if snap:
                    import_snapshot(snap)
                    save_snapshot_local(snap)
                    source = "github-snapshot"
            except Exception:
                source = "tex"
    # Always register every class folder (even empty tex) so danh sách lớp đủ.
    sync_classes_from_danh_sach(only_if_empty=False)
    return source

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
    flash(f"Đã đồng bộ từ danh-sach/<lớp>/: tạo {created} lớp, thêm {added} HS, cập nhật {updated} HS.")
    return redirect(url_for("index"))

@app.get("/snapshot.json")
def download_snapshot():
    path, _ = save_snapshot_local()
    return send_file(path, as_attachment=True, download_name="snapshot.json", mimetype="application/json")

@app.post("/snapshot/save-github")
def snapshot_save_github():
    try:
        url = push_snapshot_to_github()
        msg = f"Đã lưu snapshot lên GitHub: {url}"
        try:
            pushed = push_all_class_tex_to_github()
            msg += f" · Đã đẩy .tex {len(pushed)} lớp: {', '.join(pushed[:8])}" + ("…" if len(pushed) > 8 else "")
        except Exception as te:
            msg += f" · (Snapshot OK; đẩy .tex lỗi: {te})"
        flash(msg)
    except Exception as e:
        flash(f"Không lưu được lên GitHub: {e}")
    return redirect(url_for("index"))

@app.post("/snapshot/load-github")
def snapshot_load_github():
    try:
        snap = fetch_snapshot_from_github()
        n = import_snapshot(snap)
        save_snapshot_local(snap)
        # Snapshot may be old (only 10T1) — merge in all class folders from repo.
        c2, a2, u2 = sync_classes_from_danh_sach(only_if_empty=False)
        flash(
            f"Đã mở từ GitHub: {n[0]} lớp, {n[1]} HS, {n[2]} điểm danh, {n[3]} sự kiện. "
            f"Đã bổ sung thư mục lớp: +{c2} lớp, +{a2} HS, cập nhật {u2}."
        )
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
        c2, a2, u2 = sync_classes_from_danh_sach(only_if_empty=False)
        flash(
            f"Đã nhập snapshot: {n[0]} lớp, {n[1]} HS, {n[2]} điểm danh, {n[3]} sự kiện. "
            f"Bổ sung thư mục: +{c2} lớp, +{a2} HS."
        )
    except Exception as e:
        flash(f"File snapshot không hợp lệ: {e}")
    return redirect(url_for("index"))

@app.route("/")
def index():
    with db() as c:
        classes = c.execute("""
            SELECT classes.*,
                   (SELECT COUNT(*) FROM students WHERE students.class_id=classes.id) AS student_count
            FROM classes ORDER BY name
        """).fetchall()
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
    rows=int(cl["layout_rows"] or 6); cols=int(cl["layout_cols"] or 8)
    seat_map, unseated=build_seat_map(students, rows, cols)
    photo_urls, photo_folder, photo_count = student_photo_urls(cl["name"], students)
    stt_map={st["id"]: i for i, st in enumerate(students, 1)}
    return render_template(
        "class.html", cl=cl, students=students, student_summaries=student_summaries, plans=plans,
        today=date.today().isoformat(), event_defaults=EVENT_DEFAULTS,
        layout_rows=rows, layout_cols=cols, seat_map=seat_map, unseated=unseated, short_name=short_name,
        photo_urls=photo_urls, photo_folder=photo_folder, photo_count=photo_count, stt_map=stt_map,
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
    ws.append(["HS001","NGUYỄN VĂN A","10/04/2011","Nam","","","","","1","1","1","8.2","7.9"])
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

@app.get("/anh-lop/<folder>/<path:filename>")
def serve_class_photo(folder, filename):
    folder = secure_filename(folder) or folder
    # Keep unicode folder names like 10T1; only block path traversal.
    folder = folder.replace("..", "").replace("/", "").replace("\\", "").strip()
    if not folder:
        abort(404)
    base = os.path.realpath(os.path.join(ANH_LOP_DIR, folder))
    root = os.path.realpath(ANH_LOP_DIR)
    if not base.startswith(root + os.sep) and base != root:
        abort(404)
    safe_name = os.path.basename(filename)
    ext = os.path.splitext(safe_name)[1].lower()
    if ext not in PHOTO_EXTS:
        abort(404)
    path = os.path.join(base, safe_name)
    if not os.path.isfile(path):
        abort(404)
    return send_from_directory(base, safe_name, max_age=86400)

@app.post("/class/<int:cid>/photo/<int:sid>")
def upload_student_photo(cid, sid):
    upload = request.files.get("photo")
    if not upload or not upload.filename:
        flash("Chọn file ảnh học sinh.")
        return redirect(url_for("class_page", cid=cid) + "#so-do-lop")
    ext = os.path.splitext(upload.filename)[1].lower()
    if ext not in PHOTO_EXTS:
        flash("Chỉ nhận ảnh .jpg .jpeg .png .webp .gif")
        return redirect(url_for("class_page", cid=cid) + "#so-do-lop")
    with db() as c:
        st = c.execute("SELECT * FROM students WHERE id=? AND class_id=?", (sid, cid)).fetchone()
        cl = c.execute("SELECT name FROM classes WHERE id=?", (cid,)).fetchone()
        if not st or not cl:
            return "Không tìm thấy", 404
    photo_dir, _folder = class_photo_dir(cl["name"], create=True)
    code = secure_filename(st["student_code"] or f"hs{sid}") or f"hs{sid}"
    # Remove old photos for this code (any ext)
    for fn in list(os.listdir(photo_dir)):
        stem, e = os.path.splitext(fn)
        if stem.lower() == code.lower() and e.lower() in PHOTO_EXTS:
            try:
                os.remove(os.path.join(photo_dir, fn))
            except OSError:
                pass
    dest = os.path.join(photo_dir, code + ext)
    upload.save(dest)
    flash(f"Đã lưu ảnh {code}{ext} vào anh-lop/{_folder}/. Nên commit lên GitHub để giữ sau khi Render restart.")
    return redirect(url_for("class_page", cid=cid) + "#so-do-lop")

@app.post("/class/<int:cid>/photos-bulk")
def upload_class_photos_bulk(cid):
    files = request.files.getlist("photos")
    if not files:
        flash("Chọn một hoặc nhiều ảnh (đặt tên theo mã HS, VD: HS019.jpg).")
        return redirect(url_for("class_page", cid=cid) + "#so-do-lop")
    with db() as c:
        cl = c.execute("SELECT name FROM classes WHERE id=?", (cid,)).fetchone()
        if not cl:
            return "Không tìm thấy lớp", 404
        codes = {
            (r["student_code"] or "").strip().lower(): r["student_code"]
            for r in c.execute("SELECT student_code FROM students WHERE class_id=?", (cid,)).fetchall()
        }
    photo_dir, folder = class_photo_dir(cl["name"], create=True)
    saved = skipped = 0
    for upload in files:
        if not upload or not upload.filename:
            continue
        ext = os.path.splitext(upload.filename)[1].lower()
        if ext not in PHOTO_EXTS:
            skipped += 1
            continue
        stem = os.path.splitext(os.path.basename(upload.filename))[0]
        key = fold_key(stem)
        # Prefer matching known student codes
        dest_stem = None
        for ck, original in codes.items():
            if key == fold_key(ck) or key == fold_key(ck).lstrip("hs"):
                dest_stem = secure_filename(original) or original
                break
            m = re.search(r"(\d+)", ck)
            m2 = re.search(r"(\d+)", key)
            if m and m2 and m.group(1).lstrip("0") == m2.group(1).lstrip("0"):
                dest_stem = secure_filename(original) or original
                break
        if not dest_stem:
            dest_stem = secure_filename(stem) or fold_key(stem) or f"anh{saved+1}"
        # clear old
        for fn in list(os.listdir(photo_dir)):
            s, e = os.path.splitext(fn)
            if s.lower() == dest_stem.lower() and e.lower() in PHOTO_EXTS:
                try: os.remove(os.path.join(photo_dir, fn))
                except OSError: pass
        upload.save(os.path.join(photo_dir, dest_stem + ext))
        saved += 1
    flash(f"Đã lưu {saved} ảnh vào anh-lop/{folder}/" + (f" (bỏ qua {skipped})" if skipped else "") + ". Commit GitHub để giữ lâu dài.")
    return redirect(url_for("class_page", cid=cid) + "#so-do-lop")

@app.get("/class/<int:cid>/seating")
def seating_get(cid):
    """Browser may land here via refresh or post-login redirect — never 405."""
    flash("Trang này chỉ dùng để lưu sơ đồ. Hãy xếp chỗ rồi bấm «Lưu sơ đồ».")
    return redirect(url_for("class_page", cid=cid) + "#so-do-lop")

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
    try: cols=max(1, min(20, int(request.form.get("layout_cols") or 8)))
    except ValueError: cols=8
    with db() as c:
        if not c.execute("SELECT id FROM classes WHERE id=?",(cid,)).fetchone():
            return "Không tìm thấy lớp",404
        students=c.execute("SELECT id FROM students WHERE class_id=?",(cid,)).fetchall()
        # Expand grid if form seats sit outside declared size (avoids wiping col 8 when cols=7).
        max_r, max_c = rows, cols
        pending=[]
        for st in students:
            sid=st["id"]
            team=normalize_team(request.form.get(f"team_{sid}",""))
            clear=request.form.get(f"clear_seat_{sid}")=="1"
            r=parse_int_cell(request.form.get(f"seat_row_{sid}",""))
            col=parse_int_cell(request.form.get(f"seat_col_{sid}",""))
            if not clear:
                if r is not None: max_r=max(max_r, r)
                if col is not None: max_c=max(max_c, col)
            pending.append((sid, team, clear, r, col))
        rows=min(20, max_r)
        cols=min(20, max_c)
        c.execute("UPDATE classes SET layout_rows=?, layout_cols=? WHERE id=?", (rows, cols, cid))
        # Two-pass: clear all seats first to avoid unique collisions when swapping.
        for sid, team, clear, r, col in pending:
            c.execute("UPDATE students SET seat_row=NULL, seat_col=NULL WHERE id=?", (sid,))
        for sid, team, clear, r, col in pending:
            if clear or r is None or col is None:
                c.execute("UPDATE students SET team=?, seat_row=NULL, seat_col=NULL WHERE id=?", (team, sid))
            else:
                if not (1<=r<=rows): r=None
                if not (1<=col<=cols): col=None
                if r is None or col is None:
                    c.execute("UPDATE students SET team=?, seat_row=NULL, seat_col=NULL WHERE id=?", (team, sid))
                else:
                    c.execute("UPDATE students SET team=?, seat_row=?, seat_col=? WHERE id=?", (team, r, col, sid))
    flash(f"Đã lưu sơ đồ lớp ({rows}×{cols}) và tổ/chỗ ngồi.")
    # Chỉ ghi .tex local — không đẩy GitHub ở đây (push sẽ kích hoạt Render redeploy và làm lệch chỗ ngồi).
    try:
        note, _ = persist_class_tex(cid, push_github=False)
        flash(note + ". Dùng «Lưu danh sách .tex lên GitHub» hoặc «Lưu lên GitHub» ở trang chủ khi cần.")
    except Exception as e:
        flash(f"Sơ đồ đã lưu trong app nhưng chưa ghi .tex local: {e}")
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
    try:
        note, _ = persist_class_tex(cid, push_github=True)
        flash(note)
    except Exception as e:
        flash(f"Danh sách đã vào app nhưng chưa ghi/đẩy .tex: {e}")
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
    try:
        note, _ = persist_class_tex(cid, push_github=True)
        flash(note)
    except Exception as e:
        flash(f"Danh sách đã vào app nhưng chưa ghi/đẩy .tex: {e}")
    return redirect(url_for("class_page",cid=cid))

@app.post("/class/<int:cid>/save-tex-github")
def save_tex_github(cid):
    try:
        folder, n, url1, url2 = push_class_tex_to_github(cid)
        flash(f"Đã lưu .tex lớp {folder} ({n} HS) lên GitHub: {url1}")
    except Exception as e:
        flash(f"Không lưu .tex lên GitHub: {e}")
    return redirect(url_for("class_page", cid=cid))

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
        s.setdefault("avg_grade_10",""); s.setdefault("avg_grade_11","")
    result=upsert_students(cid, parsed)
    if result is None: return "Không tìm thấy lớp",404
    added,updated=result
    flash(f"Đọc xong {len(parsed)} học sinh: thêm {added}, cập nhật {updated}. Lịch sử đã có được giữ nguyên theo mã học sinh.")
    try:
        note, _ = persist_class_tex(cid, push_github=True)
        flash(note)
    except Exception as e:
        flash(f"Đã nhập .tex vào app nhưng chưa đồng bộ lại GitHub: {e}")
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
    avg10=normalize_avg_grade(request.form.get("avg_grade_10",""))
    avg11=normalize_avg_grade(request.form.get("avg_grade_11",""))
    with db() as c:
        row=c.execute("SELECT class_id FROM students WHERE id=?",(sid,)).fetchone()
        if not row: return "Không tìm thấy học sinh",404
        try:
            c.execute("""UPDATE students SET student_code=?,name=?,birth_date=?,gender=?,note=?,phone=?,parent_name=?,parent_phone=?,team=?,seat_row=?,seat_col=?,avg_grade_10=?,avg_grade_11=? WHERE id=?""",
             (code,name,request.form.get("birth_date",""),request.form.get("gender",""),request.form.get("note",""),request.form.get("phone","").strip(),request.form.get("parent_name","").strip(),request.form.get("parent_phone","").strip(),team,seat_row,seat_col,avg10,avg11,sid))
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
