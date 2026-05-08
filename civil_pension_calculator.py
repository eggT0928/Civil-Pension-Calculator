from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st

# =====================================
# 기본 설정
# =====================================
st.set_page_config(
    page_title="공무원연금 시뮬레이터",
    page_icon="🏛️",
    layout="wide",
)

CURRENT_DATE = date.today()
CURRENT_YEAR = CURRENT_DATE.year

CONTRIBUTION_RATE = 0.09
DEFAULT_SALARY_GROWTH = 0.025
DEFAULT_INFLATION = 0.020
DEFAULT_PERIOD2_RATE = 0.019

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

OFFICIAL_A_VALUES = {
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
    2026: 5950000,
}

A_VALUE_PERIODS = [
    (date(2025, 5, 1), date(2026, 4, 30), 5710000),
    (date(2026, 5, 1), date(2027, 4, 30), 5950000),
]

GEPS_HOME_URL = "https://www.geps.or.kr/index"
GEPS_ESTIMATE_GUIDE_TEXT = "공무원연금공단 홈페이지 → 연금복지포털 → 로그인 → 나의 연금예상액 → 상세보기"

BASE_DIR = Path(__file__).resolve().parent
IMPLEMENTATION_TABLE_PATH = BASE_DIR / "implementation_factor_table.csv"


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

    exact_p1_pension_value: float
    exact_p1_lump_value: float
    exact_p1_allowance_value: float
    exact_post2010_lump_allowance_value: float

    retirement_basis: str
    manual_implementation_factor_pct: Optional[float]
    retirement_allowance_deduction_months: int


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

    actual_total_service_years: float
    pre_2016_service_years: float
    before_2010_service_years: float
    after_2010_service_years: float
    service_cap_years: int
    recognized_service_years: float

    raw_y1: float
    raw_y2: float
    raw_y3: float
    y1: float
    y2: float
    y3: float

    implementation_factor: float
    implementation_factor_pct: float
    implementation_factor_source: str
    implementation_factor_lookup_years: float

    base_p1_income: float
    base_p2_income: float
    base_p3_income: float
    avg_rate_2016plus: float

    monthly_pension_real: float
    monthly_pension_nominal: float

    period1_monthly: float
    period2_monthly: float
    period3_monthly: float

    period3_redistribution_monthly: float
    period3_personal_monthly: float
    period3_over30_monthly: float
    period3_new_formula_monthly: float
    period3_old_rule_cap_monthly: float
    period3_applied_rule: str

    retirement_allowance_real: float
    retirement_allowance_nominal: float
    retirement_allowance_service_years: float
    retirement_allowance_deduction_months: int
    retirement_allowance_rate_pct: float
    p1_retirement_allowance_real: float
    post2010_retirement_allowance_real: float

    pension_lump_sum_real: float
    pension_lump_sum_nominal: float
    p1_lump_sum_real: float
    post2010_lump_sum_real: float


# =====================================
# 이행률표 로딩 / 조회
# =====================================
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
) -> tuple[float, str, float]:
    """
    이행률표 조회.

    중요 수정:
    - 2010.1.1 이후 임용자(신규자)는 월 단위 인정기간이 아니라
      실제 날짜 기준 총 재직연수(actual_total_service_years)로 이행률 구간을 조회합니다.
    - 공단 화면상 재직기간은 12년 0월처럼 표시되어도,
      실제 날짜상 만 12년 미만이면 11년 이상~12년 미만 구간을 적용하는 케이스가 있습니다.
    """
    if manual_implementation_factor_pct is not None and manual_implementation_factor_pct > 0:
        return manual_implementation_factor_pct / 100, "수동 입력", actual_total_service_years

    if table.empty:
        return 1.0, "이행률표 파일 없음: 기본 100%", actual_total_service_years

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

    lookup_after = min(max(after_2010_years, 0.0), 32.999999)
    lookup_before = min(max(before_2010_years, 0.0), 32.999999)

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


# =====================================
# 유틸
# =====================================
def won(value: float) -> str:
    return f"{int(round(value)):,}원"


def pct(value: float) -> str:
    return f"{value:.2f}%"


def years_between(start_date: date, end_date: date) -> float:
    if end_date <= start_date:
        return 0.0
    return (end_date - start_date).days / 365.2425


def year_fraction(d: date) -> float:
    return d.year + ((d.timetuple().tm_yday - 1) / 365.2425)


def month_index(d: date) -> int:
    return d.year * 12 + d.month


def overlap_months(
    start_date: date,
    end_date: date,
    period_start: date,
    period_end: date,
) -> int:
    s = max(month_index(start_date), month_index(period_start))
    e = min(month_index(end_date), month_index(period_end))
    return max(0, e - s + 1)


def get_current_a_value(target_date: date) -> int:
    for start, end, value in A_VALUE_PERIODS:
        if start <= target_date <= end:
            return value

    if target_date > A_VALUE_PERIODS[-1][1]:
        return A_VALUE_PERIODS[-1][2]

    return OFFICIAL_A_VALUES.get(target_date.year, 5710000)


def get_default_retirement_date(current_age: int, retirement_basis: str) -> date:
    retirement_age = 60 if "60세" in retirement_basis else 62
    years_left = max(0, retirement_age - current_age)
    retire_year = CURRENT_YEAR + years_left

    if "교원" in retirement_basis:
        return date(retire_year, 3, 1) - timedelta(days=1)

    return date(retire_year, 12, 31)


def pension_rate_for_year(year: int) -> float:
    if year in PENSION_RATES:
        return PENSION_RATES[year]
    if year < 2016:
        return 1.9
    return 1.7


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
            total_rate += pension_rate_for_year(year) * weight
            total_weight += weight

        year += 1

    return total_rate / total_weight if total_weight > 0 else 0.0


def recognized_service_cap(pre_2016_service_years: float) -> int:
    if pre_2016_service_years >= 21:
        return 33
    if pre_2016_service_years >= 17:
        return 34
    if pre_2016_service_years >= 15:
        return 35
    return 36


def apply_service_cap(raw_y1: float, raw_y2: float, raw_y3: float, cap_years: int):
    remaining = float(cap_years)

    y1 = min(raw_y1, remaining)
    remaining -= y1

    y2 = min(raw_y2, max(0.0, remaining))
    remaining -= y2

    y3 = min(raw_y3, max(0.0, remaining))

    return y1, y2, y3


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


def infer_current_standard_income(current_contribution: int) -> float:
    return current_contribution / CONTRIBUTION_RATE if current_contribution > 0 else 0.0


def estimate_b_and_redist(current_standard_income: float, current_a_value: float):
    est_b_value = current_standard_income * 0.90
    capped_b = min(est_b_value, current_a_value * 1.6)
    est_redist = (current_a_value + capped_b) / 2
    return capped_b, est_redist


def split_allowance_service_years(
    y1: float,
    y2: float,
    y3: float,
    deduction_months: int,
) -> tuple[float, float, float]:
    """
    퇴직수당 재직기간 감축개월을 최근 기간부터 차감합니다.
    3기간 → 2기간 → 1기간 순서로 차감합니다.
    """
    deduction_years = max(0, deduction_months) / 12

    y3_adj = max(0.0, y3 - deduction_years)
    remaining = max(0.0, deduction_years - y3)

    y2_adj = max(0.0, y2 - remaining)
    remaining = max(0.0, remaining - y2)

    y1_adj = max(0.0, y1 - remaining)

    return y1_adj, y2_adj, y3_adj


# =====================================
# 재직기간 계산
# =====================================
def calculate_service_years(entry_date: date, retirement_date: date):
    p1_start = date(1970, 1, 1)
    p1_end = date(2009, 12, 31)
    p2_start = date(2010, 1, 1)
    p2_end = date(2015, 12, 31)
    p3_start = date(2016, 1, 1)
    p3_end = date(2100, 12, 31)

    raw_m1 = overlap_months(entry_date, retirement_date, p1_start, p1_end)
    raw_m2 = overlap_months(entry_date, retirement_date, p2_start, p2_end)
    raw_m3 = overlap_months(entry_date, retirement_date, p3_start, p3_end)

    raw_y1 = raw_m1 / 12
    raw_y2 = raw_m2 / 12
    raw_y3 = raw_m3 / 12

    pre_2016 = raw_y1 + raw_y2
    cap_years = recognized_service_cap(pre_2016)

    y1, y2, y3 = apply_service_cap(raw_y1, raw_y2, raw_y3, cap_years)

    actual_total_service_years = years_between(entry_date, retirement_date)

    return {
        "actual_total_service_years": actual_total_service_years,
        "raw_y1": raw_y1,
        "raw_y2": raw_y2,
        "raw_y3": raw_y3,
        "pre_2016_service_years": pre_2016,
        "before_2010_service_years": y1,
        "after_2010_service_years": y2 + y3,
        "cap_years": cap_years,
        "y1": y1,
        "y2": y2,
        "y3": y3,
        "recognized_service_years": y1 + y2 + y3,
        "actual_start": year_fraction(entry_date),
    }


# =====================================
# 핵심 계산
# =====================================
def calculate_pension(inputs: Inputs, implementation_table: pd.DataFrame) -> Result:
    retirement_year = inputs.retirement_date.year
    years_to_retire = max(0.0, years_between(CURRENT_DATE, inputs.retirement_date))
    retirement_age_est = inputs.current_age + years_to_retire

    service = calculate_service_years(inputs.entry_date, inputs.retirement_date)

    current_standard_income = infer_current_standard_income(inputs.current_contribution)
    current_a_value = get_current_a_value(CURRENT_DATE)
    inferred_b_value, inferred_redist_value = estimate_b_and_redist(
        current_standard_income,
        current_a_value,
    )

    actual_p1_pension_value = (
        inputs.exact_p1_pension_value
        if inputs.use_exact_data and inputs.exact_p1_pension_value > 0
        else current_standard_income
    )

    actual_p1_lump_value = (
        inputs.exact_p1_lump_value
        if inputs.use_exact_data and inputs.exact_p1_lump_value > 0
        else actual_p1_pension_value
    )

    actual_p1_allowance_value = (
        inputs.exact_p1_allowance_value
        if inputs.use_exact_data and inputs.exact_p1_allowance_value > 0
        else actual_p1_lump_value
    )

    actual_b_value = (
        inputs.exact_b_value
        if inputs.use_exact_data and inputs.exact_b_value > 0
        else inferred_b_value
    )

    actual_p3_value = (
        inputs.exact_redist_value
        if inputs.use_exact_data and inputs.exact_redist_value > 0
        else inferred_redist_value
    )

    post2010_lump_allowance_value = (
        inputs.exact_post2010_lump_allowance_value
        if inputs.use_exact_data and inputs.exact_post2010_lump_allowance_value > 0
        else current_standard_income
    )

    (
        implementation_factor,
        implementation_factor_source,
        implementation_factor_lookup_years,
    ) = find_implementation_factor_from_table(
        table=implementation_table,
        entry_date=inputs.entry_date,
        before_2010_years=service["before_2010_service_years"],
        after_2010_years=service["after_2010_service_years"],
        actual_total_service_years=service["actual_total_service_years"],
        manual_implementation_factor_pct=inputs.manual_implementation_factor_pct,
    )

    # 1기간 연금
    period1_monthly = 0.0
    if service["y1"] > 0:
        if service["y1"] >= 20:
            period1_monthly = (
                actual_p1_pension_value * 0.5
                + actual_p1_pension_value * (service["y1"] - 20) * 0.02
            )
        else:
            period1_monthly = actual_p1_pension_value * service["y1"] * 0.025

    # 2기간 연금
    period2_monthly = 0.0
    if service["y2"] > 0:
        period2_monthly = actual_b_value * implementation_factor * service["y2"] * inputs.period2_rate

    # 3기간 연금
    period3_monthly = 0.0
    period3_redistribution_monthly = 0.0
    period3_personal_monthly = 0.0
    period3_over30_monthly = 0.0
    period3_new_formula_monthly = 0.0
    period3_old_rule_cap_monthly = 0.0
    period3_applied_rule = "3기간 없음"
    avg_rate_2016plus = 0.0

    if service["y3"] > 0:
        period3_start = max(2016.0, service["actual_start"])
        period3_end = period3_start + service["y3"]
        avg_rate_2016plus = weighted_average_rate(period3_start, period3_end)

        pre_2016_recognized_years = service["y1"] + service["y2"]
        years_under_30 = min(service["y3"], max(0.0, 30.0 - pre_2016_recognized_years))
        years_over_30 = max(0.0, service["y3"] - years_under_30)

        avg_rate_under30 = weighted_average_rate(period3_start, period3_start + years_under_30)
        avg_rate_over30 = weighted_average_rate(period3_start + years_under_30, period3_end)

        period3_redistribution_monthly = (
            actual_p3_value
            * implementation_factor
            * years_under_30
            * 0.01
        )

        period3_personal_monthly = (
            actual_b_value
            * implementation_factor
            * years_under_30
            * max(0.0, avg_rate_under30 - 1.0)
            / 100
        )

        period3_over30_monthly = (
            actual_b_value
            * implementation_factor
            * years_over_30
            * avg_rate_over30
            / 100
        )

        period3_new_formula_monthly = (
            period3_redistribution_monthly
            + period3_personal_monthly
            + period3_over30_monthly
        )

        period3_old_rule_cap_monthly = (
            actual_b_value
            * implementation_factor
            * service["y3"]
            * inputs.period2_rate
        )

        if period3_old_rule_cap_monthly > 0 and period3_new_formula_monthly > period3_old_rule_cap_monthly:
            period3_monthly = period3_old_rule_cap_monthly
            period3_applied_rule = "종전규정 비교액 적용"
        else:
            period3_monthly = period3_new_formula_monthly
            period3_applied_rule = "개정산식 적용"

    monthly_pension_today = period1_monthly + period2_monthly + period3_monthly

    growth_factor = (1 + inputs.salary_growth) ** years_to_retire
    inflation_factor = (1 + inputs.inflation) ** years_to_retire

    monthly_pension_nominal = monthly_pension_today * growth_factor
    monthly_pension_real = monthly_pension_nominal / inflation_factor

    # 퇴직수당: 1기간과 2010년 이후기간 분리 계산
    y1_allowance, y2_allowance, y3_allowance = split_allowance_service_years(
        service["y1"],
        service["y2"],
        service["y3"],
        inputs.retirement_allowance_deduction_months,
    )

    retirement_allowance_service_years = y1_allowance + y2_allowance + y3_allowance
    allowance_rate_after2010 = retirement_allowance_rate(retirement_allowance_service_years)

    p1_retirement_allowance_real = (
        actual_p1_allowance_value
        * y1_allowance
        * 0.60
    )

    post2010_retirement_allowance_real = (
        post2010_lump_allowance_value
        * (y2_allowance + y3_allowance)
        * allowance_rate_after2010
    )

    retirement_allowance_real = p1_retirement_allowance_real + post2010_retirement_allowance_real
    retirement_allowance_nominal = retirement_allowance_real * growth_factor

    # 연금일시금: 1기간과 2010년 이후기간 분리 계산
    total_service_years = service["recognized_service_years"]

    p1_lump_multiplier = 1.5 + max(0.0, total_service_years - 5.0) * 0.01
    post2010_lump_multiplier = 0.975 + max(0.0, total_service_years - 5.0) * 0.0065

    p1_lump_sum_real = (
        actual_p1_lump_value
        * service["y1"]
        * p1_lump_multiplier
    )

    post2010_lump_sum_real = (
        post2010_lump_allowance_value
        * (service["y2"] + service["y3"])
        * post2010_lump_multiplier
    )

    pension_lump_sum_real = p1_lump_sum_real + post2010_lump_sum_real
    pension_lump_sum_nominal = pension_lump_sum_real * growth_factor

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
        actual_total_service_years=service["actual_total_service_years"],
        pre_2016_service_years=service["pre_2016_service_years"],
        before_2010_service_years=service["before_2010_service_years"],
        after_2010_service_years=service["after_2010_service_years"],
        service_cap_years=service["cap_years"],
        recognized_service_years=service["recognized_service_years"],
        raw_y1=service["raw_y1"],
        raw_y2=service["raw_y2"],
        raw_y3=service["raw_y3"],
        y1=service["y1"],
        y2=service["y2"],
        y3=service["y3"],
        implementation_factor=implementation_factor,
        implementation_factor_pct=implementation_factor * 100,
        implementation_factor_source=implementation_factor_source,
        implementation_factor_lookup_years=implementation_factor_lookup_years,
        base_p1_income=actual_p1_pension_value if service["y1"] > 0 else 0.0,
        base_p2_income=actual_b_value if service["y2"] > 0 else 0.0,
        base_p3_income=actual_p3_value if service["y3"] > 0 else 0.0,
        avg_rate_2016plus=avg_rate_2016plus,
        monthly_pension_real=monthly_pension_real,
        monthly_pension_nominal=monthly_pension_nominal,
        period1_monthly=period1_monthly,
        period2_monthly=period2_monthly,
        period3_monthly=period3_monthly,
        period3_redistribution_monthly=period3_redistribution_monthly,
        period3_personal_monthly=period3_personal_monthly,
        period3_over30_monthly=period3_over30_monthly,
        period3_new_formula_monthly=period3_new_formula_monthly,
        period3_old_rule_cap_monthly=period3_old_rule_cap_monthly,
        period3_applied_rule=period3_applied_rule,
        retirement_allowance_real=retirement_allowance_real,
        retirement_allowance_nominal=retirement_allowance_nominal,
        retirement_allowance_service_years=retirement_allowance_service_years,
        retirement_allowance_deduction_months=inputs.retirement_allowance_deduction_months,
        retirement_allowance_rate_pct=allowance_rate_after2010 * 100,
        p1_retirement_allowance_real=p1_retirement_allowance_real,
        post2010_retirement_allowance_real=post2010_retirement_allowance_real,
        pension_lump_sum_real=pension_lump_sum_real,
        pension_lump_sum_nominal=pension_lump_sum_nominal,
        p1_lump_sum_real=p1_lump_sum_real,
        post2010_lump_sum_real=post2010_lump_sum_real,
    )


# =====================================
# 적용보수 입력 가이드
# =====================================
def is_missing_amount(value: Optional[float]) -> bool:
    return value is None or value <= 0


def get_missing_exact_fields(
    entry_date: date,
    exact_b_value: Optional[float],
    exact_redist_value: Optional[float],
    exact_p1_pension_value: Optional[float],
    exact_p1_lump_value: Optional[float],
    exact_p1_allowance_value: Optional[float],
    exact_post2010_lump_allowance_value: Optional[float],
):
    missing = []

    if is_missing_amount(exact_b_value):
        missing.append("개인 평균 기준소득월액(B값)")
    if is_missing_amount(exact_redist_value):
        missing.append("소득재분배 반영 기준소득월액")
    if is_missing_amount(exact_post2010_lump_allowance_value):
        missing.append("2010.1.1 이후기간 <Ⅱ·Ⅲ기간> - 일시금/퇴직수당 칸 금액")

    if entry_date <= date(2009, 12, 31):
        if is_missing_amount(exact_p1_pension_value):
            missing.append("2009.12.31 이전기간 <Ⅰ기간> - 연금 칸 금액")
        if is_missing_amount(exact_p1_lump_value):
            missing.append("2009.12.31 이전기간 <Ⅰ기간> - 일시금 칸 금액")
        if is_missing_amount(exact_p1_allowance_value):
            missing.append("2009.12.31 이전기간 <Ⅰ기간> - 퇴직수당 칸 금액")

    return missing


def render_exact_input_guide(
    entry_date: date,
    exact_b_value: Optional[float],
    exact_redist_value: Optional[float],
    exact_p1_pension_value: Optional[float],
    exact_p1_lump_value: Optional[float],
    exact_p1_allowance_value: Optional[float],
    exact_post2010_lump_allowance_value: Optional[float],
):
    missing = get_missing_exact_fields(
        entry_date,
        exact_b_value,
        exact_redist_value,
        exact_p1_pension_value,
        exact_p1_lump_value,
        exact_p1_allowance_value,
        exact_post2010_lump_allowance_value,
    )

    if not missing:
        return

    st.divider()
    st.subheader("🧭 적용보수 값 입력 가이드")
    st.info(
        "`적용보수 값 사용`을 켠 상태입니다. 아래 설명을 보고 숫자를 직접 입력해주세요. "
        "필요한 값이 모두 입력되면 이 안내는 자동으로 사라집니다."
    )

    guide_df = pd.DataFrame(
        {
            "입력칸": [
                "개인 평균 기준소득월액 (B값)",
                "소득재분배 반영 기준소득월액",
                "2009.12.31 이전기간 <Ⅰ기간> - 연금 칸 금액",
                "2009.12.31 이전기간 <Ⅰ기간> - 일시금 칸 금액",
                "2009.12.31 이전기간 <Ⅰ기간> - 퇴직수당 칸 금액",
                "2010.1.1 이후기간 <Ⅱ·Ⅲ기간> - 일시금/퇴직수당 칸 금액",
            ],
            "서류에서 찾는 항목": [
                "적용보수 표의 '개인 평균 기준소득월액'",
                "적용보수 표의 '2016년 이후 소득재분배 반영 평균 기준소득월액'",
                "적용보수 표에서 Ⅰ기간 아래 '연금' 칸",
                "적용보수 표에서 Ⅰ기간 아래 '일시금' 칸",
                "적용보수 표에서 Ⅰ기간 아래 '퇴직수당' 칸",
                "적용보수 표에서 Ⅱ·Ⅲ기간 아래 '일시금' 또는 '퇴직수당' 칸",
            ],
        }
    )

    st.dataframe(guide_df, use_container_width=True, hide_index=True)
    st.warning(f"아직 입력이 필요한 항목: **{', '.join(missing)}**")
    st.link_button("공무원연금공단 홈페이지 열기", GEPS_HOME_URL)


# =====================================
# UI
# =====================================
file_mtime = IMPLEMENTATION_TABLE_PATH.stat().st_mtime if IMPLEMENTATION_TABLE_PATH.exists() else 0
implementation_table = load_implementation_table(str(IMPLEMENTATION_TABLE_PATH), file_mtime)

st.title("🏛️ 공무원연금 시뮬레이터")
st.markdown(
    "왼쪽 사이드바에 **현재 일반기여금, 현재 나이, 최초임용일**을 입력하면 "
    "예상 공무원연금을 계산합니다. "
    "이번 버전은 전체 이행률표 CSV, 3기간 종전규정 비교상한, 퇴직수당/일시금 적용보수 분리 계산을 반영합니다."
)

if implementation_table.empty:
    st.error(
        "`implementation_factor_table.csv` 파일을 찾지 못했습니다. "
        "이행률은 기본 100%로 계산됩니다."
    )

with st.sidebar:
    st.header("1. 기본 정보 입력")

    retirement_basis = st.radio(
        "기본 정년 기준 선택",
        ["일반공무원 기준 (정년 60세)", "교원 기준 (정년 62세)"],
        index=1,
        help="공무원연금 산식은 동일합니다. 이 선택은 예상 퇴직일 자동 계산용입니다.",
    )

    st.caption("공무원연금 산식은 동일하고, 여기서는 정년 기준만 다르게 적용합니다.")

    current_contribution = st.number_input(
        "현재 매월 납부하는 일반기여금 (원)",
        min_value=0,
        value=None,
        step=1000,
        placeholder="예: 601000",
        help="현재 실제로 내고 있는 일반기여금을 입력하세요.",
    )

    current_age = st.number_input(
        "현재 나이 (세)",
        min_value=20,
        max_value=80,
        value=None,
        step=1,
        placeholder="예: 45",
    )

    entry_date = st.date_input(
        "최초임용일",
        value=None,
        min_value=date(1970, 1, 1),
        max_value=date(2100, 12, 31),
    )

    use_custom_retirement_date = st.toggle("예상 퇴직일 직접 입력", value=False)

    retirement_date = None

    if use_custom_retirement_date:
        default_retirement_date = (
            get_default_retirement_date(int(current_age), retirement_basis)
            if current_age is not None
            else None
        )

        retirement_date = st.date_input(
            "예상 퇴직일",
            value=default_retirement_date,
            min_value=date(2000, 1, 1),
            max_value=date(2100, 12, 31),
        )
    else:
        if current_age is not None:
            retirement_date = get_default_retirement_date(int(current_age), retirement_basis)
            st.caption(f"자동 계산된 예상 퇴직일: **{retirement_date.strftime('%Y-%m-%d')}**")
        else:
            st.caption("현재 나이를 입력하면 예상 퇴직일이 자동 계산됩니다.")

    st.divider()

    st.header("2. 적용보수 직접 입력 (선택)")
    use_exact_data = st.toggle("✅ 적용보수 값 사용", value=False)

    exact_b_value = None
    exact_redist_value = None
    exact_p1_pension_value = None
    exact_p1_lump_value = None
    exact_p1_allowance_value = None
    exact_post2010_lump_allowance_value = None

    if use_exact_data:
        st.caption("공단 예상퇴직급여 내역의 적용보수 표를 보고 숫자를 직접 입력합니다.")

        exact_b_value = st.number_input(
            "개인 평균 기준소득월액 (B값)",
            min_value=0,
            value=None,
            step=10000,
            placeholder="예: 4518107",
        )

        exact_redist_value = st.number_input(
            "소득재분배 반영 기준소득월액",
            min_value=0,
            value=None,
            step=10000,
            placeholder="예: 5486337",
        )

        exact_post2010_lump_allowance_value = st.number_input(
            "2010.1.1 이후기간 <Ⅱ·Ⅲ기간> - 일시금/퇴직수당 칸 금액",
            min_value=0,
            value=None,
            step=10000,
            placeholder="예: 5578769",
            help="적용보수 표에서 2010.1.1 이후기간 <Ⅱ·Ⅲ기간> 아래 '일시금' 또는 '퇴직수당' 칸 금액을 입력합니다.",
        )

        exact_p1_pension_value = st.number_input(
            "2009.12.31 이전기간 <Ⅰ기간> - 연금 칸 금액",
            min_value=0,
            value=None,
            step=10000,
            placeholder="해당 없으면 비워두기",
            help="적용보수 표에서 2009.12.31 이전기간 <Ⅰ기간> 아래 '연금' 칸 금액입니다. 해당 기간이 없으면 비워둡니다.",
        )

        exact_p1_lump_value = st.number_input(
            "2009.12.31 이전기간 <Ⅰ기간> - 일시금 칸 금액",
            min_value=0,
            value=None,
            step=10000,
            placeholder="해당 없으면 비워두기",
            help="퇴직연금일시금 계산용입니다. 해당 기간이 없으면 비워둡니다.",
        )

        exact_p1_allowance_value = st.number_input(
            "2009.12.31 이전기간 <Ⅰ기간> - 퇴직수당 칸 금액",
            min_value=0,
            value=None,
            step=10000,
            placeholder="해당 없으면 비워두기",
            help="퇴직수당 계산용입니다. 해당 기간이 없으면 비워둡니다.",
        )

    st.divider()

    with st.expander("경제 지표 / 고급 설정"):
        salary_growth_pct = st.number_input(
            "미래 연 보수상승률 (%)",
            value=DEFAULT_SALARY_GROWTH * 100,
            step=0.1,
        )

        inflation_pct = st.number_input(
            "미래 연 물가상승률 (%)",
            value=DEFAULT_INFLATION * 100,
            step=0.1,
        )

        period2_rate_pct = st.number_input(
            "2기간 지급률 / 종전규정 비교 지급률 (%)",
            value=DEFAULT_PERIOD2_RATE * 100,
            step=0.001,
        )

        st.divider()

        retirement_allowance_deduction_months = st.number_input(
            "퇴직수당 재직기간 감축개월",
            min_value=0,
            value=0,
            step=1,
            help=(
                "공단 예상퇴직급여 화면에서 퇴직급여 재직기간과 퇴직수당 재직기간이 다르면 "
                "그 차이를 개월 수로 입력하세요. 예: 퇴직급여 10년3월, 퇴직수당 10년1월 → 2개월"
            ),
        )

        st.caption(
            "군복무휴직, 육아휴직처럼 일반적으로 퇴직수당에서 감축하지 않는 기간은 입력하지 않습니다. "
            "공단 화면의 두 재직기간 차이를 입력하는 방식이 가장 안전합니다."
        )

        st.divider()

        use_manual_implementation_factor = st.toggle(
            "재직기간별 적용비율(이행률) 직접 입력",
            value=False,
            help="기본값은 CSV 표 자동 조회입니다. 공단값과 맞춰보고 싶을 때만 직접 입력하세요.",
        )

        manual_implementation_factor_pct = None

        if use_manual_implementation_factor:
            manual_implementation_factor_pct = st.number_input(
                "재직기간별 적용비율(이행률, %)",
                min_value=60.0,
                max_value=120.0,
                value=100.0,
                step=0.01,
                help="예: 83.70을 입력하면 0.8370배로 계산합니다.",
            )


# =====================================
# 필수 입력 검증
# =====================================
missing_required = []

if current_contribution is None:
    missing_required.append("현재 매월 납부하는 일반기여금")

if current_age is None:
    missing_required.append("현재 나이")

if entry_date is None:
    missing_required.append("최초임용일")

if retirement_date is None:
    missing_required.append("예상 퇴직일")

if missing_required:
    st.info(
        "👈 왼쪽 사이드바에서 아래 항목을 입력하면 연금 계산이 시작됩니다.\n\n"
        + "\n".join([f"- {item}" for item in missing_required])
    )
    st.stop()


if use_exact_data:
    render_exact_input_guide(
        entry_date,
        exact_b_value,
        exact_redist_value,
        exact_p1_pension_value,
        exact_p1_lump_value,
        exact_p1_allowance_value,
        exact_post2010_lump_allowance_value,
    )


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
    exact_p1_pension_value=float(exact_p1_pension_value or 0),
    exact_p1_lump_value=float(exact_p1_lump_value or 0),
    exact_p1_allowance_value=float(exact_p1_allowance_value or 0),
    exact_post2010_lump_allowance_value=float(exact_post2010_lump_allowance_value or 0),
    retirement_basis=retirement_basis,
    manual_implementation_factor_pct=manual_implementation_factor_pct,
    retirement_allowance_deduction_months=int(retirement_allowance_deduction_months),
)

res = calculate_pension(inputs, implementation_table)


# =====================================
# 결과 출력
# =====================================
st.divider()
st.subheader("💰 퇴직 시 예상 월 연금액")

c1, c2, c3, c4 = st.columns(4)
c1.metric(
    "월 연금 (물가할인 현재가치)",
    won(res.monthly_pension_real),
    help="미래 퇴직 시점 명목 연금액을 물가상승률로 할인한 현재 체감가치입니다.",
)
c2.metric(
    "월 연금 (퇴직 시 명목가치)",
    won(res.monthly_pension_nominal),
    help="퇴직 시점 기준 액면 금액입니다. 보수상승률이 반영됩니다.",
)
c3.metric(
    "총 인정 재직기간",
    f"{res.recognized_service_years:.2f}년 (상한 {res.service_cap_years}년)",
)
c4.metric(
    "연금 개시 연령",
    f"{res.pension_start_age}세 ({res.gap_years:.1f}년 공백)",
)

st.caption(
    f"적용 이행률: **{res.implementation_factor_pct:.2f}%** "
    f"({res.implementation_factor_source}, 조회기준 {res.implementation_factor_lookup_years:.2f}년) / "
    f"3기간 적용 방식: **{res.period3_applied_rule}**"
)

st.divider()
st.subheader("💼 퇴직 시 예상 일시금액 (참고용)")

d1, d2, d3, d4 = st.columns(4)
d1.metric("퇴직수당 (현재가치)", won(res.retirement_allowance_real))
d2.metric("퇴직수당 (명목가치)", won(res.retirement_allowance_nominal))
d3.metric("연금일시금 (현재가치)", won(res.pension_lump_sum_real))
d4.metric("연금일시금 (명목가치)", won(res.pension_lump_sum_nominal))

st.info(
    f"💡 일시금으로 전액 수령 시 총액 [현재가치]: "
    f"{won(res.retirement_allowance_real + res.pension_lump_sum_real)} / "
    f"[명목가치]: {won(res.retirement_allowance_nominal + res.pension_lump_sum_nominal)}"
)

st.divider()
left, right = st.columns([1, 1])

with left:
    st.subheader("📊 적용된 기준 소득")
    income_df = pd.DataFrame(
        {
            "적용 구간": [
                "1기간 연금용",
                "2기간 연금용",
                "3기간 연금용",
            ],
            "기준 소득": [
                won(res.base_p1_income),
                won(res.base_p2_income),
                won(res.base_p3_income),
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
                "전체 공무원 A값",
                "추정 B값(적용보수 미입력 시)",
                "추정 소득재분배 반영값(적용보수 미입력 시)",
                "실제 날짜 기준 총 재직기간",
                "2016.1.1 기준 재직기간",
                "2010년 이전 인정 재직기간",
                "2010년 이후 인정 재직기간",
                "재직기간 상한",
                "적용 이행률",
                "이행률 조회 기준연수",
                "이행률 적용 방식",
                "3기간 적용 방식",
                "퇴직수당 감축개월",
                "퇴직수당 인정 재직기간",
                "퇴직수당 지급비율",
                "예상 퇴직연도",
                "퇴직 시점 나이(추정)",
                "2016년 이후 지급률 가중평균",
            ],
            "값": [
                won(inputs.current_contribution),
                won(res.current_standard_income),
                won(res.current_a_value),
                won(res.inferred_b_value),
                won(res.inferred_redist_value),
                f"{res.actual_total_service_years:.2f}년",
                f"{res.pre_2016_service_years:.2f}년",
                f"{res.before_2010_service_years:.2f}년",
                f"{res.after_2010_service_years:.2f}년",
                f"{res.service_cap_years}년",
                f"{res.implementation_factor_pct:.2f}%",
                f"{res.implementation_factor_lookup_years:.2f}년",
                res.implementation_factor_source,
                res.period3_applied_rule,
                f"{res.retirement_allowance_deduction_months}개월",
                f"{res.retirement_allowance_service_years:.2f}년",
                f"{res.retirement_allowance_rate_pct:.2f}%",
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
            "구간": ["1기간", "2기간", "3기간"],
            "원시 연수": [
                round(res.raw_y1, 2),
                round(res.raw_y2, 2),
                round(res.raw_y3, 2),
            ],
            "상한 반영 연수": [
                round(res.y1, 2),
                round(res.y2, 2),
                round(res.y3, 2),
            ],
            "연금 기여분": [
                won(res.period1_monthly),
                won(res.period2_monthly),
                won(res.period3_monthly),
            ],
        }
    )
    st.dataframe(period_df, use_container_width=True, hide_index=True)

    detail_df = pd.DataFrame(
        {
            "3기간 세부": [
                "개정산식: 소득재분배 1% 부분",
                "개정산식: 개인소득분",
                "개정산식: 30년 초과분",
                "개정산식 합계",
                "종전규정 비교액",
                "최종 적용액",
                "적용 방식",
            ],
            "값": [
                won(res.period3_redistribution_monthly),
                won(res.period3_personal_monthly),
                won(res.period3_over30_monthly),
                won(res.period3_new_formula_monthly),
                won(res.period3_old_rule_cap_monthly),
                won(res.period3_monthly),
                res.period3_applied_rule,
            ],
        }
    )
    st.markdown("##### 3기간 세부 산출")
    st.dataframe(detail_df, use_container_width=True, hide_index=True)

    lump_detail_df = pd.DataFrame(
        {
            "일시금/퇴직수당 세부": [
                "1기간 퇴직수당",
                "2010년 이후 퇴직수당",
                "퇴직수당 합계",
                "1기간 연금일시금",
                "2010년 이후 연금일시금",
                "연금일시금 합계",
            ],
            "금액": [
                won(res.p1_retirement_allowance_real),
                won(res.post2010_retirement_allowance_real),
                won(res.retirement_allowance_real),
                won(res.p1_lump_sum_real),
                won(res.post2010_lump_sum_real),
                won(res.pension_lump_sum_real),
            ],
        }
    )
    st.markdown("##### 일시금/퇴직수당 세부 산출")
    st.dataframe(lump_detail_df, use_container_width=True, hide_index=True)

    chart_df = pd.DataFrame(
        {
            "구간": ["1기간", "2기간", "3기간"],
            "연금 기여분": [
                res.period1_monthly,
                res.period2_monthly,
                res.period3_monthly,
            ],
        }
    ).set_index("구간")
    st.bar_chart(chart_df)


if use_exact_data:
    missing_exact = get_missing_exact_fields(
        entry_date,
        exact_b_value,
        exact_redist_value,
        exact_p1_pension_value,
        exact_p1_lump_value,
        exact_p1_allowance_value,
        exact_post2010_lump_allowance_value,
    )

    if missing_exact:
        st.warning(
            f"⚠️ 적용보수 직접입력 모드입니다. "
            f"아직 입력이 필요한 항목: {', '.join(missing_exact)}"
        )
    else:
        st.success("✅ 적용보수 입력이 완료되어 가이드가 자동으로 숨겨졌습니다.")
else:
    st.info(
        "ℹ️ 현재 기여금 기반 추정 모드입니다. "
        "더 정확히 계산하려면 '적용보수 값 사용'을 켜고 직접 입력하세요."
    )


st.subheader("연금 계산 공식 설명")

formula_df = pd.DataFrame(
    {
        "구간": ["1기간", "2기간", "3기간", "일시금/퇴직수당", "이행률"],
        "의미": [
            "2009.12.31 이전 재직기간",
            "2010.1.1 ~ 2015.12.31 재직기간",
            "2016.1.1 이후 재직기간",
            "퇴직연금일시금 및 퇴직수당",
            "재직기간별 기준소득월액 적용비율",
        ],
        "기본 계산방식": [
            "Ⅰ기간의 연금 칸 금액을 사용",
            "B값 × 이행률 × 연수 × 지급률(기본 1.9%)",
            "개정산식 계산 후 종전규정 비교액과 비교하여 더 낮은 금액 적용",
            "Ⅰ기간과 2010년 이후기간의 일시금/퇴직수당 적용보수를 분리 계산",
            "2010년 이후 임용자는 실제 날짜 기준 총 재직연수로 신규자열 조회",
        ],
    }
)
st.dataframe(formula_df, use_container_width=True, hide_index=True)

st.markdown(
    """
- **1기간**: 2009년 말 이전 재직기간입니다.
- **2기간**: 2010~2015년 재직기간입니다.
- **3기간**: 2016년 이후 재직기간입니다.
- **B값**: 개인 평균 기준소득월액입니다.
- **소득재분배 반영 기준소득월액**: 2016년 이후 구간 계산에 들어가는 보정된 기준 소득입니다.
- **Ⅰ기간 연금 칸 금액**: 2009.12.31 이전기간의 월연금 계산에 사용합니다.
- **Ⅰ기간 일시금/퇴직수당 칸 금액**: 2009.12.31 이전기간의 연금일시금과 퇴직수당 계산에 사용합니다.
- **Ⅱ·Ⅲ기간 일시금/퇴직수당 칸 금액**: 2010.1.1 이후기간의 연금일시금과 퇴직수당 계산에 사용합니다.
- **이행률**: 재직기간별 기준소득월액에 적용하는 비율입니다.
- **이행률 조회 보정**: 2010.1.1 이후 임용자는 월 단위 표시 재직기간이 아니라 실제 날짜 기준 총 재직연수로 신규자열을 조회합니다.
- **3기간 보정**: 개정산식으로 계산한 금액이 종전규정 비교액보다 크면 종전규정 비교액을 적용합니다.
- **퇴직수당 감축개월**: 공단 화면에서 퇴직급여 재직기간과 퇴직수당 재직기간이 다를 때 그 차이만큼 입력합니다.
- 이 버전은 `implementation_factor_table.csv`의 전체 이행률표를 읽어 자동 적용합니다.
"""
)

st.subheader("주의")
st.markdown(
    """
- 이 앱은 **공식 산정액이 아닌 추정용 시뮬레이터**입니다.
- 파일 자동 읽기 기능은 제거하고, **직접 입력 방식으로 단순화**했습니다.
- 이번 버전은 **전체 이행률표 CSV 자동 조회**, **3기간 종전규정 비교상한**, **퇴직수당 재직기간 감축개월**, **일시금/퇴직수당 적용보수 분리 계산**, **신규자 이행률 조회기준 보정**을 반영했습니다.
- 처음 접속 시 기본 개인정보 예시는 넣지 않았습니다.
- `적용보수 값 사용`을 켜면 메인 화면에 입력 가이드가 나타납니다.
- 실제 지급액은 공무원연금공단의 상세 이력, 경과규정, 실제 기준소득월액 데이터 등에 따라 달라질 수 있습니다.
"""
)
