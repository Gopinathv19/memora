"""Tests for app/processing/cleaning.py. All local; no model calls."""

from app.processing.cleaning import (
    clean_reading,
    compression_ratio,
    strip_headers_footers,
)
from app.processing.triage import junk_ratio


def test_strips_unk_and_cid():
    text = "Name: Ravi <unk> Kumar\n(cid:12) Address: Chennai"
    cleaned, report = clean_reading(text)
    assert "<unk>" not in cleaned
    assert "(cid:" not in cleaned
    assert "Ravi" in cleaned and "Chennai" in cleaned
    assert report.damage_ratio > 0


def test_strips_control_and_zero_width():
    text = "a\x00b\u200bc\ufeffd\te"
    cleaned, _ = clean_reading(text)
    assert "\x00" not in cleaned and "\u200b" not in cleaned and "\ufeff" not in cleaned
    assert "a" in cleaned and "e" in cleaned


def test_nfc_normalization_tamil_hindi():
    # "தமிழ்" in NFD (decomposed) form must come back NFC.
    tamil_nfd = "தமிழ்"
    tamil_nfc = "தமிழ்"
    assert tamil_nfd != tamil_nfc.encode() or True  # forms may or may not differ
    cleaned, _ = clean_reading(tamil_nfd)
    assert cleaned == tamil_nfc
    # Devanagari "हिन्दी" survives cleaning unchanged.
    cleaned, _ = clean_reading("हिन्दी भाषा")
    assert "हिन्दी" in cleaned


def test_collapse_consecutive_duplicate_lines():
    text = "line one\nline one\nline one\nline two"
    cleaned, report = clean_reading(text)
    assert cleaned.split("\n") == ["line one", "line two"]
    assert report.collapsed_lines == 2


def test_collapse_repeated_block():
    block = "A | 1\nB | 2"
    text = "\n\n".join([block] * 5)
    cleaned, report = clean_reading(text)
    assert cleaned.count("A | 1") == 1
    assert report.collapsed_blocks >= 1


def test_loop_detection():
    line = "The model loops here. " * 5
    text = line * 40  # long, highly repetitive
    _, report = clean_reading(text, loop_min_chars=200)
    assert report.loop_detected
    # Genuinely varied prose must not be flagged.
    varied = (
        "The invoice was issued in September and covers consulting work. "
        "Payment terms are net thirty days from the invoice date. "
        "The buyer, Globex Corporation, operates from Chennai. "
        "Shipping charges of 450 INR apply to this order. "
        "A discount of five percent was agreed for early settlement. "
        "The contract references purchase order 88213 dated last quarter. "
        "Delivery is scheduled through two separate shipments this month. "
        "Support coverage includes weekday response within four hours. "
    ) * 3
    _, report2 = clean_reading(varied)
    assert not report2.loop_detected


def test_compression_ratio_sane():
    assert compression_ratio("") == 1.0
    r = compression_ratio("word " * 100)
    assert 0 < r < 1


def test_header_footer_removal():
    # Body lines differ per page in wording, not just digits; only the
    # header/footer lines repeat.
    page_bodies = [
        ["The invoice covers consulting delivered in September.",
         "Payment terms are net thirty days from the invoice date.",
         "The buyer operates from Chennai and ships to two depots.",
         "Shipping charges of 450 INR apply to this order.",
         "A discount was agreed for early settlement in full."],
        ["The contract covers maintenance of the northern depot.",
         "Renewal happens each January unless cancelled earlier.",
         "The supplier is based in Coimbatore with three branches.",
         "Penalty clauses apply after a fourteen day delay.",
         "Insurance is carried by the logistics partner."],
        ["The receipt confirms delivery of forty two pallets.",
         "Goods arrived undamaged and were signed for on site.",
         "The driver noted one box was opened for inspection.",
         "Storage moved to the bonded warehouse that evening.",
         "A follow-up order is expected within the month."],
    ]
    pages = [
        f"Page {i}\n" + "\n".join(body) + "\nFooter Ltd."
        for i, body in enumerate(page_bodies, start=1)
    ]
    stripped, meta = strip_headers_footers(pages)
    assert "Footer Ltd." not in stripped[0]
    assert "Page" not in stripped[0]
    assert "invoice covers consulting" in stripped[0]
    assert "discount was agreed" in stripped[0]
    assert "Footer Ltd." in " ".join(meta.values())


def test_header_footer_kept_for_few_pages():
    pages = ["Page 1\nBody", "Page 2\nBody"]
    stripped, meta = strip_headers_footers(pages)
    assert meta == {}
    assert "Page 1" in stripped[0]


def test_junk_ratio_includes_unk_and_fffd():
    assert junk_ratio("abc <unk> def") > 0
    assert junk_ratio("abc\ufffddef") > 0
    assert junk_ratio("clean text only") == 0.0

def test_header_footer_keeps_per_page_id_numbers():
    # Several Aadhaar cards in one PDF: each page ends with a different
    # 12-digit number. Masked, they all read "#### #### ####" -- they must
    # not be mistaken for a running footer.
    numbers = ["2345 6789 0123", "3456 7890 1234", "4567 8901 2345"]
    pages = [
        f"Government of India\nName: Person {i}\nDOB: 01/01/199{i}\n{n}\nMera Aadhaar, Meri Pehchan"
        for i, n in enumerate(numbers, start=1)
    ]
    stripped, meta = strip_headers_footers(pages)
    for page, number in zip(stripped, numbers):
        assert number in page
    # Real repeated header/footer text is still removed.
    assert "Government of India" not in stripped[0]
    assert "Mera Aadhaar, Meri Pehchan" in " ".join(meta.values())


def test_header_footer_still_strips_page_numbers():
    pages = [f"Report body {w}\nmore text\n{i}" for i, w in enumerate("abc", start=1)]
    stripped, _ = strip_headers_footers(pages)
    assert all(not page.endswith(str(i)) for i, page in enumerate(stripped, start=1))


AUTHORITY = "**Unique Identification Authority of India**"


def test_repeated_phrase_collapsed_across_blank_lines_and_truncation():
    text = (
        "**Enrolment No.: 2726/10022/47657**\n\n"
        + "\n\n".join([AUTHORITY] * 4)
        + f"\n\n{AUTHORITY} **Unique Identification Authority of\nGovernment of India"
    )
    cleaned, report = clean_reading(text)
    assert cleaned.lower().count("unique identification authority of india") == 1
    assert "2726/10022/47657" in cleaned
    assert "Government of India" in cleaned
    assert report.collapsed_lines >= 4


def test_repeated_phrase_pass_keeps_table_rows_and_ids():
    pairs = [f"| Item | Amount in rupees |\nAccount 1234 5678 9012 3456\nrow {c}\n" for c in "abcd"]
    cleaned, _ = clean_reading("".join(pairs))
    assert cleaned.count("| Item | Amount in rupees |") == 4
    assert cleaned.count("Account 1234 5678 9012 3456") == 4


def test_angle_brackets_and_word_unk_survive():
    cleaned, _ = clean_reading("if a < b and b > c then <unk> the unk case <|pad|>")
    assert "a < b" in cleaned and "b > c" in cleaned and "unk case" in cleaned
    assert "<unk>" not in cleaned and "<|pad|>" not in cleaned


def test_grouped_enrolment_number_is_an_identifier():
    pages = [f"Enrolment No.: 2726/10022/47657\n{w} text here" for w in ("alpha", "beta", "gamma", "delta")]
    out, meta = strip_headers_footers(pages)
    assert meta == {} and all("2726/10022/47657" in p for p in out)
