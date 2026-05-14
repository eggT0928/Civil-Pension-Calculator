# app.py
# 공무원연금 예상 계산기 Streamlit 앱
# 실행: streamlit run app.py

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional, Dict, Tuple

import calendar
import math
import streamlit as st


# =========================================================
# 0. 기본 설정
# =========================================================

st.set_page_config(
    page_title="공무원연금 예상 계산기",
    page_icon="📊",
    layout="wide",
)


# =========================================================
# 1. 지급률 / 보정값 설정
# =========================================================

# 2016년 이후 연도별 연금지급률
# 실제 공단 계산과 완전히 동일한 공식 복제 목적이 아니라,
# 교육용 추정 계산을 위한 단순화 테이블입니다.
ACCRUAL_RATE_BY_YEAR: Dict[int, float] = {
    2016: 0.01878,
    2017: 0.01856,
    2018: 0.01834,
    2019: 0.01812,
    2020: 0.01790,
    2021: 0.01780,
    2022: 0.01770,
    2023: 0.01760,
    2024: 0.01750,
    2025: 0.01740,
    2026: 0.01730,
    2027: 0.01720,
    2028: 0.01710,
    2029: 0.01700,
    2030: 0.01690,
    2031: 0.01680,
    2032: 0.01670,
    2033: 0.01660,
    2034: 0.01650,
    2035: 0.01640,
}

FINAL_ACCRUAL_RATE = 0.01640
CONTRIBUTION_RATE = 0.09

# 현재 일반기여금으로 공단 적용보수표 값을 근사할 때 쓰는 경험적 보정값
EST_B_VALUE_RATIO = 0.925
EST_POST2010_LUMP_ALLOWANCE_RATIO = 1.184

DEFAULT_A_VALUE = 5_440_000
DEFAULT_CURRENT_CONTRIBUTION = 395_000

JOB_TEACHER = "교원"
JOB_GENERAL = "일반직 공무원"


# =========================================================
# 2. 데이터 구조
# =========================================================

@dataclass
class UserInputs:
    job_type: str
    birth_date: date
    appointment_date: date
    base_date: date
    retirement_date: date
    current_contribution: int
    salary_growth_rate: float
    inflation_rate: float
    current_a_value: int
    use_exact_data: bool
    exact_b_value: Optional[int]
    exact_redist_value: Optional[int]
    exact_post2010_lump_allowance_value: Optional[int]
    exact_p1_lump_value: Optional[int]
    exact_p1_allowance_value: Optional[int]
    exact_p1_pension_value: Optional[int]


@dataclass
class EstimatedValues:
    current_standard_income: float
    inferred_b_value: float
    inferred_redist_value: float
    inferred_post2010_lump_allowance: float
    actual_b_value: float
    actual_p3_value: float
    post2010_lump_allowance_value: float
    p1_lump_value: float
    p1_allowance_value: float
    p1_pension_value: float


@dataclass
class PensionResult:
    current_age: float
    service_years_to_base: float
    remaining_service_years: float
    total_service_years: float
    years_until_retirement: float
    nominal_monthly_pension: float
    real_monthly_pension: float
    nominal_lump_sum: float
    real_lump_sum: float
    nominal_retirement_allowance: float
    real_retirement_allowance: float
    total_nominal_value: float
    total_real_value: float
    avg_accrual_rate: float


# =========================================================
# 3. 날짜 / 표시 유틸
# =========================================================

def add_years(d: date, years: int) -> date:
    """윤년 2월 29일도 안전하게 처리하면서 연도를 더합니다."""
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return d.replace(month=2, day=28, year=d.year + years)


def last_day_of_month(year: int, month: int) -> date:
    """특정 연월의 마지막 날짜를 반환합니다."""
    return date(year, month, calendar.monthrange(year, month)[1])


def years_between(start: date, end: date) -> float:
    """두 날짜 사이의 기간을 연 단위로 계산합니다."""
    days = (end - start).days
    return max(days / 365.2425, 0.0)


def get_recommended_retirement_date(job_type: str, birth_date: date) -> date:
    """
    구분별 정년퇴직일을 대략 자동 제안합니다.
    실제 개인별 퇴직예정일은 학교/기관 및 인사 기준에 따라 확인이 필요하므로,
    화면에서는 사용자가 직접 수정할 수 있게 둡니다.
    """
    if job_type == JOB_TEACHER:
        reach_date = add_years(birth_date, 62)
        if reach_date.month <= 2:
            return last_day_of_month(reach_date.year, 2)
        if reach_date.month <= 8:
            return date(reach_date.year, 8, 31)
        return last_day_of_month(reach_date.year + 1, 2)

    reach_date = add_years(birth_date, 60)
    if reach_date.month <= 6:
        return date(reach_date.year, 6, 30)
    return date(reach_date.year, 12, 31)


def won(value: float) -> str:
    if value is None:
        return "-"
    try:
        if math.isnan(float(value)):
            return "-"
    except Exception:
        return "-"
    return f"{value:,.0f}원"


def manwon(value: float) -> str:
    if value is None:
        return "-"
    try:
        if math.isnan(float(value)):
            return "-"
    except Exception:
        return "-"
    return f"{value / 10_000:,.1f}만 원"


def eokwon(value: float) -> str:
    if value is None:
        return "-"
    try:
        if math.isnan(float(value)):
            return "-"
    except Exception:
        return "-"
    return f"{value / 100_000_000:,.2f}억 원"


def percent(value: float) -> str:
    return f"{value * 100:,.3f}%"


def safe_positive(value: Optional[int | float]) -> float:
    if value is None:
        return 0.0
    try:
        v = float(value)
        return v if v > 0 else 0.0
    except Exception:
        return 0.0


def get_accrual_rate(year: int) -> float:
    if year in ACCRUAL_RATE_BY_YEAR:
        return ACCRUAL_RATE_BY_YEAR[year]
    if year < 2016:
        return ACCRUAL_RATE_BY_YEAR[2016]
    return FINAL_ACCRUAL_RATE


# =========================================================
# 4. 적용보수 추정 로직
# =========================================================

def estimate_exact_values_from_contribution(
    current_contribution: int,
    current_a_value: float,
) -> Tuple[float, float, float, float]:
    """
    현재 일반기여금으로 적용보수 관련 값을 추정합니다.

    핵심 보정식:
    - 현재 기준소득월액 = 현재 일반기여금 / 9%
    - 개인 평균 기준소득월액 B값 = 현재 기준소득월액 × 0.925
    - 2016년 이후 소득재분배 반영 기준소득월액 = A값과 B값의 평균
    - 2010년 이후 일시금/퇴직수당 칸 금액 = 현재 기준소득월액 × 1.184
    """
    current_standard_income = current_contribution / CONTRIBUTION_RATE if current_contribution > 0 else 0.0
    inferred_b_value = current_standard_income * EST_B_VALUE_RATIO
    inferred_redist_value = (float(current_a_value) + inferred_b_value) / 2 if current_a_value > 0 else inferred_b_value
    inferred_post2010_lump_allowance = current_standard_income * EST_POST2010_LUMP_ALLOWANCE_RATIO

    return (
        current_standard_income,
        inferred_b_value,
        inferred_redist_value,
        inferred_post2010_lump_allowance,
    )


def build_estimated_values(inputs: UserInputs) -> EstimatedValues:
    """직접 입력값이 있으면 직접 입력값을 우선 사용하고, 없으면 현재 기여금 기반 추정값을 사용합니다."""
    (
        current_standard_income,
        inferred_b_value,
        inferred_redist_value,
        inferred_post2010_lump_allowance,
    ) = estimate_exact_values_from_contribution(
        current_contribution=inputs.current_contribution,
        current_a_value=inputs.current_a_value,
    )

    exact_b = safe_positive(inputs.exact_b_value)
    exact_redist = safe_positive(inputs.exact_redist_value)
    exact_post2010 = safe_positive(inputs.exact_post2010_lump_allowance_value)
    exact_p1_lump = safe_positive(inputs.exact_p1_lump_value)
    exact_p1_allowance = safe_positive(inputs.exact_p1_allowance_value)
    exact_p1_pension = safe_positive(inputs.exact_p1_pension_value)

    actual_b_value = exact_b if inputs.use_exact_data and exact_b > 0 else inferred_b_value
    actual_p3_value = exact_redist if inputs.use_exact_data and exact_redist > 0 else inferred_redist_value
    post2010_lump_allowance_value = (
        exact_post2010 if inputs.use_exact_data and exact_post2010 > 0 else inferred_post2010_lump_allowance
    )

    return EstimatedValues(
        current_standard_income=current_standard_income,
        inferred_b_value=inferred_b_value,
        inferred_redist_value=inferred_redist_value,
        inferred_post2010_lump_allowance=inferred_post2010_lump_allowance,
        actual_b_value=actual_b_value,
        actual_p3_value=actual_p3_value,
        post2010_lump_allowance_value=post2010_lump_allowance_value,
        p1_lump_value=exact_p1_lump,
        p1_allowance_value=exact_p1_allowance,
        p1_pension_value=exact_p1_pension,
    )


# =========================================================
# 5. 연금 계산 로직
# =========================================================

def calculate_average_accrual_rate(appointment_date: date, retirement_date: date) -> float:
    """임용일부터 퇴직일까지의 연도별 지급률을 기간 가중 평균으로 단순 계산합니다."""
    if retirement_date <= appointment_date:
        return FINAL_ACCRUAL_RATE

    total_days = (retirement_date - appointment_date).days
    weighted_sum = 0.0

    current = appointment_date
    while current < retirement_date:
        year_end = date(current.year, 12, 31) + timedelta(days=1)
        segment_end = min(year_end, retirement_date)
        days = (segment_end - current).days
        weighted_sum += get_accrual_rate(current.year) * days
        current = segment_end

    return weighted_sum / total_days if total_days > 0 else FINAL_ACCRUAL_RATE


def calculate_pension(inputs: UserInputs, values: EstimatedValues) -> PensionResult:
    current_age = years_between(inputs.birth_date, inputs.base_date)
    service_years_to_base = years_between(inputs.appointment_date, inputs.base_date)
    remaining_service_years = years_between(inputs.base_date, inputs.retirement_date)
    total_service_years = years_between(inputs.appointment_date, inputs.retirement_date)
    years_until_retirement = remaining_service_years

    salary_growth = inputs.salary_growth_rate / 100
    inflation = inputs.inflation_rate / 100

    growth_factor = (1 + salary_growth) ** years_until_retirement
    inflation_factor = (1 + inflation) ** years_until_retirement

    future_p3_value = values.actual_p3_value * growth_factor
    future_post2010_lump_allowance_value = values.post2010_lump_allowance_value * growth_factor

    avg_accrual_rate = calculate_average_accrual_rate(inputs.appointment_date, inputs.retirement_date)

    # 월 연금 추정
    nominal_monthly_pension = future_p3_value * avg_accrual_rate * total_service_years

    # 2009년 이전 연금 칸 금액이 있으면 보조적으로 반영합니다.
    if values.p1_pension_value > 0:
        nominal_monthly_pension += values.p1_pension_value * growth_factor * 0.02

    real_monthly_pension = nominal_monthly_pension / inflation_factor if inflation_factor > 0 else nominal_monthly_pension

    # 일시금 추정
    nominal_lump_sum = future_post2010_lump_allowance_value * total_service_years * 0.85
    if values.p1_lump_value > 0:
        nominal_lump_sum += values.p1_lump_value * growth_factor
    real_lump_sum = nominal_lump_sum / inflation_factor if inflation_factor > 0 else nominal_lump_sum

    # 퇴직수당 추정
    allowance_coefficient = 0.10
    if total_service_years >= 20:
        allowance_coefficient = 0.13
    if total_service_years >= 30:
        allowance_coefficient = 0.16

    nominal_retirement_allowance = future_post2010_lump_allowance_value * total_service_years * allowance_coefficient
    if values.p1_allowance_value > 0:
        nominal_retirement_allowance += values.p1_allowance_value * growth_factor
    real_retirement_allowance = nominal_retirement_allowance / inflation_factor if inflation_factor > 0 else nominal_retirement_allowance

    pension_25y_nominal_value = nominal_monthly_pension * 12 * 25
    pension_25y_real_value = real_monthly_pension * 12 * 25

    total_nominal_value = pension_25y_nominal_value + nominal_lump_sum + nominal_retirement_allowance
    total_real_value = pension_25y_real_value + real_lump_sum + real_retirement_allowance

    return PensionResult(
        current_age=current_age,
        service_years_to_base=service_years_to_base,
        remaining_service_years=remaining_service_years,
        total_service_years=total_service_years,
        years_until_retirement=years_until_retirement,
        nominal_monthly_pension=nominal_monthly_pension,
        real_monthly_pension=real_monthly_pension,
        nominal_lump_sum=nominal_lump_sum,
        real_lump_sum=real_lump_sum,
        nominal_retirement_allowance=nominal_retirement_allowance,
        real_retirement_allowance=real_retirement_allowance,
        total_nominal_value=total_nominal_value,
        total_real_value=total_real_value,
        avg_accrual_rate=avg_accrual_rate,
    )


# =========================================================
# 6. 화면 구성
# =========================================================

def render_title() -> None:
    st.title("📊 공무원연금 예상 계산기")
    st.caption(
        "퇴직예정일, 현재 일반기여금, 공단 예상퇴직급여 조회서의 적용보수 값을 바탕으로 "
        "연금 규모를 대략 가늠해보는 교육용 계산기입니다."
    )

    with st.expander("⚠️ 사용 전 꼭 읽어주세요", expanded=False):
        st.markdown(
            """
            - 이 앱은 **공무원연금공단 공식 계산기가 아닙니다.**
            - 실제 연금액은 개인별 재직이력, 휴직, 군경력, 소득재분배, 기준소득월액 상한, 지급개시연령 등에 따라 달라질 수 있습니다.
            - 결과는 **노후 준비 규모를 감 잡기 위한 참고값**으로만 사용해 주세요.
            - 정확한 값은 공무원연금공단의 예상퇴직급여 조회 결과를 우선해야 합니다.
            """
        )


def render_sidebar() -> UserInputs:
    st.sidebar.header("1. 기본 정보")

    job_type = st.sidebar.selectbox(
        "구분",
        [JOB_TEACHER, JOB_GENERAL],
        index=0,
        help="교원/일반직 공무원 구분에 따라 퇴직예정일 자동 제안값이 달라집니다. 실제 퇴직일은 직접 수정할 수 있습니다.",
    )

    birth_date = st.sidebar.date_input(
        "생년월일",
        value=date(1993, 3, 23),
        min_value=date(1950, 1, 1),
        max_value=date(2100, 12, 31),
    )

    appointment_date = st.sidebar.date_input(
        "임용일",
        value=date(2016, 3, 1),
        min_value=date(1980, 1, 1),
        max_value=date(2100, 12, 31),
    )

    base_date = st.sidebar.date_input(
        "현재 기준일",
        value=date.today(),
        min_value=date(1980, 1, 1),
        max_value=date(2100, 12, 31),
        help="현재까지 재직기간과 퇴직까지 남은 기간을 계산하는 기준일입니다.",
    )

    suggested_retirement_date = get_recommended_retirement_date(job_type, birth_date)

    st.sidebar.caption(f"선택한 구분 기준 자동 제안 퇴직일: {suggested_retirement_date.strftime('%Y-%m-%d')}")

    retirement_date = st.sidebar.date_input(
        "퇴직예정일",
        value=suggested_retirement_date,
        min_value=date(1980, 1, 1),
        max_value=date(2100, 12, 31),
        help="원래 버전처럼 퇴직예정일을 직접 넣는 방식입니다. 자동 제안값이 맞지 않으면 직접 수정하세요.",
    )

    current_contribution = st.sidebar.number_input(
        "현재 일반기여금 월 납부액",
        min_value=0,
        value=DEFAULT_CURRENT_CONTRIBUTION,
        step=10_000,
        help="급여명세서의 공무원연금 일반기여금 월 납부액을 입력합니다.",
    )

    st.sidebar.header("2. 공단 조회서 적용보수 직접 입력")

    use_exact_data = st.sidebar.toggle(
        "공단 예상퇴직급여 조회서 값 직접 입력",
        value=False,
        help="켜면 직접 입력한 값이 현재 일반기여금 기반 추정값보다 우선 적용됩니다.",
    )

    exact_b_value: Optional[int] = None
    exact_redist_value: Optional[int] = None
    exact_post2010_lump_allowance_value: Optional[int] = None
    exact_p1_lump_value: Optional[int] = None
    exact_p1_allowance_value: Optional[int] = None
    exact_p1_pension_value: Optional[int] = None

    if use_exact_data:
        st.sidebar.caption("0원으로 두면 해당 항목은 비워둔 것으로 보고 추정값을 사용합니다.")

        exact_b_value = st.sidebar.number_input(
            "개인 평균 기준소득월액 (B값)",
            min_value=0,
            value=0,
            step=10_000,
            help="퇴직연금액 예상보고서의 개인 평균 기준소득월액입니다.",
        )

        exact_redist_value = st.sidebar.number_input(
            "2016년 이후 소득재분배 반영 기준소득월액",
            min_value=0,
            value=0,
            step=10_000,
            help="2016년 이후 기간의 연금 산정에 쓰이는 소득재분배 반영 기준소득월액입니다.",
        )

        exact_post2010_lump_allowance_value = st.sidebar.number_input(
            "2010.1.1 이후기간 <Ⅱ·Ⅲ기간> - 일시금/퇴직수당 칸 금액",
            min_value=0,
            value=0,
            step=10_000,
            help="적용보수 표에서 2010.1.1 이후기간 <Ⅱ·Ⅲ기간> 아래 일시금 또는 퇴직수당 칸 금액입니다.",
        )

        st.sidebar.markdown("---")
        st.sidebar.caption("2009.12.31 이전기간 <Ⅰ기간>은 보고서 순서대로 입력합니다.")

        exact_p1_lump_value = st.sidebar.number_input(
            "2009.12.31 이전기간 <Ⅰ기간> - 일시금 칸 금액",
            min_value=0,
            value=0,
            step=10_000,
        )

        exact_p1_allowance_value = st.sidebar.number_input(
            "2009.12.31 이전기간 <Ⅰ기간> - 퇴직수당 칸 금액",
            min_value=0,
            value=0,
            step=10_000,
        )

        exact_p1_pension_value = st.sidebar.number_input(
            "2009.12.31 이전기간 <Ⅰ기간> - 연금 칸 금액",
            min_value=0,
            value=0,
            step=10_000,
        )

    st.sidebar.header("3. 가정값")

    salary_growth_rate = st.sidebar.number_input(
        "연 보수상승률 (%)",
        min_value=0.0,
        max_value=10.0,
        value=2.5,
        step=0.1,
    )

    inflation_rate = st.sidebar.number_input(
        "연 물가상승률 (%)",
        min_value=0.0,
        max_value=10.0,
        value=2.5,
        step=0.1,
    )

    current_a_value = st.sidebar.number_input(
        "전체 공무원 평균 기준소득월액 (A값, 추정용)",
        min_value=0,
        value=DEFAULT_A_VALUE,
        step=10_000,
        help="정확히 모르면 기본값을 그대로 두어도 됩니다. 2016년 이후 소득재분배 반영값 추정에 사용됩니다.",
    )

    return UserInputs(
        job_type=job_type,
        birth_date=birth_date,
        appointment_date=appointment_date,
        base_date=base_date,
        retirement_date=retirement_date,
        current_contribution=int(current_contribution),
        salary_growth_rate=float(salary_growth_rate),
        inflation_rate=float(inflation_rate),
        current_a_value=int(current_a_value),
        use_exact_data=bool(use_exact_data),
        exact_b_value=exact_b_value,
        exact_redist_value=exact_redist_value,
        exact_post2010_lump_allowance_value=exact_post2010_lump_allowance_value,
        exact_p1_lump_value=exact_p1_lump_value,
        exact_p1_allowance_value=exact_p1_allowance_value,
        exact_p1_pension_value=exact_p1_pension_value,
    )


def render_input_overview(inputs: UserInputs, result: PensionResult) -> None:
    st.subheader("입력 정보 요약")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("구분", inputs.job_type)
    col2.metric("현재 나이", f"{result.current_age:,.1f}세")
    col3.metric("현재까지 재직기간", f"{result.service_years_to_base:,.1f}년")
    col4.metric("총 예상 재직기간", f"{result.total_service_years:,.1f}년")

    col5, col6, col7, col8 = st.columns(4)
    col5.metric("임용일", inputs.appointment_date.strftime("%Y-%m-%d"))
    col6.metric("퇴직예정일", inputs.retirement_date.strftime("%Y-%m-%d"))
    col7.metric("퇴직까지 남은 기간", f"{result.remaining_service_years:,.1f}년")
    col8.metric("평균 지급률", percent(result.avg_accrual_rate))


def render_estimation_panel(values: EstimatedValues, inputs: UserInputs) -> None:
    st.subheader("① 현재 일반기여금 기반 적용보수 추정")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("현재 기준소득월액 역산", manwon(values.current_standard_income))
    col2.metric("개인 평균 기준소득월액 B값 추정", manwon(values.inferred_b_value))
    col3.metric("2016년 이후 소득재분배 반영값 추정", manwon(values.inferred_redist_value))
    col4.metric("2010년 이후 일시금/퇴직수당 추정", manwon(values.inferred_post2010_lump_allowance))

    with st.expander("추정 방식 보기", expanded=False):
        st.markdown(
            f"""
            현재 일반기여금으로 역산한 기준소득월액은 다음 방식으로 계산합니다.

            ```text
            현재 기준소득월액 = 현재 일반기여금 ÷ 0.09
            ```

            이후 적용보수 추정은 다음 보정식을 사용합니다.

            ```text
            개인 평균 기준소득월액 B값 ≈ 현재 기준소득월액 × {EST_B_VALUE_RATIO}
            2016년 이후 소득재분배 반영 기준소득월액 ≈ (A값 + B값) ÷ 2
            2010년 이후 일시금/퇴직수당 칸 금액 ≈ 현재 기준소득월액 × {EST_POST2010_LUMP_ALLOWANCE_RATIO}
            ```

            공단 보고서의 실제 값이 있다면, 직접 입력한 값이 추정값보다 우선 적용됩니다.
            """
        )

    st.subheader("② 실제 계산에 적용된 값")
    col5, col6, col7 = st.columns(3)
    col5.metric("적용 B값", manwon(values.actual_b_value))
    col6.metric("적용 2016년 이후 소득재분배값", manwon(values.actual_p3_value))
    col7.metric("적용 2010년 이후 일시금/퇴직수당값", manwon(values.post2010_lump_allowance_value))

    if inputs.use_exact_data:
        st.info("직접 입력한 값이 있는 항목은 직접 입력값을 우선 사용했습니다. 0원으로 둔 항목은 현재 일반기여금 기반 추정값을 사용했습니다.")
    else:
        st.warning("공단 보고서 값을 직접 입력하지 않았기 때문에 현재 일반기여금 기반 추정값으로 계산합니다.")


def render_result_panel(result: PensionResult) -> None:
    st.subheader("예상 결과")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("월 연금 명목금액", manwon(result.nominal_monthly_pension))
    col2.metric("월 연금 현재가치", manwon(result.real_monthly_pension))
    col3.metric("일시금 명목 추정", eokwon(result.nominal_lump_sum))
    col4.metric("퇴직수당 명목 추정", eokwon(result.nominal_retirement_allowance))

    st.markdown("---")

    col5, col6, col7 = st.columns(3)
    col5.metric("일시금 현재가치", eokwon(result.real_lump_sum))
    col6.metric("퇴직수당 현재가치", eokwon(result.real_retirement_allowance))
    col7.metric("총 가치 현재가치 추정", eokwon(result.total_real_value))

    st.markdown("---")

    detail_rows = [
        {"구분": "월 연금", "퇴직시점 명목금액": won(result.nominal_monthly_pension), "현재가치 환산": won(result.real_monthly_pension)},
        {"구분": "일시금", "퇴직시점 명목금액": won(result.nominal_lump_sum), "현재가치 환산": won(result.real_lump_sum)},
        {"구분": "퇴직수당", "퇴직시점 명목금액": won(result.nominal_retirement_allowance), "현재가치 환산": won(result.real_retirement_allowance)},
        {"구분": "연금 25년 수령 가정 + 일시금 + 퇴직수당", "퇴직시점 명목금액": won(result.total_nominal_value), "현재가치 환산": won(result.total_real_value)},
    ]
    st.dataframe(detail_rows, hide_index=True, use_container_width=True)


def render_interpretation(result: PensionResult) -> None:
    st.subheader("결과 해석")

    st.markdown(
        f"""
        이 계산에서는 퇴직 후 월 연금을 현재가치 기준으로 약 **{manwon(result.real_monthly_pension)}** 정도로 추정했습니다.

        여기서 중요한 포인트는 **명목금액과 현재가치의 차이**입니다.  
        퇴직 시점의 숫자는 커 보일 수 있지만, 물가상승률을 반영하면 실제 구매력은 다르게 느껴질 수 있습니다.

        공무원연금은 노후의 중요한 1층 안전망입니다. 다만 이것만으로 모든 노후 생활비를 해결한다고 보기보다는
        연금저축, IRP, ISA, 일반 투자계좌 등과 함께 보는 편이 더 현실적입니다.
        """
    )

    if result.real_monthly_pension < 2_000_000:
        st.warning("현재가치 기준 월 연금 추정치가 200만 원 미만입니다. 개인연금이나 투자자산 보완 필요성이 클 수 있습니다.")
    elif result.real_monthly_pension < 3_500_000:
        st.info("현재가치 기준으로 기본 생활비의 한 축은 될 수 있지만, 여유 있는 노후를 위해서는 추가 현금흐름이 필요할 수 있습니다.")
    else:
        st.success("현재가치 기준 월 연금 추정치가 비교적 높은 편입니다. 다만 실제 생활비와 가구 기준 지출을 함께 확인하는 것이 좋습니다.")


def render_debug_table(values: EstimatedValues, result: PensionResult) -> None:
    with st.expander("계산값 점검용 표", expanded=False):
        debug_rows = [
            {"항목": "현재 기준소득월액 역산", "값": won(values.current_standard_income)},
            {"항목": "B값 추정", "값": won(values.inferred_b_value)},
            {"항목": "2016년 이후 소득재분배값 추정", "값": won(values.inferred_redist_value)},
            {"항목": "2010년 이후 일시금/퇴직수당값 추정", "값": won(values.inferred_post2010_lump_allowance)},
            {"항목": "실제 적용 B값", "값": won(values.actual_b_value)},
            {"항목": "실제 적용 2016년 이후 소득재분배값", "값": won(values.actual_p3_value)},
            {"항목": "실제 적용 2010년 이후 일시금/퇴직수당값", "값": won(values.post2010_lump_allowance_value)},
            {"항목": "현재 나이", "값": f"{result.current_age:,.2f}세"},
            {"항목": "현재까지 재직기간", "값": f"{result.service_years_to_base:,.2f}년"},
            {"항목": "퇴직까지 남은 기간", "값": f"{result.remaining_service_years:,.2f}년"},
            {"항목": "총 예상 재직기간", "값": f"{result.total_service_years:,.2f}년"},
            {"항목": "평균 지급률", "값": percent(result.avg_accrual_rate)},
        ]
        st.dataframe(debug_rows, hide_index=True, use_container_width=True)


def render_notice() -> None:
    st.markdown(
        """
        ## 계산기 사용 시 주의사항

        이 앱은 **정확한 공단 산식 복제용**이 아니라, 선생님들이 노후 준비 규모를 감 잡기 위한 **교육용 추정 계산기**입니다.

        특히 아래 항목은 실제 공단 계산과 차이가 날 수 있습니다.

        - 기간별 기준소득월액 산정 방식
        - 2010년 이전, 2010년 이후, 2016년 이후 기간별 산식
        - 소득재분배 반영 방식
        - 기준소득월액 상한 및 전체 공무원 평균값
        - 퇴직수당 세부 산식
        - 연금 지급개시연령 및 실제 수령 시점
        - 개인별 휴직, 군경력, 추가 산입 기간 등

        따라서 실제 의사결정은 반드시 공무원연금공단의 예상퇴직급여 조회 결과를 기준으로 확인해야 합니다.
        """
    )


# =========================================================
# 7. 메인 실행
# =========================================================

def main() -> None:
    render_title()
    inputs = render_sidebar()

    if inputs.retirement_date <= inputs.appointment_date:
        st.error("퇴직예정일은 임용일보다 뒤여야 합니다. 날짜를 다시 확인해 주세요.")
        return

    if inputs.base_date < inputs.appointment_date:
        st.error("현재 기준일은 임용일 이후여야 합니다. 날짜를 다시 확인해 주세요.")
        return

    values = build_estimated_values(inputs)
    result = calculate_pension(inputs, values)

    render_input_overview(inputs, result)
    st.markdown("---")

    tab1, tab2, tab3 = st.tabs(["계산 결과", "적용보수 확인", "주의사항"])

    with tab1:
        render_result_panel(result)
        render_interpretation(result)

    with tab2:
        render_estimation_panel(values, inputs)
        render_debug_table(values, result)

    with tab3:
        render_notice()


if __name__ == "__main__":
    main()
