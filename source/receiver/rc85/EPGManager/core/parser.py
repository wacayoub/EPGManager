# -*- coding: utf-8 -*-
"""
core/parser.py

Shared "primary selector -> fallback selector -> alternative parser ->
validation" helpers (spec section 21). Every source module already had its
own version of this pattern (e.g. Medi1TV's `soup.select(...) or
soup.find_all(...)` chain, 2M/Chada's `tree.xpath(a) or tree.xpath(b)`);
this module gives them one shared, testable implementation instead of
subtly-different copies.

A SOURCE_WARNING is raised (as a plain exception the source module catches)
rather than silently returning an empty list, so the difference between
"the site is fine but had genuinely nothing to show" and "our selectors
broke" is visible in the logs/status screen instead of being swallowed.
"""

from . import dependencies
from .logger import get_logger

log = get_logger(__name__)


class SourceWarning(Exception):
    """Raised by a source when no programmes could be extracted, so the
    manager can surface a WARNING status instead of a silent empty file."""
    pass


def get_soup(html_text):
    """Build a BeautifulSoup document using the best available backend,
    with an automatic html.parser fallback if lxml isn't installed."""
    if not dependencies.check_module("bs4"):
        return None
    from bs4 import BeautifulSoup
    parser_name = dependencies.get_html_parser_name()
    try:
        return BeautifulSoup(html_text, parser_name)
    except Exception:
        return BeautifulSoup(html_text, "html.parser")


def get_lxml_tree(html_text):
    """Build an lxml.etree tree if lxml is available, else None (caller
    should fall back to get_soup())."""
    if not dependencies.check_module("lxml"):
        return None
    from lxml import etree
    try:
        return etree.HTML(html_text)
    except Exception:
        return None


def try_selectors(soup, selectors):
    """Try a list of (tag_names, class_list) or CSS-selector strings in
    order, return the first non-empty result set. `selectors` is a list of
    callables taking `soup` and returning a list (possibly empty)."""
    for selector_fn in selectors:
        try:
            result = selector_fn(soup)
            if result:
                return result
        except Exception as e:
            log.debug("Selector strategy failed: %s", e)
            continue
    return []


def require_nonempty(items, source_name, what="programmes"):
    if not items:
        raise SourceWarning(
            "%s: no %s could be extracted with any known selector - the "
            "site's HTML structure may have changed" % (source_name, what)
        )
    return items
