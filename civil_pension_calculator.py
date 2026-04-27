from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, Tuple

import pandas as pd
import streamlit as st

# =====================================
# 기본 설정
# =====================================
st.set_page_config(
    page_title="공무원연금 시뮬레이터 v2",
    page_icon="🏛️",
    layout="wide",
)

CURRENT_DATE = date.today()
CURRENT_YEAR = CURRENT_DATE.year

# 일반기여금률: 2020년 이후 9%
CONTRIBUTION_RATE = 0.09

# 기본 경제 가정
DEFAULT_SALARY_GROWTH = 0.025
DEFAULT_INFLATION = 0.020
DEFAULT_PERIOD2_RATE = 0.019

# 공무원연금 지급률: 2016~2035년 단계적 인하
# 단위: %
PENSION_RATES: Dict[int, float] = {
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

# 전체 공무원 평균기준소득월액 A값 참고용
# 실제 최신값은 공단 공지/예상퇴직급여 상세에서 확인 권장
OFFICIAL_A_VALUES: Dict[int, int] = {
    2011: 3950000,
    2012: 4150000,
    2013: 4350000,
    2014: 4470000,
    2015: 4670000,
    2016: 4910000,
    2017: 5100000,
    2018: 5220000,
    2019: 5300000,
    2020: 5390000,
    2021: 5350000,
    2022: 5390000,
    2023: 5440000,
    2024: 5520000,
    2025: 5710000,
    2026: 5710000,
}

# 2016년 이후 신규 임용자가 36년을 채우는 사례에서 자주 인용되는 재직기간별 적용비율 예시
# 공단 실제 개인별 이행률/적용비율은 예상퇴직급여 상세자료에서 확인하는 것이 가장 정확함
DEFAULT_FULL_36_TRANSITION_RATIO = 1.0344  # 103.44%

GEPS_HOME_URL = "https://www.geps.or.kr/index"
GEPS_ESTIMATE_GUIDE_TEXT = "공무원연금공단 홈페이지 → 연금복지포털 → 로그인 → 나의 연금예상액 → 상세보기"


# =====================================
# 데이터 클래스
# =====================================
@dataclass
class Inputs:
    current_age: int
    entry_date: date
    retirement_date: date
    current_contribution: int
    salary_growth: float
    inflation: float
    period2_rate: float
    use_exact_data: bool
    exact_b_value: float
    exact_redist_value: float
    exact_p1_value: float
    exact_transition_ratio_pct: float
    job_type: str


@dataclass
class Result:
    retirement_year: int
    years_to_retire: float
    retirement_age_est: float
    pension_start_age: int
    pension_start_year: int
    gap_years: float

    current_standard_income: float
    current_a_value: float
    inferred_b_value: float
    inferred_redist_value: float

    pre_2016_service_years: float
    service_cap_years: int
    recognized_service_years: float
    raw_y1: float
    raw_y2: float
    raw_y3: float
    y1: float
    y2: float
    y3: float

    transition_ratio: float
    transition_ratio_source: str

    base_p1_income: float
    base_p2_income: float
    base_redist_income: float
    avg_rate_2016plus: float
    avg_rate_2016_first30: float
    avg_rate_2016_over30: float

    period3_redist_years: float
    period3_personal_years: float
    period3_over30_years: float

    monthly_pension_real: float
    monthly_pension_nominal: float
    period1_monthly: float
    period2_monthly: float
    period3_monthly: float
    period3_redist_monthly: float
    period3_personal_monthly: float
    period3_over30_monthly: float

    retirement_allowance_real: float
    retirement_allowance_nominal: float
    pension_lump_sum_real: float
    pension_lump_sum_nominal: float


# =====================================
# 출력 유틸
# =====================================
def won(value: float) -> str:
    return f"{int(round(value)):,}원"


def manwon(value: float) -> str:
    return f"{value / 10000:,.1f}만 원"


def pct(value: float) -> str:
    return f"{value:.3f}%"


def ratio_pct(value: float) -> str:
    return f"{value * 100:.2f}%"


# =====================================
# 날짜/기간 유틸
# =====================================
def years_between(start_date: date, end_date: date) -> float:
    if end_date <= start_date:
        return 0.0
    return (end_date - start_date).days / 365.2425


def year_fraction(d: date) -> float:
    return d.year + ((d.timetuple().tm_yday - 1) / 365.2425)


def get_default_retirement_date(current_age: int, job_type: str) -> date:
    """현재 나이 기준의 간단한 예상 정년퇴직일.

    교원은 정년 62세 도달 학년도 말/학기 말 규정이 더 세부적일 수 있으므로,
    이 앱에서는 간편 추정값으로 2월 말 기준을 기본값으로 둔다.
    사용자가 직접 예상 퇴직일을 넣는 것을 권장한다.
    """
    retirement_age = 60 if "일반공무원" in job_type else 62
    years_left = max(0, retirement_age - current_age)
    retire_year = CURRENT_YEAR + years_left

    if "교원" in job_type:
        return date(retire_year, 3, 1) - timedelta(days=1)
    return date(retire_year, 12, 31)


def get_pension_start_age(entry_date: date, retirement_year: int) -> int:
    """1996년 이후 임용자의 연금지급개시연령 단계적 연장 반영."""
    if entry_date >= date(1996, 1, 1):
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
    return 60


# =====================================
# 공무원연금 계산 유틸
# =====================================
def pension_rate_for_year(year: int) -> float:
    """해당 연도 공무원연금 지급률(%)."""
    if year in PENSION_RATES:
        return PENSION_RATES[year]
    if year < 2016:
        return 1.9
    return 1.7


def weighted_average_rate(start_year_float: float, end_year_float: float) -> float:
    """연도별 지급률을 기간 가중평균으로 계산한다. 반환 단위는 %."""
    if end_year_float <= start_year_float:
        return 0.0

    total_rate = 0.0
    total_weight = 0.0
    year = int(start_year_float)

    while year < int(end_year_float) + 1:
        s = max(start_year_float, float(year))
        e = min(end_year_float, float(year + 1))
        weight = max(0.0, e - s)
        if weight > 0:
            total_rate += pension_rate_for_year(year) * weight
            total_weight += weight
        year += 1

    return total_rate / total_weight if total_weight > 0 else 0.0


def recognized_service_cap(pre_2016_service_years: float) -> int:
    """2016년 개정 이후 재직기간 상한.

    2016년 이전 재직기간에 따라 33~36년까지 단계적 연장.
    """
    if pre_2016_service_years >= 21:
        return 33
    if pre_2016_service_years >= 17:
        return 34
    if pre_2016_service_years >= 15:
        return 35
    return 36


def apply_service_cap(raw_y1: float, raw_y2: float, raw_y3: float, cap_years: int) -> Tuple[float, float, float]:
    """기간별 재직연수를 오래된 구간부터 상한에 맞춰 반영한다."""
    remaining = float(cap_years)

    y1 = min(raw_y1, remaining)
    remaining -= y1

    y2 = min(raw_y2, max(0.0, remaining))
    remaining -= y2

    y3 = min(raw_y3, max(0.0, remaining))

    return y1, y2, y3


def calculate_service_years(entry_date: date, retirement_date: date):
    actual_start = year_fraction(entry_date)
    actual_end = year_fraction(retirement_date)

    raw_y1 = max(0.0, min(actual_end, 2010.0) - max(actual_start, 0.0))
    raw_y2 = max(0.0, min(actual_end, 2016.0) - max(actual_start, 2010.0))
    raw_y3 = max(0.0, actual_end - max(actual_start, 2016.0))

    pre_2016 = raw_y1 + raw_y2
    cap_years = recognized_service_cap(pre_2016)
    y1, y2, y3 = apply_service_cap(raw_y1, raw_y2, raw_y3, cap_years)

    return {
        "raw_y1": raw_y1,
        "raw_y2": raw_y2,
        "raw_y3": raw_y3,
        "pre_2016_service_years": pre_2016,
        "cap_years": cap_years,
        "y1": y1,
        "y2": y2,
        "y3": y3,
        "recognized_service_years": y1 + y2 + y3,
        "actual_start": actual_start,
    }


def infer_current_standard_income(current_contribution: int) -> float:
    """현재 일반기여금을 기준소득월액으로 역산한다."""
    return current_contribution / CONTRIBUTION_RATE if current_contribution > 0 else 0.0


def estimate_b_and_redist(current_standard_income: float, current_a_value: float):
    """적용보수 미입력 시 B값과 소득재분배 반영값을 보수적으로 추정한다.

    주의: 실제 공단 적용보수와 다를 수 있으므로, 정확성을 높이려면 직접 입력 모드를 사용해야 한다.
    """
    est_b_value = current_standard_income * 0.90
    capped_b = min(est_b_value, current_a_value * 1.6)
    est_redist = (current_a_value + capped_b) / 2
    return capped_b, est_redist


def estimate_transition_ratio(post_2010_years: float, cap_years: int) -> float:
    """재직기간별 적용비율(이행률) 자동 추정.

    2016년 이후 36년 재직 시 103.44% 사례를 기준으로 선형 보간한다.
    개인별 실제 이행률은 공단 상세자료 입력을 권장한다.
    """
    if post_2010_years <= 0:
        return 1.0
    effective_years = min(post_2010_years, float(cap_years), 36.0)
    return 1.0 + (DEFAULT_FULL_36_TRANSITION_RATIO - 1.0) * (effective_years / 36.0)


def retirement_allowance_rate(total_years: float) -> float:
    if total_years < 1:
        return 0.0
    if total_years < 5:
        return 0.065
    if total_years < 10:
        return 0.2275
    if total_years < 15:
        return 0.2925
    if total_years < 20:
        return 0.325
    return 0.39


def calculate_period3_pension(
    *,
    y3: float,
    period3_start: float,
    transition_ratio: float,
    redist_income: float,
    b_income: float,
) -> Tuple[float, float, float, float, float, float, float, float]:
    """2016년 이후 구간을 공식 구조에 가깝게 3덩어리로 계산한다.

    반환값:
    (총액, 소득재분배분, 개인소득분, 30년초과분, 평균지급률전체, 평균지급률30년까지, 평균지급률30년초과, 30년초과연수)
    """
    if y3 <= 0:
        return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0

    first30_years = min(y3, 30.0)
    over30_years = max(0.0, y3 - 30.0)

    first30_start = period3_start
    first30_end = first30_start + first30_years
    over30_start = first30_end
    over30_end = over30_start + over30_years

    avg_rate_all = weighted_average_rate(period3_start, period3_start + y3)
    avg_rate_first30 = weighted_average_rate(first30_start, first30_end) if first30_years > 0 else 0.0
    avg_rate_over30 = weighted_average_rate(over30_start, over30_end) if over30_years > 0 else 0.0

    # 30년까지: 1%는 소득재분배 반영 기준소득월액, 나머지 지급률-1%는 개인 평균기준소득월액
    redist_part = redist_income * transition_ratio * first30_years * 0.01
    personal_rate_first30 = max(avg_rate_first30 - 1.0, 0.0) / 100
    personal_part = b_income * transition_ratio * first30_years * personal_rate_first30

    # 30년 초과분: 소득재분배 없이 개인 평균기준소득월액 × 지급률
    over30_part = b_income * transition_ratio * over30_years * (avg_rate_over30 / 100) if over30_years > 0 else 0.0

    total = redist_part + personal_part + over30_part
    return total, redist_part, personal_part, over30_part, avg_rate_all, avg_rate_first30, avg_rate_over30, over30_years


# =====================================
# 메인 연금 계산
# =====================================
def calculate_pension(inputs: Inputs) -> Result:
    retirement_year = inputs.retirement_date.year
    years_to_retire = max(0.0, years_between(CURRENT_DATE, inputs.retirement_date))
    retirement_age_est = inputs.current_age + years_to_retire

    service = calculate_service_years(inputs.entry_date, inputs.retirement_date)

    current_standard_income = infer_current_standard_income(inputs.current_contribution)
    current_a_value = OFFICIAL_A_VALUES[max(OFFICIAL_A_VALUES.keys())]
    inferred_b_value, inferred_redist_value = estimate_b_and_redist(current_standard_income, current_a_value)

    actual_p1_value = inputs.exact_p1_value if (inputs.use_exact_data and inputs.exact_p1_value > 0) else current_standard_income
    actual_b_value = inputs.exact_b_value if (inputs.use_exact_data and inputs.exact_b_value > 0) else inferred_b_value
    actual_redist_value = inputs.exact_redist_value if (inputs.use_exact_data and inputs.exact_redist_value > 0) else inferred_redist_value

    post_2010_years = service["y2"] + service["y3"]
    if inputs.exact_transition_ratio_pct > 0:
        transition_ratio = inputs.exact_transition_ratio_pct / 100
        transition_ratio_source = "직접 입력"
    else:
        transition_ratio = estimate_transition_ratio(post_2010_years, service["cap_years"])
        transition_ratio_source = "자동 추정"

    # 1기간: 2009.12.31 이전
    period1_monthly = 0.0
    if service["y1"] > 0:
        if service["y1"] >= 20:
            period1_monthly = actual_p1_value * 0.5 + actual_p1_value * (service["y1"] - 20) * 0.02
        else:
            period1_monthly = actual_p1_value * service["y1"] * 0.025

    # 2기간: 2010.1.1~2015.12.31
    period2_monthly = 0.0
    if service["y2"] > 0:
        period2_monthly = actual_b_value * transition_ratio * service["y2"] * inputs.period2_rate

    # 3기간: 2016.1.1 이후
    period3_monthly = 0.0
    period3_redist_monthly = 0.0
    period3_personal_monthly = 0.0
    period3_over30_monthly = 0.0
    avg_rate_2016plus = 0.0
    avg_rate_2016_first30 = 0.0
    avg_rate_2016_over30 = 0.0
    period3_redist_years = 0.0
    period3_personal_years = 0.0
    period3_over30_years = 0.0

    if service["y3"] > 0:
        period3_start = max(2016.0, service["actual_start"])
        period3_redist_years = min(service["y3"], 30.0)
        period3_personal_years = period3_redist_years

        (
            period3_monthly,
            period3_redist_monthly,
            period3_personal_monthly,
            period3_over30_monthly,
            avg_rate_2016plus,
            avg_rate_2016_first30,
            avg_rate_2016_over30,
            period3_over30_years,
        ) = calculate_period3_pension(
            y3=service["y3"],
            period3_start=period3_start,
            transition_ratio=transition_ratio,
            redist_income=actual_redist_value,
            b_income=actual_b_value,
        )

    monthly_pension_today = period1_monthly + period2_monthly + period3_monthly

    # 현재가치 기준으로 계산된 월연금액을 미래 명목가치와 현재 체감가치로 변환
    # 주의: salary_growth는 단순 보수인상률만이 아니라, 호봉상승 등 기준소득 상승 효과까지 포함한 가정값으로 해석해야 함.
    salary_multiplier = (1 + inputs.salary_growth) ** years_to_retire
    inflation_multiplier = (1 + inputs.inflation) ** years_to_retire

    monthly_pension_nominal = monthly_pension_today * salary_multiplier
    monthly_pension_real = monthly_pension_nominal / inflation_multiplier

    projected_final_income_nominal = current_standard_income * salary_multiplier
    allowance_rate = retirement_allowance_rate(service["recognized_service_years"])

    retirement_allowance_real = current_standard_income * service["recognized_service_years"] * allowance_rate
    retirement_allowance_nominal = projected_final_income_nominal * service["recognized_service_years"] * allowance_rate

    # 연금일시금은 참고용 간이식. 실제 선택/세금/개인별 경과규정은 공단 확인 필요.
    excess_5_years = max(0.0, service["recognized_service_years"] - 5.0)
    lump_sum_multiplier = 0.975 + (excess_5_years * 0.0065)

    pension_lump_sum_real = current_standard_income * service["recognized_service_years"] * lump_sum_multiplier
    pension_lump_sum_nominal = projected_final_income_nominal * service["recognized_service_years"] * lump_sum_multiplier

    pension_start_age = get_pension_start_age(inputs.entry_date, retirement_year)
    pension_start_year = retirement_year + max(0, pension_start_age - int(round(retirement_age_est)))
    gap_years = max(0.0, pension_start_age - retirement_age_est)

    return Result(
        retirement_year=retirement_year,
        years_to_retire=years_to_retire,
        retirement_age_est=retirement_age_est,
        pension_start_age=pension_start_age,
        pension_start_year=pension_start_year,
        gap_years=gap_years,
        current_standard_income=current_standard_income,
        current_a_value=current_a_value,
        inferred_b_value=inferred_b_value,
        inferred_redist_value=inferred_redist_value,
        pre_2016_service_years=service["pre_2016_service_years"],
        service_cap_years=service["cap_years"],
        recognized_service_years=service["recognized_service_years"],
        raw_y1=service["raw_y1"],
        raw_y2=service["raw_y2"],
        raw_y3=service["raw_y3"],
        y1=service["y1"],
        y2=service["y2"],
        y3=service["y3"],
        transition_ratio=transition_ratio,
        transition_ratio_source=transition_ratio_source,
        base_p1_income=actual_p1_value if service["y1"] > 0 else 0.0,
        base_p2_income=actual_b_value if service["y2"] > 0 else 0.0,
        base_redist_income=actual_redist_value if service["y3"] > 0 else 0.0,
        avg_rate_2016plus=avg_rate_2016plus,
        avg_rate_2016_first30=avg_rate_2016_first30,
        avg_rate_2016_over30=avg_rate_2016_over30,
        period3_redist_years=period3_redist_years,
        period3_personal_years=period3_personal_years,
        period3_over30_years=period3_over30_years,
        monthly_pension_real=monthly_pension_real,
        monthly_pension_nominal=monthly_pension_nominal,
        period1_monthly=period1_monthly,
        period2_monthly=period2_monthly,
        period3_monthly=period3_monthly,
        period3_redist_monthly=period3_redist_monthly,
        period3_personal_monthly=period3_personal_monthly,
        period3_over30_monthly=period3_over30_monthly,
        retirement_allowance_real=retirement_allowance_real,
        retirement_allowance_nominal=retirement_allowance_nominal,
        pension_lump_sum_real=pension_lump_sum_real,
        pension_lump_sum_nominal=pension_lump_sum_nominal,
    )


# =====================================
# 적용보수 입력 가이드
# =====================================
def get_missing_exact_fields(
    entry_date: date,
    exact_b_value: float,
    exact_redist_value: float,
    exact_p1_value: float,
    exact_transition_ratio_pct: float,
):
    missing = []

    if exact_b_value <= 0:
        missing.append("개인 평균 기준소득월액(B값)")
    if exact_redist_value <= 0:
        missing.append("소득재분배 반영 기준소득월액")
    if exact_transition_ratio_pct <= 0:
        missing.append("재직기간별 적용비율(이행률)")
    if entry_date <= date(2009, 12, 31) and exact_p1_value <= 0:
        missing.append("2009년 이전 평균 보수월액")

    return missing


def render_exact_input_guide(
    entry_date: date,
    exact_b_value: float,
    exact_redist_value: float,
    exact_p1_value: float,
    exact_transition_ratio_pct: float,
):
    missing = get_missing_exact_fields(entry_date, exact_b_value, exact_redist_value, exact_p1_value, exact_transition_ratio_pct)
    if not missing:
        return

    st.divider()
    st.subheader("🧭 적용보수 값 입력 가이드")
    st.info(
        "`적용보수 값 사용`을 켠 상태입니다. 공단 예상퇴직급여 상세자료를 보고 숫자를 직접 입력하면 "
        "기여금 역산 추정보다 계산 신뢰도가 올라갑니다. 필요한 값이 모두 입력되면 이 안내는 자동으로 사라집니다."
    )

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("#### 1) 어디에 입력하나요?")
        st.markdown(
            """
사이드바의 **`2. 적용보수 직접 입력`** 영역에 아래 항목을 입력합니다.

- **개인 평균 기준소득월액(B값)**
- **소득재분배 반영 기준소득월액**
- **재직기간별 적용비율(이행률, %)**
- **2009년 이전 평균 보수월액** (해당자만)

쉼표는 빼고 숫자만 넣어도 됩니다.
예: `3,807,467` → `3807467`  
예: `103.44%` → `103.44`
            """
        )

    with col2:
        st.markdown("#### 2) 공단 서류에서 어디를 보나요?")
        st.markdown(
            f"""
공무원연금공단에서 **예상퇴직급여 내역**을 열고 아래 순서로 확인하시면 됩니다.

- **공무원연금공단 홈페이지**
- **연금복지포털 로그인**
- **나의 연금예상액**
- **상세보기**

확인 경로: **{GEPS_ESTIMATE_GUIDE_TEXT}**
            """
        )
        st.link_button("공무원연금공단 홈페이지 열기", GEPS_HOME_URL)

    guide_df = pd.DataFrame(
        {
            "입력칸": [
                "개인 평균 기준소득월액(B값)",
                "소득재분배 반영 기준소득월액",
                "재직기간별 적용비율(이행률)",
                "2009년 이전 평균 보수월액",
            ],
            "서류에서 찾는 항목": [
                "적용보수 표의 개인 평균 기준소득월액",
                "2016년 이후 소득재분배 반영 평균 기준소득월액",
                "재직기간별 적용비율 또는 이행률(%)",
                "2009.12.31 이전 재직기간이 있는 경우 해당 구간 평균 보수월액",
            ],
            "입력 필요 여부": [
                "권장",
                "권장",
                "권장",
                "2009.12.31 이전 재직기간이 있을 때만 필요",
            ],
        }
    )

    st.markdown("#### 3) 어떤 값을 입력하나요?")
    st.dataframe(guide_df, use_container_width=True, hide_index=True)
    st.warning(f"아직 입력이 필요한 항목: **{', '.join(missing)}**")


# =====================================
# UI
# =====================================
st.title("🏛️ 공무원연금 시뮬레이터 v2")
st.caption("공식 산정액이 아니라, 공무원연금 구조를 이해하기 위한 간이 시뮬레이터입니다.")

st.warning(
    "이 앱은 공무원연금공단 공식 계산기가 아닙니다. 정확한 예상액은 반드시 공무원연금공단 "
    "연금복지포털의 '나의 연금예상액 → 상세보기'에서 확인하세요."
)

with st.sidebar:
    st.header("1. 기본 정보 입력")

    job_type = st.radio(
        "직종 선택",
        ["일반공무원 (정년 60세)", "교원 (정년 62세)"],
        index=1,
    )

    current_contribution = st.number_input(
        "현재 매월 납부하는 일반기여금 (원)",
        min_value=0,
        value=396500,
        step=1000,
        help="현재 급여명세서에 표시되는 일반기여금을 입력하세요. 기준소득월액을 역산하는 데 사용합니다.",
    )

    current_age = st.number_input(
        "현재 나이 (세)",
        min_value=20,
        max_value=80,
        value=33,
        step=1,
    )

    entry_date = st.date_input(
        "최초임용일",
        value=date(2016, 3, 1),
        min_value=date(1970, 1, 1),
        max_value=date(2100, 12, 31),
    )

    use_custom_retirement_date = st.toggle("예상 퇴직일 직접 입력", value=False)

    if use_custom_retirement_date:
        retirement_date = st.date_input(
            "예상 퇴직일",
            value=get_default_retirement_date(int(current_age), job_type),
            min_value=date(2000, 1, 1),
            max_value=date(2100, 12, 31),
        )
    else:
        retirement_date = get_default_retirement_date(int(current_age), job_type)
        st.caption(f"자동 계산된 예상 퇴직일: **{retirement_date.strftime('%Y-%m-%d')}**")

    st.divider()
    st.header("2. 적용보수 직접 입력")
    use_exact_data = st.toggle("✅ 적용보수 값 사용", value=False)

    exact_b_value = 0.0
    exact_redist_value = 0.0
    exact_p1_value = 0.0
    exact_transition_ratio_pct = 0.0

    if use_exact_data:
        st.caption("공단 예상퇴직급여 상세자료를 보고 숫자를 직접 입력합니다.")

        exact_b_value = st.number_input(
            "개인 평균 기준소득월액(B값)",
            min_value=0,
            value=0,
            step=10000,
        )

        exact_redist_value = st.number_input(
            "소득재분배 반영 기준소득월액",
            min_value=0,
            value=0,
            step=10000,
        )

        exact_transition_ratio_pct = st.number_input(
            "재직기간별 적용비율 / 이행률 (%)",
            min_value=0.0,
            value=0.0,
            step=0.01,
            help="예: 103.44%라면 103.44를 입력합니다. 비워두면 자동 추정값을 사용합니다.",
        )

        exact_p1_value = st.number_input(
            "2009년 이전 평균 보수월액 (해당 시만)",
            min_value=0,
            value=0,
            step=10000,
        )

    st.divider()
    with st.expander("경제 지표 가정"):
        salary_growth_pct = st.number_input(
            "미래 연 기준소득 상승률 (%)",
            value=DEFAULT_SALARY_GROWTH * 100,
            step=0.1,
            help=(
                "주의: 이 앱은 호봉표를 자동으로 읽지 않습니다. 교원은 보수인상률뿐 아니라 "
                "호봉 상승 효과까지 포함한 '기준소득 상승률'로 해석해서 입력하는 편이 안전합니다."
            ),
        )
        inflation_pct = st.number_input(
            "미래 연 물가상승률 (%)",
            value=DEFAULT_INFLATION * 100,
            step=0.1,
        )
        period2_rate_pct = st.number_input(
            "2기간 지급률 (%)",
            value=DEFAULT_PERIOD2_RATE * 100,
            step=0.001,
        )


# =====================================
# 메인 가이드 표시
# =====================================
if use_exact_data:
    render_exact_input_guide(entry_date, exact_b_value, exact_redist_value, exact_p1_value, exact_transition_ratio_pct)


# =====================================
# 계산
# =====================================
inputs = Inputs(
    current_age=int(current_age),
    entry_date=entry_date,
    retirement_date=retirement_date,
    current_contribution=int(current_contribution),
    salary_growth=float(salary_growth_pct) / 100,
    inflation=float(inflation_pct) / 100,
    period2_rate=float(period2_rate_pct) / 100,
    use_exact_data=use_exact_data,
    exact_b_value=float(exact_b_value or 0),
    exact_redist_value=float(exact_redist_value or 0),
    exact_p1_value=float(exact_p1_value or 0),
    exact_transition_ratio_pct=float(exact_transition_ratio_pct or 0),
    job_type=job_type,
)

res = calculate_pension(inputs)


# =====================================
# 결과 출력
# =====================================
st.divider()
st.subheader("💰 퇴직 시 예상 월 연금액")

c1, c2, c3, c4 = st.columns(4)
c1.metric(
    "월 연금 (물가할인 현재가치)",
    won(res.monthly_pension_real),
    help="미래 퇴직 시점 명목 연금액을 입력한 물가상승률로 할인한 현재 체감가치입니다.",
)
c2.metric(
    "월 연금 (퇴직 시 명목가치)",
    won(res.monthly_pension_nominal),
    help="퇴직 시점 기준 액면 금액입니다. 기준소득 상승률이 반영됩니다.",
)
c3.metric(
    "총 인정 재직기간",
    f"{res.recognized_service_years:.2f}년 (상한 {res.service_cap_years}년)",
)
c4.metric(
    "연금 개시 연령",
    f"{res.pension_start_age}세 ({res.gap_years:.1f}년 공백)",
)

st.divider()
st.subheader("💼 퇴직 시 예상 일시금액 (참고용)")
st.markdown("퇴직수당은 간이 추정이며, 연금일시금도 참고용 추정치입니다.")

d1, d2, d3, d4 = st.columns(4)
d1.metric("퇴직수당 (현재가치)", won(res.retirement_allowance_real))
d2.metric("퇴직수당 (명목가치)", won(res.retirement_allowance_nominal))
d3.metric("연금일시금 (현재가치)", won(res.pension_lump_sum_real))
d4.metric("연금일시금 (명목가치)", won(res.pension_lump_sum_nominal))

st.info(
    f"💡 일시금으로 전액 수령 시 총액 [현재가치]: {won(res.retirement_allowance_real + res.pension_lump_sum_real)} / "
    f"[명목가치]: {won(res.retirement_allowance_nominal + res.pension_lump_sum_nominal)}"
)

st.divider()
left, right = st.columns([1, 1])

with left:
    st.subheader("📊 적용된 기준 소득")
    income_df = pd.DataFrame(
        {
            "적용 구간": [
                "1기간 (2009년 이전)",
                "2기간 (2010~2015년)",
                "3기간 소득재분배 기준",
                "3기간 개인소득 기준(B값)",
            ],
            "기준 소득": [
                won(res.base_p1_income),
                won(res.base_p2_income),
                won(res.base_redist_income),
                won(res.base_p2_income if res.y3 > 0 else 0),
            ],
        }
    )
    st.dataframe(income_df, use_container_width=True, hide_index=True)

    st.subheader("📘 핵심 계산 근거")
    basis_df = pd.DataFrame(
        {
            "항목": [
                "현재 일반기여금",
                "현재 기준소득월액(역산)",
                "전체 공무원 A값(참고)",
                "추정 B값(적용보수 미입력 시)",
                "추정 소득재분배 반영값(적용보수 미입력 시)",
                "재직기간별 적용비율/이행률",
                "이행률 산정 방식",
                "2016.1.1 기준 재직기간",
                "재직기간 상한",
                "예상 퇴직연도",
                "퇴직 시점 나이(추정)",
                "2016년 이후 평균 지급률",
            ],
            "값": [
                won(inputs.current_contribution),
                won(res.current_standard_income),
                won(res.current_a_value),
                won(res.inferred_b_value),
                won(res.inferred_redist_value),
                ratio_pct(res.transition_ratio),
                res.transition_ratio_source,
                f"{res.pre_2016_service_years:.2f}년",
                f"{res.service_cap_years}년",
                f"{res.retirement_year}년",
                f"{res.retirement_age_est:.1f}세",
                pct(res.avg_rate_2016plus),
            ],
        }
    )
    st.dataframe(basis_df, use_container_width=True, hide_index=True)

with right:
    st.subheader("📈 기간별 연금 산출 내역")
    period_df = pd.DataFrame(
        {
            "구간": ["1기간", "2기간", "3기간 합계"],
            "원시 연수": [round(res.raw_y1, 2), round(res.raw_y2, 2), round(res.raw_y3, 2)],
            "상한 반영 연수": [round(res.y1, 2), round(res.y2, 2), round(res.y3, 2)],
            "연금 기여분": [won(res.period1_monthly), won(res.period2_monthly), won(res.period3_monthly)],
        }
    )
    st.dataframe(period_df, use_container_width=True, hide_index=True)

    st.subheader("🔎 2016년 이후 구간 세부 분해")
    period3_df = pd.DataFrame(
        {
            "항목": ["소득재분배 1% 부분", "개인소득 지급률 초과분", "30년 초과분"],
            "반영 연수": [
                round(res.period3_redist_years, 2),
                round(res.period3_personal_years, 2),
                round(res.period3_over30_years, 2),
            ],
            "적용 평균 지급률": [
                "1.000%",
                f"{max(res.avg_rate_2016_first30 - 1.0, 0):.3f}%",
                pct(res.avg_rate_2016_over30),
            ],
            "기여분": [
                won(res.period3_redist_monthly),
                won(res.period3_personal_monthly),
                won(res.period3_over30_monthly),
            ],
        }
    )
    st.dataframe(period3_df, use_container_width=True, hide_index=True)

    chart_df = pd.DataFrame(
        {
            "구간": ["1기간", "2기간", "3기간-소득재분배", "3기간-개인소득", "3기간-30년초과"],
            "연금 기여분": [
                res.period1_monthly,
                res.period2_monthly,
                res.period3_redist_monthly,
                res.period3_personal_monthly,
                res.period3_over30_monthly,
            ],
        }
    ).set_index("구간")
    st.bar_chart(chart_df)


# =====================================
# 상태 메시지
# =====================================
if use_exact_data:
    missing_exact = get_missing_exact_fields(entry_date, exact_b_value, exact_redist_value, exact_p1_value, exact_transition_ratio_pct)
    if missing_exact:
        st.warning(f"⚠️ 적용보수 직접입력 모드입니다. 아직 입력이 필요한 항목: {', '.join(missing_exact)}")
    else:
        st.success("✅ 적용보수 입력이 완료되어 기여금 역산값보다 입력값을 우선 사용합니다.")
else:
    st.info(
        "ℹ️ 현재는 기여금 기반 추정 모드입니다. 더 정확히 계산하려면 '적용보수 값 사용'을 켜고 "
        "공단 예상퇴직급여 상세자료의 적용보수 값을 직접 입력하세요."
    )


# =====================================
# 설명
# =====================================
st.divider()
st.subheader("📚 연금 계산 공식 설명")

formula_df = pd.DataFrame(
    {
        "구간": ["1기간", "2기간", "3기간"],
        "의미": [
            "2009.12.31 이전 재직기간",
            "2010.1.1 ~ 2015.12.31 재직기간",
            "2016.1.1 이후 재직기간",
        ],
        "기본 계산방식": [
            "평균보수월액 기반 과거 경과규정 반영",
            "B값 × 이행률 × 연수 × 1.9%",
            "소득재분배값 × 이행률 × 30년까지 × 1% + B값 × 이행률 × 30년까지 × (지급률-1%) + B값 × 이행률 × 30년초과 × 지급률",
        ],
    }
)
st.dataframe(formula_df, use_container_width=True, hide_index=True)

st.markdown(
    """
### 계산 로직에서 보완한 부분

- 2016년 이후 구간을 단순히 `소득재분배값 × 연수 × 평균지급률`로 계산하지 않고,
  **① 소득재분배 1% 부분, ② 개인소득 지급률 초과분, ③ 30년 초과분**으로 나누었습니다.
- 2010년 이후 구간에 **재직기간별 적용비율(이행률)** 을 반영했습니다.
- 직접 입력값이 없을 때는 36년 재직 기준 103.44% 사례를 바탕으로 간단 추정합니다.
- 더 정확한 계산을 원하면 공단 상세자료의 **B값, 소득재분배 반영값, 이행률** 을 직접 입력하세요.

### 용어 정리

- **B값**: 개인 평균 기준소득월액입니다.
- **소득재분배 반영 기준소득월액**: 2016년 이후 구간의 1% 부분에 들어가는 보정 기준소득입니다.
- **이행률 / 재직기간별 적용비율**: 2010년 이후 기준소득월액 산식에 반영되는 경과규정 성격의 비율입니다.
- **현재 기여금 기반 추정모드**: `현재 기여금 ÷ 9%`로 현재 기준소득월액을 역산한 뒤 추정합니다.
- **적용보수 직접입력 모드**: 사용자가 직접 넣은 공단 상세자료 수치를 우선 사용합니다.
"""
)

st.subheader("⚠️ 주의")
st.markdown(
    """
- 이 앱은 **공식 산정액이 아닌 추정용 시뮬레이터**입니다.
- 파일 자동 읽기 기능은 제거하고, **직접 입력 방식**으로 단순화했습니다.
- 교원 호봉표를 자동으로 읽는 구조가 아니므로, `미래 연 기준소득 상승률`은 보수인상률뿐 아니라 호봉상승 효과까지 감안해 시나리오로 입력하는 편이 안전합니다.
- 퇴직수당과 연금일시금은 **간이 추정**입니다.
- 세금, 건강보험료, 장기요양보험료, 연금소득 과세, 지급정지 가능성은 반영하지 않았습니다.
- 실제 지급액은 공무원연금공단의 상세 이력, 경과규정, 실제 기준소득월액 데이터 등에 따라 달라질 수 있습니다.
"""
)
