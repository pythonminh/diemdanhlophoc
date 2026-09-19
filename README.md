# Điểm danh lớp học — bản cập nhật danh sách LaTeX

## Định dạng hỗ trợ
Đọc dòng bảng LaTeX dạng `1 & HS001 & HỌ VÀ TÊN & 10/04/2011 & Nam \\` (và các dòng tương tự). Tự bỏ qua `\hline`, `\begin{longtable}`, tiêu đề và comment.

## Cập nhật
Trong trang lớp, chọn file `.tex` từ `danh-sach/10T1.tex`, nhấn **Đọc và cập nhật danh sách**. Mã HS là khóa đối chiếu: mã tồn tại sẽ cập nhật họ tên/ngày sinh/giới tính/ghi chú; mã mới sẽ thêm. Không tự xóa học sinh vắng khỏi file, không xóa lịch sử.

## Chạy / Render
`pip install -r requirements.txt`; `gunicorn app:app`. Gắn Persistent Disk và đặt `DATABASE_PATH=/var/data/diemdanh.db` để lưu dữ liệu bền vững.

**Bảo mật:** chưa có đăng nhập/phân quyền. Tránh đưa dữ liệu liên hệ thật lên ứng dụng công khai trước khi có xác thực.

## Tổng quan học sinh trong bảng lớp
Bảng danh sách hiển thị 3 ghi nhận gần nhất (hoạt động/lỗi và ghi chú) cùng tổng điểm cộng/trừ lũy kế của từng học sinh. Điểm được tính bằng tổng trường `points` trong lịch sử hoạt động; số dương là cộng, số âm là trừ.


## Điện thoại và lưu dữ liệu để giám sát
- Trên màn hình nhỏ, bảng học sinh chuyển thành các thẻ dọc có nhãn; ghi nhận/lỗi, tổng điểm và nút mở hồ sơ hiển thị rõ.
- Dữ liệu SQLite cần lưu tại ổ đĩa bền vững của Render: đặt `DATABASE_PATH=/var/data/diemdanh.db` và gắn Persistent Disk vào `/var/data`. `render.yaml` minh họa cấu hình.
- Persistent Disk thường yêu cầu gói Render trả phí. Nếu dùng filesystem tạm, dữ liệu có thể mất khi deploy/restart.
- Để giám sát từ xa an toàn cần bổ sung đăng nhập/phân quyền và sao lưu. Bản này chưa có đăng nhập; không đưa thông tin liên hệ thật lên ứng dụng công khai.
\n## Bảng lớp có cột tên cố định và chi tiết mở rộng\n- Bảng có thể cuộn ngang; cột mã HS và họ tên được ghim bên trái.\n- Nhấn vào tên học sinh để mở/thu gọn một vùng chi tiết ngay bên dưới, gồm toàn bộ lịch sử hoạt động/vi phạm, ghi chú, điểm cộng-trừ, lịch sử điểm danh và tổng điểm lũy kế.\n- Liên kết mở hồ sơ dùng để sửa thông tin hoặc nhập ghi nhận mới.\n
## Ghi nhận nhanh cả lớp (v8)
Trong trang lớp, mỗi dòng học sinh có lựa chọn điểm danh và lựa chọn hoạt động/vi phạm, điểm cộng-trừ và ghi chú riêng. Ngày, môn, bài/tiết có thể nhập chung phía trên. Nhấn “Lưu ghi nhận cả lớp” để lưu các mục đã chọn cùng lúc; lựa chọn để trống sẽ không tạo bản ghi. Các mục được lưu vào bảng attendance và student_events để xem lại trong hồ sơ học sinh.
