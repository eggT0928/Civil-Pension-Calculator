from dataclasses import dataclass
from datetime import date

import pandas as pd
import streamlit as st

# =====================================
# 기본 설정
# =====================================
st.set_page_config(
    page_title="교사용 공무원연금 계산기",
    page_icon="🏫",
    layout="wide",
)

CURRENT_DATE = date(2026, 4, 21)
CURRENT_YEAR = CURRENT_DATE.year
DEFAULT_RETIREMENT_AGE = 62
CONTRIBUTION_RATE = 0.09
DEFAULT_SALARY_GROWTH = 0.0252
DEFAULT_INFLATION = 0.0209
DEFAULT_PERIOD2_RATE = 0.019

# 2016년 이후 지급률 단순화 테이블
PENSION_RATES = {
    2016: 1.878,
    2017: 1.856,
    2018: 1.834,
    2019: 1.812,
    2020: 1.790,
    2021: 1.780,
    2022: 1.770,
    2023: 1.760,
    2024: 1.750,
    2025: 1.740,
    2026: 1.736,
    2027: 1.732,
    2028: 1.728,
    2029: 1.724,
    2030: 1.720,
    2031: 1.716,
    2032: 1.712,
    2033: 1.708,
    2034: 1.704,
    2035: 1.700,
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
    """1996년 이후 임용자 기준 단계 상향 구조를 단순 반영"""
    if entry_date <= date(1995, 12, 31):
        return 62
    if retirement_year <= 2021:
        return 60
    if retirement_year <= 2023:
        return 61
    if retirement_year <= 2026:
        return 62
    if retirement_year <= 2029:
        return 63
    if retirement_year <= 2032:
        return 64
    return 65


def pension_rate_for_year(year: int) -> float:
    if year in PENSION_RATES:
        return PENSION_RATES[year]
    if year < 2016:
        return 1.9
    return 1.7


def overlap_years(start: float, end: float, range_start: int, range_end_exclusive: int) -> float:
    return max(0.0, min(end, range_end_exclusive) - max(start, range_start))


def weighted_average_rate(start_year_float: float, end_year_float: float) -> float:
    """2016년 이후 지급률 평균(단순 가중평균)"""
    if end_year_float <= start_year_float:
        return 0.0

    total_rate = 0.0
    total_weight = 0.0
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
    """2016.1.1 현재 재직기간에 따른 상한"""
    if pre_2016_service_years >= 21:
        return 33
    if pre_2016_service_years >= 17:
        return 34
    if pre_2016_service_years >= 15:
        return 35
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
    pension_start_year: int
    gap_years: int
    current_standard_income: float
    projected_retirement_income: float
    average_final_3_years: float
    pre_2016_service_years: float
    service_cap_years: int
    recognized_service_years: float
    raw_y1: float
    raw_y2: float
    raw_y3: float
    y1: float
    y2: float
    y3: float
    avg_rate_2016plus: float
    period1_monthly: float
    period2_monthly: float
    period3_monthly: float
    estimated_monthly_pension: float
    present_value_monthly_pension: float
    current_monthly_contribution: float


# =====================================
# 핵심 계산 로직
# =====================================
def calculate_pension(
    current_contribution: int,
    current_age: int,
    entry_date: date,
    retirement_age: int,
    salary_growth: float,
    inflation: float,
    period2_rate: float,
) -> PensionResult:
    """
    현재 일반기여금, 현재 나이, 최초임용일로 대략적인 연금수령액을 추정한다.
    - 현재 기준소득월액 = 일반기여금 ÷ 9%
    - 2016.1.1 현재 재직기간에 따라 33/34/35/36년 상한 적용
    - 정년은 기본 62세
    - 공단의 소득재분배 평균기준소득월액은 단순화하여 미반영
    """
    current_standard_income = current_contribution / CONTRIBUTION_RATE if current_contribution > 0 else 0.0
    years_to_retire = max(0, retirement_age - current_age)
    retirement_year = CURRENT_YEAR + years_to_retire

    # 재직기간 구간(원시값)
    pension_timeline_start = float(entry_date.year + (entry_date.timetuple().tm_yday - 1) / 365.2425)
    pension_timeline_end = float(retirement_year + 1)  # 단순하게 퇴직연도 말까지 재직 가정

    raw_y1 = overlap_years(pension_timeline_start, pension_timeline_end, 0, 2010)
    raw_y2 = overlap_years(pension_timeline_start, pension_timeline_end, 2010, 2016)
    raw_y3 = overlap_years(pension_timeline_start, pension_timeline_end, 2016, retirement_year + 1)

    # 2016.1.1 기준 재직기간과 상한 적용
    pre_2016_service_years = years_between(entry_date, date(2016, 1, 1))
    service_cap_years = recognized_service_cap(pre_2016_service_years)
    y1, y2, y3 = apply_service_cap(raw_y1, raw_y2, raw_y3, service_cap_years)
    recognized_service_years = y1 + y2 + y3

    # 퇴직 시점 기준소득월액 추정
    projected_retirement_income = current_standard_income * ((1 + salary_growth) ** years_to_retire)
    projected_1_year_before = current_standard_income * ((1 + salary_growth) ** max(0, years_to_retire - 1))
    projected_2_years_before = current_standard_income * ((1 + salary_growth) ** max(0, years_to_retire - 2))
    average_final_3_years = (projected_retirement_income + projected_1_year_before + projected_2_years_before) / 3

    # 1기간, 2기간, 3기간 단순 산식
    if y1 >= 20:
        period1_monthly = average_final_3_years * 0.5 + average_final_3_years * (y1 - 20) * 0.02
    else:
        period1_monthly = average_final_3_years * y1 * 0.025

    period2_monthly = average_final_3_years * y2 * period2_rate
    avg_rate_2016plus = weighted_average_rate(max(2016, pension_timeline_start), 2016 + y3) if y3 > 0 else 0.0
    period3_monthly = average_final_3_years * y3 * (avg_rate_2016plus / 100)

    estimated_monthly_pension = period1_monthly + period2_monthly + period3_monthly

    pension_start_age = get_pension_start_age(entry_date, retirement_year)
    pension_start_year = CURRENT_YEAR + max(0, pension_start_age - current_age)
    gap_years = max(0, pension_start_age - retirement_age)
    present_value_monthly_pension = estimated_monthly_pension / ((1 + inflation) ** max(0, pension_start_year - CURRENT_YEAR))

    return PensionResult(
        current_age=current_age,
        entry_date=entry_date,
        retirement_year=retirement_year,
        years_to_retire=years_to_retire,
        pension_start_age=pension_start_age,
        pension_start_year=pension_start_year,
        gap_years=gap_years,
        current_standard_income=current_standard_income,
        projected_retirement_income=projected_retirement_income,
        average_final_3_years=average_final_3_years,
        pre_2016_service_years=pre_2016_service_years,
        service_cap_years=service_cap_years,
        recognized_service_years=recognized_service_years,
        raw_y1=raw_y1,
        raw_y2=raw_y2,
        raw_y3=raw_y3,
        y1=y1,
        y2=y2,
        y3=y3,
        avg_rate_2016plus=avg_rate_2016plus,
        period1_monthly=period1_monthly,
        period2_monthly=period2_monthly,
        period3_monthly=period3_monthly,
        estimated_monthly_pension=estimated_monthly_pension,
        present_value_monthly_pension=present_value_monthly_pension,
        current_monthly_contribution=current_contribution,
    )


# =====================================
# 화면 구성
# =====================================
st.title("🏫 교사용 공무원연금 계산기")
st.caption("현재 나이, 현재 일반기여금, 최초임용일과 기본 가정만으로 수령연금을 추정하는 간단 버전입니다.")

with st.sidebar:
    st.header("입력값")
    current_contribution = st.number_input("현재 일반기여금", min_value=0, value=396500, step=1000)
    current_age = st.number_input("현재 나이", min_value=20, max_value=80, value=33, step=1)
    entry_date = st.date_input("최초임용일", value=date(2016, 3, 1), min_value=date(1980, 1, 1), max_value=date(2060, 12, 31))

    st.divider()
    st.header("기본 가정")
    retirement_age = st.number_input("정년", min_value=55, max_value=70, value=DEFAULT_RETIREMENT_AGE, step=1)
    salary_growth_pct = st.number_input("연 보수상승률(%)", min_value=0.00, max_value=10.00, value=DEFAULT_SALARY_GROWTH * 100, step=0.01)
    inflation_pct = st.number_input("연 물가상승률(%)", min_value=0.00, max_value=10.00, value=DEFAULT_INFLATION * 100, step=0.01)
    period2_rate_pct = st.number_input("2기간 지급률(%)", min_value=0.00, max_value=5.00, value=DEFAULT_PERIOD2_RATE * 100, step=0.001)

result = calculate_pension(
    current_contribution=int(current_contribution),
    current_age=int(current_age),
    entry_date=entry_date,
    retirement_age=int(retirement_age),
    salary_growth=float(salary_growth_pct) / 100,
    inflation=float(inflation_pct) / 100,
    period2_rate=float(period2_rate_pct) / 100,
)

st.info(
    "이 계산기는 공식 산정액이 아닌 추정용 베타입니다. "
    "현재 일반기여금으로 기준소득월액을 역산하고, 최초임용일을 기준으로 2016.1.1 현재 재직기간을 계산해 33·34·35·36년 상한을 적용합니다. "
    "다만 실제 공무원연금공단의 기준소득월액 이력, 소득재분배 평균기준소득월액, 경과규정, 휴직 이력 등을 완전하게 재현하지 못하므로 참고용으로만 활용해 주세요."
)

# 핵심 결과 카드
c1, c2, c3, c4 = st.columns(4)
c1.metric("현재 기준소득월액(역산)", won(result.current_standard_income))
c2.metric("예상 월연금", won(result.estimated_monthly_pension))
c3.metric("현재가치 기준 월연금", won(result.present_value_monthly_pension))
c4.metric("연금 개시연령", f"{result.pension_start_age}세")

c5, c6, c7, c8 = st.columns(4)
c5.metric("현재 나이", f"{result.current_age}세")
c6.metric("예상 퇴직연도", f"{result.retirement_year}년")
c7.metric("정년~개시 공백", f"{result.gap_years}년")
c8.metric("총 인정 재직연수", f"{result.recognized_service_years:.1f}년")

st.subheader("연금 계산 공식 설명")
st.markdown(
    """
### 1) 현재 기준소득월액 역산
- **현재 기준소득월액 = 현재 일반기여금 ÷ 9%**
- 사용자가 입력한 일반기여금을 바탕으로 현재 공단상 기준소득월액을 역산합니다.

### 2) 퇴직 시점 기준소득월액 추정
- **퇴직 시점 기준소득월액 = 현재 기준소득월액 × (1 + 연 보수상승률)^(남은 연수)**
- 마지막 3년 평균은 퇴직 시점, 1년 전, 2년 전 기준소득월액의 평균으로 단순 추정합니다.

### 3) 재직기간 상한 적용
- **2016.1.1 현재 재직기간**에 따라 상한이 달라집니다.
- 21년 이상이면 33년, 17년 이상이면 34년, 15년 이상이면 35년, 15년 미만이면 36년 상한을 적용합니다.
- 이 앱은 입력한 **최초임용일**로 2016.1.1 기준 재직기간을 계산해 상한을 자동 적용합니다.

### 4) 재직기간을 1기간·2기간·3기간으로 분리
- **1기간**: 2009년 말까지의 재직기간
- **2기간**: 2010년 ~ 2015년 재직기간
- **3기간**: 2016년 이후 재직기간
- 공무원연금 개혁으로 시기별 산식과 지급률이 달라져서 기간을 나눠 계산합니다.

### 5) 최종 월연금
- **최종 월연금 = 1기간 월연금 + 2기간 월연금 + 3기간 월연금**
    """
)

st.subheader("1기간 · 2기간 · 3기간이란?")
st.markdown(
    """
- **1기간**은 연금 개혁 이전인 **2009년 말까지**의 재직기간입니다.
- **2기간**은 2010년 제도 변경 이후부터 2015년 말까지의 재직기간입니다.
- **3기간**은 2016년 공무원연금 개혁 이후의 재직기간입니다.
- 왜 나누냐면, **시기마다 적용되는 지급률과 계산 구조가 다르기 때문**입니다.
- 그래서 같은 사람도 재직기간이 여러 시기에 걸쳐 있으면 **구간별로 따로 계산한 뒤 합산**합니다.
    """
)

left, right = st.columns([1.15, 0.85])

with left:
    st.subheader("연금이 어떻게 계산됐는지")
    explain_df = pd.DataFrame(
        {
            "단계": [
                "1. 현재 일반기여금 입력",
                "2. 현재 기준소득월액 역산",
                "3. 2016.1.1 기준 재직기간 계산",
                "4. 재직기간 상한 결정",
                "5. 정년까지 남은 기간 계산",
                "6. 퇴직 시점 기준소득월액 추정",
                "7. 마지막 3년 평균 추정",
                "8. 1·2·3기간 분리 후 상한 반영",
                "9. 구간별 월연금 계산",
                "10. 최종 월연금 합산",
            ],
            "내용": [
                won(result.current_monthly_contribution),
                f"{won(result.current_monthly_contribution)} ÷ 9% = {won(result.current_standard_income)}",
                f"{result.pre_2016_service_years:.2f}년",
                f"최대 {result.service_cap_years}년 인정",
                f"정년 {retirement_age}세까지 {result.years_to_retire}년 남음",
                won(result.projected_retirement_income),
                won(result.average_final_3_years),
                f"1기간 {result.y1:.2f}년 / 2기간 {result.y2:.2f}년 / 3기간 {result.y3:.2f}년",
                f"1기간 {won(result.period1_monthly)} + 2기간 {won(result.period2_monthly)} + 3기간 {won(result.period3_monthly)}",
                won(result.estimated_monthly_pension),
            ],
        }
    )
    st.dataframe(explain_df, use_container_width=True, hide_index=True)

    st.subheader("구간별 기여분")
    period_df = pd.DataFrame(
        {
            "구간": ["1기간(2009 이전)", "2기간(2010~2015)", "3기간(2016 이후)"],
            "원시연수": [round(result.raw_y1, 2), round(result.raw_y2, 2), round(result.raw_y3, 2)],
            "상한 반영 연수": [round(result.y1, 2), round(result.y2, 2), round(result.y3, 2)],
            "월연금 기여분": [result.period1_monthly, result.period2_monthly, result.period3_monthly],
        }
    )
    display_period_df = period_df.copy()
    display_period_df["월연금 기여분"] = display_period_df["월연금 기여분"].map(won)
    st.dataframe(display_period_df, use_container_width=True, hide_index=True)
    st.bar_chart(period_df.set_index("구간")[["월연금 기여분"]])

with right:
    st.subheader("계산에 쓰인 핵심 숫자")
    numbers_df = pd.DataFrame(
        {
            "항목": [
                "현재 일반기여금",
                "현재 기준소득월액(역산)",
                "최초임용일",
                "2016.1.1 기준 재직기간",
                "재직기간 상한",
                "예상 퇴직연도",
                "퇴직 시점 기준소득월액 추정",
                "마지막 3년 평균 추정",
                "2016년 이후 평균 지급률",
                "연금 개시연령",
            ],
            "값": [
                won(result.current_monthly_contribution),
                won(result.current_standard_income),
                result.entry_date.isoformat(),
                f"{result.pre_2016_service_years:.2f}년",
                f"{result.service_cap_years}년",
                f"{result.retirement_year}년",
                won(result.projected_retirement_income),
                won(result.average_final_3_years),
                pct(result.avg_rate_2016plus),
                f"{result.pension_start_age}세",
            ],
        }
    )
    st.dataframe(numbers_df, use_container_width=True, hide_index=True)

    st.subheader("현재 반영한 기본 가정")
    st.markdown(
        f"""
- **입력값**: 현재 일반기여금 / 현재 나이 / 최초임용일
- **정년**: {retirement_age}세
- **연 보수상승률**: {salary_growth_pct:.2f}%
- **연 물가상승률**: {inflation_pct:.2f}%
- **2기간 지급률**: {period2_rate_pct:.3f}%
- **재직기간 상한**: 2016.1.1 현재 재직기간 기준 자동 적용
        """
    )

st.subheader("주의")
st.markdown(
    """
- 이 앱은 **공식 산정액이 아닌 추정용 베타 계산기**입니다.
- 실제 지급액은 공무원연금공단의 **기준소득월액 이력**, **소득재분배 평균기준소득월액**, **경과규정**, **휴직 이력**, **군복무 산입 승인 여부**, **제도 변경** 등에 따라 달라질 수 있습니다.
- 현재 버전은 사용자가 **수령연금이 어떤 구조로 계산되는지**를 이해하고, **대략적인 규모**를 감 잡는 데 목적이 있습니다.
- 따라서 결과는 **대략적인 추정치**로만 활용해 주세요.
    """
)
