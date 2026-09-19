# Điểm danh lớp học

## Chạy local
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```
Mở http://127.0.0.1:5000

## Deploy Render
Tạo Web Service từ GitHub repo, Build Command `pip install -r requirements.txt`, Start Command `gunicorn app:app`.
Để giữ dữ liệu qua lần deploy, gắn Persistent Disk và đặt `DATABASE_PATH` tới file trên disk (ví dụ `/var/data/diemdanh.db`).

## Chức năng
- Tạo lớp; lưu năm học và thông tin GVCN.
- Thêm học sinh trực tiếp khi danh sách thiếu; sửa mã, họ tên và liên hệ.
- Hồ sơ từng học sinh, lịch sử điểm danh, hoạt động và điểm cộng/trừ.
- Kế hoạch dạy học theo môn, bài, ngày, tiết.

**Bảo mật:** bản này chưa có đăng nhập/phân quyền. Không nhập số điện thoại thật của học sinh/phụ huynh trên đường dẫn công khai cho đến khi bổ sung xác thực và bảo vệ dữ liệu.
