import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DATA_DIRECTORY = PROJECT_ROOT / "data" / "raw"

COMPANIES: dict[str, dict[str, str]] = {
        "tesla": {
            "company": "Tesla, Inc.",
            "ticker": "TSLA",
            "cik": "0001318605",
        },
        "aurora": {
            "company": "Aurora Innovation, Inc.",
            "ticker": "AUR",
            "cik": "0001828108",
        },
        "mobileye": {
            "company": "Mobileye Global Inc.",
            "ticker": "MBLY",
            "cik": "0001910139",
        },
        "alphabet": {
            "company": "Alphabet Inc.",
            "ticker": "GOOGL",
            "cik": "0001652044",
        },
        "general_motors": {
            "company": "General Motors Company",
            "ticker": "GM",
            "cik": "0001467858",
        },
        "ford": {
            "company": "Ford Motor Company",
            "ticker": "F",
            "cik": "0000037996",
        },
        "nvidia": {
            "company": "NVIDIA Corporation",
            "ticker": "NVDA",
            "cik": "0001045810",
        },
        "qualcomm": {
            "company": "QUALCOMM Incorporated",
            "ticker": "QCOM",
            "cik": "0000804328",
        },
        "aptiv": {
            "company": "Aptiv PLC",
            "ticker": "APTV",
            "cik": "0001521332",
        },
        "ouster": {
            "company": "Ouster, Inc.",
            "ticker": "OUST",
            "cik": "0001816581",
        },
    }

def fetch_latest_10k(company: str, user: str) -> dict[str, str]:
    """Fetch the latest normal 10-K filing for a company."""

    company_key = company.strip().lower()
    if company_key not in COMPANIES:
        raise ValueError("company must be either 'tesla' or 'mobileye'")
    if not user.strip():
        raise ValueError("user_agent must identify your application and contact email")

    company_info = COMPANIES[company_key]
    headers = {
        "User-Agent": user,
        "Host": "data.sec.gov",
    }
    submissions_url = (
        f"https://data.sec.gov/submissions/CIK{company_info['cik']}.json"
    )

    with urlopen(Request(submissions_url, headers=headers), timeout=30) as response:
        submissions = json.load(response)

    recent = submissions["filings"]["recent"]
    try:
        filing_index = recent["form"].index("10-K")
    except ValueError as exc:
        raise LookupError(f"No 10-K filing found for {company_info['company']}") from exc

    accession_number = recent["accessionNumber"][filing_index]
    primary_document = recent["primaryDocument"][filing_index]
    accession_without_dashes = accession_number.replace("-", "")
    cik_without_leading_zeros = company_info["cik"].lstrip("0")
    filing_url = (
        "https://www.sec.gov/Archives/edgar/data/"
        f"{cik_without_leading_zeros}/{accession_without_dashes}/{primary_document}"
    )

    filing_headers = {
        "User-Agent": user,
        "Host": "www.sec.gov",
    }
    with urlopen(Request(filing_url, headers=filing_headers), timeout=30) as response:
        filing_html = response.read().decode("utf-8", errors="replace")
    return {
        "company": company_info["company"],
        "ticker": company_info["ticker"],
        "cik": company_info["cik"],
        "form": "10-K",
        "filing_date": recent["filingDate"][filing_index],
        "reporting_period": recent["reportDate"][filing_index],
        "accession_number": accession_number,
        "source_url": filing_url,
        "html": filing_html,
    }

def save_filing_html(filing: dict[str, str], output_directory: str | Path, save_metadata=True, overwrite=False) -> Path:
    """Save filing HTML without overwriting an existing downloaded filing."""

    company_directory = Path(output_directory) / filing["ticker"]
    company_directory.mkdir(parents=True, exist_ok=True)

    filing_year = filing["reporting_period"][:4]
    output_path = company_directory / f"{filing_year}-10-K.html"
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"Filing already exists: {output_path}")

    output_path.write_text(filing["html"], encoding="utf-8")
    if save_metadata:
        json_path = company_directory / f"{filing_year}-10-K.metadata.json"
        if json_path.exists() and not overwrite:
            raise FileExistsError(f"Metadata json already exists: {json_path}")
        metadata = filing.copy()
        metadata.pop("html", None)
        with open(json_path,"w") as file:
            json.dump(metadata, file, indent=2)
    return output_path


def fetch_and_save_all_filings(
    user: str,
    output_directory: str | Path = RAW_DATA_DIRECTORY,
):
    """Fetch and save the latest 10-K filings from the companies dictionary."""
    for key, company in COMPANIES.items():
        filing = fetch_latest_10k(key, user)
        save_filing_html(filing, output_directory)

if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    user_agent = os.getenv("SEC_USER_AGENT")
    fetch_and_save_all_filings(str(user_agent))
