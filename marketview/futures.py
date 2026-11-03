"""국채선물 가격 ↔ 내재수익률 (표준물 이론가 기준).

상품 명세 (2026-10-14 KRX Global 공식 명세 페이지 3·10·30년 직접 확인: global.krx.co.kr/contents/GLB/02/0201/
0201040501|0201040503|0201040506): 기초자산 = 표면금리 연 5%, 6개월 단위 이자지급 국고채 표준물, 계약 1억원,
최종결제 현금(KOFIA 가 고시한 바스켓 채권 수익률로 산정), 최종거래일 = 결제월 세 번째 화요일, 상장 결제월 2개(분기).
틱 3·10년 0.01pt(1만원)·30년 0.02pt(2만원). 가격은 액면 100 당. 3·10년은 야간장(18:00~06:00)이 있고 30년은 정규장만.

내재수익률은 '표준물이 이 가격이 되는 만기수익률'(반년 복리)이다. 결제월 직전 최종결제기준채권(CTD)의
수익률이나 실제 국고채 수익률과 정확히 같지 않다 — 레벨 비교용 참고치로 쓴다.
표준물은 쿠폰일 직후(경과이자 0)에 평가하고 잔존 반년 주기를 정수 n 으로 둔다.
"""

from __future__ import annotations

COUPON = 5.0          # 연 %
FREQ = 2              # 연 2회


def price_from_yield(y_pct: float, years: int, coupon: float = COUPON) -> float:
    n = years * FREQ
    r = y_pct / 100 / FREQ
    c = coupon / FREQ
    if abs(r) < 1e-12:
        return c * n + 100.0
    disc = (1 + r) ** -n
    return c * (1 - disc) / r + 100.0 * disc


def implied_yield(price: float, years: int, coupon: float = COUPON) -> float | None:
    """가격 → 수익률(%). 이분법. 가격이 수렴 범위(-5%~30%) 밖이면 None (0 으로 채우지 않는다)."""
    lo, hi = -5.0, 30.0
    p_lo, p_hi = price_from_yield(lo, years, coupon), price_from_yield(hi, years, coupon)
    if not (p_hi <= price <= p_lo):
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        if price_from_yield(mid, years, coupon) > price:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-10:
            break
    return (lo + hi) / 2
