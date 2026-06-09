#!/usr/bin/env python3
"""Generate publication and preprint CV exports from NASA ADS.

Run this from the `files/` directory next to the TeX CV:

    ADS_DEV_KEY=... python3 update_cv_publications.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "Wogan_Nicholas_CV_publications_generated.tex"
DEFAULT_PREPRINT_TEX_OUTPUT = ROOT / "Wogan_Nicholas_CV_preprints_generated.tex"
DEFAULT_TEXT_OUTPUT = ROOT / "Wogan_Nicholas_CV_publications.txt"
DEFAULT_PREPRINT_OUTPUT = ROOT / "Wogan_Nicholas_CV_preprints.txt"
ADS_SEARCH_URL = "https://api.adsabs.harvard.edu/v1/search/query"
TARGET_AUTHOR = "Nicholas Wogan"
TARGET_AUTHOR_LATEX = r"\textbf{Nicholas Wogan}"
AUTHOR_QUERY = '(author:"Wogan, N" OR author:"Wogan, Nick" OR author:"Wogan, Nicholas")'
PUBLISHED_QUERY = f"{AUTHOR_QUERY} property:refereed"
SEARCH_FIELDS = "author,title,pub,year,doi,bibcode,pubdate,bibstem,doctype"
ROWS_PER_PAGE = 100
TARGET_LAST_NAME = "Wogan"
TARGET_FIRST_INITIAL = "N"


def latex_escape(text: str) -> str:
    """Escape the subset of characters that break TeX."""
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
    }
    return "".join(replacements.get(char, char) for char in text)


def ascii_fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def sanitize_ads_text(text: str) -> str:
    """Convert ADS/HTML/unicode title text into TeX-safe markup."""
    if not text:
        return ""

    text = str(text)
    math_tokens: dict[str, str] = {}
    token_count = 0

    def add_math(fragment: str) -> str:
        nonlocal token_count
        token = f"@@MATH{token_count}@@"
        token_count += 1
        math_tokens[token] = fragment
        return token

    def subscript_repl(match: re.Match[str]) -> str:
        inner = ascii_fold(match.group(1))
        inner = inner.replace("⊕", r"\oplus")
        return add_math(rf"$_{{{inner}}}$")

    text = re.sub(r"<SUB>(.*?)</SUB>", subscript_repl, text, flags=re.I)
    text = re.sub(r"<sub>(.*?)</sub>", subscript_repl, text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)

    text = text.replace("μ", add_math(r"$\mu$"))
    text = text.replace("⊕", add_math(r"$\oplus$"))
    text = text.replace("−", "--")
    text = text.replace("–", "--")
    text = text.replace("—", "---")
    text = text.replace("─", "--")
    text = text.replace("\u00a0", " ")

    text = ascii_fold(text)
    text = latex_escape(text)
    for token, fragment in math_tokens.items():
        text = text.replace(token, fragment)
    return text


def plain_text_ads_text(text: str) -> str:
    """Convert ADS/HTML/unicode text into plain ASCII text."""
    if not text:
        return ""

    text = str(text)

    def subscript_repl(match: re.Match[str]) -> str:
        return ascii_fold(match.group(1))

    text = re.sub(r"<SUB>(.*?)</SUB>", subscript_repl, text, flags=re.I)
    text = re.sub(r"<sub>(.*?)</sub>", subscript_repl, text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("μ", "mu")
    text = text.replace("⊕", "oplus")
    text = text.replace("−", "-")
    text = text.replace("–", "-")
    text = text.replace("—", "-")
    text = text.replace("─", "-")
    text = text.replace("\u00a0", " ")
    text = ascii_fold(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_author(author: str) -> str:
    author = author.strip()
    if "," in author:
        last, first = [part.strip() for part in author.split(",", 1)]
        author = f"{first} {last}".strip()
    return author


def is_self_author(author: str) -> bool:
    normalized = normalize_author(author)
    parts = normalized.split()
    if len(parts) < 2:
        return False
    first = parts[0]
    last = parts[-1]
    return last == TARGET_LAST_NAME and first.startswith(TARGET_FIRST_INITIAL)


def is_first_author(record: dict[str, Any]) -> bool:
    authors = record.get("author", [])
    if isinstance(authors, str):
        authors = [authors]
    if not authors:
        return False
    return is_self_author(authors[0])


def render_author(author: str) -> str:
    normalized = normalize_author(author)
    normalized = ascii_fold(normalized)
    if is_self_author(normalized):
        return TARGET_AUTHOR_LATEX
    return latex_escape(normalized)


def join_english(parts: list[str]) -> str:
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return f"{', '.join(parts[:-1])}, and {parts[-1]}"


def format_author_list(authors: list[str], override: str | None = None) -> str:
    if override:
        return override

    clean_authors = [normalize_author(author) for author in authors if author.strip()]
    if not clean_authors:
        return TARGET_AUTHOR_LATEX

    rendered = [render_author(author) for author in clean_authors]
    self_positions = [idx for idx, author in enumerate(clean_authors) if is_self_author(author)]

    if len(rendered) <= 5:
        return join_english(rendered)

    if self_positions:
        self_index = self_positions[0]
        if self_index <= 2:
            return f"{', '.join(rendered[:3])}, et al."
        return f"{rendered[0]}, ..., {rendered[self_index]}, et al."

    return f"{', '.join(rendered[:3])}, et al."


def ads_request(token: str, query: str, start: int = 0, rows: int = ROWS_PER_PAGE) -> dict[str, Any]:
    params = {
        "q": query,
        "fl": SEARCH_FIELDS,
        "rows": rows,
        "start": start,
    }
    url = f"{ADS_SEARCH_URL}?{urlencode(params)}"
    request = Request(url, headers={"Authorization": f"Bearer {token}"})

    try:
        with urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except HTTPError as exc:
        raise RuntimeError(f"ADS request failed for query {query}: HTTP {exc.code}") from exc
    except URLError as exc:
        raise RuntimeError(f"ADS request failed for query {query}: {exc.reason}") from exc

    docs = payload.get("response", {}).get("docs", [])
    return {
        "docs": docs,
        "num_found": payload.get("response", {}).get("numFound", 0),
    }


def fetch_records(token: str, query: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    start = 0

    while True:
        payload = ads_request(token, query, start=start)
        docs = payload["docs"]
        records.extend(docs)
        if len(docs) < ROWS_PER_PAGE:
            break
        start += ROWS_PER_PAGE

    def author_matches(record: dict[str, Any]) -> bool:
        authors = record.get("author", [])
        if isinstance(authors, str):
            authors = [authors]
        for author in authors:
            if is_self_author(author):
                return True
        return False

    records = [record for record in records if author_matches(record)]

    def sort_key(record: dict[str, Any]) -> tuple[int, str, str]:
        year = record.get("year", 0)
        try:
            year_int = int(year)
        except (TypeError, ValueError):
            year_int = 0
        pubdate = str(record.get("pubdate", ""))
        title = str(record.get("title", [""])[0] if isinstance(record.get("title"), list) else record.get("title", ""))
        return (year_int, pubdate, title)

    records.sort(key=sort_key, reverse=True)
    return records


def fetch_published_records(token: str) -> list[dict[str, Any]]:
    records = fetch_records(token, PUBLISHED_QUERY)
    return [record for record in records if is_self_author_from_record(record)]


def fetch_preprint_records(token: str) -> list[dict[str, Any]]:
    records = fetch_records(token, AUTHOR_QUERY)
    return [record for record in records if is_self_author_from_record(record) and is_preprint_record(record)]


def is_self_author_from_record(record: dict[str, Any]) -> bool:
    authors = record.get("author", [])
    if isinstance(authors, str):
        authors = [authors]
    for author in authors:
        if is_self_author(author):
            return True
    return False


def normalize_field_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item]
    if value:
        return [str(value)]
    return []


def is_preprint_record(record: dict[str, Any]) -> bool:
    doi = choose_doi(record.get("doi"))
    if not doi.startswith("10.48550/arXiv."):
        return False

    pub = plain_text_ads_text(pick_text(record, record, "pub")).lower()
    bibstems = [stem.lower() for stem in normalize_field_list(record.get("bibstem"))]
    doctype = plain_text_ads_text(str(record.get("doctype", ""))).lower()

    if "arxiv" in pub:
        return True
    if any("arxiv" in stem for stem in bibstems):
        return True
    if "e-print" in pub or "eprint" in pub or "arxiv" in doctype:
        return True
    return False


def choose_doi(value: Any) -> str:
    if isinstance(value, list):
        doi_values = [str(item) for item in value if item]
    elif value:
        doi_values = [str(value)]
    else:
        doi_values = []

    for doi in doi_values:
        if not doi.startswith("10.48550/arXiv."):
            return doi
    return doi_values[0] if doi_values else ""


def pick_text(entry: dict[str, Any], record: dict[str, Any], key: str) -> str:
    override = entry.get(f"{key}_tex")
    if override:
        return override
    value = record.get(key, "")
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value)


def infer_journal(entry: dict[str, Any], record: dict[str, Any]) -> str:
    override = entry.get("pub_tex")
    if override:
        return str(override)

    doi = choose_doi(record.get("doi")).lower()
    bibstems = [stem.lower() for stem in normalize_field_list(record.get("bibstem"))]
    pub = pick_text(entry, record, "pub")

    if any(stem in {"apjl", "apjletters", "apj lett"} or "apjl" in stem for stem in bibstems):
        return "The Astrophysical Journal Letters"
    if doi.startswith("10.3847/2041-8213/"):
        return "The Astrophysical Journal Letters"
    if doi.startswith("10.3847/1538-4357/"):
        return "The Astrophysical Journal"

    return pub


def render_entry(entry: dict[str, Any], record: dict[str, Any]) -> str:
    authors = record.get("author", [])
    if not isinstance(authors, list):
        authors = [str(authors)]

    author_text = format_author_list(authors, entry.get("authors_tex"))
    title = sanitize_ads_text(pick_text(entry, record, "title"))
    journal = sanitize_ads_text(infer_journal(entry, record))
    year = str(entry.get("year") or record.get("year") or "")
    doi = choose_doi(record.get("doi"))

    body = f"\\item[{latex_escape(year)}]\n  {author_text} ({latex_escape(year)}). {title}."
    if journal:
        body += f" \\emph{{{journal}}}."
    else:
        body += " \\emph{Publication details unavailable from ADS}."
    body += f" \\href{{https://doi.org/{doi}}}{{DOI: {latex_escape(doi)}}}."
    return body


def render_plain_author(author: str) -> str:
    normalized = normalize_author(author)
    normalized = ascii_fold(normalized)
    if is_self_author(normalized):
        return TARGET_AUTHOR
    return normalized


def format_plain_author_list(authors: list[str]) -> str:
    clean_authors = [normalize_author(author) for author in authors if author.strip()]
    if not clean_authors:
        return TARGET_AUTHOR

    rendered = [render_plain_author(author) for author in clean_authors]
    self_positions = [idx for idx, author in enumerate(clean_authors) if is_self_author(author)]

    if len(rendered) <= 5:
        return join_english(rendered)

    if self_positions:
        self_index = self_positions[0]
        if self_index <= 2:
            return f"{', '.join(rendered[:3])}, et al."
        return f"{rendered[0]}, ..., {rendered[self_index]}, et al."

    return f"{', '.join(rendered[:3])}, et al."


def render_plain_text_entry(record: dict[str, Any]) -> str:
    authors = record.get("author", [])
    if not isinstance(authors, list):
        authors = [str(authors)]

    author_text = format_plain_author_list(authors)
    title = plain_text_ads_text(pick_text(record, record, "title"))
    journal = plain_text_ads_text(infer_journal(record, record))
    year = str(record.get("year", ""))
    doi = choose_doi(record.get("doi"))
    parts = [f"{author_text} ({year}). {title}."]
    if journal:
        parts.append(f"{journal}.")
    parts.append(f"DOI: {doi}.")
    return " ".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Where to write the generated TeX fragment.",
    )
    parser.add_argument(
        "--text-output",
        type=Path,
        default=DEFAULT_TEXT_OUTPUT,
        help="Where to write the plain-text export.",
    )
    parser.add_argument(
        "--preprint-output",
        type=Path,
        default=DEFAULT_PREPRINT_OUTPUT,
        help="Where to write the arXiv-only plain-text export.",
    )
    parser.add_argument(
        "--preprint-tex-output",
        type=Path,
        default=DEFAULT_PREPRINT_TEX_OUTPUT,
        help="Where to write the arXiv-only TeX fragment.",
    )
    parser.add_argument(
        "--first-author-only",
        action="store_true",
        help="Restrict the plain-text export to first-author publications.",
    )
    args = parser.parse_args()
    output_path = args.output
    text_output_path = args.text_output
    preprint_output_path = args.preprint_output
    preprint_tex_output_path = args.preprint_tex_output
    first_author_only = args.first_author_only

    token = os.environ.get("ADS_DEV_KEY") or os.environ.get("ADS_TOKEN")
    if not token:
        raise SystemExit(
            "ADS_DEV_KEY (or ADS_TOKEN) is required to refresh the CV from ADS."
        )

    entries = fetch_published_records(token)
    total_papers = len(entries)
    first_author_papers = sum(1 for entry in entries if is_first_author(entry))
    print(f"Total refereed papers: {total_papers}")
    print(f"First-author papers: {first_author_papers}")

    preprint_entries = fetch_preprint_records(token)
    preprint_total = len(preprint_entries)
    preprint_first_author = sum(1 for entry in preprint_entries if is_first_author(entry))
    print(f"Preprint papers: {preprint_total}")
    print(f"Preprint first-author papers: {preprint_first_author}")

    rendered_entries = []
    text_entries = []
    for entry in entries:
        if not entry.get("doi"):
            continue
        rendered_entries.append(render_entry(entry, entry))
        if first_author_only and not is_first_author(entry):
            continue
        text_entries.append(render_plain_text_entry(entry))

    preprint_text_entries = []
    preprint_rendered_entries = []
    for entry in preprint_entries:
        if not entry.get("doi"):
            continue
        if first_author_only and not is_first_author(entry):
            continue
        preprint_rendered_entries.append(render_entry(entry, entry))
        preprint_text_entries.append(render_plain_text_entry(entry))

    output = ["\\begin{cvlist}"]
    output.extend(rendered_entries)
    output.append("\\end{cvlist}")
    output_path.write_text("\n".join(output) + "\n")
    text_output_path.write_text("\n\n".join(text_entries) + "\n")
    preprint_tex_output_path.write_text(
        "\n".join(["\\begin{cvlist}", *preprint_rendered_entries, "\\end{cvlist}"]) + "\n"
    )
    preprint_output_path.write_text("\n\n".join(preprint_text_entries) + "\n")
    print(f"Wrote {output_path}")
    print(f"Wrote {text_output_path}")
    print(f"Wrote {preprint_tex_output_path}")
    print(f"Wrote {preprint_output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
