"""Active filing corpus and query aliases shared by runtime and evaluation."""

ACTIVE_FILINGS: dict[str, str] = {
    "AUR": "2025-10-K",
    "TSLA": "2025-10-K",
    "MBLY": "2025-10-K",
    "GOOGL": "2025-10-K",
    "GM": "2025-10-K",
    "F": "2025-10-K",
    "NVDA": "2026-10-K",
    "QCOM": "2025-10-K",
    "APTV": "2025-10-K",
    "OUST": "2025-10-K",
    "RIVN": "2025-10-K",
}

COMPANY_NAMES: dict[str, str] = {
    "AUR": "Aurora Innovation, Inc.",
    "TSLA": "Tesla, Inc.",
    "MBLY": "Mobileye Global Inc.",
    "GOOGL": "Alphabet Inc.",
    "GM": "General Motors Company",
    "F": "Ford Motor Company",
    "NVDA": "NVIDIA Corporation",
    "QCOM": "QUALCOMM Incorporated",
    "APTV": "Aptiv PLC",
    "OUST": "Ouster, Inc.",
    "RIVN": "Rivian Automotive, Inc.",
}

COMPANY_ALIASES: dict[str, tuple[str, ...]] = {
    "AUR": ("aurora innovation", "aurora", "aurora driver"),
    "TSLA": ("tesla",),
    "MBLY": ("mobileye", "eyeq", "mobileye drive"),
    "GOOGL": ("alphabet", "google", "waymo"),
    "GM": ("general motors", "gm", "super cruise"),
    "F": ("ford motor company", "ford"),
    "NVDA": ("nvidia",),
    "QCOM": ("qualcomm", "snapdragon digital chassis", "snapdragon"),
    "APTV": ("aptiv",),
    "OUST": ("ouster",),
    "RIVN": (
        "rivian automotive", "rivian", "rivn", "r1t", "r1s", "r2", "r3",
        "r3x", "rivian commercial van",
    ),
}

if set(ACTIVE_FILINGS) != set(COMPANY_ALIASES) or set(ACTIVE_FILINGS) != set(COMPANY_NAMES):
    raise RuntimeError("Active filing, company-name, and alias ticker sets must match")

ACTIVE_COMPANY_COUNT = len(ACTIVE_FILINGS)
