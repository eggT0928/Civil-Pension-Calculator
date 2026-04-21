from dataclasses import dataclass
from datetime import date

import pandas as pd
import streamlit as st

# =====================================
# 기본 설정
# =====================================
st.set_page_config(
    page_title="공무원연금 시뮬레이터 (추정/정밀)",
    page_icon="🏛️",
    layout="wide",
)

CURRENT_DATE = date.today()
CURRENT_YEAR = CURRENT_DATE.year
CONTRIBUTION_RATE = 0.09
DEFAULT_SALARY_GROWTH = 0.025
DEFAULT_INFLATION = 0.020

PENSION_RATES = {
    2016: 1.878, 2017: 1.856, 2018: 1.834, 2019: 1.812,
    2020: 1.790, 2021: 1.780, 2022: 1.770, 2023: 1.760,
    2024: 1.750, 2025: 1.740, 2026: 1.736, 2027: 1.732,
    2028: 1.728, 2029: 1.724, 2030: 1.720, 2031: 1.716,
    2032: 1.712, 2033: 1.708, 2034: 1.704, 2035: 1.700,
}

OFFICIAL_A_VALUES = {
    2011: 3950000, 2012: 4150000, 2013: 4350000, 2014: 4470000, 2015: 4670000,
    2016: 4910000, 2017: 5100000, 2018: 5220000, 2019: 5300000, 2020: 5390000,
    2021: 5350000, 2022: 5390000, 2023: 5440000, 2024: 5520000, 2025: 5710000,
    2026: 5710000
}

# =====================================
# 유틸 함수
# =====================================
def won(value: float) -> str:
    return f"{int(round(value)):,}원"

def get_pension_start_age(entry_date: date, retirement_year: int) -> int:
    """1996.1.1 이후 임용자는 퇴직연도별 표를 적용"""
    if entry_date >= date(1996, 1, 1):
        if retirement_year <= 2021: return 60
        elif retirement_year <= 2023: return 61
        elif retirement_year <= 2026: return 62
        elif retirement_year <= 2029: return 63
        elif retirement_year <= 2032: return 64
        else: return 65
    return 60

def pension_rate_for_year(year: int) -> float:
    if year in PENSION_RATES: return PENSION_RATES[year]
    if year < 2016: return 1.9
    return 1.7

def overlap_years(start: float, end: float, range_start: int, range_end_exclusive: int) -> float:
    return max(0.0, min(end, range_end_exclusive) - max(start, range_start))

def weighted_average_rate(start_year_float: float, end_year_float: float) -> float:
    if end_year_float <= start_year_float: return 0.0
    total_rate, total_weight = 0.0, 0.0
    year = int(start_year_float)
    while year < int(end_year_float) + 1:
        s = max(start_year_float, year)
        e = min(end_year_float, year + 1)
        weight = max(0.0, e - s)
        if weight > 0:
            total_rate += pension_rate_for_year(year) * weight
            total_weight += weight
        year += 1
    return total_rate / total_weight if total_weight > 0 else 0.0

def recognized_service_cap(pre_2016_service_years: float) -> int:
    if pre_2016_service_years >= 21: return 33
    if pre_2016_service_years >= 17: return 34
    if pre_2016_service_years >= 15: return 35
    return 36

def apply_service_cap(raw_y1: float, raw_y2: float, raw_y3: float, cap_years: int) -> tuple[float, float, float]:
    remaining = float(cap_years)
    y1 = min(raw_y1, remaining)
    remaining -= y1
    y2 = min(raw_y2, max(0.0, remaining))
    remaining -= y2
    y3 = min(raw_y3, max(0.0, remaining))
    return y1, y2, y3

# =====================================
# 핵심 계산 로직
# =====================================
def calculate_pension(
    current_age: int, entry_date: date, retirement_date: date, 
    military_months: int, leave_months: int,
    current_contribution: int, salary_growth: float, inflation: float,
    use_exact_data: bool, exact_b_value: float, exact_redist_value: float, exact_p1_value: float
):
    retirement_year = retirement_date.year
    years_to_retire = max(0.0, (retirement_date - CURRENT_DATE).days / 365.2425)

    military_years = military_months / 12.0
    leave_years = leave_months / 12.0
    
    actual_start = float(entry_date.year + (entry_date.timetuple().tm_yday - 1) / 365.2425)
    actual_end = float(retirement_date.year + (retirement_date.timetuple().tm_yday - 1) / 365.2425)

    raw_y1 = overlap_years(actual_start, actual_end, 0, 2010)
    raw_y2 = overlap_years(actual_start, actual_end, 2010, 2016)
    raw_y3 = overlap_years(actual_start, actual_end, 2016, actual_end + 1)

    raw_y1 += military_years

    remaining_leave = leave_years
    deduct_y3 = min(raw_y3, remaining_leave)
    raw_y3 -= deduct_y3
    remaining_leave -= deduct_y3
    
    deduct_y2 = min(raw_y2, remaining_leave)
    raw_y2 -= deduct_y2
    remaining_leave -= deduct_y2
    
    deduct_y1 = min(raw_y1, remaining_leave)
    raw_y1 -= deduct_y1

    pre_2016_service_years = raw_y1 + raw_y2
    service_cap_years = recognized_service_cap(pre_2016_service_years)
    y1, y2, y3 = apply_service_cap(raw_y1, raw_y2, raw_y3, service_cap_years)
    recognized_service_years = y1 + y2 + y3

    # ==========================================
    # 기준소득월액 설정 (현재 기준)
    # ==========================================
    current_standard_income = current_contribution / CONTRIBUTION_RATE if current_contribution > 0 else 0.0
    current_a_value = OFFICIAL_A_VALUES[max(OFFICIAL_A_VALUES.keys())] 
    
    est_final_3_years = current_standard_income
    est_b_value = current_standard_income * 0.90
    capped_est_b_value = min(est_b_value, current_a_value * 1.6)
    est_redist_value = (current_a_value + capped_est_b_value) / 2

    actual_p1_value = exact_p1_value if (use_exact_data and exact_p1_value > 0) else est_final_3_years
    actual_b_value = exact_b_value if (use_exact_data and exact_b_value > 0) else capped_est_b_value
    actual_p3_value = exact_redist_value if (use_exact_data and exact_redist_value > 0) else est_redist_value

    base_p1_income = actual_p1_value if y1 > 0 else 0.0
    base_p2_income = actual_b_value if y2 > 0 else 0.0
    base_p3_income = actual_p3_value if y3 > 0 else 0.0

    # ==========================================
    # 1. 퇴직연금액 계산 (명목 및 물가상승률 할인 현재가치)
    # ==========================================
    period1_monthly, period2_monthly, period3_monthly = 0.0, 0.0, 0.0
    
    if y1 > 0:
        if y1 >= 20: period1_monthly = base_p1_income * 0.5 + base_p1_income * (y1 - 20) * 0.02
        else: period1_monthly = base_p1_income * y1 * 0.025

    if y2 > 0:
        period2_monthly = base_p2_income * y2 * 0.019
        
    if y3 > 0:
        start_year_for_rate = max(2016.0, actual_start)
        avg_rate_2016plus = weighted_average_rate(start_year_for_rate, start_year_for_rate + y3)
        period3_monthly = base_p3_income * y3 * (avg_rate_2016plus / 100)

    # 💡 [수정] 물가상승률(Inflation)을 적용하여 진짜 체감 현재가치 산출
    base_pension_today_wage = period1_monthly + period2_monthly + period3_monthly
    estimated_monthly_pension = base_pension_today_wage * ((1 + salary_growth) ** years_to_retire) # 통장에 찍힐 명목 가치
    present_value_monthly_pension = estimated_monthly_pension / ((1 + inflation) ** years_to_retire) # 물가를 반영한 체감 현재 가치

    # ==========================================
    # 2. 퇴직수당 및 일시금 계산 (공단 PDF 공식 역산 적용)
    # ==========================================
    total_years = recognized_service_years
    final_nominal_income = current_standard_income * ((1 + salary_growth) ** years_to_retire)
    
    if total_years < 1: allowance_rate = 0.0
    elif total_years < 5: allowance_rate = 0.065
    elif total_years < 10: allowance_rate = 0.2275
    elif total_years < 15: allowance_rate = 0.2925
    elif total_years < 20: allowance_rate = 0.325
    else: allowance_rate = 0.39

    # 퇴직수당 (최종 기준소득월액 기반)
    allowance_pv = current_standard_income * int(total_years) * allowance_rate
    allowance_fv = final_nominal_income * int(total_years) * allowance_rate
    
    # 💡 [수정] 연금일시금 산식 정밀화 (최종 기준소득월액 기반 역산치 적용)
    excess_5_years = max(0.0, total_years - 5.0)
    lump_sum_multiplier = 0.975 + (excess_5_years * 0.0065)

    lump_sum_total_pv = current_standard_income * total_years * lump_sum_multiplier
    lump_sum_total_fv = final_nominal_income * total_years * lump_sum_multiplier

    pension_start_age = get_pension_start_age(entry_date, retirement_year)
    retirement_age_est = current_age + int(years_to_retire)
    gap_years = max(0, pension_start_age - retirement_age_est)
    
    return {
        "current_standard_income": current_standard_income,
        "current_a_value": current_a_value,
        "recognized_service_years": recognized_service_years,
        "service_cap_years": service_cap_years,
        "y1": y1, "y2": y2, "y3": y3,
        "base_p1_income": base_p1_income, "base_p2_income": base_p2_income, "base_p3_income": base_p3_income,
        "period1_monthly": period1_monthly, "period2_monthly": period2_monthly, "period3_monthly": period3_monthly,
        "estimated_monthly_pension": estimated_monthly_pension,
        "present_value_monthly_pension": present_value_monthly_pension,
        "allowance_pv": allowance_pv, "allowance_fv": allowance_fv,
        "lump_sum_total_pv": lump_sum_total_pv, "lump_sum_total_fv": lump_sum_total_fv,
        "pension_start_age": pension_start_age, "gap_years": gap_years, "retirement_year": retirement_year
    }

# =====================================
# 화면 구성
# =====================================
st.title("🏛️ 공무원연금 시뮬레이터 (추정 / 서류기반 정밀모드)")
st.markdown("정확한 계산을 위해서는 연금공단의 **[예상퇴직급여내역서]** 데이터를 직접 입력하는 것을 권장합니다.")

with st.sidebar:
    st.header("1. 기본 정보 입력")
    # 💡 [수정] 정밀 모드에서도 퇴직수당 계산을 위해 일반기여금이 반드시 필요하므로 밖으로 꺼냄
    current_contribution = st.number_input("현재 매월 납부하는 일반기여금 (원)", min_value=0, value=396500, step=1000)
    current_age = st.number_input("현재 나이 (세)", min_value=20, max_value=80, value=33)
    entry_date = st.date_input("최초임용일", value=date(2016, 3, 1), min_value=date(1980, 1, 1))
    retirement_date = st.date_input("예상 퇴직일", value=date(2055, 2, 28), help="정확한 재직기간 산출을 위해 예상되는 실제 퇴직 날짜를 입력하세요.")
    
    c1, c2 = st.columns(2)
    military_months = c1.number_input("군복무 산입(개월)", min_value=0, value=0, help="※ 기간 배분에 오차가 발생할 수 있으므로 추정 보정용으로만 사용하세요.")
    leave_months = c2.number_input("제외 휴직(개월)", min_value=0, value=0)

    st.divider()
    st.header("2. 소득 정보 방식 선택")
    use_exact_data = st.toggle("✅ 연단 예상퇴직급여내역서 데이터 직접 입력", value=True)
    
    exact_b_value, exact_redist_value, exact_p1_value = 0.0, 0.0, 0.0

    if use_exact_data:
        st.info("퇴직급여예상액 내역서 하단의 **'적용보수'** 표를 보고 입력하세요.")
        exact_b_value = st.number_input("개인 평균 기준소득월액 (B값)", value=3807467, step=10000)
        exact_redist_value = st.number_input("2016년 이후 소득재분배 반영 기준소득월액", value=5076495, step=10000)
        exact_p1_value = st.number_input("2009년 이전 3년 평균 보수월액", value=0, step=10000)
    else:
        st.warning("⚠️ **추정 모드 한계:** 기여금만으로는 과거 소득 이력을 정확히 알 수 없어 연금액 오차가 발생합니다.")

    st.divider()
    with st.expander("경제 지표 가정 (옵션)"):
        salary_growth_pct = st.number_input("미래 연 보수상승률 (%)", value=2.50, step=0.1)
        inflation_pct = st.number_input("미래 연 물가상승률 (%)", value=2.00, step=0.1, help="산출된 미래 명목가치를 체감 가능한 현재가치로 할인하는 데 사용됩니다.")

# 계산 실행
res = calculate_pension(
    current_age=int(current_age), entry_date=entry_date, retirement_date=retirement_date,
    military_months=int(military_months), leave_months=int(leave_months),
    current_contribution=int(current_contribution), 
    salary_growth=float(salary_growth_pct)/100, inflation=float(inflation_pct)/100,
    use_exact_data=use_exact_data, exact_b_value=exact_b_value, exact_redist_value=exact_redist_value, exact_p1_value=exact_p1_value
)

# 핵심 결과 출력 (연금)
st.subheader("💰 퇴직 시 예상 월 연금액")
c1, c2, c3, c4 = st.columns(4)
c1.metric("월 연금 (현재가치)", won(res["present_value_monthly_pension"]), help="미래의 연금액을 입력된 물가상승률로 할인한 실질 체감 금액입니다.")
c2.metric("월 연금 (명목가치)", won(res["estimated_monthly_pension"]), help="통장에 찍힐 액면가 금액입니다.")
c3.metric("총 인정 재직기간", f"{res['recognized_service_years']:.2f}년 (최대 {res['service_cap_years']}년)")
c4.metric("연금 개시 연령", f"{res['pension_start_age']}세 ({res['gap_years']}년 소득공백)")

st.divider()

# 핵심 결과 출력 (일시금)
st.subheader("💼 퇴직 시 예상 일시금액 (수당 및 연금일시금)")
d1, d2, d3, d4 = st.columns(4)
d1.metric("퇴직수당 (현재가치)", won(res["allowance_pv"]))
d2.metric("퇴직수당 (명목가치)", won(res["allowance_fv"]))
d3.metric("연금일시금 (현재가치)", won(res["lump_sum_total_pv"]))
d4.metric("연금일시금 (명목가치)", won(res["lump_sum_total_fv"]))

st.info(f"💡 **일시금으로 전액 수령 시 총액 [현재가치]:** {won(res['allowance_pv'] + res['lump_sum_total_pv'])} / **[명목가치]:** {won(res['allowance_fv'] + res['lump_sum_total_fv'])}")

st.divider()

# 구간별 분석
left, right = st.columns([1, 1])

with left:
    st.subheader("📊 적용된 기준 소득 (베이스 라인)")
    income_df = pd.DataFrame({
        "적용 구간": ["1기간 (2009년 이전)", "2기간 (2010~2015년)", "3기간 (2016년 이후)"],
        "기준 소득": [won(res["base_p1_income"]), won(res["base_p2_income"]), won(res["base_p3_income"])]
    })
    st.dataframe(income_df, use_container_width=True, hide_index=True)

with right:
    st.subheader("📈 기간별 연금 산출 내역")
    period_df = pd.DataFrame({
        "구간": ["1기간", "2기간", "3기간"],
        "적용 연수": [round(res["y1"], 2), round(res["y2"], 2), round(res["y3"], 2)],
        "연금 기여분 (현재가치)": [won(res["period1_monthly"]), won(res["period2_monthly"]), won(res["period3_monthly"])]
    })
    st.dataframe(period_df, use_container_width=True, hide_index=True)

# 💡 [수정] 성공 메시지 기대치 하향 조정
if use_exact_data:
    st.success("✅ 공무원연금공단 서류 데이터를 반영하여 산출된 추정치입니다.")
else:
    st.warning("⚠️ 현재 기여금만을 바탕으로 한 추정 모드입니다. 정확도를 높이려면 사이드바에서 토글 스위치를 켜고 서류 데이터를 입력하세요.")
