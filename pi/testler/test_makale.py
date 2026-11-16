"""Makale araştırma modülü (ağ gerektirmez: sahte XML ile)."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import makale  # noqa: E402

XML = b"""<?xml version="1.0"?>
<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>12345</PMID>
<Article><Journal><JournalIssue><PubDate><Year>2020</Year></PubDate></JournalIssue><Title>Neuroradiology</Title></Journal>
<ArticleTitle>Posterior fossa tumors: <i>imaging</i> review</ArticleTitle>
<Abstract><AbstractText Label="Amac">Ayirici tani.</AbstractText><AbstractText>Sonuc metni.</AbstractText></Abstract>
<PublicationTypeList><PublicationType>Review</PublicationType></PublicationTypeList>
</Article></MedlineCitation></PubmedArticle></PubmedArticleSet>"""


def test_ayikla():
    m = makale.makaleleri_ayikla(XML)
    assert len(m) == 1
    assert m[0]["pmid"] == "12345" and m[0]["yil"] == "2020"
    assert "imaging" in m[0]["baslik"]
    assert "Amac: Ayirici tani." in m[0]["ozet"]


def test_bicim():
    t = makale.bicimle(makale.makaleleri_ayikla(XML), "q")
    assert "PMID 12345" in t
    assert "sonuç bulamadım" in makale.bicimle([], "q")


def test_arac_tanimli_ve_mesaj_bos():
    import asyncio
    import brain
    assert any(t.get("name") == "makale_ara" for t in brain.TOOLS)
    assert "boş" in asyncio.run(makale.makale_ara(""))
    assert "makale_ara" in brain.radyoloji_kurallari()


if __name__ == "__main__":
    test_ayikla()
    test_bicim()
    test_arac_tanimli_ve_mesaj_bos()
    print("tamam")
