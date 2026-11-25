"""Testy strony-wizytówki.

Uruchom:  python -m pytest -v
Bez sieci: python -m pytest -v -m "not network"
"""
from __future__ import annotations

import re
import ssl
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "index.html"
BASE_URL = "https://astyu27.github.io/STRONA_O_MNIE/"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36"
try:
    import certifi
    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:  # pragma: no cover
    SSL_CTX = ssl.create_default_context()
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class Page(HTMLParser):
    """Zbiera linki, zasoby, meta-tagi, pary tłumaczeń i tekst per język."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []
        self.ids: set[str] = set()
        self.assets: list[str] = []
        self.meta: dict[str, str] = {}
        self.lang_children: dict[int, dict[str, int]] = {}  # id rodzica -> {pl: n, en: n}
        self.headings: list[int] = []
        self.blank_links_without_noopener: list[str] = []
        self.stack: list[tuple[str, int, str | None]] = []  # (tag, id, lang)
        self.text: dict[str, list[str]] = {"pl": [], "en": [], "both": []}
        self._next_id = 0

    def _current_lang(self) -> str:
        for _, _, lang in reversed(self.stack):
            if lang in ("pl", "en"):
                return lang
        return "both"

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get("id"):
            self.ids.add(a["id"])
        if tag == "a" and a.get("href"):
            self.links.append(a["href"])
            if a.get("target") == "_blank" and "noopener" not in (a.get("rel") or ""):
                self.blank_links_without_noopener.append(a["href"])
        if tag in ("img", "script") and a.get("src"):
            self.assets.append(a["src"])
        if tag == "link" and a.get("rel") == "icon":
            self.assets.append(a["href"])
        if tag == "meta":
            key = a.get("property") or a.get("name")
            if key:
                self.meta[key] = a.get("content", "")
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.headings.append(int(tag[1]))
        lang = a.get("lang") if tag != "html" else None
        if lang in ("pl", "en") and self.stack:
            counts = self.lang_children.setdefault(self.stack[-1][1], {"pl": 0, "en": 0})
            counts[lang] += 1
        if tag not in VOID:
            self._next_id += 1
            self.stack.append((tag, self._next_id, lang))

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        while self.stack:
            if self.stack.pop()[0] == tag:
                break

    def handle_data(self, data):
        if data.strip() and not any(t in ("script", "style") for t, _, _ in self.stack):
            self.text[self._current_lang()].append(data.strip())


@pytest.fixture(scope="module")
def page() -> Page:
    p = Page()
    p.feed(INDEX.read_text(encoding="utf-8"))
    return p


def visible_text(page: Page, lang: str) -> str:
    return " ".join(page.text["both"] + page.text[lang])


# --- struktura -------------------------------------------------------------

def test_kazdy_tekst_ma_wersje_pl_i_en(page):
    bad = {k: v for k, v in page.lang_children.items() if v["pl"] != v["en"]}
    assert not bad, f"Niesparowane tłumaczenia: {bad}"


def test_naglowki_nie_przeskakuja_poziomow(page):
    assert page.headings[0] == 1
    for prev, cur in zip(page.headings, page.headings[1:]):
        assert cur <= prev + 1, f"Przeskok nagłówka h{prev} -> h{cur}"


def test_lokalne_zasoby_istnieja(page):
    local = [s for s in page.assets if not s.startswith(("http:", "https:", "//", "data:"))]
    assert local, "Brak lokalnych zasobów — parser coś zgubił"
    for src in local:
        assert (ROOT / src).is_file(), f"Brak pliku {src}"


def test_obrazek_podgladu_linku(page):
    og = page.meta["og:image"]
    assert og.startswith(BASE_URL)
    assert (ROOT / og[len(BASE_URL):]).is_file()
    assert page.meta["og:url"] == BASE_URL
    assert page.meta.get("og:title") and page.meta.get("og:description")


def test_linki_w_nowej_karcie_sa_bezpieczne(page):
    assert not page.blank_links_without_noopener


def test_mailto_poprawny_i_jeden_adres(page):
    mails = {l for l in page.links if l.startswith("mailto:")}
    assert len(mails) == 1
    assert re.fullmatch(r"mailto:[\w.+-]+@[\w-]+(\.[\w-]+)+", mails.pop())


# --- treść -----------------------------------------------------------------

@pytest.mark.parametrize("lang", ["pl", "en"])
def test_glowne_projekty_sa_na_stronie(page, lang):
    text = visible_text(page, lang)
    for name in ("Film Recommender", "Znowu SKM", "TGE Monitor"):
        assert name in text


@pytest.mark.parametrize("lang", ["pl", "en"])
def test_usuniete_projekty_zniknely(page, lang):
    text = visible_text(page, lang)
    assert "TV Remote" not in text
    assert "rozkład SKM" not in text and "SKM timetable" not in text
    assert not any("SKM-CHYLONIA" in l for l in page.links)


@pytest.mark.parametrize("lang,phrases", [
    ("pl", ["Szukasz kogoś", "na rozmowie", "zatrudnij", "szukam pracy"]),
    ("en", ["Looking for someone", "interview", "hire me", "open to work"]),
])
def test_ton_wizytowki_a_nie_szukania_pracy(page, lang, phrases):
    text = visible_text(page, lang).lower()
    for p in phrases:
        assert p.lower() not in text, f"Fraza w stylu szukania pracy: {p!r}"


def test_tge_ma_link_do_demo_i_haslo(page):
    assert any(l.startswith("https://znowuskm.tail37d1bd.ts.net:10000") for l in page.links)
    assert "demo" in visible_text(page, "pl").lower()


@pytest.mark.parametrize("lang", ["pl", "en"])
def test_jest_ai_agenci_i_szkolenia(page, lang):
    text = visible_text(page, lang)
    for needle in ("agent", "AI", "Google", "SGH"):
        assert needle in text, f"Brak '{needle}' w wersji {lang}"


def test_kotwice_prowadza_do_istniejacych_sekcji(page):
    anchors = [l[1:] for l in page.links if l.startswith("#")]
    assert len(anchors) >= 4
    for a in anchors:
        assert a in page.ids, f"Brak sekcji #{a}"


@pytest.mark.parametrize("lang", ["pl", "en"])
def test_uczciwy_tytul_i_sciezka(page, lang):
    text = visible_text(page, lang)
    # oficjalne stanowisko, nie "Data Scientist w Enerdze"
    assert ("Wydział Polityki Cenowej" in text) or ("Pricing Policy Department" in text)
    assert "Data Scientist" not in text
    for needle in ("Sii", "Uniwersytet Gdański" if lang == "pl" else "University of Gdańsk",
                   "Microsoft", "Big Data"):
        assert needle in text, f"Brak '{needle}' w wersji {lang}"


def test_sgh_ukonczone_z_projektem_filmowym(page):
    text = visible_text(page, "pl")
    assert "ukończone" in text and "Projekt dyplomowy: Film Recommender" in text


# --- sieć ------------------------------------------------------------------

def _status(url: str) -> int:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=20, context=SSL_CTX) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


# LinkedIn odpowiada botom kodem 999 (strona istnieje, ale nie wpuszcza skryptów).
BOT_BLOCKED = {"www.linkedin.com": {999}}
# Demo SKM stoi na domowym PC — gdy komputer śpi, link chwilowo nie działa.
HOME_SERVER = "tail37d1bd.ts.net"


@pytest.mark.network
def test_linki_zewnetrzne_dzialaja(page):
    failures, home_down = [], []
    for url in sorted({l for l in page.links if l.startswith("http")}):
        host = url.split("/")[2]
        try:
            code: int | str = _status(url)
        except Exception as e:  # noqa: BLE001 — timeout / DNS
            code = repr(e)
        if isinstance(code, int) and (code < 400 or code in BOT_BLOCKED.get(host, set())):
            continue
        (home_down if HOME_SERVER in host else failures).append(f"{url} -> {code}")
    assert not failures, "\n".join(failures)
    if home_down:
        pytest.skip("Demo na domowym serwerze niedostępne: " + "; ".join(home_down))


@pytest.mark.network
def test_opublikowana_strona_i_zasoby(page):
    assert _status(BASE_URL) == 200
    local = [s for s in page.assets if not s.startswith("http")]
    for src in ["assets/og.jpg", *local]:
        assert _status(BASE_URL + src) == 200, src
