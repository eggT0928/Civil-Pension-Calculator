from dataclasses import dataclass
from datetime import date

import pandas as pd
import streamlit as st

# =====================================
# 기본 설정
# =====================================
st.set_page_config(
    page_title="교사용 공무원연금 계산기 (고도화 버전)",
    page_icon="🏫",
    layout="wide",
)

CURRENT_DATE = date(2026, 4, 21)
CURRENT_YEAR = CURRENT_DATE.year
DEFAULT_RETIREMENT_AGE = 62
CONTRIBUTION_RATE = 0.09
DEFAULT_SALARY_GROWTH = 0.025  # 연평균 보수상승률 추정치
DEFAULT_INFLATION = 0.020      # 연평균 물가상승률 추정치
DEFAULT_PERIOD2_RATE = 0.019   # 2010~2015년 지급률 1.9%
DEFAULT_A_VALUE = 5500000      # 2026년 기준 전체 공무원 평균소득 추정치(A값)

# 2016년 이후 지급률 단계적 인하 (공무원연금법 부칙 반영)
PENSION_RATES = {
    2016: 1.878, 2017: 1.856, 2018: 1.834, 2019: 1.812,
    2020: 1.790, 2021: 1.780, 2022: 1.770, 2023: 1.760,
    2024: 1.750, 2025: 1.740, 2026: 1.736, 2027: 1.732,
    2028: 1.728, 2029: 1.724, 2030: 1.720, 2031: 1.716,
    2032: 1.712, 2033: 1.708, 2034: 1.704, 2035: 1.700,
}

# =====================================
# 유틸 함수
# =====================================
def won(value: float) -> str:
    return f"{int(round(value)):,}원"

def pct(value: float) -> str:
    return f"{value:.3f}%"

def years_between(start_date: date, end_date: date) -> float:
    if end_date <= start_date:
        return 0.0
    return (end_date - start_date).days / 365.2425

def get_pension_start_age(entry_date: date, retirement_year: int) -> int:
    """공무원연금 개시연령 규정 (1996년 이후 임용자 기준)"""
    if entry_date <= date(1995, 12, 31):
        return 60  # 1995년 이전 임용자는 종전 규정(보통 60세) 적용 (경과조치 있음)
    if entry_date <= date(2009, 12, 31):
        if retirement_year <= 2021: return 60
        elif retirement_year <= 2023: return 61
        elif retirement_year <= 2026: return 62
        elif retirement_year <= 2029: return 63
        elif retirement_year <= 2032: return 64
        else: return 65
    return 65  # 2010년 이후 임용자는 무조건 65세 개시

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
    """2016.1.1 기준 재직기간에 따른 상한연수"""
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

@dataclass
class PensionResult:
    current_age: int
    entry_date: date
    retirement_year: int
    years_to_retire: int
    pension_start_age: int
    gap_years: int
    current_standard_income: float
    projected_retirement_income: float
    average_final_3_years: float
    lifetime_average_income_est: float
    pre_2016_service_years: float
    service_cap_years: int
    recognized_service_years: float
    raw_y1: float; raw_y2: float; raw_y3: float
    y1: float; y2: float; y3: float
    avg_rate_2016plus: float
    period1_monthly: float
    period2_monthly: float
    period3_monthly: float
    estimated_monthly_pension: float
    present_value_monthly_pension: float
    current_monthly_contribution: float

# =====================================
# 핵심 계산 로직 (공단 산식 모사)
# =====================================
def calculate_pension(
    current_contribution: int, current_age: int, entry_date: date,
    retirement_age: int, salary_growth: float, inflation: float,
    period2_rate: float, a_value: float
) -> PensionResult:
    
    # 1. 현재 기준소득월액 역산 (기여금 / 9%)
    current_standard_income = current_contribution / CONTRIBUTION_RATE if current_contribution > 0 else 0.0
    years_to_retire = max(0, retirement_age - current_age)
    retirement_year = CURRENT_YEAR + years_to_retire

    pension_timeline_start = float(entry_date.year + (entry_date.timetuple().tm_yday - 1) / 365.2425)
    pension_timeline_end = float(retirement_year + 1)

    raw_y1 = overlap_years(pension_timeline_start, pension_timeline_end, 0, 2010)
    raw_y2 = overlap_years(pension_timeline_start, pension_timeline_end, 2010, 2016)
    raw_y3 = overlap_years(pension_timeline_start, pension_timeline_end, 2016, retirement_year + 1)

    pre_2016_service_years = years_between(entry_date, date(2016, 1, 1))
    service_cap_years = recognized_service_cap(pre_2016_service_years)
    y1, y2, y3 = apply_service_cap(raw_y1, raw_y2, raw_y3, service_cap_years)
    recognized_service_years = y1 + y2 + y3

    # 2. 미래/과거 소득 추정 (단순화된 보수/물가상승률 적용)
    # 퇴직 시점 기준소득월액 (1기간용)
    projected_retirement_income = current_standard_income * ((1 + salary_growth) ** years_to_retire)
    projected_1_year_before = current_standard_income * ((1 + salary_growth) ** max(0, years_to_retire - 1))
    projected_2_years_before = current_standard_income * ((1 + salary_growth) ** max(0, years_to_retire - 2))
    average_final_3_years = (projected_retirement_income + projected_1_year_before + projected_2_years_before) / 3

    # 전기간 평균기준소득월액 추정 (2, 3기간용 B값)
    # 현실에서는 과거 급여 이력을 물가상승률로 환산해야 하나, 여기서는 현재 소득을 기준으로 약간 할인된 값으로 추정
    # 보수 상승이 가파를수록 전기간 평균은 현재보다 낮아짐
    lifetime_average_income_est = current_standard_income * 0.95 

    # 3. 기간별 연금 산식 적용
    # [1기간: 2009년 이전] 퇴직전 3년 평균보수월액 기준
    if y1 > 0:
        if y1 >= 20:
            period1_monthly = average_final_3_years * 0.5 + average_final_3_years * (y1 - 20) * 0.02
        else:
            period1_monthly = average_final_3_years * y1 * 0.025
    else:
        period1_monthly = 0.0

    # [2기간: 2010~2015년] 전기간 평균소득월액 기준 (1.9%)
    period2_monthly = lifetime_average_income_est * y2 * period2_rate

    # [3기간: 2016년 이후] 소득재분배(A값+B값) 적용 + 단계적 인하율
    avg_rate_2016plus = weighted_average_rate(max(2016, pension_timeline_start), pension_timeline_start + recognized_service_years) if y3 > 0 else 0.0
    
    if y3 > 0:
        # 소득재분배 산식: (A값 + 내 소득) / 2
        redistributed_income = (a_value + lifetime_average_income_est) / 2
        # 최고/최저 기준 적용 (공단 규정 모사)
        redistributed_income = min(redistributed_income, a_value * 1.6)
        
        # 30년 이하 기간은 1%의 유족연금 재원 확보 분을 뺀 지급률 적용 (복잡성으로 단순 평균 반영)
        period3_monthly = redistributed_income * y3 * (avg_rate_2016plus / 100)
    else:
        period3_monthly = 0.0

    estimated_monthly_pension = period1_monthly + period2_monthly + period3_monthly

    pension_start_age = get_pension_start_age(entry_date, retirement_year)
    pension_start_year = CURRENT_YEAR + max(0, pension_start_age - current_age)
    gap_years = max(0, pension_start_age - retirement_age)
    
    # 연금 현재가치 환산 (개시 시점까지의 물가상승률로 할인)
    years_until_pension = max(0, pension_start_year - CURRENT_YEAR)
    present_value_monthly_pension = estimated_monthly_pension / ((1 + inflation) ** years_until_pension)

    return PensionResult(
        current_age=current_age, entry_date=entry_date, retirement_year=retirement_year,
        years_to_retire=years_to_retire, pension_start_age=pension_start_age, gap_years=gap_years,
        current_standard_income=current_standard_income, projected_retirement_income=projected_retirement_income,
        average_final_3_years=average_final_3_years, lifetime_average_income_est=lifetime_average_income_est,
        pre_2016_service_years=pre_2016_service_years, service_cap_years=service_cap_years,
        recognized_service_years=recognized_service_years, raw_y1=raw_y1, raw_y2=raw_y2, raw_y3=raw_y3,
        y1=y1, y2=y2, y3=y3, avg_rate_2016plus=avg_rate_2016plus,
        period1_monthly=period1_monthly, period2_monthly=period2_monthly, period3_monthly=period3_monthly,
        estimated_monthly_pension=estimated_monthly_pension, present_value_monthly_pension=present_value_monthly_pension,
        current_monthly_contribution=current_contribution,
    )

# =====================================
# 화면 구성
# =====================================
st.title("🏫 교사용 공무원연금 계산기 (Pro 버전)")
st.caption("소득재분배(A값) 및 기간별 산식을 모사하여 정확도를 높인 추정 계산기입니다.")

with st.sidebar:
    st.header("입력값")
    current_contribution = st.number_input("현재 일반기여금 (원)", min_value=0, value=396500, step=1000)
    current_age = st.number_input("현재 나이", min_value=20, max_value=80, value=33, step=1)
    entry_date = st.date_input("최초임용일", value=date(2016, 3, 1), min_value=date(1980, 1, 1), max_value=date(2060, 12, 31))

    st.divider()
    st.header("기본 가정 (조정 가능)")
    retirement_age = st.number_input("정년 (세)", min_value=55, max_value=70, value=DEFAULT_RETIREMENT_AGE, step=1)
    a_value = st.number_input("전체 공무원 평균소득 (A값)", min_value=3000000, value=DEFAULT_A_VALUE, step=100000)
    salary_growth_pct = st.number_input("연 보수상승률 (%)", min_value=0.00, max_value=10.00, value=DEFAULT_SALARY_GROWTH * 100, step=0.01)
    inflation_pct = st.number_input("연 물가상승률 (%)", min_value=0.00, max_value=10.00, value=DEFAULT_INFLATION * 100, step=0.01)

result = calculate_pension(
    current_contribution=int(current_contribution), current_age=int(current_age),
    entry_date=entry_date, retirement_age=int(retirement_age),
    salary_growth=float(salary_growth_pct) / 100, inflation=float(inflation_pct) / 100,
    period2_rate=DEFAULT_PERIOD2_RATE, a_value=float(a_value)
)

st.warning(
    "⚠️ **주의:** 본 계산기는 공단 지급 기준을 최대한 모사했으나, 과거 급여 이력 전체 데이터가 없기 때문에 "
    "실제 수령액과 오차가 발생합니다. (정확한 금액은 공무원연금공단 홈페이지 내 연금예상액 조회를 이용하세요)"
)

# 핵심 결과 카드
c1, c2, c3, c4 = st.columns(4)
c1.metric("현재 기준소득월액", won(result.current_standard_income))
c2.metric("퇴직 시 예상 월연금", won(result.estimated_monthly_pension))
c3.metric("현재가치 기준 월연금", won(result.present_value_monthly_pension))
c4.metric("연금 개시연령", f"{result.pension_start_age}세")

c5, c6, c7, c8 = st.columns(4)
c5.metric("현재 나이", f"{result.current_age}세")
c6.metric("예상 퇴직연도", f"{result.retirement_year}년")
c7.metric("소득 공백기 (정년~개시)", f"{result.gap_years}년")
c8.metric("총 인정 재직연수", f"{result.recognized_service_years:.1f}년")

st.subheader("📊 연금 계산 세부 공식 (공단 규정 모사)")
st.markdown(
    """
### 1) 1기간 (2009년 이전 재직분) : 종전 보수월액 기준
- **계산식:** 퇴직 전 3년 평균보수월액 × 재직연수 × 2.5% (20년 초과분은 2%)
- 과거에는 퇴직 직전 급여가 가장 중요했습니다. 앱에서는 기여금을 기반으로 미래 퇴직 소득을 추정하여 적용합니다.

### 2) 2기간 (2010년 ~ 2015년 재직분) : 전 기간 평균소득 기준
- **계산식:** 전 기간 평균기준소득월액(B값) × 재직연수 × 1.9%
- 개혁 이후, 직전 급여가 아닌 공무원 생활 전체 기간의 평균 소득으로 기준이 변경되었습니다.

### 3) 3기간 (2016년 이후 재직분) : 소득재분배(A값) 도입
- **계산식:** **(A값 + B값) / 2** × 재직연수 × 연도별 인하된 지급률(1.7%까지 하락)
- **A값(전체 공무원 평균):** 하위직의 연금을 올려주고 고위직을 깎는 '소득재분배' 장치입니다.
    """
)

left, right = st.columns([1.15, 0.85])

with left:
    st.subheader("구간별 기여분 상세내역")
    period_df = pd.DataFrame({
        "구간": ["1기간 (2009 이전)", "2기간 (2010~2015)", "3기간 (2016 이후)"],
        "적용 연수": [round(result.y1, 2), round(result.y2, 2), round(result.y3, 2)],
        "월연금 산출액": [won(result.period1_monthly), won(result.period2_monthly), won(result.period3_monthly)],
    })
    st.dataframe(period_df, use_container_width=True, hide_index=True)
    
    chart_data = pd.DataFrame({
        "구간": ["1기간", "2기간", "3기간"],
        "월연금 기여분": [result.period1_monthly, result.period2_monthly, result.period3_monthly]
    }).set_index("구간")
    st.bar_chart(chart_data)

with right:
    st.subheader("계산에 쓰인 핵심 지표")
    numbers_df = pd.DataFrame({
        "항목": [
            "적용된 A값 (소득재분배용)",
            "내 평균소득(B값) 추정",
            "1기간 적용 보수월액 추정",
            "3기간 평균 지급률 (1.7% 향해 하락)",
            "적용된 재직 상한연수",
        ],
        "값": [
            won(a_value),
            won(result.lifetime_average_income_est),
            won(result.average_final_3_years),
            pct(result.avg_rate_2016plus),
            f"{result.service_cap_years}년",
        ]
    })
    st.dataframe(numbers_df, use_container_width=True, hide_index=True)
