# app.py
# 공무원연금 예상 계산기 Streamlit 앱
# 실행: streamlit run app.py

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, Optional, Tuple

import calendar
import math

import pandas as pd
import streamlit as st


# =========================================================
# 0. 기본 설정
# =========================================================

st.set_page_config(
    page_title="공무원연금 예상 계산기",
    page_icon="📊",
    layout="wide",
)

BASE_DIR = Path(__file__).resolve().parent
IMPLEMENTATION_TABLE_PATH = BASE_DIR / "implementation_factor_table.csv"


# =========================================================
# 1. 지급률 / 기본값 설정
# =========================================================

# 2016년 이후 연도별 연금지급률
# 단위: 소수. 예: 1.878% = 0.01878
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
    2026: 0.01736,
    2027: 0.01732,
    2028: 0.01728,
    2029: 0.01724,
    2030: 0.01720,
    2031: 0.01716,
    2032: 0.01712,
    2033: 0.01708,
    2034: 0.01704,
    2035: 0.01700,
}

FINAL_ACCRUAL_RATE = 0.01700
PERIOD2_RATE = 0.019

JOB_TEACHER = "교원"
JOB_GENERAL = "일반직 공무원"

LUMP_BASIS_B_VALUE = "B값 기준(정년 미래추정 권장)"
LUMP_BASIS_REPORT_VALUE = "보고서 일시금/퇴직수당 적용보수 기준(오늘퇴직 검산용)"


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
    salary_growth_rate: float
    inflation_rate: float

    # 퇴직급여 예상보고서 적용보수 입력값
    report_b_value: int
    report_redist_value: int
    report_post2010_lump_allowance_value: int
    report_p1_lump_value: int
    report_p1_allowance_value: int
    report_p1_pension_value: int

    # 인정기간 보정
    retirement_benefit_exclusion_months: int
    retirement_allowance_extra_exclusion_months: int

    # 이행률
    manual_implementation_factor_pct: Optional[float]

    # 미래 추정 보정
    future_lump_basis_mode: str
    monthly_pension_adjustment_factor: float
    lump_allowance_adjustment_factor: float


@dataclass
class ServiceResult:
    raw_y1: float
    raw_y2: float
    raw_y3: float
    y1: float
    y2: float
    y3: float
    allowance_y1: float
    allowance_y2: float
    allowance_y3: float
    pre_2016_years: float
    before_2010_years: float
    after_2010_years: float
    recognized_service_years: float
    allowance_service_years: float
    service_cap_years: int
    actual_total_service_years: float


@dataclass
class PensionResult:
    current_age: float
    service_years_to_base: float
    remaining_service_years: float
    years_until_retirement: float
    service: ServiceResult

    pension_start_age: int
    pension_gap_years: float

    implementation_factor: float
    implementation_factor_pct: float
    implementation_factor_source: str
    implementation_factor_lookup_years: float

    avg_rate_2016plus: float
    period1_monthly: float
    period2_monthly: float
    period3_monthly: float
    period3_new_formula_monthly: float
    period3_old_rule_cap_monthly: float
    period3_applied_rule: str

    monthly_pension_today_value_before_adjustment: float
    monthly_pension_today_value: float
    nominal_monthly_pension: float
    real_monthly_pension: float

    lump_allowance_basis_value: float
    lump_allowance_basis_source: str

    nominal_lump_sum: float
    real_lump_sum: float
    p1_lump_sum: float
    post2010_lump_sum: float

    nominal_retirement_allowance: float
    real_retirement_allowance: float
    p1_retirement_allowance: float
    post2010_retirement_allowance: float
    retirement_allowance_rate: float

    total_nominal_value: float
    total_real_value: float


# =========================================================
# 3. 날짜 / 표시 유틸
# =========================================================

def add_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return d.replace(month=2, day=28, year=d.year + years)


def last_day_of_month(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def years_between(start: date, end: date) -> float:
    days = (end - start).days
    return max(days / 365.2425, 0.0)


def month_index(d: date) -> int:
    return d.year * 12 + d.month


def overlap_months(
    start_date: date,
    end_date: date,
    period_start: date,
    period_end: date,
) -> int:
    """
    공단 재직기간 표기와 맞추기 위해 월 단위 포함 방식으로 계산합니다.
    예: 2016.03.01 ~ 2026.05.18 => 123개월
    """
    s = max(month_index(start_date), month_index(period_start))
    e = min(month_index(end_date), month_index(period_end))
    return max(0, e - s + 1)


def year_fraction(d: date) -> float:
    return d.year + ((d.timetuple().tm_yday - 1) / 365.2425)


def get_recommended_retirement_date(job_type: str, birth_date: date) -> date:
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


def get_pension_start_age(entry_date: date, retirement_year: int) -> int:
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


def safe_int(value: Optional[int | float]) -> int:
    if value is None:
        return 0
    try:
        v = int(value)
        return v if v > 0 else 0
    except Exception:
        return 0


def get_accrual_rate(year: int) -> float:
    if year in ACCRUAL_RATE_BY_YEAR:
        return ACCRUAL_RATE_BY_YEAR[year]
    if year < 2016:
        return ACCRUAL_RATE_BY_YEAR[2016]
    return FINAL_ACCRUAL_RATE


def weighted_average_rate(start_year_float: float, end_year_float: float) -> float:
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
            total_rate += get_accrual_rate(year) * weight
            total_weight += weight

        year += 1

    return total_rate / total_weight if total_weight > 0 else 0.0


# =========================================================
# 4. 이행률표 로딩 / 조회
# =========================================================

@st.cache_data
def load_implementation_table(path_str: str, file_mtime: float) -> pd.DataFrame:
    path = Path(path_str)

    required_cols = [
        "old_label",
        "old_min",
        "old_max",
        "new_label",
        "new_min",
        "new_max",
        "factor_pct",
    ]

    if not path.exists():
        return pd.DataFrame(columns=required_cols)

    try:
        df = pd.read_csv(path, encoding="utf-8-sig")
    except UnicodeDecodeError:
        df = pd.read_csv(path, encoding="cp949")

    missing_cols = [col for col in required_cols if col not in df.columns]
    if missing_cols:
        st.error(f"이행률표 CSV에 필요한 컬럼이 없습니다: {', '.join(missing_cols)}")
        return pd.DataFrame(columns=required_cols)

    df["old_label"] = df["old_label"].astype(str).str.strip()
    df["new_label"] = df["new_label"].astype(str).str.strip()

    numeric_cols = ["old_min", "old_max", "new_min", "new_max", "factor_pct"]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    return df.dropna(subset=numeric_cols)


def find_implementation_factor_from_table(
    table: pd.DataFrame,
    entry_date: date,
    before_2010_years: float,
    after_2010_years: float,
    actual_total_service_years: float,
    manual_implementation_factor_pct: Optional[float] = None,
) -> Tuple[float, str, float]:
    if manual_implementation_factor_pct is not None and manual_implementation_factor_pct > 0:
        return manual_implementation_factor_pct / 100, "수동 입력", actual_total_service_years

    if table.empty:
        return 1.0, "이행률표 파일 없음: 기본 100%", actual_total_service_years

    # 2010.1.1 이후 임용자는 신규자열 조회.
    # 공단 화면상 재직기간이 월 단위로 반올림되어 보일 수 있어 실제 날짜 기준 총 재직연수로 조회합니다.
    if entry_date >= date(2010, 1, 1):
        lookup_after = min(max(actual_total_service_years, 0.0), 32.999999)

        candidates = table[
            (table["old_label"] == "신규자")
            & (table["new_min"] <= lookup_after)
            & (lookup_after < table["new_max"])
        ]

        if candidates.empty:
            return 1.0, "신규자열 조회 실패: 기본 100%", lookup_after

        row = candidates.iloc[0]
        return (
            float(row["factor_pct"]) / 100,
            f"이행률표 자동조회: 신규자 / {row['new_label']}",
            lookup_after,
        )

    lookup_before = min(max(before_2010_years, 0.0), 32.999999)
    lookup_after = min(max(after_2010_years, 0.0), 32.999999)

    candidates = table[
        (table["old_label"] != "신규자")
        & (table["old_min"] <= lookup_before)
        & (lookup_before < table["old_max"])
        & (table["new_min"] <= lookup_after)
        & (lookup_after < table["new_max"])
    ]

    if candidates.empty:
        return 1.0, "전체 이행률표 조회 실패: 기본 100%", lookup_after

    row = candidates.iloc[0]
    return (
        float(row["factor_pct"]) / 100,
        f"이행률표 자동조회: 종전 {row['old_label']} / 이후 {row['new_label']}",
        lookup_after,
    )


# =========================================================
# 5. 재직기간 / 연금 계산 로직
# =========================================================

def recognized_service_cap(pre_2016_service_years: float) -> int:
    if pre_2016_service_years >= 21:
        return 33
    if pre_2016_service_years >= 17:
        return 34
    if pre_2016_service_years >= 15:
        return 35
    return 36


def apply_service_cap(raw_y1: float, raw_y2: float, raw_y3: float, cap_years: int) -> Tuple[float, float, float]:
    remaining = float(cap_years)

    y1 = min(raw_y1, remaining)
    remaining -= y1

    y2 = min(raw_y2, max(0.0, remaining))
    remaining -= y2

    y3 = min(raw_y3, max(0.0, remaining))

    return y1, y2, y3


def deduct_from_recent_periods(y1: float, y2: float, y3: float, deduction_months: int) -> Tuple[float, float, float]:
    deduction_years = max(0, deduction_months) / 12

    y3_adj = max(0.0, y3 - deduction_years)
    remaining = max(0.0, deduction_years - y3)

    y2_adj = max(0.0, y2 - remaining)
    remaining = max(0.0, remaining - y2)

    y1_adj = max(0.0, y1 - remaining)

    return y1_adj, y2_adj, y3_adj


def calculate_service(inputs: UserInputs) -> ServiceResult:
    p1_start = date(1970, 1, 1)
    p1_end = date(2009, 12, 31)
    p2_start = date(2010, 1, 1)
    p2_end = date(2015, 12, 31)
    p3_start = date(2016, 1, 1)
    p3_end = date(2100, 12, 31)

    raw_m1 = overlap_months(inputs.appointment_date, inputs.retirement_date, p1_start, p1_end)
    raw_m2 = overlap_months(inputs.appointment_date, inputs.retirement_date, p2_start, p2_end)
    raw_m3 = overlap_months(inputs.appointment_date, inputs.retirement_date, p3_start, p3_end)

    raw_y1 = raw_m1 / 12
    raw_y2 = raw_m2 / 12
    raw_y3 = raw_m3 / 12

    # 퇴직급여 제외기간은 전체 연금/일시금 인정기간에서 차감합니다.
    raw_y1, raw_y2, raw_y3 = deduct_from_recent_periods(
        raw_y1,
        raw_y2,
        raw_y3,
        inputs.retirement_benefit_exclusion_months,
    )

    pre_2016 = raw_y1 + raw_y2
    cap_years = recognized_service_cap(pre_2016)
    y1, y2, y3 = apply_service_cap(raw_y1, raw_y2, raw_y3, cap_years)

    # 퇴직수당은 퇴직급여 인정기간에서 추가 감축개월을 한 번 더 반영합니다.
    allowance_y1, allowance_y2, allowance_y3 = deduct_from_recent_periods(
        y1,
        y2,
        y3,
        inputs.retirement_allowance_extra_exclusion_months,
    )

    actual_total_service_years = years_between(inputs.appointment_date, inputs.retirement_date)

    return ServiceResult(
        raw_y1=raw_y1,
        raw_y2=raw_y2,
        raw_y3=raw_y3,
        y1=y1,
        y2=y2,
        y3=y3,
        allowance_y1=allowance_y1,
        allowance_y2=allowance_y2,
        allowance_y3=allowance_y3,
        pre_2016_years=pre_2016,
        before_2010_years=y1,
        after_2010_years=y2 + y3,
        recognized_service_years=y1 + y2 + y3,
        allowance_service_years=allowance_y1 + allowance_y2 + allowance_y3,
        service_cap_years=cap_years,
        actual_total_service_years=actual_total_service_years,
    )


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


def choose_lump_allowance_basis(inputs: UserInputs, years_until_retirement: float) -> Tuple[float, str]:
    """
    핵심 수정:
    - 오늘 퇴직 검산: 공단 보고서의 2010년 이후 일시금/퇴직수당 적용보수 사용
    - 정년 미래 추정: B값 기준 사용 권장
      노조총연맹 웹 결과와 비교했을 때 미래 퇴직수당·연금일시금은 B값 기준이 더 보수적이고 가까웠음.
    """
    if years_until_retirement <= (31 / 365.2425):
        return (
            inputs.report_post2010_lump_allowance_value,
            "오늘퇴직 검산: 보고서 일시금/퇴직수당 적용보수 자동 사용",
        )

    if inputs.future_lump_basis_mode == LUMP_BASIS_B_VALUE:
        return (
            inputs.report_b_value,
            "정년 미래추정: B값 기준",
        )

    return (
        inputs.report_post2010_lump_allowance_value,
        "미래추정: 보고서 일시금/퇴직수당 적용보수 기준",
    )


def calculate_pension(inputs: UserInputs, implementation_table: pd.DataFrame) -> PensionResult:
    current_age = years_between(inputs.birth_date, inputs.base_date)
    service_years_to_base = years_between(inputs.appointment_date, inputs.base_date)
    remaining_service_years = years_between(inputs.base_date, inputs.retirement_date)
    years_until_retirement = remaining_service_years

    salary_growth = inputs.salary_growth_rate / 100
    inflation = inputs.inflation_rate / 100

    growth_factor = (1 + salary_growth) ** years_until_retirement
    inflation_factor = (1 + inflation) ** years_until_retirement

    service = calculate_service(inputs)

    implementation_factor, implementation_source, implementation_lookup_years = find_implementation_factor_from_table(
        table=implementation_table,
        entry_date=inputs.appointment_date,
        before_2010_years=service.before_2010_years,
        after_2010_years=service.after_2010_years,
        actual_total_service_years=service.actual_total_service_years,
        manual_implementation_factor_pct=inputs.manual_implementation_factor_pct,
    )

    # 1기간 연금
    period1_monthly = 0.0
    if service.y1 > 0:
        if service.y1 >= 20:
            period1_monthly = (
                inputs.report_p1_pension_value * 0.5
                + inputs.report_p1_pension_value * (service.y1 - 20) * 0.02
            )
        else:
            period1_monthly = inputs.report_p1_pension_value * service.y1 * 0.025

    # 2기간 연금
    period2_monthly = 0.0
    if service.y2 > 0:
        period2_monthly = inputs.report_b_value * implementation_factor * service.y2 * PERIOD2_RATE

    # 3기간 연금: 개정산식과 종전규정 비교액 중 낮은 금액 적용
    period3_monthly = 0.0
    period3_new_formula_monthly = 0.0
    period3_old_rule_cap_monthly = 0.0
    period3_applied_rule = "3기간 없음"
    avg_rate_2016plus = 0.0

    if service.y3 > 0:
        period3_start = max(2016.0, year_fraction(inputs.appointment_date))
        period3_end = period3_start + service.y3
        avg_rate_2016plus = weighted_average_rate(period3_start, period3_end)

        pre_2016_recognized_years = service.y1 + service.y2
        years_under_30 = min(service.y3, max(0.0, 30.0 - pre_2016_recognized_years))
        years_over_30 = max(0.0, service.y3 - years_under_30)

        avg_rate_under30 = weighted_average_rate(period3_start, period3_start + years_under_30)
        avg_rate_over30 = weighted_average_rate(period3_start + years_under_30, period3_end)

        redistribution_part = (
            inputs.report_redist_value
            * implementation_factor
            * years_under_30
            * 0.01
        )

        personal_part = (
            inputs.report_b_value
            * implementation_factor
            * years_under_30
            * max(0.0, avg_rate_under30 - 0.01)
        )

        over30_part = (
            inputs.report_b_value
            * implementation_factor
            * years_over_30
            * avg_rate_over30
        )

        period3_new_formula_monthly = redistribution_part + personal_part + over30_part
        period3_old_rule_cap_monthly = (
            inputs.report_b_value
            * implementation_factor
            * service.y3
            * PERIOD2_RATE
        )

        if period3_old_rule_cap_monthly > 0 and period3_new_formula_monthly > period3_old_rule_cap_monthly:
            period3_monthly = period3_old_rule_cap_monthly
            period3_applied_rule = "종전규정 비교액 적용"
        else:
            period3_monthly = period3_new_formula_monthly
            period3_applied_rule = "개정산식 적용"

    monthly_pension_today_value_before_adjustment = period1_monthly + period2_monthly + period3_monthly
    monthly_pension_today_value = (
        monthly_pension_today_value_before_adjustment
        * inputs.monthly_pension_adjustment_factor
    )

    nominal_monthly_pension = monthly_pension_today_value * growth_factor
    real_monthly_pension = nominal_monthly_pension / inflation_factor if inflation_factor > 0 else nominal_monthly_pension

    # 일시금/퇴직수당 기준보수 선택
    lump_allowance_basis_value, lump_allowance_basis_source = choose_lump_allowance_basis(
        inputs=inputs,
        years_until_retirement=years_until_retirement,
    )

    adjusted_lump_allowance_basis_value = (
        lump_allowance_basis_value * inputs.lump_allowance_adjustment_factor
    )

    # 연금일시금
    total_service_for_lump = service.recognized_service_years
    p1_lump_multiplier = 1.5 + max(0.0, total_service_for_lump - 5.0) * 0.01
    post2010_lump_multiplier = 0.975 + max(0.0, total_service_for_lump - 5.0) * 0.0065

    p1_lump_sum = inputs.report_p1_lump_value * service.y1 * p1_lump_multiplier
    post2010_lump_sum = (
        adjusted_lump_allowance_basis_value
        * (service.y2 + service.y3)
        * post2010_lump_multiplier
    )

    lump_sum_today_value = p1_lump_sum + post2010_lump_sum
    nominal_lump_sum = lump_sum_today_value * growth_factor
    real_lump_sum = nominal_lump_sum / inflation_factor if inflation_factor > 0 else nominal_lump_sum

    # 퇴직수당
    allowance_rate = retirement_allowance_rate(service.allowance_service_years)

    p1_retirement_allowance = inputs.report_p1_allowance_value * service.allowance_y1 * 0.60
    post2010_retirement_allowance = (
        adjusted_lump_allowance_basis_value
        * (service.allowance_y2 + service.allowance_y3)
        * allowance_rate
    )

    retirement_allowance_today_value = p1_retirement_allowance + post2010_retirement_allowance
    nominal_retirement_allowance = retirement_allowance_today_value * growth_factor
    real_retirement_allowance = nominal_retirement_allowance / inflation_factor if inflation_factor > 0 else nominal_retirement_allowance

    # 연금 25년 수령 가정 총액
    pension_25y_nominal_value = nominal_monthly_pension * 12 * 25
    pension_25y_real_value = real_monthly_pension * 12 * 25

    total_nominal_value = pension_25y_nominal_value + nominal_lump_sum + nominal_retirement_allowance
    total_real_value = pension_25y_real_value + real_lump_sum + real_retirement_allowance

    pension_start_age = get_pension_start_age(inputs.appointment_date, inputs.retirement_date.year)
    retirement_age_est = years_between(inputs.birth_date, inputs.retirement_date)
    pension_gap_years = max(0.0, pension_start_age - retirement_age_est)

    return PensionResult(
        current_age=current_age,
        service_years_to_base=service_years_to_base,
        remaining_service_years=remaining_service_years,
        years_until_retirement=years_until_retirement,
        service=service,
        pension_start_age=pension_start_age,
        pension_gap_years=pension_gap_years,
        implementation_factor=implementation_factor,
        implementation_factor_pct=implementation_factor * 100,
        implementation_factor_source=implementation_source,
        implementation_factor_lookup_years=implementation_lookup_years,
        avg_rate_2016plus=avg_rate_2016plus,
        period1_monthly=period1_monthly,
        period2_monthly=period2_monthly,
        period3_monthly=period3_monthly,
        period3_new_formula_monthly=period3_new_formula_monthly,
        period3_old_rule_cap_monthly=period3_old_rule_cap_monthly,
        period3_applied_rule=period3_applied_rule,
        monthly_pension_today_value_before_adjustment=monthly_pension_today_value_before_adjustment,
        monthly_pension_today_value=monthly_pension_today_value,
        nominal_monthly_pension=nominal_monthly_pension,
        real_monthly_pension=real_monthly_pension,
        lump_allowance_basis_value=adjusted_lump_allowance_basis_value,
        lump_allowance_basis_source=lump_allowance_basis_source,
        nominal_lump_sum=nominal_lump_sum,
        real_lump_sum=real_lump_sum,
        p1_lump_sum=p1_lump_sum,
        post2010_lump_sum=post2010_lump_sum,
        nominal_retirement_allowance=nominal_retirement_allowance,
        real_retirement_allowance=real_retirement_allowance,
        p1_retirement_allowance=p1_retirement_allowance,
        post2010_retirement_allowance=post2010_retirement_allowance,
        retirement_allowance_rate=allowance_rate,
        total_nominal_value=total_nominal_value,
        total_real_value=total_real_value,
    )


# =========================================================
# 6. 화면 구성
# =========================================================

def render_title() -> None:
    st.title("📊 공무원연금 예상 계산기")
    st.caption(
        "퇴직예정일과 공무원연금공단 예상퇴직급여 조회서의 적용보수 값을 바탕으로 "
        "미래 연금 규모를 가늠해보는 교육용 계산기입니다."
    )

    with st.expander("⚠️ 사용 전 꼭 읽어주세요", expanded=False):
        st.markdown(
            """
            - 이 앱은 **공무원연금공단 공식 계산기가 아닙니다.**
            - 현재 일반기여금은 입력받지 않습니다.
            - 공단 예상퇴직급여 조회서에 표시된 **적용보수값**을 직접 입력하는 방식입니다.
            - 오늘 퇴직 기준 검산을 할 때는 `현재 기준일`과 `퇴직예정일`을 보고서 조회일과 맞춰주세요.
            - 정년 미래 추정 시 퇴직수당·연금일시금은 기본적으로 **B값 기준**으로 계산합니다.
            - 결과는 **노후 준비 규모를 감 잡기 위한 참고값**으로만 사용해 주세요.
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

    st.sidebar.caption(
        f"선택한 구분 기준 자동 제안 퇴직일: {suggested_retirement_date.strftime('%Y-%m-%d')}"
    )

    use_custom_retirement_date = st.sidebar.toggle(
        "퇴직예정일 직접 설정",
        value=False,
        help=(
            "끄면 교원/일반직 공무원 구분과 생년월일을 기준으로 퇴직예정일을 자동 설정합니다. "
            "켜면 퇴직예정일을 직접 수정할 수 있습니다."
        ),
    )

    if use_custom_retirement_date:
        retirement_date = st.sidebar.date_input(
            "퇴직예정일",
            value=base_date,
            min_value=date(1980, 1, 1),
            max_value=date(2100, 12, 31),
            help="공단 예상퇴직급여 조회서와 비교할 때는 보고서의 퇴직예정일과 맞춰 입력하세요.",
        )
    else:
        retirement_date = suggested_retirement_date
        st.sidebar.success(
            f"퇴직예정일 자동 적용: {retirement_date.strftime('%Y-%m-%d')}"
        )

    st.sidebar.header("2. 공단 조회서 적용보수 입력")
    st.sidebar.caption("퇴직급여 예상보고서의 적용보수 표를 보고 입력합니다.")

    report_b_value = st.sidebar.number_input(
        "개인 평균 기준소득월액 (B값)",
        min_value=0,
        value=0,
        step=10_000,
        help="퇴직연금액 예상보고서의 개인 평균 기준소득월액입니다.",
    )

    report_redist_value = st.sidebar.number_input(
        "2016년 이후 소득재분배 반영 기준소득월액",
        min_value=0,
        value=0,
        step=10_000,
        help="2016년 이후 기간의 연금 산정에 쓰이는 소득재분배 반영 기준소득월액입니다.",
    )

    report_post2010_lump_allowance_value = st.sidebar.number_input(
        "2010.1.1 이후기간 <Ⅱ·Ⅲ기간> - 일시금/퇴직수당 칸 금액",
        min_value=0,
        value=0,
        step=10_000,
        help=(
            "적용보수 표에서 2010.1.1 이후기간 <Ⅱ·Ⅲ기간> 아래 일시금 또는 퇴직수당 칸 금액입니다. "
            "퇴직급여 계산액 표의 퇴직연금일시금 총액을 넣는 칸이 아닙니다."
        ),
    )

    st.sidebar.markdown("---")
    st.sidebar.caption("2009.12.31 이전기간 <Ⅰ기간>은 보고서 순서대로 입력합니다.")

    report_p1_lump_value = st.sidebar.number_input(
        "2009.12.31 이전기간 <Ⅰ기간> - 일시금 칸 금액",
        min_value=0,
        value=0,
        step=10_000,
        help="해당 기간이 없으면 0원으로 둡니다.",
    )

    report_p1_allowance_value = st.sidebar.number_input(
        "2009.12.31 이전기간 <Ⅰ기간> - 퇴직수당 칸 금액",
        min_value=0,
        value=0,
        step=10_000,
        help="해당 기간이 없으면 0원으로 둡니다.",
    )

    report_p1_pension_value = st.sidebar.number_input(
        "2009.12.31 이전기간 <Ⅰ기간> - 연금 칸 금액",
        min_value=0,
        value=0,
        step=10_000,
        help="해당 기간이 없으면 0원으로 둡니다.",
    )

    st.sidebar.header("3. 인정기간 / 이행률 설정")

    retirement_benefit_exclusion_months = st.sidebar.number_input(
        "퇴직급여 제외기간(개월)",
        min_value=0,
        value=0,
        step=1,
        help="퇴직급여 재직기간에서 제외되는 기간입니다. 공단 보고서의 퇴직급여 재직기간과 맞추기 위한 보정값입니다.",
    )

    retirement_allowance_extra_exclusion_months = st.sidebar.number_input(
        "퇴직수당 추가 제외기간(개월)",
        min_value=0,
        value=0,
        step=1,
        help="퇴직급여 재직기간보다 퇴직수당 재직기간이 짧을 때 그 차이를 입력합니다. 예: 퇴직급여 123개월, 퇴직수당 121개월이면 2개월",
    )

    use_manual_implementation_factor = st.sidebar.toggle(
        "이행률 직접 입력",
        value=False,
        help="기본값은 implementation_factor_table.csv 자동 조회입니다. 공단값과 맞춰보고 싶을 때 직접 입력하세요.",
    )

    manual_implementation_factor_pct = None
    if use_manual_implementation_factor:
        manual_implementation_factor_pct = st.sidebar.number_input(
            "재직기간별 적용비율(이행률, %)",
            min_value=50.0,
            max_value=120.0,
            value=100.0,
            step=0.01,
            help="예: 82.89를 입력하면 0.8289배로 계산합니다.",
        )

    st.sidebar.header("4. 미래추정 보정 / 가정값")

    future_lump_basis_mode = st.sidebar.selectbox(
        "정년 미래 일시금·퇴직수당 기준보수",
        [LUMP_BASIS_B_VALUE, LUMP_BASIS_REPORT_VALUE],
        index=0,
        help=(
            "오늘 퇴직 검산 시에는 자동으로 보고서 일시금/퇴직수당 적용보수를 사용합니다. "
            "정년 미래 추정은 B값 기준이 더 보수적이고 노조총연맹 웹 계산 결과와 가까웠습니다."
        ),
    )

    with st.sidebar.expander("개인보정계수(선택)", expanded=False):
        st.caption(
            "다른 계산기 결과와 맞춰보고 싶을 때만 조정합니다. 기본값 1.000을 권장합니다."
        )

        monthly_pension_adjustment_factor = st.number_input(
            "월연금 개인보정계수",
            min_value=0.80,
            max_value=1.20,
            value=1.000,
            step=0.005,
            format="%.3f",
            help="예: 1.045를 입력하면 월연금 추정액이 4.5% 증가합니다.",
        )

        lump_allowance_adjustment_factor = st.number_input(
            "일시금·퇴직수당 개인보정계수",
            min_value=0.80,
            max_value=1.20,
            value=1.000,
            step=0.005,
            format="%.3f",
            help="일시금·퇴직수당 미래 추정액을 보정합니다.",
        )

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

    return UserInputs(
        job_type=job_type,
        birth_date=birth_date,
        appointment_date=appointment_date,
        base_date=base_date,
        retirement_date=retirement_date,
        salary_growth_rate=float(salary_growth_rate),
        inflation_rate=float(inflation_rate),
        report_b_value=safe_int(report_b_value),
        report_redist_value=safe_int(report_redist_value),
        report_post2010_lump_allowance_value=safe_int(report_post2010_lump_allowance_value),
        report_p1_lump_value=safe_int(report_p1_lump_value),
        report_p1_allowance_value=safe_int(report_p1_allowance_value),
        report_p1_pension_value=safe_int(report_p1_pension_value),
        retirement_benefit_exclusion_months=int(retirement_benefit_exclusion_months),
        retirement_allowance_extra_exclusion_months=int(retirement_allowance_extra_exclusion_months),
        manual_implementation_factor_pct=manual_implementation_factor_pct,
        future_lump_basis_mode=future_lump_basis_mode,
        monthly_pension_adjustment_factor=float(monthly_pension_adjustment_factor),
        lump_allowance_adjustment_factor=float(lump_allowance_adjustment_factor),
    )


def validate_inputs(inputs: UserInputs) -> list[str]:
    errors = []

    if inputs.retirement_date < inputs.base_date:
        errors.append("퇴직예정일은 현재 기준일과 같거나 이후여야 합니다.")

    if inputs.retirement_date <= inputs.appointment_date:
        errors.append("퇴직예정일은 임용일보다 뒤여야 합니다.")

    if inputs.base_date < inputs.appointment_date:
        errors.append("현재 기준일은 임용일 이후여야 합니다.")

    if inputs.report_b_value <= 0:
        errors.append("개인 평균 기준소득월액(B값)을 입력해야 합니다.")

    if inputs.report_redist_value <= 0:
        errors.append("2016년 이후 소득재분배 반영 기준소득월액을 입력해야 합니다.")

    if inputs.report_post2010_lump_allowance_value <= 0:
        errors.append("2010.1.1 이후기간 <Ⅱ·Ⅲ기간> 일시금/퇴직수당 칸 금액을 입력해야 합니다.")

    if inputs.appointment_date <= date(2009, 12, 31):
        if inputs.report_p1_lump_value <= 0:
            errors.append("2009.12.31 이전기간 <Ⅰ기간> 일시금 칸 금액을 입력해야 합니다.")
        if inputs.report_p1_allowance_value <= 0:
            errors.append("2009.12.31 이전기간 <Ⅰ기간> 퇴직수당 칸 금액을 입력해야 합니다.")
        if inputs.report_p1_pension_value <= 0:
            errors.append("2009.12.31 이전기간 <Ⅰ기간> 연금 칸 금액을 입력해야 합니다.")

    return errors


def render_result_panel(result: PensionResult, inputs: UserInputs) -> None:
    st.subheader("💰 퇴직 시 예상 월 연금액")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("월 연금 (물가할인 현재가치)", won(result.real_monthly_pension))
    c2.metric("월 연금 (퇴직 시 명목가치)", won(result.nominal_monthly_pension))
    c3.metric("총 인정 재직기간", f"{result.service.recognized_service_years:.2f}년")
    c4.metric("연금 개시 연령", f"{result.pension_start_age}세 ({result.pension_gap_years:.1f}년 공백)")

    st.caption(
        f"이행률: **{result.implementation_factor_pct:.2f}%** "
        f"({result.implementation_factor_source}, 조회기준 {result.implementation_factor_lookup_years:.2f}년) / "
        f"3기간 적용 방식: **{result.period3_applied_rule}** / "
        f"보수상승률: **{inputs.salary_growth_rate:.1f}%** / "
        f"물가상승률: **{inputs.inflation_rate:.1f}%**"
    )

    if inputs.monthly_pension_adjustment_factor != 1.0:
        st.info(
            f"월연금 개인보정계수 {inputs.monthly_pension_adjustment_factor:.3f}이 적용되었습니다. "
            f"보정 전 월연금 현재가치: {won(result.monthly_pension_today_value_before_adjustment)}"
        )

    st.divider()

    st.subheader("💼 퇴직 시 예상 일시금액 (참고용)")
    st.markdown("퇴직수당과 연금일시금은 공단 공식 산정액이 아니라 적용보수 입력값을 활용한 참고 추정치입니다.")

    d1, d2, d3, d4 = st.columns(4)
    d1.metric("퇴직수당 (현재가치)", won(result.real_retirement_allowance))
    d2.metric("퇴직수당 (명목가치)", won(result.nominal_retirement_allowance))
    d3.metric("연금일시금 (현재가치)", won(result.real_lump_sum))
    d4.metric("연금일시금 (명목가치)", won(result.nominal_lump_sum))

    st.info(
        f"💡 일시금으로 전액 수령 시 총액 [현재가치]: "
        f"{won(result.real_retirement_allowance + result.real_lump_sum)} / "
        f"[명목가치]: {won(result.nominal_retirement_allowance + result.nominal_lump_sum)}"
    )

    st.caption(
        f"일시금·퇴직수당 기준보수: **{won(result.lump_allowance_basis_value)}** "
        f"({result.lump_allowance_basis_source})"
    )

    st.divider()

    left, right = st.columns([1, 1])

    with left:
        st.subheader("📊 적용된 기준 소득")
        income_rows = [
            {"적용 구간": "1기간 연금용", "기준 소득": won(inputs.report_p1_pension_value)},
            {"적용 구간": "2기간 연금용 B값", "기준 소득": won(inputs.report_b_value)},
            {"적용 구간": "3기간 연금용 소득재분배값", "기준 소득": won(inputs.report_redist_value)},
            {"적용 구간": "보고서 2010년 이후 일시금/퇴직수당", "기준 소득": won(inputs.report_post2010_lump_allowance_value)},
            {"적용 구간": "실제 미래 일시금/퇴직수당 기준보수", "기준 소득": won(result.lump_allowance_basis_value)},
        ]
        st.dataframe(income_rows, use_container_width=True, hide_index=True)

        st.subheader("📘 핵심 계산 근거")
        basis_rows = [
            {"항목": "구분", "값": inputs.job_type},
            {"항목": "생년월일", "값": inputs.birth_date.strftime("%Y-%m-%d")},
            {"항목": "현재 기준일", "값": inputs.base_date.strftime("%Y-%m-%d")},
            {"항목": "임용일", "값": inputs.appointment_date.strftime("%Y-%m-%d")},
            {"항목": "퇴직예정일", "값": inputs.retirement_date.strftime("%Y-%m-%d")},
            {"항목": "현재 나이", "값": f"{result.current_age:.2f}세"},
            {"항목": "현재까지 재직기간", "값": f"{result.service_years_to_base:.2f}년"},
            {"항목": "퇴직까지 남은 기간", "값": f"{result.remaining_service_years:.2f}년"},
            {"항목": "퇴직급여 제외기간", "값": f"{inputs.retirement_benefit_exclusion_months}개월"},
            {"항목": "퇴직수당 추가 제외기간", "값": f"{inputs.retirement_allowance_extra_exclusion_months}개월"},
            {"항목": "총 인정 재직기간", "값": f"{result.service.recognized_service_years:.2f}년"},
            {"항목": "퇴직수당 인정 재직기간", "값": f"{result.service.allowance_service_years:.2f}년"},
            {"항목": "재직기간 상한", "값": f"{result.service.service_cap_years}년"},
            {"항목": "이행률", "값": f"{result.implementation_factor_pct:.2f}%"},
            {"항목": "2016년 이후 지급률 가중평균", "값": percent(result.avg_rate_2016plus)},
            {"항목": "퇴직수당 지급비율", "값": percent(result.retirement_allowance_rate)},
        ]
        st.dataframe(basis_rows, use_container_width=True, hide_index=True)

    with right:
        st.subheader("📈 산출 내역")
        growth_factor = (1 + inputs.salary_growth_rate / 100) ** result.years_until_retirement

        pension_rows = [
            {
                "구분": "1기간 월연금",
                "퇴직 시 명목금액": won(result.period1_monthly * growth_factor),
                "현재가치": won(result.period1_monthly),
            },
            {
                "구분": "2기간 월연금",
                "퇴직 시 명목금액": won(result.period2_monthly * growth_factor),
                "현재가치": won(result.period2_monthly),
            },
            {
                "구분": "3기간 월연금",
                "퇴직 시 명목금액": won(result.period3_monthly * growth_factor),
                "현재가치": won(result.period3_monthly),
            },
            {
                "구분": "월 연금 합계",
                "퇴직 시 명목금액": won(result.nominal_monthly_pension),
                "현재가치": won(result.real_monthly_pension),
            },
            {
                "구분": "연금일시금",
                "퇴직 시 명목금액": won(result.nominal_lump_sum),
                "현재가치": won(result.real_lump_sum),
            },
            {
                "구분": "퇴직수당",
                "퇴직 시 명목금액": won(result.nominal_retirement_allowance),
                "현재가치": won(result.real_retirement_allowance),
            },
        ]
        st.dataframe(pension_rows, use_container_width=True, hide_index=True)

        st.subheader("🧾 3기간 비교")
        period3_rows = [
            {"항목": "개정산식 계산액", "금액": won(result.period3_new_formula_monthly)},
            {"항목": "종전규정 비교액", "금액": won(result.period3_old_rule_cap_monthly)},
            {"항목": "최종 적용액", "금액": won(result.period3_monthly)},
            {"항목": "적용 방식", "금액": result.period3_applied_rule},
        ]
        st.dataframe(period3_rows, use_container_width=True, hide_index=True)


def render_report_input_panel(inputs: UserInputs, result: PensionResult) -> None:
    st.subheader("① 퇴직급여 예상보고서 입력값")
    rows = [
        {"구분": "개인 평균 기준소득월액 B값", "입력값": won(inputs.report_b_value), "용도": "2기간·개인소득분 연금 계산"},
        {"구분": "2016년 이후 소득재분배 반영 기준소득월액", "입력값": won(inputs.report_redist_value), "용도": "3기간 소득재분배 계산"},
        {"구분": "2010.1.1 이후기간 Ⅱ·Ⅲ기간 일시금/퇴직수당", "입력값": won(inputs.report_post2010_lump_allowance_value), "용도": "오늘퇴직 검산 또는 선택 시 미래 일시금·퇴직수당 계산"},
        {"구분": "Ⅰ기간 일시금", "입력값": won(inputs.report_p1_lump_value), "용도": "2009년 이전 재직자 일시금 계산"},
        {"구분": "Ⅰ기간 퇴직수당", "입력값": won(inputs.report_p1_allowance_value), "용도": "2009년 이전 재직자 퇴직수당 계산"},
        {"구분": "Ⅰ기간 연금", "입력값": won(inputs.report_p1_pension_value), "용도": "2009년 이전 재직자 연금 계산"},
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)

    st.subheader("② 기간별 재직기간")
    service_rows = [
        {"구간": "1기간", "퇴직급여 인정연수": f"{result.service.y1:.2f}년", "퇴직수당 인정연수": f"{result.service.allowance_y1:.2f}년"},
        {"구간": "2기간", "퇴직급여 인정연수": f"{result.service.y2:.2f}년", "퇴직수당 인정연수": f"{result.service.allowance_y2:.2f}년"},
        {"구간": "3기간", "퇴직급여 인정연수": f"{result.service.y3:.2f}년", "퇴직수당 인정연수": f"{result.service.allowance_y3:.2f}년"},
    ]
    st.dataframe(service_rows, use_container_width=True, hide_index=True)

    st.info(
        "현재 일반기여금 입력은 제거했습니다. 이 계산기는 공단 조회서의 적용보수값, 이행률, 인정기간 보정값을 활용해 계산합니다."
    )


def render_interpretation(result: PensionResult) -> None:
    st.subheader("결과 해석")

    st.markdown(
        f"""
        이 계산에서는 퇴직 후 월 연금을 현재가치 기준으로 약 **{manwon(result.real_monthly_pension)}** 정도로 추정했습니다.

        오늘 날짜 퇴직 검산에서는 `보수상승률`과 `물가상승률`이 거의 반영되지 않으므로,  
        공단 보고서와 차이가 난다면 대부분 **이행률표, 제외기간, 재직기간 월수 계산, 3기간 종전규정 비교액** 쪽을 확인해야 합니다.

        정년 미래 추정에서는 퇴직수당·연금일시금 기준값을 무엇으로 보느냐에 따라 차이가 커집니다.  
        기본값은 **B값 기준**으로 두었고, 이는 미래 일시금·퇴직수당을 더 보수적으로 추정하기 위한 설정입니다.
        """
    )

    if result.real_monthly_pension < 2_000_000:
        st.warning("현재가치 기준 월 연금 추정치가 200만 원 미만입니다. 개인연금이나 투자자산 보완 필요성이 클 수 있습니다.")
    elif result.real_monthly_pension < 3_500_000:
        st.info("현재가치 기준으로 기본 생활비의 한 축은 될 수 있지만, 여유 있는 노후를 위해서는 추가 현금흐름이 필요할 수 있습니다.")
    else:
        st.success("현재가치 기준 월 연금 추정치가 비교적 높은 편입니다. 다만 실제 생활비와 가구 기준 지출을 함께 확인하는 것이 좋습니다.")


def render_notice() -> None:
    st.markdown(
        """
        ## 계산기 사용 시 주의사항

        이 앱은 **정확한 공단 산식 복제용**이 아니라, 선생님들이 노후 준비 규모를 감 잡기 위한 **교육용 추정 계산기**입니다.

        이번 버전에서는 현재 일반기여금 입력을 제거했습니다.  
        대신 공무원연금공단의 **퇴직급여 예상보고서 적용보수값**을 직접 입력하게 했습니다.

        특히 아래 항목은 실제 공단 계산과 차이가 날 수 있습니다.

        - 기간별 기준소득월액 산정 방식
        - 2010년 이전, 2010년 이후, 2016년 이후 기간별 산식
        - 소득재분배 반영 방식
        - 기준소득월액 상한 및 전체 공무원 평균값
        - 퇴직수당 세부 산식
        - 연금 지급개시연령 및 실제 수령 시점
        - 개인별 휴직, 군경력, 추가 산입 기간 등

        오늘 날짜 퇴직 기준으로 공단 보고서와 맞춰볼 때는 다음을 확인하세요.

        1. 현재 기준일과 퇴직예정일을 보고서 조회일과 맞췄는지
        2. B값, 소득재분배값, 일시금/퇴직수당 적용보수를 보고서와 똑같이 넣었는지
        3. 퇴직급여 재직기간과 퇴직수당 재직기간 차이를 제외기간에 넣었는지
        4. `implementation_factor_table.csv`가 같은 폴더에 있는지 또는 이행률을 직접 입력했는지
        5. 3기간 적용 방식이 `종전규정 비교액 적용`으로 잡히는지

        정년 미래 추정에서 노조총연맹 웹 계산 결과와 비교할 때는 다음을 확인하세요.

        - 실질임금상승률 0% 시나리오와 비교하려면 보수상승률과 물가상승률을 같게 입력합니다.
        - 퇴직수당·연금일시금이 과대계산되면 `정년 미래 일시금·퇴직수당 기준보수`를 **B값 기준**으로 둡니다.
        - 월연금 차이를 맞춰보고 싶다면 `월연금 개인보정계수`를 조정합니다.
        """
    )


# =========================================================
# 7. 메인 실행
# =========================================================

def main() -> None:
    render_title()

    file_mtime = IMPLEMENTATION_TABLE_PATH.stat().st_mtime if IMPLEMENTATION_TABLE_PATH.exists() else 0
    implementation_table = load_implementation_table(str(IMPLEMENTATION_TABLE_PATH), file_mtime)

    if implementation_table.empty:
        st.warning(
            "`implementation_factor_table.csv` 파일을 찾지 못했거나 읽지 못했습니다. "
            "이행률은 기본 100%로 계산됩니다. 공단값과 맞추려면 이행률 직접 입력을 사용하세요."
        )

    inputs = render_sidebar()

    errors = validate_inputs(inputs)
    if errors:
        st.warning("왼쪽 사이드바에서 아래 항목을 입력하면 계산이 시작됩니다.")
        for error in errors:
            st.markdown(f"- {error}")
        st.stop()

    result = calculate_pension(inputs, implementation_table)

    tab1, tab2, tab3 = st.tabs(["계산 결과", "보고서 입력값 확인", "주의사항"])

    with tab1:
        render_result_panel(result, inputs)
        render_interpretation(result)

    with tab2:
        render_report_input_panel(inputs, result)

    with tab3:
        render_notice()


if __name__ == "__main__":
    main()
