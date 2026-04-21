import math
from dataclasses import dataclass

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

CURRENT_YEAR = 2026

# 2026 교원 봉급표(유·초·중·고 교원 등) 기반 입력값
# 출처 반영용 숫자만 코드에 넣고, 설명은 화면에 별도 표기
TEACHER_PAY_2026 = {
    1: 2041500,
    2: 2103300,
    3: 2166000,
    4: 2228500,
    5: 2291500,
    6: 2354400,
    7: 2416600,
    8: 2478600,
    9: 2495600,
    10: 2516700,
    11: 2538300,
    12: 2585900,
    13: 2657500,
    14: 2773700,
    15: 2889700,
    16: 3006200,
    17: 3121000,
    18: 3241500,
    19: 3361200,
    20: 3481000,
    21: 3600700,
    22: 3733600,
    23: 3865300,
    24: 3997500,
    25: 4129400,
    26: 4261900,
    27: 4400100,
    28: 4538000,
    29: 4682100,
    30: 4826800,
    31: 4971100,
    32: 5115200,
    33: 5261600,
    34: 5407500,
    35: 5553600,
    36: 5699100,
    37: 5825700,
    38: 5952500,
    39: 6079500,
    40: 6205700,
}

# 최근 대화 기준 가정값
DEFAULTS = {
    "salary_growth": 2.52,
    "inflation": 2.09,
    "teaching_allowance": 250000,
    "meal_allowance": 160000,
    "homeroom_allowance": 250000,
    "position_allowance": 150000,
    "research_fee": 60000,
    "geunsok_gabong": 81000,
}

# 2026 교육공무원 성과상여금(12개월 근무 기준, 차등지급률 50%)
PERFORMANCE_PAY_2026 = {
    "S": 5256020,
    "A": 4401380,
    "B": 3760400,
}

# 2016 이후 지급률 단순화 테이블
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
    return f"{value:.2f}%"


def clamp(value: int, min_value: int, max_value: int) -> int:
    return max(min_value, min(value, max_value))


def get_pension_start_age(entry_year: int, retirement_year: int) -> int:
    """1996년 이후 임용자 기준 단계 상향 구조를 단순화해 반영"""
    if entry_year <= 1995:
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


def child_allowance(child_count: int) -> int:
    total = 0
    for i in range(1, child_count + 1):
        if i == 1:
            total += 50000
        elif i == 2:
            total += 80000
        else:
            total += 120000
    return total


def regular_bonus_rate(service_years: float) -> float:
    if service_years < 1:
        return 0.0
    if service_years < 2:
        return 0.05
    if service_years < 3:
        return 0.10
    if service_years < 4:
        return 0.15
    if service_years < 5:
        return 0.20
    if service_years < 6:
        return 0.25
    if service_years < 7:
        return 0.30
    if service_years < 8:
        return 0.35
    if service_years < 9:
        return 0.40
    if service_years < 10:
        return 0.45
    return 0.50


def weighted_average_rate(start_year_float: float, end_year_float: float) -> float:
    """2016년 이후 지급률 평균(단순 가중평균)"""
    if end_year_float <= start_year_float:
        return 0.0

    total_rate = 0.0
    total_weight = 0.0
    year = math.floor(start_year_float)

    while year < math.ceil(end_year_float):
        s = max(start_year_float, year)
        e = min(end_year_float, year + 1)
        weight = max(0.0, e - s)
        if weight > 0:
            total_rate += pension_rate_for_year(year) * weight
            total_weight += weight
        year += 1

    if total_weight == 0:
        return 0.0
    return total_rate / total_weight


def geometric_contribution_estimate(monthly_base: float, annual_growth: float, years_left: int) -> float:
    if years_left <= 0:
        return 0.0
    total = 0.0
    growth = 1 + annual_growth / 100
    for i in range(years_left):
        total += monthly_base * (growth ** i) * 12 * 0.09
    return total


def overlap_years(start: float, end: float, range_start: int, range_end_exclusive: int) -> float:
    return max(0.0, min(end, range_end_exclusive) - max(start, range_start))


@dataclass
class SalaryBreakdown:
    adjusted_step: int
    base_pay: float
    recurring_allowances: float
    holiday_bonus_annual: float
    regular_bonus_annual: float
    performance_bonus_annual: float
    monthly_equivalent_income: float
    family_allowance_monthly: float
    service_years_now: float


@dataclass
class PensionResult:
    current_age: int
    retirement_year: int
    years_to_retire: int
    pension_start_age: int
    pension_start_year: int
    gap_years: int
    recognized_service_years: float
    y1: float
    y2: float
    y3: float
    average_final_3_years: float
    projected_retirement_income: float
    period1_monthly: float
    period2_monthly: float
    period3_monthly: float
    estimated_monthly_pension: float
    present_value_monthly_pension: float
    current_monthly_contribution: float
    future_contribution_sum: float
    avg_rate_2016plus: float


# =====================================
# 계산 로직
# =====================================
def build_salary_breakdown(
    input_mode: str,
    teacher_step: int,
    onejeong_step: bool,
    graduate_step: bool,
    geunsok_count: int,
    include_teaching_allowance: bool,
    include_meal_allowance: bool,
    is_homeroom: bool,
    is_position_teacher: bool,
    include_research_fee: bool,
    research_fee: int,
    has_spouse: bool,
    child_count: int,
    dependent_count: int,
    include_holiday_bonus: bool,
    include_regular_bonus: bool,
    include_performance_bonus: bool,
    performance_grade: str,
    manual_income: int,
    entry_year: int,
    military_months: int,
) -> SalaryBreakdown:
    service_years_now = max(0.0, CURRENT_YEAR - entry_year + military_months / 12)

    adjusted_step = teacher_step + (1 if onejeong_step else 0) + (1 if graduate_step else 0)
    adjusted_step = clamp(adjusted_step, 1, 40)

    if input_mode == "직접입력":
        return SalaryBreakdown(
            adjusted_step=adjusted_step,
            base_pay=float(manual_income),
            recurring_allowances=0.0,
            holiday_bonus_annual=0.0,
            regular_bonus_annual=0.0,
            performance_bonus_annual=0.0,
            monthly_equivalent_income=float(manual_income),
            family_allowance_monthly=0.0,
            service_years_now=service_years_now,
        )

    family_monthly = (40000 if has_spouse else 0) + child_allowance(child_count) + dependent_count * 20000
    base_pay = TEACHER_PAY_2026[adjusted_step] + geunsok_count * DEFAULTS["geunsok_gabong"]

    recurring = 0.0
    if include_teaching_allowance:
        recurring += DEFAULTS["teaching_allowance"]
    if include_meal_allowance:
        recurring += DEFAULTS["meal_allowance"]
    if is_homeroom:
        recurring += DEFAULTS["homeroom_allowance"]
    if is_position_teacher:
        recurring += DEFAULTS["position_allowance"]
    if include_research_fee:
        recurring += research_fee
    recurring += family_monthly

    holiday_bonus_annual = base_pay * 0.6 * 2 if include_holiday_bonus else 0.0
    regular_bonus_annual = base_pay * regular_bonus_rate(service_years_now) * 2 if include_regular_bonus else 0.0
    performance_bonus_annual = PERFORMANCE_PAY_2026[performance_grade] if include_performance_bonus else 0.0

    monthly_equivalent = base_pay + recurring + (holiday_bonus_annual + regular_bonus_annual + performance_bonus_annual) / 12

    return SalaryBreakdown(
        adjusted_step=adjusted_step,
        base_pay=base_pay,
        recurring_allowances=recurring,
        holiday_bonus_annual=holiday_bonus_annual,
        regular_bonus_annual=regular_bonus_annual,
        performance_bonus_annual=performance_bonus_annual,
        monthly_equivalent_income=monthly_equivalent,
        family_allowance_monthly=family_monthly,
        service_years_now=service_years_now,
    )



def calculate_pension(
    birth_year: int,
    retirement_age: int,
    entry_year: int,
    salary_growth: float,
    inflation: float,
    current_income: float,
    military_months: int,
    unpaid_leave_months: int,
    childcare_leave_months: int,
    period2_factor: float,
    period3_factor: float,
) -> PensionResult:
    current_age = CURRENT_YEAR - birth_year
    retirement_year = birth_year + retirement_age
    years_to_retire = max(0, retirement_year - CURRENT_YEAR)

    # 현재 대화 기준: 휴직/병역 관련 기간은 기여금 납부 완료 가정
    pension_timeline_start = entry_year - (military_months / 12)
    pension_timeline_end = retirement_year

    y1 = overlap_years(pension_timeline_start, pension_timeline_end, 0, 2010)
    y2 = overlap_years(pension_timeline_start, pension_timeline_end, 2010, 2016)
    y3 = overlap_years(pension_timeline_start, pension_timeline_end, 2016, retirement_year + 1)

    projected_retirement_income = current_income * ((1 + salary_growth / 100) ** years_to_retire)
    projected_1_year_before = current_income * ((1 + salary_growth / 100) ** max(0, years_to_retire - 1))
    projected_2_years_before = current_income * ((1 + salary_growth / 100) ** max(0, years_to_retire - 2))
    average_final_3_years = (projected_retirement_income + projected_1_year_before + projected_2_years_before) / 3

    if y1 >= 20:
        period1_monthly = average_final_3_years * 0.5 + average_final_3_years * (y1 - 20) * 0.02
    else:
        period1_monthly = average_final_3_years * y1 * 0.025

    period2_monthly = average_final_3_years * y2 * 0.019 * period2_factor
    avg_rate_2016plus = weighted_average_rate(max(2016, pension_timeline_start), pension_timeline_end)
    period3_monthly = average_final_3_years * y3 * (avg_rate_2016plus / 100) * period3_factor

    estimated_monthly_pension = period1_monthly + period2_monthly + period3_monthly

    pension_start_age = get_pension_start_age(entry_year, retirement_year)
    pension_start_year = birth_year + pension_start_age
    gap_years = max(0, pension_start_age - retirement_age)

    present_value_monthly_pension = estimated_monthly_pension / ((1 + inflation / 100) ** max(0, pension_start_year - CURRENT_YEAR))
    current_monthly_contribution = current_income * 0.09
    future_contribution_sum = geometric_contribution_estimate(current_income, salary_growth, years_to_retire)

    recognized_service_years = max(0.0, pension_timeline_end - pension_timeline_start)

    return PensionResult(
        current_age=current_age,
        retirement_year=retirement_year,
        years_to_retire=years_to_retire,
        pension_start_age=pension_start_age,
        pension_start_year=pension_start_year,
        gap_years=gap_years,
        recognized_service_years=recognized_service_years,
        y1=y1,
        y2=y2,
        y3=y3,
        average_final_3_years=average_final_3_years,
        projected_retirement_income=projected_retirement_income,
        period1_monthly=period1_monthly,
        period2_monthly=period2_monthly,
        period3_monthly=period3_monthly,
        estimated_monthly_pension=estimated_monthly_pension,
        present_value_monthly_pension=present_value_monthly_pension,
        current_monthly_contribution=current_monthly_contribution,
        future_contribution_sum=future_contribution_sum,
        avg_rate_2016plus=avg_rate_2016plus,
    )


# =====================================
# 사이드바 입력
# =====================================
st.title("🏫 교사용 공무원연금 계산기")
st.caption("GitHub에 올려 Streamlit Community Cloud로 배포하기 좋은 1파일 구조입니다.")

with st.sidebar:
    st.header("입력값")

    input_mode = st.radio("기준소득월액 입력 방식", ["자동계산", "직접입력"], index=0)

    entry_year = st.number_input("임용연도", min_value=1980, max_value=2060, value=2020, step=1)
    birth_year = st.number_input("출생연도", min_value=1950, max_value=2010, value=1993, step=1)
    retirement_age = st.slider("정년(퇴직연령)", min_value=55, max_value=65, value=62, step=1)

    st.divider()
    st.subheader("호봉/보수")

    teacher_step = st.number_input("현재 호봉", min_value=1, max_value=40, value=19, step=1)
    geunsok_count = st.number_input("40호봉 초과 근속가봉 횟수", min_value=0, max_value=10, value=0, step=1)
    onejeong_step = st.checkbox("1정연수 +1호봉 가정", value=False)
    graduate_step = st.checkbox("대학원 +1호봉 가정", value=False)

    manual_income = 4200000
    if input_mode == "직접입력":
        manual_income = st.number_input("현재 기준소득월액(직접입력)", min_value=0, value=4200000, step=10000)

    include_teaching_allowance = st.checkbox("교직수당 반영", value=True)
    include_meal_allowance = st.checkbox("정액급식비 반영", value=True)
    is_homeroom = st.checkbox("담임수당 반영(25만원)", value=False)
    is_position_teacher = st.checkbox("보직교사수당 반영(15만원)", value=False)

    include_research_fee = st.checkbox("교원연구비 반영", value=False)
    research_fee = st.number_input("교원연구비 금액", min_value=0, value=DEFAULTS["research_fee"], step=1000)

    st.divider()
    st.subheader("가족수당")
    has_spouse = st.checkbox("배우자 있음", value=False)
    child_count = st.number_input("자녀 수", min_value=0, max_value=10, value=0, step=1)
    dependent_count = st.number_input("기타 부양가족 수", min_value=0, max_value=10, value=0, step=1)

    st.divider()
    st.subheader("연간 수당의 월환산 반영")
    include_holiday_bonus = st.checkbox("명절휴가비 반영", value=True)
    include_regular_bonus = st.checkbox("정근수당 반영", value=True)
    include_performance_bonus = st.checkbox("성과급 반영", value=True)
    performance_grade = st.selectbox("성과급 등급", ["S", "A", "B"], index=1)

    st.divider()
    st.subheader("휴직/산입 기간")
    unpaid_leave_months = st.number_input("일반휴직 개월수", min_value=0, max_value=120, value=0, step=1)
    childcare_leave_months = st.number_input("육아휴직 개월수", min_value=0, max_value=120, value=0, step=1)
    military_months = st.number_input("병역 산입 개월수", min_value=0, max_value=60, value=0, step=1)

    st.caption("현재 버전은 일반휴직·육아휴직·병역 관련 기간 모두 기여금을 납부한 것으로 가정합니다.")

    st.divider()
    st.subheader("기본 가정")
    salary_growth = st.number_input("연 보수상승률(%)", value=DEFAULTS["salary_growth"], step=0.01, format="%.2f")
    inflation = st.number_input("연 물가상승률(%)", value=DEFAULTS["inflation"], step=0.01, format="%.2f")
    period2_factor = st.number_input("2기간 보정계수", value=1.00, step=0.01, format="%.2f")
    period3_factor = st.number_input("3기간 보정계수", value=1.00, step=0.01, format="%.2f")


salary = build_salary_breakdown(
    input_mode=input_mode,
    teacher_step=int(teacher_step),
    onejeong_step=onejeong_step,
    graduate_step=graduate_step,
    geunsok_count=int(geunsok_count),
    include_teaching_allowance=include_teaching_allowance,
    include_meal_allowance=include_meal_allowance,
    is_homeroom=is_homeroom,
    is_position_teacher=is_position_teacher,
    include_research_fee=include_research_fee,
    research_fee=int(research_fee),
    has_spouse=has_spouse,
    child_count=int(child_count),
    dependent_count=int(dependent_count),
    include_holiday_bonus=include_holiday_bonus,
    include_regular_bonus=include_regular_bonus,
    include_performance_bonus=include_performance_bonus,
    performance_grade=performance_grade,
    manual_income=int(manual_income),
    entry_year=int(entry_year),
    military_months=int(military_months),
)

pension = calculate_pension(
    birth_year=int(birth_year),
    retirement_age=int(retirement_age),
    entry_year=int(entry_year),
    salary_growth=float(salary_growth),
    inflation=float(inflation),
    current_income=salary.monthly_equivalent_income,
    military_months=int(military_months),
    unpaid_leave_months=int(unpaid_leave_months),
    childcare_leave_months=int(childcare_leave_months),
    period2_factor=float(period2_factor),
    period3_factor=float(period3_factor),
)


# =====================================
# 본문 출력
# =====================================
st.info(
    "이 계산기는 공식 산정액이 아닌 추정용 베타입니다. "
    "다만 2026 교원 봉급표, 2026 교육공무원 성과상여금 공지액, "
    "기여금 9%, 1·2·3기간 분할, 1996년 이후 지급개시연령 단계 상향 구조를 반영해 "
    "실제 의사결정에 도움이 되도록 설계했습니다."
)

m1, m2, m3, m4 = st.columns(4)
m1.metric("현재 추정 기준소득월액", won(salary.monthly_equivalent_income))
m2.metric("예상 월연금", won(pension.estimated_monthly_pension))
m3.metric("현재가치 기준 월연금", won(pension.present_value_monthly_pension))
m4.metric("현재 월 기여금(9%)", won(pension.current_monthly_contribution))

m5, m6, m7, m8 = st.columns(4)
m5.metric("현재 나이", f"{pension.current_age}세")
m6.metric("예상 퇴직연도", f"{pension.retirement_year}년")
m7.metric("연금 개시연령", f"{pension.pension_start_age}세")
m8.metric("정년~연금개시 공백", f"{pension.gap_years}년")

left, right = st.columns([1.1, 0.9])

with left:
    st.subheader("1) 현재 보수 추정 상세")
    detail_df = pd.DataFrame(
        {
            "항목": [
                "조정 후 호봉",
                "호봉 기준 본봉",
                "가족수당(월)",
                "매월 수당 합계",
                "명절휴가비(연)",
                "정근수당(연)",
                f"성과급({performance_grade}등급, 연)",
                "월환산 기준소득 추정치",
            ],
            "값": [
                f"{salary.adjusted_step}호봉",
                won(salary.base_pay),
                won(salary.family_allowance_monthly),
                won(salary.recurring_allowances),
                won(salary.holiday_bonus_annual),
                won(salary.regular_bonus_annual),
                won(salary.performance_bonus_annual),
                won(salary.monthly_equivalent_income),
            ],
        }
    )
    st.dataframe(detail_df, use_container_width=True, hide_index=True)

    st.subheader("2) 연금 계산 내역")
    period_df = pd.DataFrame(
        {
            "구간": ["1기간(2009 이전)", "2기간(2010~2015)", "3기간(2016 이후)"],
            "인정연수": [round(pension.y1, 2), round(pension.y2, 2), round(pension.y3, 2)],
            "월연금 기여분": [
                won(pension.period1_monthly),
                won(pension.period2_monthly),
                won(pension.period3_monthly),
            ],
        }
    )
    st.dataframe(period_df, use_container_width=True, hide_index=True)

with right:
    st.subheader("3) 핵심 추정치")
    summary_df = pd.DataFrame(
        {
            "항목": [
                "인정 재직연수(추정)",
                "퇴직 시점 추정 기준소득월액",
                "마지막 3년 평균 추정치",
                "2016년 이후 평균 지급률",
                "퇴직 전까지 추가 기여금 추정",
            ],
            "값": [
                f"{pension.recognized_service_years:.2f}년",
                won(pension.projected_retirement_income),
                won(pension.average_final_3_years),
                pct(pension.avg_rate_2016plus),
                won(pension.future_contribution_sum),
            ],
        }
    )
    st.dataframe(summary_df, use_container_width=True, hide_index=True)

    st.subheader("4) 현재 반영한 기준")
    st.markdown(
        """
- **성과급**: 2026 교육공무원 12개월 근무 기준 지급액 사용
- **담임수당**: 25만 원 가정
- **보직교사수당**: 15만 원 가정
- **명절휴가비**: 월봉급액의 60% × 연 2회
- **정근수당**: 근무연수별 비율 × 연 2회
- **휴직/병역**: 기여금 납부 완료 가정
        """
    )

st.subheader("5) 배포용 안내")
st.code(
    """# requirements.txt
streamlit>=1.44
pandas>=2.2
""",
    language="text",
)

st.markdown(
    """
### GitHub + Streamlit Community Cloud 배포 순서
1. 이 파일명을 **app.py** 로 저장합니다.
2. 같은 저장소에 **requirements.txt** 를 함께 올립니다.
3. GitHub 저장소를 Streamlit Community Cloud에 연결합니다.
4. Main file path를 **app.py** 로 지정해 배포합니다.

### 주의
- 이 계산기는 **공식 산정액이 아닌 추정용 베타**입니다.
- 실제 지급액은 공무원연금공단 산정, 기준소득월액 이력, 소득재분배 평균기준소득월액,
  군복무 산입 승인 여부, 휴직 중 실제 기여금 납부 이력 등에 따라 달라질 수 있습니다.
    """
)
