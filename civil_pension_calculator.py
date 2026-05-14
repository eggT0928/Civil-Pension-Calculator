# app.py
# 공무원연금 예상 계산기 Streamlit 앱
# 실행: streamlit run app.py

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict, List, Tuple

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
# 1. 지급률 / 연령 / 보정값 설정
# =========================================================

# 2016년 이후 연도별 연금지급률
# 공무원연금 개혁 이후 단계적으로 낮아지는 구조를 반영한 단순 추정 테이블입니다.
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

# 적용보수 추정 보정값
# 현재 기여금으로 역산한 기준소득월액과 공단 보고서상 평균값의 차이를 줄이기 위한 경험적 보정값입니다.
EST_B_VALUE_RATIO = 0.925
EST_POST2010_LUMP_ALLOWANCE_RATIO = 1.184


# =========================================================
# 2. 데이터 구조
# =========================================================

@dataclass
class UserInputs:
    current_age: int
    appointment_year: int
    appointment_month: int
    retirement_age: int
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
    total_service_years: float
    years_until_retirement: int
    nominal_monthly_pension: float
    real_monthly_pension: float
    nominal_lump_sum: float
    real_lump_sum: float
    nominal_retirement_allowance: float
    real_retirement_allowance: float
    total_nominal_value: float
    total_real_value: float
    replacement_monthly_income: float


# =========================================================
# 3. 유틸 함수
# =========================================================

def won(value: float) -> str:
    """원 단위 금액을 보기 좋게 표시합니다."""
    if value is None or math.isnan(float(value)):
        return "-"
    return f"{value:,.0f}원"


def manwon(value: float) -> str:
    """원 단위 금액을 만 원 단위로 표시합니다."""
    if value is None or math.isnan(float(value)):
        return "-"
    return f"{value / 10_000:,.1f}만 원"


def eokwon(value: float) -> str:
    """원 단위 금액을 억 원 단위로 표시합니다."""
    if value is None or math.isnan(float(value)):
        return "-"
    return f"{value / 100_000_000:,.2f}억 원"


def safe_positive(value: Optional[int | float]) -> float:
    """None 또는 0 이하 값을 0으로 처리합니다."""
    if value is None:
        return 0.0
    try:
        v = float(value)
        return v if v > 0 else 0.0
    except Exception:
        return 0.0


def get_accrual_rate(year: int) -> float:
    """해당 연도의 연금지급률을 반환합니다."""
    if year in ACCRUAL_RATE_BY_YEAR:
        return ACCRUAL_RATE_BY_YEAR[year]
    if year < 2016:
        return ACCRUAL_RATE_BY_YEAR[2016]
    return FINAL_ACCRUAL_RATE


def estimate_service_years(appointment_year: int, appointment_month: int, retirement_age: int, current_age: int) -> Tuple[float, int]:
    """현재 나이와 정년 나이를 기준으로 남은 기간 및 총 재직기간을 단순 추정합니다."""
    years_until_retirement = max(retirement_age - current_age, 0)

    # 현재 연도는 앱 실행 시점의 연도 대신 사용자가 입력한 나이 기반으로만 단순 추정합니다.
    # 총 재직기간은 임용연도부터 정년까지의 긴 기간을 정확히 알기 어렵기 때문에,
    # 현재까지 재직기간 + 남은 재직기간 형태가 아니라 임용연령을 역산하는 단순 모델로 처리합니다.
    # 교사 신규 임용 대표값으로 만 24세 전후를 고려해 현재 나이와 임용 후 경과기간을 사용합니다.
    current_year = 2026
    elapsed_years = max(current_year - appointment_year + (1 - appointment_month) / 12, 0)
    total_service_years = elapsed_years + years_until_retirement

    return total_service_years, years_until_retirement


def estimate_exact_values_from_contribution(current_contribution: int, current_a_value: float) -> Tuple[float, float, float, float]:
    """
    현재 일반기여금으로 적용보수 관련 값을 추정합니다.

    핵심:
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
    """사용자 직접 입력값이 있으면 직접 입력값을 우선하고, 없으면 현재 기여금 기반 추정값을 사용합니다."""
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
# 4. 계산 로직
# =========================================================

def calculate_average_accrual_rate(appointment_year: int, total_service_years: float) -> float:
    """재직기간 동안의 평균 지급률을 단순 평균으로 계산합니다."""
    if total_service_years <= 0:
        return FINAL_ACCRUAL_RATE

    full_years = max(int(round(total_service_years)), 1)
    rates = []
    for i in range(full_years):
        year = appointment_year + i
        rates.append(get_accrual_rate(year))
    return sum(rates) / len(rates)


def calculate_pension(inputs: UserInputs, values: EstimatedValues) -> PensionResult:
    """공무원연금, 일시금, 퇴직수당을 교육용으로 단순 추정합니다."""
    total_service_years, years_until_retirement = estimate_service_years(
        appointment_year=inputs.appointment_year,
        appointment_month=inputs.appointment_month,
        retirement_age=inputs.retirement_age,
        current_age=inputs.current_age,
    )

    salary_growth = inputs.salary_growth_rate / 100
    inflation = inputs.inflation_rate / 100

    growth_factor = (1 + salary_growth) ** years_until_retirement
    inflation_factor = (1 + inflation) ** years_until_retirement

    # 퇴직 시점 추정 적용보수
    future_b_value = values.actual_b_value * growth_factor
    future_p3_value = values.actual_p3_value * growth_factor
    future_post2010_lump_allowance_value = values.post2010_lump_allowance_value * growth_factor

    # 연금 월액 단순 추정
    # 실제 공단 산식은 기간별 평균기준소득월액, 소득재분배, 상한, 지급개시연령, 재직기간 상한 등 다양한 요소가 있습니다.
    avg_accrual_rate = calculate_average_accrual_rate(inputs.appointment_year, total_service_years)
    nominal_monthly_pension = future_p3_value * avg_accrual_rate * total_service_years

    # 2009년 이전 연금 칸 금액이 있으면 일부 반영
    # 2010년 이후 임용자는 대부분 해당 없음입니다.
    if values.p1_pension_value > 0:
        nominal_monthly_pension += values.p1_pension_value * growth_factor * 0.02

    real_monthly_pension = nominal_monthly_pension / inflation_factor if inflation_factor > 0 else nominal_monthly_pension

    # 일시금 단순 추정
    # 2010년 이후 일시금/퇴직수당 칸 금액을 기준으로 재직기간 계수를 곱합니다.
    nominal_lump_sum = future_post2010_lump_allowance_value * total_service_years * 0.85
    if values.p1_lump_value > 0:
        nominal_lump_sum += values.p1_lump_value * growth_factor
    real_lump_sum = nominal_lump_sum / inflation_factor if inflation_factor > 0 else nominal_lump_sum

    # 퇴직수당 단순 추정
    # 공단 실제 산식과 완전히 같지는 않지만, 퇴직수당 규모감 파악용으로 보수적으로 추정합니다.
    allowance_coefficient = 0.10
    if total_service_years >= 20:
        allowance_coefficient = 0.13
    if total_service_years >= 30:
        allowance_coefficient = 0.16

    nominal_retirement_allowance = future_post2010_lump_allowance_value * total_service_years * allowance_coefficient
    if values.p1_allowance_value > 0:
        nominal_retirement_allowance += values.p1_allowance_value * growth_factor
    real_retirement_allowance = (
        nominal_retirement_allowance / inflation_factor if inflation_factor > 0 else nominal_retirement_allowance
    )

    # 연금 월액의 현재가치 25년 수령 가정 + 일시금 + 퇴직수당
    pension_25y_nominal_value = nominal_monthly_pension * 12 * 25
    pension_25y_real_value = real_monthly_pension * 12 * 25

    total_nominal_value = pension_25y_nominal_value + nominal_lump_sum + nominal_retirement_allowance
    total_real_value = pension_25y_real_value + real_lump_sum + real_retirement_allowance

    replacement_monthly_income = real_monthly_pension

    return PensionResult(
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
        replacement_monthly_income=replacement_monthly_income,
    )


# =========================================================
# 5. 화면 구성
# =========================================================

def render_title() -> None:
    st.title("📊 공무원연금 예상 계산기")
    st.caption(
        "현재 일반기여금과 공단 예상퇴직급여 조회서의 적용보수 값을 바탕으로 "
        "퇴직 후 연금 규모를 대략 가늠해보는 교육용 계산기입니다."
    )

    with st.expander("⚠️ 사용 전 꼭 읽어주세요", expanded=False):
        st.markdown(
            """
            - 이 앱은 **공단 공식 계산기가 아닙니다.**
            - 실제 연금액은 공무원연금공단 산식, 재직기간, 소득재분배, 기준소득월액 상한, 개인별 이력에 따라 달라집니다.
            - 결과는 **노후 준비 규모를 감 잡기 위한 참고값**으로만 사용해 주세요.
            - 정확한 값은 공무원연금공단의 예상퇴직급여 조회서를 우선해야 합니다.
            """
        )


def render_sidebar() -> UserInputs:
    st.sidebar.header("1. 기본 정보")

    current_age = st.sidebar.number_input(
        "현재 나이",
        min_value=20,
        max_value=65,
        value=33,
        step=1,
        help="현재 만 나이에 가깝게 입력하면 됩니다.",
    )

    appointment_year = st.sidebar.number_input(
        "임용연도",
        min_value=1980,
        max_value=2050,
        value=2016,
        step=1,
    )

    appointment_month = st.sidebar.number_input(
        "임용월",
        min_value=1,
        max_value=12,
        value=3,
        step=1,
    )

    retirement_age = st.sidebar.number_input(
        "예상 퇴직 나이",
        min_value=50,
        max_value=70,
        value=62,
        step=1,
    )

    current_contribution = st.sidebar.number_input(
        "현재 일반기여금 월 납부액",
        min_value=0,
        value=395_000,
        step=10_000,
        help="급여명세서의 공무원연금 일반기여금 월 납부액을 입력합니다.",
    )

    st.sidebar.header("2. 적용보수 직접 입력 (선택)")
    use_exact_data = st.sidebar.toggle(
        "공단 예상퇴직급여 조회서의 적용보수 값 직접 입력",
        value=False,
    )

    exact_b_value: Optional[int] = None
    exact_redist_value: Optional[int] = None
    exact_post2010_lump_allowance_value: Optional[int] = None
    exact_p1_lump_value: Optional[int] = None
    exact_p1_allowance_value: Optional[int] = None
    exact_p1_pension_value: Optional[int] = None

    if use_exact_data:
        st.sidebar.caption("보고서에 있는 순서와 최대한 비슷하게 입력 흐름을 맞췄습니다.")

        exact_b_value = st.sidebar.number_input(
            "개인 평균 기준소득월액 (B값)",
            min_value=0,
            value=None,
            step=10_000,
            placeholder="예: 4,518,107",
            help="퇴직연금액 예상보고서에서 비교적 쉽게 찾을 수 있는 개인 평균 기준소득월액입니다.",
        )

        exact_redist_value = st.sidebar.number_input(
            "2016년 이후 소득재분배 반영 기준소득월액",
            min_value=0,
            value=None,
            step=10_000,
            placeholder="예: 5,486,337",
            help="2016년 이후 기간의 연금 산정에 쓰이는 소득재분배 반영 기준소득월액입니다.",
        )

        exact_post2010_lump_allowance_value = st.sidebar.number_input(
            "2010.1.1 이후기간 <Ⅱ·Ⅲ기간> - 일시금/퇴직수당 칸 금액",
            min_value=0,
            value=None,
            step=10_000,
            placeholder="예: 5,578,769",
            help="적용보수 표에서 2010.1.1 이후기간 <Ⅱ·Ⅲ기간> 아래 일시금 또는 퇴직수당 칸 금액입니다.",
        )

        st.sidebar.markdown("---")
        st.sidebar.caption("2009.12.31 이전기간 <Ⅰ기간>은 보고서 순서대로 입력합니다.")

        # 2009년 이전 입력 순서: 일시금 → 퇴직수당 → 연금
        exact_p1_lump_value = st.sidebar.number_input(
            "2009.12.31 이전기간 <Ⅰ기간> - 일시금 칸 금액",
            min_value=0,
            value=None,
            step=10_000,
            placeholder="해당 없으면 비워두기",
        )

        exact_p1_allowance_value = st.sidebar.number_input(
            "2009.12.31 이전기간 <Ⅰ기간> - 퇴직수당 칸 금액",
            min_value=0,
            value=None,
            step=10_000,
            placeholder="해당 없으면 비워두기",
        )

        exact_p1_pension_value = st.sidebar.number_input(
            "2009.12.31 이전기간 <Ⅰ기간> - 연금 칸 금액",
            min_value=0,
            value=None,
            step=10_000,
            placeholder="해당 없으면 비워두기",
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
        value=5_440_000,
        step=10_000,
        help="정확히 모르면 기본값을 그대로 두어도 됩니다. 소득재분배 반영 기준소득월액 추정에 사용됩니다.",
    )

    return UserInputs(
        current_age=int(current_age),
        appointment_year=int(appointment_year),
        appointment_month=int(appointment_month),
        retirement_age=int(retirement_age),
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


def render_estimation_panel(values: EstimatedValues, inputs: UserInputs) -> None:
    st.subheader("① 현재 기여금 기반 적용보수 추정")

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

            다만 공단 보고서의 실제 값이 있다면, 직접 입력한 값이 추정값보다 우선 적용됩니다.
            """
        )

    st.subheader("② 실제 계산에 적용된 값")
    col5, col6, col7 = st.columns(3)
    col5.metric("적용 B값", manwon(values.actual_b_value))
    col6.metric("적용 2016년 이후 소득재분배값", manwon(values.actual_p3_value))
    col7.metric("적용 2010년 이후 일시금/퇴직수당값", manwon(values.post2010_lump_allowance_value))

    if inputs.use_exact_data:
        st.info("직접 입력한 값이 있는 항목은 직접 입력값을 우선 사용했습니다. 비워둔 항목은 현재 일반기여금 기반 추정값을 사용했습니다.")
    else:
        st.warning("공단 보고서 값을 직접 입력하지 않았기 때문에 현재 일반기여금 기반 추정값으로 계산합니다.")


def render_result_panel(result: PensionResult) -> None:
    st.subheader("③ 예상 결과")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("예상 총 재직기간", f"{result.total_service_years:,.1f}년")
    col2.metric("퇴직까지 남은 기간", f"{result.years_until_retirement}년")
    col3.metric("월 연금 명목금액", manwon(result.nominal_monthly_pension))
    col4.metric("월 연금 현재가치", manwon(result.real_monthly_pension))

    st.markdown("---")

    col5, col6, col7 = st.columns(3)
    col5.metric("일시금 명목 추정", eokwon(result.nominal_lump_sum))
    col6.metric("퇴직수당 명목 추정", eokwon(result.nominal_retirement_allowance))
    col7.metric("총 가치 현재가치 추정", eokwon(result.total_real_value))

    st.markdown("---")

    st.subheader("④ 상세표")
    rows = [
        ["월 연금", won(result.nominal_monthly_pension), won(result.real_monthly_pension)],
        ["일시금", won(result.nominal_lump_sum), won(result.real_lump_sum)],
        ["퇴직수당", won(result.nominal_retirement_allowance), won(result.real_retirement_allowance)],
        ["연금 25년 수령 가정 + 일시금 + 퇴직수당", won(result.total_nominal_value), won(result.total_real_value)],
    ]
    st.dataframe(
        rows,
        column_config={
            0: "구분",
            1: "퇴직시점 명목금액",
            2: "현재가치 환산",
        },
        hide_index=True,
        use_container_width=True,
    )


def render_interpretation(result: PensionResult, inputs: UserInputs) -> None:
    st.subheader("⑤ 해석")

    st.markdown(
        f"""
        이 계산에서는 퇴직 후 월 연금을 현재가치 기준으로 약 **{manwon(result.real_monthly_pension)}** 정도로 추정했습니다.

        여기서 중요한 포인트는 명목금액과 현재가치의 차이입니다.  
        퇴직 시점에는 월 연금 숫자가 커 보일 수 있지만, 물가상승률을 반영하면 실제 구매력은 줄어들 수 있습니다.

        그래서 공무원연금은 노후의 중요한 1층 안전망이지만, 이것만으로 모든 노후 생활비를 해결한다고 보기보다는
        연금저축, IRP, ISA, 일반 투자계좌 등과 함께 보는 편이 더 현실적입니다.
        """
    )

    if result.real_monthly_pension < 2_000_000:
        st.warning("현재가치 기준 월 연금 추정치가 200만 원 미만입니다. 개인연금이나 투자자산 보완 필요성이 클 수 있습니다.")
    elif result.real_monthly_pension < 3_500_000:
        st.info("현재가치 기준으로 기본 생활비의 한 축은 될 수 있지만, 여유 있는 노후를 위해서는 추가 현금흐름이 필요할 수 있습니다.")
    else:
        st.success("현재가치 기준 월 연금 추정치가 비교적 높은 편입니다. 다만 실제 생활비와 배우자/가구 기준 지출을 함께 확인하는 것이 좋습니다.")


def render_debug_table(values: EstimatedValues, result: PensionResult) -> None:
    with st.expander("계산값 점검용 표", expanded=False):
        debug_rows = [
            ["현재 기준소득월액 역산", won(values.current_standard_income)],
            ["B값 추정", won(values.inferred_b_value)],
            ["2016년 이후 소득재분배값 추정", won(values.inferred_redist_value)],
            ["2010년 이후 일시금/퇴직수당값 추정", won(values.inferred_post2010_lump_allowance)],
            ["실제 적용 B값", won(values.actual_b_value)],
            ["실제 적용 2016년 이후 소득재분배값", won(values.actual_p3_value)],
            ["실제 적용 2010년 이후 일시금/퇴직수당값", won(values.post2010_lump_allowance_value)],
            ["총 재직기간", f"{result.total_service_years:,.2f}년"],
            ["퇴직까지 남은 기간", f"{result.years_until_retirement}년"],
        ]
        st.dataframe(debug_rows, column_config={0: "항목", 1: "값"}, hide_index=True, use_container_width=True)


# =========================================================
# 6. 메인 실행
# =========================================================

def main() -> None:
    render_title()
    inputs = render_sidebar()

    values = build_estimated_values(inputs)
    result = calculate_pension(inputs, values)

    tab1, tab2, tab3 = st.tabs(["계산 결과", "적용보수 확인", "주의사항"])

    with tab1:
        render_result_panel(result)
        render_interpretation(result, inputs)

    with tab2:
        render_estimation_panel(values, inputs)
        render_debug_table(values, result)

    with tab3:
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


if __name__ == "__main__":
    main()
