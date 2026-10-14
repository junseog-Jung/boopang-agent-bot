"""샘플 패킹리스트 생성 (다양한 검수 케이스 포함)."""
from __future__ import annotations

import io

from openpyxl import Workbook

HEADERS = ["주문번호", "SKU", "품목명", "주문수량", "검수수량", "단가", "할인율", "수령인", "연락처", "주소", "출고일", "운송장번호", "배송상태", "비고"]
ROWS = [
    ["A1001", "SKU-001", "부팡 에코백", 10, 10, 12000, "10%", "김부팡", "01012345678", "서울시 강남구 테헤란로 1", None, None, None, ""],
    ["A1001", "SKU-002", "부팡 텀블러", 5, 5, 18000, 0, "김부팡", "010-1234-5678", "서울시 강남구 테헤란로 1", None, None, None, ""],
    ["A1002", "SKU-003", "부팡 머그컵", 20, 15, 8500, 0, "이물류", "02-123-4567", "부산시 해운대구 센텀로 2", "2026-10-10", "CJ1234567890", "배송중", "5개 입고 부족"],
    ["A1003", "SKU-001", "부팡 에코백", 3, 3, 12000, 0, "박주문", "010-9999-8888", "대구시 수성구 3", "2026/10/05", "HJ9876543210", "배송완료", ""],
    ["A1004", "SKU-004", "부팡 키링", 100, None, 1500, 0, "최검수", "010-5555-4444", "인천시 연수구 4", None, None, None, "검수 전"],
    ["A1005", "SKU-005", "부팡 노트", 0, 0, 3000, 0, "정오류", "010-0000-0000", "광주시 5", None, None, None, "수량 0 → 오류"],
    ["A1006", "SKU-006", "부팡 펜", 4, 4, "abc", 0, "한단가", "010-1111-2222", "대전시 6", None, None, None, "단가 오류"],
    ["A1007", "SKU-007", "부팡 파우치", 2, 2, 7000, 0, "오취소", "1234", "울산시 7", None, None, "취소", "연락처 형식 오류"],
    ["A1008", "SKU-008", "@SUM(1+1) 특수문자 품목", 1, 1, 5000, 0, "이수식", "010-2222-3333", "세종시 8", None, None, None, "특수문자 시작 품목명 테스트"],
    ["A1009", "SKU-009", "부팡 스티커", 50, 0, 500, 0, "서재고", "010-3333-4444", "제주시 9", None, None, None, "재고 없음"],
    ["A1001", "SKU-001", "부팡 에코백", 1, 1, 12000, 0, "김부팡", "01012345678", "서울시 강남구 테헤란로 1", None, None, None, "중복 SKU"],
]


def sample_xlsx() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "packing_list"
    ws.append(HEADERS)
    for r in ROWS:
        ws.append(r)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


if __name__ == "__main__":
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "samples" / "sample_packing_list.xlsx"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(sample_xlsx())
    print(f"생성: {path}")
