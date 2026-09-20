# Thư mục danh sách theo lớp

Mỗi **một thư mục = một lớp**. Trong thư mục có **2 file LaTeX**:

| File | Mục đích |
|------|----------|
| `danh_sach.tex` | Danh sách học sinh (mã, họ tên, ngày sinh, giới tính…) |
| `so_do.tex` | Sơ đồ chỗ ngồi (tổ, hàng, cột) |

## Các lớp hiện có

- `10T1`, `10T2`, `10VD18`
- `10QT1`, `10QT2`, `10QT3`
- `11QT1`, `11QT2`, `11QT3`
- `12QT1`, `12QT2`, `12QT3`
- `NguyenVanDau` → hiển thị **Nguyễn Văn Đậu**
- `QuangTrung` → hiển thị **Quang Trung**
- `LopTangCuong` → hiển thị **Lớp tăng cường**

## Cách cập nhật

1. Sửa file `.tex` trong thư mục lớp trên GitHub (hoặc máy local rồi commit).
2. Trên app: đăng nhập → **Đồng bộ lại từ danh-sach/&lt;lớp&gt;/**.
3. Mã HS trùng → cập nhật hồ sơ / chỗ ngồi; lịch sử điểm danh & điểm được giữ.

## Quy ước sơ đồ (`so_do.tex`)

- Hàng 1 sát bảng giáo viên
- Cột 1 từ **trái** khi nhìn lên bảng
- Dòng mẫu: `STT & Mã HS & Họ tên & NS & GT & Tổ & Hàng & Cột \\`
