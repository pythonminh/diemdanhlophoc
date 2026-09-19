# Điểm danh lớp học — bản cập nhật danh sách LaTeX

## Định dạng hỗ trợ
Đọc dòng bảng LaTeX dạng `1 & HS001 & HỌ VÀ TÊN & 10/04/2011 & Nam \\` (và các dòng tương tự). Tự bỏ qua `\hline`, `\begin{longtable}`, tiêu đề và comment.

## Cập nhật
Trong trang lớp, chọn file `.tex` từ `danh-sach/10T1.tex`, nhấn **Đọc và cập nhật danh sách**. Mã HS là khóa đối chiếu: mã tồn tại sẽ cập nhật họ tên/ngày sinh/giới tính/ghi chú; mã mới sẽ thêm. Không tự xóa học sinh vắng khỏi file, không xóa lịch sử.

## Chạy / Render
`pip install -r requirements.txt`; `gunicorn app:app`. Gắn Persistent Disk và đặt `DATABASE_PATH=/var/data/diemdanh.db` để lưu dữ liệu bền vững.

**Bảo mật:** chưa có đăng nhập/phân quyền. Tránh đưa dữ liệu liên hệ thật lên ứng dụng công khai trước khi có xác thực.
