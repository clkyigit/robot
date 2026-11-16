"""Makale araştırma: PubMed'de (NCBI E-utilities, ücretsiz) arama yapar.

Radyolojik bir bulgu için ayırıcı tanı konuşulurken, robotun elindeki bilgiyi
gerçek makalelerle desteklemesi için kullanılır. Sadece standart kütüphane.
İsteğe bağlı: NCBI_API_KEY ortam değişkeni (limit artar); yoksa anahtarsız çalışır.
"""

import asyncio
import json
import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
TIMEOUT = 20
MAX_SONUC = 6


def _istek(yol: str, **params) -> bytes:
    key = os.environ.get("NCBI_API_KEY", "").strip()
    if key:
        params["api_key"] = key
    params["tool"] = "piko-robot"
    url = f"{EUTILS}/{yol}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "piko-robot/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:  # noqa: S310
        return r.read()


def _kisalt(metin: str, n: int) -> str:
    metin = " ".join(metin.split())
    return metin if len(metin) <= n else metin[:n].rsplit(" ", 1)[0] + " …"


def makaleleri_ayikla(xml_bytes: bytes, ozet_uzunluk: int = 700) -> list[dict]:
    """PubMed efetch XML'inden makale özetlerini çıkarır."""
    out = []
    root = ET.fromstring(xml_bytes)
    for art in root.iter("PubmedArticle"):
        pmid = (art.findtext(".//MedlineCitation/PMID") or "").strip()
        baslik = "".join(art.find(".//ArticleTitle").itertext()) if art.find(".//ArticleTitle") is not None else ""
        dergi = art.findtext(".//Journal/Title") or ""
        yil = (art.findtext(".//JournalIssue/PubDate/Year")
               or (art.findtext(".//JournalIssue/PubDate/MedlineDate") or "")[:4])
        parcalar = []
        for ab in art.findall(".//Abstract/AbstractText"):
            etiket = ab.get("Label")
            metin = "".join(ab.itertext()).strip()
            parcalar.append(f"{etiket}: {metin}" if etiket else metin)
        tur = [pt.text for pt in art.findall(".//PublicationType") if pt.text]
        out.append({"pmid": pmid, "baslik": _kisalt(baslik, 250), "dergi": dergi, "yil": yil,
                    "tur": tur[:3], "ozet": _kisalt(" ".join(parcalar), ozet_uzunluk)})
    return out


def bicimle(makaleler: list[dict], sorgu: str) -> str:
    if not makaleler:
        return f"PubMed'de '{sorgu}' için sonuç bulamadım. Sorguyu sadeleştirip (daha az terim) tekrar deneyebilirsin."
    satirlar = [f"PubMed sonuçları ('{sorgu}'), {len(makaleler)} makale. Yalnızca bu listede olanları kaynak göster; "
                "özetten fazlasını uydurma, tam metni okumadığını belirt:"]
    for i, m in enumerate(makaleler, 1):
        tur = f" [{', '.join(m['tur'])}]" if m["tur"] else ""
        satirlar.append(f"{i}. {m['baslik']} — {m['dergi']}, {m['yil']}{tur} (PMID {m['pmid']})\n"
                        f"   Özet: {m['ozet'] or '(özet yok)'}")
    return "\n".join(satirlar)


def _ara_sync(sorgu: str, adet: int) -> str:
    adet = max(1, min(int(adet), MAX_SONUC))
    arama = json.loads(_istek("esearch.fcgi", db="pubmed", term=sorgu, retmax=adet,
                              sort="relevance", retmode="json"))
    idler = arama.get("esearchresult", {}).get("idlist", [])
    if not idler:
        return bicimle([], sorgu)
    xml_bytes = _istek("efetch.fcgi", db="pubmed", id=",".join(idler), retmode="xml")
    return bicimle(makaleleri_ayikla(xml_bytes), sorgu)


async def makale_ara(sorgu: str, adet: int = 5) -> str:
    """PubMed'de arar; hata olursa anlaşılır bir mesaj döner (asla fırlatmaz)."""
    sorgu = (sorgu or "").strip()
    if not sorgu:
        return "Arama sorgusu boş."
    try:
        return await asyncio.wait_for(asyncio.to_thread(_ara_sync, sorgu[:300], adet), timeout=60)
    except Exception as e:  # noqa: BLE001
        return f"Makale araması başarısız oldu ({e.__class__.__name__}: {str(e)[:100]}). İnternet bağlantısını kontrol et; uydurma kaynak verme."
