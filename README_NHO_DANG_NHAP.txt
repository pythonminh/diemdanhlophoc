TÍNH NĂNG GHI NHỚ ĐĂNG NHẬP

- Ở trang /login, nhập mật khẩu chỉnh sửa.
- Tích “Ghi nhớ đăng nhập trên thiết bị này” để giữ phiên tối đa 30 ngày.
- Không tích: phiên chỉ tồn tại trong phiên trình duyệt.
- Đăng xuất sẽ xóa toàn bộ session.
- Chỉ nên bật ghi nhớ trên máy cá nhân; không bật trên máy dùng chung.

LƯU Ý TRIỂN KHAI RENDER
- Giữ SECRET_KEY ổn định trong Environment; không thay đổi sau mỗi lần khởi động.
- Cookie Secure bật mặc định (HTTPS). Nếu chạy localhost HTTP, đặt COOKIE_SECURE=0.
- Dữ liệu SQLite cần lưu trên Persistent Disk, theo DATABASE_PATH trong render.yaml.
