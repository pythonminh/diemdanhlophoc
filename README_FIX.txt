SỬA LỖI NHẬP DANH SÁCH / TẢI EXCEL MẪU

1. Thay app.py trên GitHub bằng app.py trong gói này.
2. Cập nhật requirements.txt (cần openpyxl để đọc/tạo Excel).
3. Commit cả hai file, sau đó Render > Manual Deploy > Deploy latest commit.
4. Kiểm tra:
   - GET /class/1/students-template.xlsx tải mẫu
   - POST /class/1/import-xlsx nhận Excel
   - POST /class/1/import-text nhận danh sách dán
   - POST /class/1/import-tex nhận .tex

Nhập văn bản: mỗi dòng một học sinh, các cột nên ngăn bằng TAB:
Mã HS[TAB]Họ và tên[TAB]Ngày sinh[TAB]Giới tính[TAB]Điện thoại HS[TAB]Họ tên phụ huynh[TAB]Điện thoại phụ huynh[TAB]Ghi chú

Dữ liệu cũ không bị xóa; khi trùng mã HS, cập nhật thông tin học sinh hiện có,
giữ nguyên ID để liên kết lịch sử. Hãy sao lưu database trước khi deploy.
