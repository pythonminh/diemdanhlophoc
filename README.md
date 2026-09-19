# Điểm danh Thầy Minh — MVP

## Chạy trên máy
```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# Ubuntu: source .venv/bin/activate
pip install -r requirements.txt
python app.py
```
Mở http://127.0.0.1:5000

## Nhập dữ liệu
Mỗi file `.tex` là một lớp; tên file là tên lớp, ví dụ `12QT1.tex`. Parser lấy dòng có 5 cột `STT & Mã HS & Họ tên & Ngày sinh & Ghi chú \\` và bỏ qua header longtable.

## MVP hiện có
- Nhập nhiều file `.tex` cùng lúc, cập nhật học sinh theo mã HS.
- Danh sách lớp và số học sinh.
- Điểm danh theo ngày/tiết, lưu SQLite.
- Xuất danh sách `.tex`.

## Chưa có trong bản đầu
Đăng nhập/phân quyền, chỉnh sửa trực tiếp toàn bộ hồ sơ trên bảng, báo cáo nâng cao, đồng bộ Google Sheets, sao lưu tự động và triển khai Render. Cần bổ sung trước khi dùng dữ liệu thật trên môi trường nhiều người dùng.
