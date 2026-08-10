import json
import re
from datetime import datetime
from pathlib import Path

from lxml import html as lxml_html

from .dom_processing import normalize_text
from .fetch_data import COMPANIES


FILING_FILENAME_PATTERN = re.compile(r"(?P<year>\d{4})-10-K\.html$")
COVER_SPECIFIC_FACTS = {
    "exchange": "SecurityExchangeName",
    "address": "EntityAddressAddressLine1",
    "city": "EntityAddressCityOrTown",
    "country": "EntityAddressCountry",
    "postal_code": "EntityAddressPostalZipCode",
}


def find_latest_local_filing(
    company_name: str,
    raw_directory: str | Path = "data/raw",
) -> tuple[int, Path]:
    """Return the newest locally downloaded normal 10-K for a company."""
    company_key = company_name.strip().lower()
    if company_key not in COMPANIES:
        raise ValueError(f"Unknown company: {company_name}")

    company_directory = Path(raw_directory) / COMPANIES[company_key]["ticker"]
    filings = []
    for filing_path in company_directory.glob("*-10-K.html"):
        match = FILING_FILENAME_PATTERN.fullmatch(filing_path.name)
        if match:
            filings.append((int(match["year"]), filing_path))

    if not filings:
        raise FileNotFoundError(
            f"No normal 10-K HTML filing found in {company_directory}"
        )

    return max(filings, key=lambda filing: filing[0])


def load_latest_filing_html(
    company_name: str,
    raw_directory: str | Path = "data/raw",
) -> tuple[int, bytes]:
    """Load the newest local normal 10-K without requiring its filing year."""
    filing_year, filing_path = find_latest_local_filing(company_name, raw_directory)
    return filing_year, filing_path.read_bytes()


def parse_filing_html(html_content: bytes):
    return lxml_html.fromstring(html_content)


def extract_inline_xbrl_fact(root, fact_name: str) -> str:
    """Extract the first visible value for a named DEI Inline XBRL fact."""
    expected_name = fact_name.casefold()
    for node in root.xpath("//*[@name]"):
        node_name = node.get("name", "").split(":")[-1].casefold()
        if node_name == expected_name:
            return normalize_text(node.text_content())
    return ""


def normalize_iso_date(value: str) -> str:
    """Convert common filing date displays to YYYY-MM-DD when possible."""
    value = normalize_text(str(value or ""))
    if not value:
        return ""

    for date_format in ("%Y-%m-%d", "%B %d, %Y", "%B %d %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(value, date_format).date().isoformat()
        except ValueError:
            continue
    return value


def load_extraction_metadata(
    filing_path: Path,
    root,
    company_info: dict[str, str],
    filing_year: int,
) -> dict[str, str | int]:
    """Combine configured identity, optional acquisition metadata, and DEI facts."""
    metadata_path_candidates = (
        filing_path.with_suffix(".metadata.json"),
        filing_path.parent / "metadata.json",
    )
    stored_metadata = {}
    for metadata_path in metadata_path_candidates:
        if not metadata_path.exists():
            continue
        with metadata_path.open(encoding="utf-8") as metadata_file:
            stored_metadata = json.load(metadata_file)
        if not isinstance(stored_metadata, dict):
            raise ValueError(f"Filing metadata must be a JSON object: {metadata_path}")
        break

    for identity_field in ("ticker", "cik"):
        stored_identity = str(stored_metadata.get(identity_field) or "")
        expected_identity = company_info[identity_field]
        if stored_identity and stored_identity != expected_identity:
            raise ValueError(
                f"Metadata {identity_field} {stored_identity!r} does not match "
                f"configured value {expected_identity!r}: {filing_path}"
            )

    stored_year = stored_metadata.get("filing_year")
    if stored_year is not None and int(stored_year) != filing_year:
        raise ValueError(
            f"Metadata filing year {stored_year} does not match filename year "
            f"{filing_year}: {filing_path}"
        )

    reporting_period = stored_metadata.get("reporting_period", "")
    if not reporting_period:
        reporting_period = extract_inline_xbrl_fact(root, "DocumentPeriodEndDate")

    document_type = extract_inline_xbrl_fact(root, "DocumentType")
    form = str(stored_metadata.get("form") or document_type or "10-K").upper()
    if form != "10-K":
        raise ValueError(f"Expected a normal 10-K, found form {form!r}: {filing_path}")

    cover_metadata = {
        key: extract_inline_xbrl_fact(root, fact) for key, fact in COVER_SPECIFIC_FACTS.items()
    }

    metadata = {
        "company": stored_metadata.get("company") or company_info["company"],
        "ticker": stored_metadata.get("ticker") or company_info["ticker"],
        "cik": stored_metadata.get("cik") or company_info["cik"],
        "form": form,
        "filing_year": filing_year,
        "filing_date": normalize_iso_date(stored_metadata.get("filing_date", "")),
        "reporting_period": normalize_iso_date(reporting_period),
        "accession_number": str(stored_metadata.get("accession_number") or ""),
        "source_url": str(stored_metadata.get("source_url") or ""),
    }
    metadata = metadata | cover_metadata

    reporting_year = str(metadata["reporting_period"])[:4]
    if reporting_year.isdigit() and int(reporting_year) != filing_year:
        raise ValueError(
            f"Metadata reporting period {metadata['reporting_period']} does not match "
            f"filing filename year {filing_year}: {filing_path}"
        )
    return metadata
