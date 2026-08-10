import html as html_lib
import re
import unicodedata


ITEM_HEADING_PATTERN = re.compile(
    r"^item\s+(?P<number>\d{1,2}[a-z]?)\s*[.\-—:]?\s*(?P<title>.+?)\.?$",
    re.IGNORECASE,
)
PAGE_NUMBER_PATTERN = re.compile(r"^(?:\d{1,3}|[ivxlcdm]{1,8})$", re.IGNORECASE)


def raw_tag(node):
    return str(node.tag).lower() if isinstance(node.tag, str) else ""


def drop_non_text_nodes(root):
    """Remove DOM nodes with non-text tags."""
    for node in root.xpath("//script | //style | //noscript | //img | //svg | //picture"):
        node.drop_tree()

    for node in root.xpath("//head"):
        node.drop_tree()


def drop_hidden_nodes(root):
    for node in list(root.iter()):
        tag = raw_tag(node)
        style = compact_style(node)

        explicitly_hidden = (
                node.get("hidden") is not None
                or node.get("aria-hidden", "").lower() == "true"
                or "display:none" in style
                or "visibility:hidden" in style
                or tag == "ix:hidden"
                or tag.endswith("}hidden")
        )

        if explicitly_hidden:
            node.drop_tree()


def drop_xbrl_tags(root):
    for node in list(root.iter()):
        tag = raw_tag(node)

        if (
                tag in {"ix:nonnumeric", "ix:nonfraction", "ix:continuation"}
                or tag.endswith("}nonnumeric")
                or tag.endswith("}nonfraction")
                or tag.endswith("}continuation")
        ):
            node.drop_tag()


def normalize_text(text):
    text = html_lib.unescape(text)
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\xa0", " ")
    text = text.replace("\u00ad", "")
    text = text.replace("\u200b", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def compact_style(node) -> str:
    """Return inline CSS without insignificant spaces and with normalized case."""
    return "".join(node.get("style", "").lower().split())


def is_page_furniture(node) -> bool:
    """Conservatively identify repeated page navigation and printed page markers."""
    tag = raw_tag(node)
    text = normalize_text(node.text_content()) if hasattr(node, "text_content") else ""
    style = compact_style(node)

    if tag == "hr" and "page-break-after:always" in style:
        return True

    if "page-break-after:always" in style and not text:
        return True

    if text.casefold().strip() == "table of contents":
        internal_links = node.xpath(".//a[starts-with(@href, '#')]")
        if tag == "a" and node.get("href", "").startswith("#"):
            return True
        if internal_links:
            return True

    if PAGE_NUMBER_PATTERN.fullmatch(text):
        context_styles = [style]
        for ancestor in list(node.iterancestors())[:4]:
            context_styles.append(compact_style(ancestor))
        page_context = "".join(context_styles)
        mobileye_footer = (
            "text-align:center" in page_context
            and "margin:24pt0pt0pt0pt" in page_context
        )
        tesla_footer = (
            "text-align:center" in page_context
            and "position:absolute" in page_context
            and "bottom:0" in page_context
        )
        if mobileye_footer or tesla_footer:
            return True

    return False


def drop_page_furniture(root) -> int:
    """Remove recognized page furniture while preserving section anchors."""
    candidates = []
    for node in list(root.iter()):
        if not is_page_furniture(node):
            continue
        if any(ancestor in candidates for ancestor in node.iterancestors()):
            continue
        candidates.append(node)

    removed = 0
    for node in candidates:
        if node.getparent() is not None:
            node.drop_tree()
            removed += 1
    return removed


def is_leaf_content_div(node) -> bool:
    """Return True when a div contains text but no nested block container."""
    if raw_tag(node) != "div" or not normalize_text(node.text_content()):
        return False

    nested_block_tags = {"div", "p", "table", "ul", "ol", "li"}
    return not any(
        raw_tag(descendant) in nested_block_tags
        for descendant in node.iterdescendants()
    )


def find_source_anchor(node) -> str | None:
    """Find an ID on the node or on a nearby empty preceding sibling."""
    if node.get("id"):
        return node.get("id")

    previous = node.getprevious()
    for _ in range(5):
        if previous is None:
            break
        previous_text = normalize_text(previous.text_content())
        if previous_text:
            break
        if previous.get("id"):
            return previous.get("id")
        previous = previous.getprevious()
    return None


def text_excluding_descendants(node, excluded_tags: set[str]) -> str:
    """Collect text in order while omitting selected nested structures."""
    fragments = []

    def collect(current_node) -> None:
        if current_node.text:
            fragments.append(current_node.text)
        for child in current_node:
            if raw_tag(child) not in excluded_tags:
                collect(child)
            if child.tail:
                fragments.append(child.tail)

    collect(node)
    return normalize_text("".join(fragments))


def has_heading_style(node) -> bool:
    """Check whether a node has common SEC heading presentation signals."""
    for candidate in [node, *node.iterdescendants()]:
        if is_bold_element(candidate):
            return True
    return False


def is_bold_element(node) -> bool:
    """Return True when an element explicitly renders its complete text as bold."""
    style = compact_style(node)
    return (
        raw_tag(node) in {"b", "strong"}
        or "font-weight:bold" in style
        or "font-weight:700" in style
    )


def is_subheading_candidate(node, text: str) -> bool:
    """Conservatively detect a short standalone block whose full text is bold."""
    if raw_tag(node) not in {"p", "div"} or not text or len(text) > 200:
        return False
    if len(text.split()) > 25 or text.endswith((".", "?", "!", ";")):
        return False
    if any(raw_tag(ancestor) in {"table", "li"} for ancestor in node.iterancestors()):
        return False
    if node.xpath(".//a[starts-with(@href, '#')]"):
        return False

    return any(
        is_bold_element(candidate)
        and normalize_text(candidate.text_content()) == text
        for candidate in [node, *node.iterdescendants()]
    )


def identify_item_section(node, text: str) -> str | None:
    """Return a canonical major Item heading, or None for ordinary text."""
    if len(text) > 250:
        return None

    match = ITEM_HEADING_PATTERN.fullmatch(text)
    if not match:
        return None
    if not (has_heading_style(node) or text.isupper()):
        return None

    item_number = match.group("number").upper()
    item_title = match.group("title").strip().rstrip(".")
    return f"Item {item_number} — {item_title}"
