from __future__ import annotations


def nhap_si_so() -> list[str]:
    danh_sach = input("Nhập danh sách học sinh (cách nhau bởi dấu phẩy): ").strip()
    if not danh_sach:
        return []
    return [ten.strip() for ten in danh_sach.split(",") if ten.strip()]


def diem_danh(danh_sach: list[str]) -> tuple[list[str], list[str]]:
    co_mat: list[str] = []
    vang: list[str] = []

    for hoc_sinh in danh_sach:
        trang_thai = input(f"{hoc_sinh} có mặt? (y/n): ").strip().lower()
        if trang_thai == "y":
            co_mat.append(hoc_sinh)
        else:
            vang.append(hoc_sinh)

    return co_mat, vang


def main() -> None:
    print("=== Chương trình điểm danh lớp học ===")
    danh_sach = nhap_si_so()

    if not danh_sach:
        print("Không có học sinh nào trong danh sách.")
        return

    co_mat, vang = diem_danh(danh_sach)

    print("\n--- Kết quả điểm danh ---")
    print(f"Sĩ số: {len(danh_sach)}")
    print(f"Có mặt ({len(co_mat)}): {', '.join(co_mat) if co_mat else 'Không có'}")
    print(f"Vắng ({len(vang)}): {', '.join(vang) if vang else 'Không có'}")


if __name__ == "__main__":
    main()
