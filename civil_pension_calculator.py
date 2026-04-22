import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

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
DEFAULT_PERIOD2_RATE = 0.019

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
    2026: 5710000,
}

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

@dataclass
class Result:
    retirement_year: int
    years_to_retire: float
    retirement_age_est: float
    pension_start_age: int
    gap_years: float
    current_standard_income: float
    current_a_value: float
    pre_2016_service_years: float
    service_cap_years: int
    recognized_service_years: float
    raw_y1: float; raw_y2: float; raw_y3: float
    y1: float; y2: float; y3: float
    base_p1_income: float; base_p2_income: float; base_p3_income: float
    monthly_pension_pv: float; monthly_pension_fv: float
    period1_monthly_pv: float; period2_monthly_pv: float; period3_monthly_pv: float
    retirement_allowance_pv: float; retirement_allowance_fv: float
    pension_lump_sum_pv: float; pension_lump_sum_fv: float

# =====================================
# 유틸 함수
# =====================================
def won(value: float) -> str:
    return f"{int(round(value)):,}원"

def years_between(start_date: date, end_date: date) -> float:
    if end_date <= start_date: return 0.0
    return (end_date - start_date).days / 365.2425

def year_fraction(d: date) -> float:
    return d.year + ((d.timetuple().tm_yday - 1) / 365.2425)

def get_default_retirement_date(current_age: int, job_type: str) -> date:
    retire_age = 60 if "일반공무원" in job_type else 62
    years_left = max(0, retire_age - current_age)
    retire_year = CURRENT_YEAR + years_left
    if "교원" in job_type:
        return date(retire_year, 3, 1) - timedelta(days=1)
    else:
        return date(retire_year, 12, 31)

def pension_rate_for_year(year: int) -> float:
    if year in PENSION_RATES: return PENSION_RATES[year]
    if year < 2016: return 1.9
    return 1.7

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

def retirement_allowance_rate(total_years: float) -> float:
    if total_years < 1: return 0.0
    if total_years < 5: return 0.065
    if total_years < 10: return 0.2275
    if total_years < 15: return 0.2925
    if total_years < 20: return 0.325
    return 0.39

def get_pension_start_age(entry_date: date, retirement_year: int) -> int:
    if entry_date >= date(1996, 1, 1):
        if retirement_year <= 2021: return 60
        elif retirement_year <= 2023: return 61
        elif retirement_year <= 2026: return 62
        elif retirement_year <= 2029: return 63
        elif retirement_year <= 2032: return 64
        else: return 65
    return 60

# =====================================
# 엑셀/CSV 데이터 초정밀 파싱 로직 
# =====================================
def parse_pension_file(file) -> dict:
    """CSV, XLS 파일에서 셀 구조의 장점을 활용해 데이터를 오차 없이 추출"""
    extracted_data = {}
    try:
        filename = file.name.lower()
        text = ""
        
        # 1. 파일 텍스트화 (공단의 가짜 xls(HTML) 및 CSV 모두 커버하는 범용 디코딩)
        try:
            text = file.getvalue().decode('cp949') # 한국 공공기관 표준 인코딩
        except UnicodeDecodeError:
            try:
                text = file.getvalue().decode('utf-8')
            except Exception:
                # 일반 엑셀 파일일 경우 Pandas로 읽어서 문자열로 변환
                df = pd.read_excel(file)
                text = df.to_string()

        # 2. 정규식 추출 (셀 구분이 쉼표나 탭 등으로 나뉘어 있어 완벽한 매칭 가능)
        
        # 임용일 추출 (예: 2016-03-01, 2016/03/01, 2016.03.01)
        date_match = re.search(r'임용일.*?(\d{4})[./-](\d{2})[./-](\d{2})', text)
        if date_match:
            extracted_data['entry_date'] = date(int(date_match.group(1)), int(date_match.group(2)), int(date_match.group(3)))
            
        # 개인 평균 기준소득월액 (글자 이후 처음 등장하는 6자리 이상 금액)
        b_match = re.search(r'개인\s*평균\s*기준소득월액\D*?([\d,]{6,})', text)
        if b_match:
            val = int(b_match.group(1).replace(',', ''))
            if 1000000 <= val <= 20000000:  # 비정상 데이터 방어
                extracted_data['b_value'] = val
                
        # 소득재분배 반영 기준소득월액
        redist_match = re.search(r'소득재분배\s*반영\D*?([\d,]{6,})', text)
        if redist_match:
            val = int(redist_match.group(1).replace(',', ''))
            if 1000000 <= val <= 20000000:
                extracted_data['redist_value'] = val
                
        # 2009년 이전 평균 보수월액 (해당자만 존재)
        p1_match = re.search(r'2009년\s*이전.*?보수월액\D*?([\d,]{6,})', text)
        if p1_match:
            val = int(p1_match.group(1).replace(',', ''))
            if 1000000 <= val <= 20000000:
                extracted_data['p1_value'] = val

    except Exception as e:
        st.error(f"파일을 분석하는 중 오류가 발생했습니다: {e}")
        
    return extracted_data

# =====================================
# 핵심 계산 로직
# =====================================
def calculate_service_years(entry_date: date, retirement_date: date) -> dict:
    actual_start = year_fraction(entry_date)
    actual_end = year_fraction(retirement_date)

    raw_y1 = max(0.0, min(actual_end, 2010.0) - max(actual_start, 0.0))
    raw_y2 = max(0.0, min(actual_end, 2016.0) - max(actual_start, 2010.0))
    raw_y3 = max(0.0, actual_end - max(actual_start, 2016.0))

    pre_2016 = raw_y1 + raw_y2
    cap_years = recognized_service_cap(pre_2016)
    y1, y2, y3 = apply_service_cap(raw_y1, raw_y2, raw_y3, cap_years)

    return {
        "raw_y1": raw_y1, "raw_y2": raw_y2, "raw_y3": raw_y3,
        "pre_2016_service_years": pre_2016, "cap_years": cap_years,
        "y1": y1, "y2": y2, "y3": y3, "recognized_service_years": y1 + y2 + y3,
        "actual_start": actual_start
    }

def calculate_pension(inputs: Inputs) -> Result:
    retirement_year = inputs.retirement_date.year
    years_to_retire = max(0.0, years_between(CURRENT_DATE, inputs.retirement_date))
    retirement_age_est = inputs.current_age + years_to_retire

    service = calculate_service_years(inputs.entry_date, inputs.retirement_date)

    current_standard_income = inputs.current_contribution / CONTRIBUTION_RATE if inputs.current_contribution > 0 else 0.0
    current_a_value = OFFICIAL_A_VALUES[max(OFFICIAL_A_VALUES.keys())]
    
    est_b_value = current_standard_income * 0.90
    capped_b = min(est_b_value, current_a_value * 1.6)
    est_redist = (current_a_value + capped_b) / 2

    actual_p1_value = inputs.exact_p1_value if (inputs.use_exact_data and inputs.exact_p1_value > 0) else current_standard_income
    actual_b_value = inputs.exact_b_value if (inputs.use_exact_data and inputs.exact_b_value > 0) else capped_b
    actual_p3_value = inputs.exact_redist_value if (inputs.use_exact_data and inputs.exact_redist_value > 0) else est_redist

    period1_monthly_today = 0.0
    if service["y1"] > 0:
        if service["y1"] >= 20: period1_monthly_today = actual_p1_value * 0.5 + actual_p1_value * (service["y1"] - 20) * 0.02
        else: period1_monthly_today = actual_p1_value * service["y1"] * 0.025

    period2_monthly_today = actual_b_value * service["y2"] * inputs.period2_rate if service["y2"] > 0 else 0.0

    period3_monthly_today, avg_rate_2016plus = 0.0, 0.0
    if service["y3"] > 0:
        period3_start = max(2016.0, service["actual_start"])
        avg_rate_2016plus = weighted_average_rate(period3_start, period3_start + service["y3"])
        period3_monthly_today = actual_p3_value * service["y3"] * (avg_rate_2016plus / 100)

    base_pension_today = period1_monthly_today + period2_monthly_today + period3_monthly_today
    monthly_pension_fv = base_pension_today * ((1 + inputs.salary_growth) ** years_to_retire)
    real_monthly_pension_pv = monthly_pension_fv / ((1 + inputs.inflation) ** years_to_retire)

    projected_final_income_fv = current_standard_income * ((1 + inputs.salary_growth) ** years_to_retire)
    allow_rate = retirement_allowance_rate(service["recognized_service_years"])
    
    retirement_allowance_pv = current_standard_income * service["recognized_service_years"] * allow_rate
    retirement_allowance_fv = projected_final_income_fv * service["recognized_service_years"] * allow_rate

    excess_5_years = max(0.0, service["recognized_service_years"] - 5.0)
    lump_sum_multiplier = 0.975 + (excess_5_years * 0.0065)
    
    pension_lump_sum_pv = current_standard_income * service["recognized_service_years"] * lump_sum_multiplier
    pension_lump_sum_fv = projected_final_income_fv * service["recognized_service_years"] * lump_sum_multiplier

    pension_start_age = get_pension_start_age(inputs.entry_date, retirement_year)
    gap_years = max(0.0, pension_start_age - retirement_age_est)

    return Result(
        retirement_year=retirement_year, years_to_retire=years_to_retire, retirement_age_est=retirement_age_est,
        pension_start_age=pension_start_age, gap_years=gap_years, current_standard_income=current_standard_income,
        current_a_value=current_a_value, pre_2016_service_years=service["pre_2016_service_years"],
        service_cap_years=service["cap_years"], recognized_service_years=service["recognized_service_years"],
        raw_y1=service["raw_y1"], raw_y2=service["raw_y2"], raw_y3=service["raw_y3"],
        y1=service["y1"], y2=service["y2"], y3=service["y3"],
        base_p1_income=actual_p1_value if service["y1"] > 0 else 0.0,
        base_p2_income=actual_b_value if service["y2"] > 0 else 0.0,
        base_p3_income=actual_p3_value if service["y3"] > 0 else 0.0,
        period1_monthly_pv=period1_monthly_today, period2_monthly_pv=period2_monthly_today, period3_monthly_pv=period3_monthly_today,
        monthly_pension_pv=real_monthly_pension_pv, monthly_pension_fv=monthly_pension_fv,
        retirement_allowance_pv=retirement_allowance_pv, retirement_allowance_fv=retirement_allowance_fv,
        pension_lump_sum_pv=pension_lump_sum_pv, pension_lump_sum_fv=pension_lump_sum_fv,
    )

# =====================================
# 세션 상태 초기화 
# =====================================
if "doc_entry_date" not in st.session_state: st.session_state.doc_entry_date = None
if "doc_b_value" not in st.session_state: st.session_state.doc_b_value = None
if "doc_redist_value" not in st.session_state: st.session_state.doc_redist_value = None
if "doc_p1_value" not in st.session_state: st.session_state.doc_p1_value = None

# =====================================
# 화면 구성 (UI)
# =====================================
st.title("🏛️ 공무원연금 시뮬레이터 (추정/서류기반 정밀모드)")
st.markdown("본인의 데이터를 직접 입력하거나 **예상퇴직급여내역서 파일(CSV/엑셀)**을 업로드하여 정확도 높은 예상 연금액을 확인하세요.")

with st.sidebar:
    st.header("1. 기본 정보")
    job_type = st.radio("직종 선택", ["일반공무원 (정년 60세)", "교원 (정년 62세)"])
    
    current_contribution = st.number_input("현재 매월 납부하는 일반기여금 (원)", min_value=0, value=None, step=1000, placeholder="예: 396500")
    current_age = st.number_input("현재 나이 (세)", min_value=20, max_value=80, value=None, placeholder="예: 33")
    entry_date = st.date_input("최초임용일", value=st.session_state.doc_entry_date, min_value=date(1970, 1, 1), max_value=date(2100, 12, 31))
    
    retirement_date_input = st.date_input(
        "예상 퇴직일 (선택)", value=None, min_value=date(2000, 1, 1), max_value=date(2100, 12, 31), 
        help="비워두시면 선택하신 직종의 정년을 기준으로 자동 계산됩니다."
    )

    st.divider()
    st.header("2. 공단 서류 자동 입력 (선택)")
    
    # 💡 파일 업로더 확장자 변경 (CSV, XLS, XLSX)
    uploaded_file = st.file_uploader("📂 예상퇴직급여내역서 엑셀/CSV 파일 업로드", type=["csv", "xls", "xlsx"], help="공단 홈페이지에서 다운받은 파일을 올리면 수치가 자동 입력됩니다.")
    
    if uploaded_file is not None:
        extracted = parse_pension_file(uploaded_file)
        if extracted:
            st.success("✅ 파일 분석 성공! 데이터를 아래 입력칸에 자동으로 채웠습니다.")
            st.session_state.doc_entry_date = extracted.get('entry_date', st.session_state.doc_entry_date)
            st.session_state.doc_b_value = extracted.get('b_value', st.session_state.doc_b_value)
            st.session_state.doc_redist_value = extracted.get('redist_value', st.session_state.doc_redist_value)
            st.session_state.doc_p1_value = extracted.get('p1_value', st.session_state.doc_p1_value)
        else:
            st.error("❌ 파일에서 필요 데이터를 찾지 못했습니다. 직접 입력해주세요.")
            
    use_exact_data = st.toggle("✅ 적용보수 값 사용", value=True)
    exact_b_value, exact_redist_value, exact_p1_value = 0.0, 0.0, 0.0

    if use_exact_data:
        exact_b_value = st.number_input("개인 평균 기준소득월액 (B값)", min_value=0, max_value=30000000, value=st.session_state.doc_b_value, step=10000, placeholder="예: 3807467")
        exact_redist_value = st.number_input("소득재분배 반영 기준소득월액", min_value=0, max_value=30000000, value=st.session_state.doc_redist_value, step=10000, placeholder="예: 5076495")
        exact_p1_value = st.number_input("2009년 이전 평균 보수월액 (선택)", min_value=0, max_value=30000000, value=st.session_state.doc_p1_value, step=10000, placeholder="해당 없으면 비워두세요.")

    st.divider()
    with st.expander("경제 지표 가정"):
        salary_growth_pct = st.number_input("미래 연 보수상승률 (%)", value=DEFAULT_SALARY_GROWTH * 100, step=0.1)
        inflation_pct = st.number_input("미래 연 물가상승률 (%)", value=DEFAULT_INFLATION * 100, step=0.1, help="명목가치를 체감 가능한 실질 현재가치로 할인합니다.")
        period2_rate_pct = st.number_input("2기간 지급률 (%)", value=DEFAULT_PERIOD2_RATE * 100, step=0.001)

# =====================================
# 필수 입력값 검증 및 실행 통제
# =====================================
missing_inputs = []
if current_contribution is None: missing_inputs.append("현재 일반기여금")
if current_age is None: missing_inputs.append("현재 나이")
if entry_date is None: missing_inputs.append("최초임용일")

if use_exact_data:
    if exact_b_value is None: missing_inputs.append("개인 평균 기준소득월액 (B값)")
    if exact_redist_value is None: missing_inputs.append("소득재분배 반영 기준소득월액")
    if exact_p1_value is None: exact_p1_value = 0.0

if missing_inputs:
    st.info(f"👈 좌측 사이드바에서 다음 필수 정보를 입력해주세요: **{', '.join(missing_inputs)}**")
    st.stop()

if retirement_date_input is None:
    retirement_date = get_default_retirement_date(int(current_age), job_type)
    st.info(f"💡 예상 퇴직일이 비어있어 선택하신 직종 정년 기준인 **{retirement_date.strftime('%Y년 %m월 %d일')}**로 자동 계산되었습니다.")
else:
    retirement_date = retirement_date_input

inputs = Inputs(
    current_age=int(current_age), entry_date=entry_date, retirement_date=retirement_date,
    current_contribution=int(current_contribution), salary_growth=float(salary_growth_pct) / 100,
    inflation=float(inflation_pct) / 100, period2_rate=float(period2_rate_pct) / 100,
    use_exact_data=use_exact_data, exact_b_value=float(exact_b_value),
    exact_redist_value=float(exact_redist_value), exact_p1_value=float(exact_p1_value)
)

res = calculate_pension(inputs)

# =====================================
# 결과 화면 출력
# =====================================
st.subheader("💰 퇴직 시 예상 월 연금액")
c1, c2, c3, c4 = st.columns(4)
c1.metric("월 연금 (물가할인 현재가치)", won(res.monthly_pension_pv), help="미래의 명목 연금액을 물가상승률로 할인하여 체감 가치로 보여줍니다.")
c2.metric("월 연금 (퇴직 시 명목가치)", won(res.monthly_pension_fv), help="보수상승률이 복리로 반영되어 훗날 통장에 찍힐 액면가입니다.")
c3.metric("총 인정 재직기간", f"{res.recognized_service_years:.2f}년 (상한 {res.service_cap_years}년)")
c4.metric("연금 개시 연령", f"{res.pension_start_age}세 ({res.gap_years:.1f}년 소득공백)")

st.divider()

st.subheader("💼 퇴직 시 예상 일시금액 (수당 및 연금일시금)")
st.markdown("공무원은 퇴직 시 **'연금 + 퇴직수당'**을 받거나, 연금을 포기하고 **'연금일시금 + 퇴직수당'**을 목돈으로 한 번에 수령할 수 있습니다.")
d1, d2, d3, d4 = st.columns(4)
d1.metric("퇴직수당 (현재가치)", won(res.retirement_allowance_pv))
d2.metric("퇴직수당 (명목가치)", won(res.retirement_allowance_fv))
d3.metric("연금일시금 (현재가치)", won(res.pension_lump_sum_pv))
d4.metric("연금일시금 (명목가치)", won(res.pension_lump_sum_fv))

st.info(
    f"💡 일시금으로 전액 수령 시 총액 [현재가치 기준]: {won(res.retirement_allowance_pv + res.pension_lump_sum_pv)} / "
    f"[명목가치 기준]: {won(res.retirement_allowance_fv + res.pension_lump_sum_fv)}"
)

st.divider()

left, right = st.columns([1, 1])

with left:
    st.subheader("📊 적용된 기준 소득 (베이스 라인)")
    income_df = pd.DataFrame({
        "적용 구간": ["1기간 (2009년 이전)", "2기간 (2010~2015년)", "3기간 (2016년 이후)"],
        "기준 소득": [won(res.base_p1_income), won(res.base_p2_income), won(res.base_p3_income)]
    })
    st.dataframe(income_df, use_container_width=True, hide_index=True)

with right:
    st.subheader("📈 기간별 연금 산출 내역 (현재물가 기준)")
    period_df = pd.DataFrame({
        "구간": ["1기간", "2기간", "3기간"],
        "적용 연수": [round(res.y1, 2), round(res.y2, 2), round(res.y3, 2)],
        "연금 기여분": [won(res.period1_monthly_pv), won(res.period2_monthly_pv), won(res.period3_monthly_pv)],
    })
    st.dataframe(period_df, use_container_width=True, hide_index=True)

if inputs.use_exact_data:
    st.success("✅ 공단 서류(예상퇴직급여내역서) 데이터를 바탕으로 산출된 고정밀 추정치입니다.")
else:
    st.warning("⚠️ 현재 기여금만을 바탕으로 과거 소득을 추정한 모드입니다. 정확도를 높이려면 사이드바에서 서류 데이터를 입력하세요.")
