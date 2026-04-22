import io
import re
import zipfile
from dataclasses import dataclass
from datetime import date, timedelta
from html import unescape
from typing import Any
import xml.etree.ElementTree as ET

import pandas as pd
import streamlit as st

try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None

# =====================================
# 기본 설정
# =====================================
st.set_page_config(
    page_title="공무원연금 시뮬레이터 (파일 자동입력 개선판)",
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
    military_months: int
    excluded_leave_months: int
    salary_growth: float
    inflation: float
    period2_rate: float
    use_exact_data: bool
    exact_b_value: float
    exact_redist_value: float
    exact_p1_value: float
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
    base_p1_income: float
    base_p2_income: float
    base_p3_income: float
    avg_rate_2016plus: float
    monthly_pension_pv: float
    monthly_pension_fv: float
    period1_monthly_pv: float
    period2_monthly_pv: float
    period3_monthly_pv: float
    retirement_allowance_pv: float
    retirement_allowance_fv: float
    pension_lump_sum_pv: float
    pension_lump_sum_fv: float


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


def year_fraction(d: date) -> float:
    return d.year + ((d.timetuple().tm_yday - 1) / 365.2425)


def get_default_retirement_date(current_age: int, job_type: str) -> date:
    retirement_age = 60 if "일반공무원" in job_type else 62
    years_left = max(0, retirement_age - current_age)
    retire_year = CURRENT_YEAR + years_left
    if "교원" in job_type:
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


def apply_service_cap(raw_y1: float, raw_y2: float, raw_y3: float, cap_years: int) -> tuple[float, float, float]:
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


def estimate_b_and_redist(current_standard_income: float, current_a_value: float) -> tuple[float, float]:
    est_b_value = current_standard_income * 0.90
    capped_b = min(est_b_value, current_a_value * 1.6)
    est_redist = (current_a_value + capped_b) / 2
    return capped_b, est_redist


# =====================================
# 파일 파싱 유틸
# =====================================
def normalize_text(text: str) -> str:
    text = unescape(text)
    text = text.replace("\x00", " ")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def read_pdf_text(file_bytes: bytes) -> str:
    if PdfReader is None:
        return ""
    try:
        reader = PdfReader(io.BytesIO(file_bytes))
        texts = []
        for page in reader.pages:
            texts.append(page.extract_text() or "")
        return normalize_text(" ".join(texts))
    except Exception:
        return ""


def read_csv_text(file_bytes: bytes) -> str:
    for encoding in ["utf-8-sig", "utf-8", "cp949", "euc-kr"]:
        try:
            return normalize_text(file_bytes.decode(encoding))
        except Exception:
            continue
    return ""


def read_excel_text_with_pandas(file_bytes: bytes) -> str:
    try:
        excel_obj = pd.read_excel(io.BytesIO(file_bytes), sheet_name=None, header=None)
        parts = []
        for sheet_name, df in excel_obj.items():
            parts.append(str(sheet_name))
            parts.append(df.fillna("").astype(str).to_string(index=False, header=False))
        return normalize_text(" ".join(parts))
    except Exception:
        return ""


def read_xlsx_text_with_zipxml(file_bytes: bytes) -> str:
    try:
        zf = zipfile.ZipFile(io.BytesIO(file_bytes))
        ns_main = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        ns_rel = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

        shared_strings = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root:
                texts = []
                for t in si.iter(f"{ns_main}t"):
                    texts.append(t.text or "")
                shared_strings.append("".join(texts))

        workbook = ET.fromstring(zf.read("xl/workbook.xml"))
        rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        rel_map = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels}

        parts = []
        sheets_node = workbook.find(f"{ns_main}sheets")
        if sheets_node is None:
            return ""

        for sheet in sheets_node:
            name = sheet.attrib.get("name", "")
            rid = sheet.attrib.get(f"{ns_rel}id")
            target = rel_map.get(rid, "")
            if not target:
                continue
            target_path = "xl/" + target.lstrip("/")
            if target_path not in zf.namelist():
                continue

            parts.append(name)
            ws = ET.fromstring(zf.read(target_path))
            rows = []
            sheet_data = ws.find(f"{ns_main}sheetData")
            if sheet_data is None:
                continue

            for row in sheet_data.findall(f"{ns_main}row"):
                cells = []
                for c in row.findall(f"{ns_main}c"):
                    cell_type = c.attrib.get("t")
                    v = c.find(f"{ns_main}v")
                    if v is None or v.text is None:
                        continue
                    value = v.text
                    if cell_type == "s":
                        idx = int(value)
                        if 0 <= idx < len(shared_strings):
                            cells.append(shared_strings[idx])
                    else:
                        cells.append(value)
                if cells:
                    rows.append(" ".join(cells))
            parts.append(" ".join(rows))

        return normalize_text(" ".join(parts))
    except Exception:
        return ""


def read_any_supported_text(uploaded_file) -> str:
    file_bytes = uploaded_file.getvalue()
    name = uploaded_file.name.lower()

    if name.endswith(".pdf"):
        return read_pdf_text(file_bytes)
    if name.endswith(".csv"):
        return read_csv_text(file_bytes)
    if name.endswith(".xlsx"):
        text = read_excel_text_with_pandas(file_bytes)
        if text:
            return text
        return read_xlsx_text_with_zipxml(file_bytes)
    if name.endswith(".xls"):
        text = read_excel_text_with_pandas(file_bytes)
        if text:
            return text
        return read_csv_text(file_bytes)
    return ""


def extract_date_near_keyword(text: str, keyword_patterns: list[str]) -> date | None:
    date_patterns = [
        r"(20\d{2})[./-](\d{1,2})[./-](\d{1,2})",
        r"(19\d{2})[./-](\d{1,2})[./-](\d{1,2})",
        r"(20\d{2})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일",
        r"(19\d{2})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일",
    ]
    for kw in keyword_patterns:
        m = re.search(kw, text, flags=re.IGNORECASE)
        if not m:
            continue
        snippet = text[m.start(): m.start() + 120]
        for dp in date_patterns:
            dm = re.search(dp, snippet)
            if dm:
                try:
                    return date(int(dm.group(1)), int(dm.group(2)), int(dm.group(3)))
                except Exception:
                    pass
    return None


def extract_amount_near_keyword(text: str, keyword_patterns: list[str], min_value: int = 0, max_value: int = 999999999) -> int | None:
    amount_pattern = r"([1-9]\d{0,2}(?:,\d{3})+|[1-9]\d{5,})"
    for kw in keyword_patterns:
        m = re.search(kw, text, flags=re.IGNORECASE)
        if not m:
            continue
        snippet = text[m.start(): m.start() + 220]
        for amt in re.findall(amount_pattern, snippet):
            val = int(amt.replace(",", ""))
            if min_value <= val <= max_value:
                return val
    return None


def parse_pension_file(uploaded_file) -> dict[str, Any]:
    text = read_any_supported_text(uploaded_file)
    if not text:
        return {}

    parsed: dict[str, Any] = {}

    entry_date = extract_date_near_keyword(text, [r"임용일", r"최초임용일"])
    if entry_date:
        parsed["entry_date"] = entry_date

    b_value = extract_amount_near_keyword(
        text,
        [r"개인\s*평균\s*기준소득월액", r"B값"],
        min_value=1000000,
        max_value=30000000,
    )
    if b_value:
        parsed["b_value"] = b_value

    redist_value = extract_amount_near_keyword(
        text,
        [r"소득재분배\s*반영\s*평균\s*기준소득월액", r"소득재분배\s*반영\s*기준소득월액"],
        min_value=1000000,
        max_value=30000000,
    )
    if redist_value:
        parsed["redist_value"] = redist_value

    p1_value = extract_amount_near_keyword(
        text,
        [r"2009\.12\.31\.\s*이전기간", r"2009년\s*이전.*보수월액", r"1기간.*보수월액"],
        min_value=1,
        max_value=30000000,
    )
    if p1_value:
        parsed["p1_value"] = p1_value

    current_standard_income = extract_amount_near_keyword(
        text,
        [r"기준소득월액", r"2025년도\s*기준소득월액", r"2026년도\s*기준소득월액"],
        min_value=1000000,
        max_value=30000000,
    )
    if current_standard_income:
        parsed["current_standard_income"] = current_standard_income

    current_contribution = extract_amount_near_keyword(
        text,
        [r"기여금", r"일반기여금"],
        min_value=10000,
        max_value=5000000,
    )
    if current_contribution:
        parsed["current_contribution"] = current_contribution

    pension_amount = extract_amount_near_keyword(
        text,
        [r"연금월액", r"퇴직연금.*연금월액"],
        min_value=10000,
        max_value=10000000,
    )
    if pension_amount:
        parsed["reference_pension_monthly"] = pension_amount

    return parsed


# =====================================
# 연금 계산 로직
# =====================================
def calculate_service_years(entry_date: date, retirement_date: date, military_months: int, excluded_leave_months: int) -> dict[str, float]:
    actual_start = year_fraction(entry_date)
    actual_end = year_fraction(retirement_date)

    raw_y1 = max(0.0, min(actual_end, 2010.0) - max(actual_start, 0.0))
    raw_y2 = max(0.0, min(actual_end, 2016.0) - max(actual_start, 2010.0))
    raw_y3 = max(0.0, actual_end - max(actual_start, 2016.0))

    military_years = military_months / 12.0
    leave_years = excluded_leave_months / 12.0

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


def calculate_pension(inputs: Inputs) -> Result:
    retirement_year = inputs.retirement_date.year
    years_to_retire = max(0.0, years_between(CURRENT_DATE, inputs.retirement_date))
    retirement_age_est = inputs.current_age + years_to_retire

    service = calculate_service_years(
        inputs.entry_date,
        inputs.retirement_date,
        inputs.military_months,
        inputs.excluded_leave_months,
    )

    current_standard_income = infer_current_standard_income(inputs.current_contribution)
    current_a_value = OFFICIAL_A_VALUES[max(OFFICIAL_A_VALUES.keys())]
    inferred_b_value, inferred_redist_value = estimate_b_and_redist(current_standard_income, current_a_value)

    actual_p1_value = inputs.exact_p1_value if (inputs.use_exact_data and inputs.exact_p1_value > 0) else current_standard_income
    actual_b_value = inputs.exact_b_value if (inputs.use_exact_data and inputs.exact_b_value > 0) else inferred_b_value
    actual_p3_value = inputs.exact_redist_value if (inputs.use_exact_data and inputs.exact_redist_value > 0) else inferred_redist_value

    period1_monthly_today = 0.0
    if service["y1"] > 0:
        if service["y1"] >= 20:
            period1_monthly_today = actual_p1_value * 0.5 + actual_p1_value * (service["y1"] - 20) * 0.02
        else:
            period1_monthly_today = actual_p1_value * service["y1"] * 0.025

    period2_monthly_today = 0.0
    if service["y2"] > 0:
        period2_monthly_today = actual_b_value * service["y2"] * inputs.period2_rate

    period3_monthly_today = 0.0
    avg_rate_2016plus = 0.0
    if service["y3"] > 0:
        period3_start = max(2016.0, service["actual_start"])
        period3_end = period3_start + service["y3"]
        avg_rate_2016plus = weighted_average_rate(period3_start, period3_end)
        period3_monthly_today = actual_p3_value * service["y3"] * (avg_rate_2016plus / 100)

    monthly_pension_today = period1_monthly_today + period2_monthly_today + period3_monthly_today
    monthly_pension_fv = monthly_pension_today * ((1 + inputs.salary_growth) ** years_to_retire)
    monthly_pension_pv = monthly_pension_fv / ((1 + inputs.inflation) ** years_to_retire)

    projected_final_income_fv = current_standard_income * ((1 + inputs.salary_growth) ** years_to_retire)
    allow_rate = retirement_allowance_rate(service["recognized_service_years"])
    retirement_allowance_pv = current_standard_income * service["recognized_service_years"] * allow_rate
    retirement_allowance_fv = projected_final_income_fv * service["recognized_service_years"] * allow_rate

    excess_5_years = max(0.0, service["recognized_service_years"] - 5.0)
    lump_sum_multiplier = 0.975 + (excess_5_years * 0.0065)
    pension_lump_sum_pv = current_standard_income * service["recognized_service_years"] * lump_sum_multiplier
    pension_lump_sum_fv = projected_final_income_fv * service["recognized_service_years"] * lump_sum_multiplier

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
        base_p1_income=actual_p1_value if service["y1"] > 0 else 0.0,
        base_p2_income=actual_b_value if service["y2"] > 0 else 0.0,
        base_p3_income=actual_p3_value if service["y3"] > 0 else 0.0,
        avg_rate_2016plus=avg_rate_2016plus,
        monthly_pension_pv=monthly_pension_pv,
        monthly_pension_fv=monthly_pension_fv,
        period1_monthly_pv=period1_monthly_today,
        period2_monthly_pv=period2_monthly_today,
        period3_monthly_pv=period3_monthly_today,
        retirement_allowance_pv=retirement_allowance_pv,
        retirement_allowance_fv=retirement_allowance_fv,
        pension_lump_sum_pv=pension_lump_sum_pv,
        pension_lump_sum_fv=pension_lump_sum_fv,
    )


# =====================================
# 세션 상태 초기화
# =====================================
DEFAULT_SESSION_VALUES = {
    "doc_entry_date": None,
    "doc_b_value": None,
    "doc_redist_value": None,
    "doc_p1_value": 0,
    "doc_current_contribution": None,
    "doc_reference_pension": None,
    "doc_current_age": None,
    "file_parse_message": "",
}
for key, val in DEFAULT_SESSION_VALUES.items():
    if key not in st.session_state:
        st.session_state[key] = val


# =====================================
# UI
# =====================================
st.title("🏛️ 공무원연금 시뮬레이터 (추정/서류기반 정밀모드)")
st.markdown("직접 입력하거나 **퇴직급여 예상액 PDF / 엑셀 / CSV** 파일을 올려 자동으로 값을 채울 수 있습니다.")

with st.sidebar:
    st.header("1. 기본 정보")
    job_type = st.radio("직종 선택", ["일반공무원 (정년 60세)", "교원 (정년 62세)"])

    current_contribution = st.number_input(
        "현재 매월 납부하는 일반기여금 (원)",
        min_value=0,
        value=st.session_state.doc_current_contribution if st.session_state.doc_current_contribution else None,
        step=1000,
        placeholder="예: 396500",
    )
    current_age = st.number_input(
        "현재 나이 (세)",
        min_value=20,
        max_value=80,
        value=st.session_state.doc_current_age if st.session_state.doc_current_age else None,
        placeholder="예: 33",
    )
    entry_date = st.date_input(
        "최초임용일",
        value=st.session_state.doc_entry_date,
        min_value=date(1970, 1, 1),
        max_value=date(2100, 12, 31),
    )
    retirement_date_input = st.date_input(
        "예상 퇴직일 (선택)",
        value=None,
        min_value=date(2000, 1, 1),
        max_value=date(2100, 12, 31),
        help="비워두면 선택한 직종의 정년 기준으로 자동 계산됩니다.",
    )

    st.divider()
    st.header("2. 공단 서류 자동 입력 (선택)")
    uploaded_file = st.file_uploader(
        "📂 내 퇴직급여 예상액 파일 업로드",
        type=["pdf", "csv", "xls", "xlsx"],
        help="PDF, CSV, 엑셀(.xls/.xlsx) 파일을 지원합니다.",
    )

    if uploaded_file is not None:
        parsed = parse_pension_file(uploaded_file)
        if parsed:
            if parsed.get("entry_date"):
                st.session_state.doc_entry_date = parsed["entry_date"]
            if parsed.get("b_value"):
                st.session_state.doc_b_value = parsed["b_value"]
            if parsed.get("redist_value"):
                st.session_state.doc_redist_value = parsed["redist_value"]
            if parsed.get("p1_value") is not None:
                st.session_state.doc_p1_value = parsed["p1_value"]
            if parsed.get("current_contribution"):
                st.session_state.doc_current_contribution = parsed["current_contribution"]
            if parsed.get("reference_pension_monthly"):
                st.session_state.doc_reference_pension = parsed["reference_pension_monthly"]
            if parsed.get("current_age"):
                st.session_state.doc_current_age = parsed["current_age"]
            st.session_state.file_parse_message = "✅ 파일 분석 성공! 찾은 값들을 아래 입력칸에 반영했습니다."
            st.rerun()
        else:
            st.session_state.file_parse_message = "❌ 파일에서 필요한 데이터를 찾지 못했습니다. 직접 입력해주세요."

    if st.session_state.file_parse_message:
        if st.session_state.file_parse_message.startswith("✅"):
            st.success(st.session_state.file_parse_message)
        else:
            st.error(st.session_state.file_parse_message)

    use_exact_data = st.toggle("✅ 적용보수 값 사용", value=True)

    exact_b_value = 0
    exact_redist_value = 0
    exact_p1_value = 0
    if use_exact_data:
        exact_b_value = st.number_input(
            "개인 평균 기준소득월액 (B값)",
            min_value=0,
            max_value=30000000,
            value=st.session_state.doc_b_value if st.session_state.doc_b_value else None,
            step=10000,
            placeholder="예: 3807467",
        )
        exact_redist_value = st.number_input(
            "소득재분배 반영 기준소득월액",
            min_value=0,
            max_value=30000000,
            value=st.session_state.doc_redist_value if st.session_state.doc_redist_value else None,
            step=10000,
            placeholder="예: 5076495",
        )
        exact_p1_value = st.number_input(
            "2009년 이전 평균 보수월액 (선택)",
            min_value=0,
            max_value=30000000,
            value=st.session_state.doc_p1_value if st.session_state.doc_p1_value else 0,
            step=10000,
            placeholder="해당 없으면 0",
        )

    st.divider()
    with st.expander("경제 지표 가정"):
        salary_growth_pct = st.number_input("미래 연 보수상승률 (%)", value=DEFAULT_SALARY_GROWTH * 100, step=0.1)
        inflation_pct = st.number_input("미래 연 물가상승률 (%)", value=DEFAULT_INFLATION * 100, step=0.1)
        period2_rate_pct = st.number_input("2기간 지급률 (%)", value=DEFAULT_PERIOD2_RATE * 100, step=0.001)


# =====================================
# 실행 전 검증
# =====================================
missing_inputs = []
if current_contribution is None:
    missing_inputs.append("현재 일반기여금")
if current_age is None:
    missing_inputs.append("현재 나이")
if entry_date is None:
    missing_inputs.append("최초임용일")
if use_exact_data:
    if exact_b_value is None:
        missing_inputs.append("개인 평균 기준소득월액(B값)")
    if exact_redist_value is None:
        missing_inputs.append("소득재분배 반영 기준소득월액")

if missing_inputs:
    st.info(f"👉 좌측 사이드바에서 다음 필수 정보를 입력해주세요: **{', '.join(missing_inputs)}**")
    st.stop()

if retirement_date_input is None:
    retirement_date = get_default_retirement_date(int(current_age), job_type)
    st.info(f"💡 예상 퇴직일이 비어 있어 **{retirement_date.strftime('%Y-%m-%d')}** 로 자동 설정했습니다.")
else:
    retirement_date = retirement_date_input

inputs = Inputs(
    current_age=int(current_age),
    entry_date=entry_date,
    retirement_date=retirement_date,
    current_contribution=int(current_contribution),
    military_months=0,
    excluded_leave_months=0,
    salary_growth=float(salary_growth_pct) / 100,
    inflation=float(inflation_pct) / 100,
    period2_rate=float(period2_rate_pct) / 100,
    use_exact_data=use_exact_data,
    exact_b_value=float(exact_b_value or 0),
    exact_redist_value=float(exact_redist_value or 0),
    exact_p1_value=float(exact_p1_value or 0),
    job_type=job_type,
)

res = calculate_pension(inputs)


# =====================================
# 결과 출력
# =====================================
st.subheader("💰 퇴직 시 예상 월 연금액")
c1, c2, c3, c4 = st.columns(4)
c1.metric("월 연금 (물가할인 현재가치)", won(res.monthly_pension_pv), help="미래의 명목 연금액을 입력한 물가상승률로 할인한 실질 체감가치입니다.")
c2.metric("월 연금 (퇴직 시 명목가치)", won(res.monthly_pension_fv), help="보수상승률이 복리로 반영된 액면가 기준입니다.")
c3.metric("총 인정 재직기간", f"{res.recognized_service_years:.2f}년 (상한 {res.service_cap_years}년)")
c4.metric("연금 개시 연령", f"{res.pension_start_age}세 ({res.gap_years:.1f}년 공백)")

if st.session_state.doc_reference_pension:
    st.caption(f"참고: 업로드 파일에서 찾은 연금월액 {won(st.session_state.doc_reference_pension)}")

st.divider()

st.subheader("💼 퇴직 시 예상 일시금액 (참고용)")
st.markdown("퇴직수당은 간이 추정이며, 연금일시금은 참고용 추정치입니다.")
d1, d2, d3, d4 = st.columns(4)
d1.metric("퇴직수당 (현재가치)", won(res.retirement_allowance_pv))
d2.metric("퇴직수당 (명목가치)", won(res.retirement_allowance_fv))
d3.metric("연금일시금 (현재가치)", won(res.pension_lump_sum_pv))
d4.metric("연금일시금 (명목가치)", won(res.pension_lump_sum_fv))

st.info(
    f"💡 일시금으로 전액 수령 시 총액 [현재가치]: {won(res.retirement_allowance_pv + res.pension_lump_sum_pv)} / "
    f"[명목가치]: {won(res.retirement_allowance_fv + res.pension_lump_sum_fv)}"
)

st.divider()

left, right = st.columns([1, 1])
with left:
    st.subheader("📊 적용된 기준 소득")
    income_df = pd.DataFrame(
        {
            "적용 구간": ["1기간 (2009년 이전)", "2기간 (2010~2015년)", "3기간 (2016년 이후)"],
            "기준 소득": [won(res.base_p1_income), won(res.base_p2_income), won(res.base_p3_income)],
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
                "추정 B값(서류 미입력 시)",
                "추정 소득재분배 반영값(서류 미입력 시)",
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
            "구간": ["1기간", "2기간", "3기간"],
            "원시 연수": [round(res.raw_y1, 2), round(res.raw_y2, 2), round(res.raw_y3, 2)],
            "상한 반영 연수": [round(res.y1, 2), round(res.y2, 2), round(res.y3, 2)],
            "연금 기여분": [won(res.period1_monthly_pv), won(res.period2_monthly_pv), won(res.period3_monthly_pv)],
        }
    )
    st.dataframe(period_df, use_container_width=True, hide_index=True)

    chart_df = pd.DataFrame(
        {
            "구간": ["1기간", "2기간", "3기간"],
            "연금 기여분": [res.period1_monthly_pv, res.period2_monthly_pv, res.period3_monthly_pv],
        }
    ).set_index("구간")
    st.bar_chart(chart_df)

if inputs.use_exact_data:
    st.success("✅ 공단 서류 데이터를 반영한 보정 모드입니다. 다만 추정 계산이며 실제 지급액과 차이가 있을 수 있습니다.")
else:
    st.warning("⚠️ 현재 기여금만으로 과거 소득을 추정한 모드입니다. 정확도를 높이려면 서류 데이터를 입력하세요.")

st.subheader("주의")
st.markdown(
    """
- 이 앱은 **공식 산정액이 아닌 추정용 시뮬레이터**입니다.
- PDF는 `pypdf`, XLSX는 `pandas` 실패 시 **ZIP/XML fallback** 으로 다시 읽도록 개선했습니다.
- 업로드 파일은 **임용일 / 개인 평균 기준소득월액(B값) / 소득재분배 반영 기준소득월액 / 일부 기여금/기준소득월액** 등을 자동 추출합니다.
- 연금월액은 공무원연금의 **1기간 / 2기간 / 3기간** 구조를 따라 추정합니다.
- 개시연령은 **1996.1.1 이후 임용자 기준 퇴직연도별 표**를 반영합니다.
- 재직기간 상한은 **2016.1.1 현재 재직기간**에 따라 33/34/35/36년을 적용합니다.
- 퇴직수당은 간이 추정이며, **연금일시금은 공단식을 완전 재현한 값이 아니라 참고용 추정치**입니다.
- 실제 지급액은 공무원연금공단의 **기준소득월액 이력, 소득재분배 평균기준소득월액, 경과규정, 휴직 이력, 세액 및 공제항목** 등에 따라 달라질 수 있습니다.
    """
)
