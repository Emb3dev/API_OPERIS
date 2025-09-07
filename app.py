from fastapi import FastAPI, Query
from typing import Dict, Any, List, Optional
import re
import httpx
from bs4 import BeautifulSoup
from urllib.parse import urljoin

app = FastAPI(title="Login + Worklist + Approbation API (E1)")

# ---------------------------------------
# Helpers généraux
# ---------------------------------------
def norm(s: str) -> str:
    if not s:
        return ""
    s = s.replace("\xa0", " ").replace("\r", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", s).strip()

def pick_iframe_url(html: str, base_url: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    iframe = soup.find("iframe", {"id": "ptifrmtgtframe", "name": "TargetContent"})
    if not iframe or not iframe.get("src"):
        return base_url
    return urljoin(base_url, iframe["src"])

def safe_text_by_id(soup: BeautifulSoup, el_id: str) -> Optional[str]:
    el = soup.find(id=el_id)
    if not el:
        return None
    # input vs span
    if el.name in ("input", "textarea"):
        v = el.get("value", "")
        if v:
            return norm(v)
    return norm(el.get_text(" "))

def first_href_by_id(soup: BeautifulSoup, el_id: str) -> Optional[str]:
    a = soup.find(id=el_id)
    if a and a.name == "a":
        return a.get("href")
    # pour l’icône, l’<a> est le parent de l’<img>
    img = soup.find(id=f"{el_id}$IMG")
    if img and img.parent and img.parent.name == "a":
        return img.parent.get("href")
    return None

def find_indexed_texts(soup: BeautifulSoup, id_prefix: str) -> Dict[int, str]:
    """
    Récupère tous les spans (ou inputs) dont l'id suit le motif
    '{id_prefix}${index}' et renvoie {index:int -> texte:str}
    """
    out: Dict[int, str] = {}
    rx = re.compile(rf"^{re.escape(id_prefix)}\$(\d+)$")
    for el in soup.find_all(id=rx):
        m = rx.match(el["id"])
        if not m:
            continue
        idx = int(m.group(1))
        if el.name in ("input", "textarea"):
            txt = el.get("value", "")
        else:
            txt = el.get_text(" ")
        out[idx] = norm(txt)
    return out

def find_indexed_inputs_value(soup: BeautifulSoup, id_prefix: str) -> Dict[int, str]:
    out: Dict[int, str] = {}
    rx = re.compile(rf"^{re.escape(id_prefix)}\$(\d+)$")
    for inp in soup.find_all("input", id=rx):
        m = rx.match(inp["id"])
        if not m:
            continue
        idx = int(m.group(1))
        out[idx] = norm(inp.get("value", ""))
    return out

DATE_RE = re.compile(r"\d{2}/\d{2}/\d{4}")
AMOUNT_RE = re.compile(r"\d{1,3}(?: \d{3})*(?:[.,]\d{2})")

def clean_date(s: Optional[str]) -> Optional[str]:
    if not s:
        return s
    m = DATE_RE.search(s)
    return m.group(0) if m else s

def clean_amount(s: Optional[str]) -> Optional[str]:
    if not s:
        return s
    m = AMOUNT_RE.search(s)
    return m.group(0) if m else s

# ---------------------------------------
# Worklist (conserve l’ancienne impl. simple par iframe)
# ---------------------------------------
@app.get("/worklist")
async def worklist(
    jsessionid: str = Query(..., description="Cookie PPOPSGL1-PORTAL-PSJSESSIONID"),
    ps_token: str = Query(..., description="Cookie PS_TOKEN"),
    parse: bool = Query(False, description="Retour HTML brut (par défaut)"),
):
    url = ("http://ppopsglat02.app.eiffage.loc/psp/PPOPSGL1/EMPLOYEE/ERP/w/WORKLIST"
           "?ICAction=ICViewWorklist&Menu=Worklist&Market=GBL&PanelGroupName=WORKLIST")
    cookies = {
        "PPOPSGL1-PORTAL-PSJSESSIONID": jsessionid,
        "PS_TOKEN": ps_token,
    }
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.6",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": "Mozilla/5.0",
        "Referer": url + "&",
    }

    async with httpx.AsyncClient(follow_redirects=True, timeout=30, verify=False) as client:
        r1 = await client.get(url, headers=headers, cookies=cookies)
        r1.raise_for_status()
        iframe_url = pick_iframe_url(r1.text, str(r1.url))
        if iframe_url != str(r1.url):
            r2 = await client.get(iframe_url, headers=headers, cookies=cookies)
            r2.raise_for_status()
            return {"status_code": r2.status_code, "url": str(r2.url), "body": r2.text}
        return {"status_code": r1.status_code, "url": str(r1.url), "body": r1.text}

# ---------------------------------------
# Parse approbation (IDs réels du HTML fourni)
# ---------------------------------------
def parse_entete_appro(soup: BeautifulSoup) -> Dict[str, Any]:
    d: Dict[str, Any] = {}

    # En-tête principaux
    d["date_facture"] = clean_date(safe_text_by_id(soup, "E1_DFO_VCHH_VW_INVOICE_DT"))
    d["entite"] = safe_text_by_id(soup, "E1_DFO_VCHH_VW_BUSINESS_UNIT")
    d["entite_descr"] = safe_text_by_id(soup, "BUS_UNIT_TBL_FS_DESCR")
    d["voucher_id"] = safe_text_by_id(soup, "E1_DFO_VCHH_VW_VOUCHER_ID")
    d["origin"] = safe_text_by_id(soup, "ORIGIN_AP_DESCRSHORT")
    d["type_piece"] = safe_text_by_id(soup, "E1_DFO_VCHH_VW_VOUCHER_STYLE")
    d["num_facture"] = safe_text_by_id(soup, "E1_DFO_VCHH_VW_INVOICE_ID")

    # Fournisseur
    d["vendor_id"] = safe_text_by_id(soup, "E1_DFO_VCHH_VW_VENDOR_ID")
    d["vendor_name"] = safe_text_by_id(soup, "VENDOR_VW1_NAME1")
    d["vendor_addr1"] = safe_text_by_id(soup, "E1_DEFACTO_WRK_ADDRESS1_VNDR")
    d["vendor_city_zip"] = safe_text_by_id(soup, "E1_DEFACTO_WRK_ADDR_CITY_ST_POST")

    # Campagnes / échéances
    d["campagne"] = safe_text_by_id(soup, "E1_CAL_CDR_WRK_E1_PYMNT_FREQUENCY")
    d["date_paiement_prochain"] = clean_date(safe_text_by_id(soup, "E1_CAL_CDR_WRK_NEXT_PYMNT_DT"))
    d["date_limite_validation"] = clean_date(safe_text_by_id(soup, "E1_CAL_CDR_WRK_E1_DATE_LIM_VALID"))
    d["date_reglement_suivant"] = clean_date(safe_text_by_id(soup, "E1_CAL_CDR_WRK_E1_PYMNT_DT_SUIV"))

    # Montants (bloc “Détails sur le montant”)
    d["devise"] = safe_text_by_id(soup, "E1_DFO_VCHH_VW_TXN_CURRENCY_CD")
    d["montant_ht"] = clean_amount(safe_text_by_id(soup, "E1_DFO_VCHH_VW_MERCHANDISE_AMT"))
    d["mnt_frais_divers"] = clean_amount(safe_text_by_id(soup, "E1_DFO_VCHH_VW_MISC_AMT"))
    d["frais_port"] = clean_amount(safe_text_by_id(soup, "E1_DFO_VCHH_VW_FREIGHT_AMT"))
    d["montant_tva"] = clean_amount(safe_text_by_id(soup, "E1_DFO_VCHH_VW_VAT_ENTRD_AMT"))
    d["montant_ttc"] = clean_amount(safe_text_by_id(soup, "E1_DFO_VCHH_VW_GROSS_AMT"))
    # Libellé affiché = "Montant TTC validé" (id E1_DEFACTO_WRK_E1_DFO_MNT_HT_VAL)
    d["montant_ttc_valide"] = clean_amount(safe_text_by_id(soup, "E1_DEFACTO_WRK_E1_DFO_MNT_HT_VAL"))

    # Liens / ressources
    d["image_piece_url"] = first_href_by_id(soup, "E1_DEFACTO_WRK_E1_VCHR_HL_DEFACTO")
    d["pieces_jointes_url"] = first_href_by_id(soup, "E1_DEFACTO_WRK_E1_VCHR_HL_PJ")

    # Icônes d’alerte/matching (présence et title utile)
    exc = soup.find(id="E1_DEFACTO_WRK_COMMENTS_EXCEPT$IMG")
    if exc:
        d["matching_alert_title"] = exc.get("title")

    return d

def parse_voucher_lines(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    """
    Grilles 'Lignes factures' E1_DFO_VCHL_VW$0, $1, ...
    On reconstruit par index (suffixe $N).
    """
    # Champs principaux (côté facture)
    piece = find_indexed_texts(soup, "E1_DFO_VCHL_VW_VOUCHER_ID")
    line_num = find_indexed_texts(soup, "E1_DFO_VCHL_VW_VOUCHER_LINE_NUM")
    code = find_indexed_texts(soup, "E1_DFO_VCHL_VW_INV_ITEM_ID")
    descr = find_indexed_texts(soup, "E1_DFO_VCHL_VW_DESCR")
    qty = find_indexed_texts(soup, "E1_DFO_VCHL_VW_QTY_VCHR")
    uom = find_indexed_texts(soup, "E1_DFO_VCHL_VW_UNIT_OF_MEASURE")
    unit_price = find_indexed_texts(soup, "E1_DFO_VCHL_VW_UNIT_PRICE")
    merch_amt = find_indexed_texts(soup, "E1_DFO_VCHL_VW_MERCHANDISE_AMT")

    # Champs commande (PO)
    code_po = find_indexed_texts(soup, "E1_DFO_VCHL_VW_INV_ITEM_ID_PO")
    descr_po = find_indexed_texts(soup, "E1_DFO_VCHL_VW_PO_ORIG_DESCR254")
    qty_po = find_indexed_texts(soup, "E1_DFO_VCHL_VW_QTY_PO")
    uom_po = find_indexed_texts(soup, "E1_DFO_VCHL_VW_PO_ORIG_UOM")
    price_po = find_indexed_texts(soup, "E1_DFO_VCHL_VW_PRICE_PO")
    merch_amt_po = find_indexed_texts(soup, "E1_DFO_VCHL_VW_MERCH_AMT_PO")
    # Lien vers PO et Réception
    po_link = find_indexed_texts(soup, "E1_DEFACTO_WRK_URL_3")
    recv_link = find_indexed_texts(soup, "E1_DEFACTO_WRK_URL_4")

    # Champs réception
    recv_idx = find_indexed_texts(soup, "E1_DFO_VCHL_VW_RECV_LN_NBR")
    code_recv = find_indexed_texts(soup, "E1_DFO_VCHL_VW_INV_ITEM_ID_RECV")
    descr_recv = find_indexed_texts(soup, "E1_DFO_VCHL_VW_DESCR254_MIXED")
    qty_recv = find_indexed_texts(soup, "E1_DFO_VCHL_VW_QTY_SH_RECVD")
    uom_recv = find_indexed_texts(soup, "E1_DFO_VCHL_VW_RECEIVE_UOM")
    price_po_recv = find_indexed_texts(soup, "E1_DFO_VCHL_VW_PRICE_PO_RECV")
    merch_amt_recv = find_indexed_texts(soup, "E1_DFO_VCHL_VW_MERCH_AMT_RECV")

    # Commentaires/écart par ligne (icône)
    comments_icons = {}
    for img in soup.find_all("img", id=re.compile(r"^E1_DEFACTO_WRK_E1_COMMENTS_LINE\$IMG\$\d+$")):
        m = re.search(r"\$(\d+)$", img.get("id", ""))
        if m:
            comments_icons[int(m.group(1))] = {
                "alt": img.get("alt"),
                "title": img.get("title"),
            }

    all_indexes = set().union(
        piece.keys(), line_num.keys(), code.keys(), descr.keys(),
        qty.keys(), uom.keys(), unit_price.keys(), merch_amt.keys(),
        code_po.keys(), descr_po.keys(), qty_po.keys(), uom_po.keys(),
        price_po.keys(), merch_amt_po.keys(), recv_idx.keys(), code_recv.keys(),
        descr_recv.keys(), qty_recv.keys(), uom_recv.keys(), price_po_recv.keys(),
        merch_amt_recv.keys(), comments_icons.keys(), po_link.keys(), recv_link.keys()
    )
    rows: List[Dict[str, Any]] = []
    for i in sorted(all_indexes):
        row = {
            "voucher_id": piece.get(i),
            "line_num": line_num.get(i),
            "code_article": code.get(i),
            "description": descr.get(i),
            "qty": qty.get(i),
            "uom": uom.get(i),
            "unit_price": clean_amount(unit_price.get(i)),
            "montant_ht": clean_amount(merch_amt.get(i)),
            # PO
            "po_code_article": code_po.get(i),
            "po_description": descr_po.get(i),
            "po_qty": qty_po.get(i),
            "po_uom": uom_po.get(i),
            "po_unit_price": clean_amount(price_po.get(i)),
            "po_montant_ht": clean_amount(merch_amt_po.get(i)),
            "po_link_label": po_link.get(i),  # libellé <a>, souvent N° Cde
            # Réception
            "recv_line": recv_idx.get(i),
            "recv_code_article": code_recv.get(i),
            "recv_description": descr_recv.get(i),
            "recv_qty": qty_recv.get(i),
            "recv_uom": uom_recv.get(i),
            "recv_price_po": clean_amount(price_po_recv.get(i)),
            "recv_montant_ht": clean_amount(merch_amt_recv.get(i)),
            "recv_link_label": recv_link.get(i),
        }
        if i in comments_icons:
            row["comment_icon"] = comments_icons[i]
        rows.append(row)
    return rows

def parse_gl_distributions(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    """
    Grilles 'Eléments de clé GL' E1_DFO_VCHD_VW$0, $1, ...
    """
    distrib_num = find_indexed_texts(soup, "E1_DFO_VCHD_VW_DISTRIB_LINE_NUM")
    status_appr = find_indexed_texts(soup, "E1_DFO_VCHD_VW_E1_DFO_STATUS_APPR")
    motif = find_indexed_texts(soup, "E1_DFO_VCHD_VW_E1_MOTIF_APPR")
    bu_gl = find_indexed_texts(soup, "E1_DFO_VCHD_VW_BUSINESS_UNIT_GL")
    account = find_indexed_texts(soup, "E1_DFO_VCHD_VW_ACCOUNT")
    project = find_indexed_texts(soup, "E1_DFO_VCHD_VW_PROJECT_ID")
    activity = find_indexed_texts(soup, "E1_DFO_VCHD_VW_ACTIVITY_ID")
    product = find_indexed_texts(soup, "E1_DFO_VCHD_VW_PRODUCT")  # Rubrique
    qty = find_indexed_texts(soup, "E1_DFO_VCHD_VW_QTY_VCHR")
    merch_amt = find_indexed_texts(soup, "E1_DFO_VCHD_VW_MERCHANDISE_AMT")
    reliquat = find_indexed_texts(soup, "E1_DFO_MNT_RELIQ")
    # champ éditable montant à valider
    mnt_valid_input = find_indexed_inputs_value(soup, "E1_DFO_MNT_VALID")
    mnt_valide_cumule = find_indexed_texts(soup, "E1_DFO_VCHD_VW_E1_DFO_MNT_HT_VAL")

    all_idx = set().union(
        distrib_num.keys(), status_appr.keys(), motif.keys(), bu_gl.keys(),
        account.keys(), project.keys(), activity.keys(), product.keys(),
        qty.keys(), merch_amt.keys(), reliquat.keys(),
        mnt_valid_input.keys(), mnt_valide_cumule.keys()
    )
    rows: List[Dict[str, Any]] = []
    for i in sorted(all_idx):
        rows.append({
            "distrib_line": distrib_num.get(i),
            "statut_appro": status_appr.get(i),
            "motif": motif.get(i),
            "business_unit_gl": bu_gl.get(i),
            "compte": account.get(i),
            "projet": project.get(i),
            "activite": activity.get(i),
            "rubrique": product.get(i),
            "quantite": qty.get(i),
            "montant_ht": clean_amount(merch_amt.get(i)),
            "montant_reliquat": clean_amount(reliquat.get(i)),
            "montant_a_valider": clean_amount(mnt_valid_input.get(i)),
            "montant_valide_cumule": clean_amount(mnt_valide_cumule.get(i)),
        })
    return rows

def extract_approbation_details(html: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    data: Dict[str, Any] = {}

    # Titre
    title = soup.find("title")
    if title:
        data["page_title"] = norm(title.get_text(" "))

    # En-tête
    data["header"] = parse_entete_appro(soup)

    # Lignes facture
    data["lignes_facture"] = parse_voucher_lines(soup)

    # Distributions GL
    data["distributions_gl"] = parse_gl_distributions(soup)

    return data

# ---------------------------------------
# /approbation (params en Query)
# ---------------------------------------
@app.get("/approbation")
async def approbation(
    jsessionid: str = Query(..., description="Cookie PPOPSGL1-PORTAL-PSJSESSIONID"),
    ps_token: str = Query(..., description="Cookie PS_TOKEN"),
    business_unit: str = Query(..., description="Code BU (ex: 00185)"),
    voucher_id: str = Query(..., description="Numéro de pièce/facture (ex: 00450940)"),
    parse: bool = Query(True, description="True = JSON parsé; False = HTML brut."),
):
    base_url = (
        "http://ppopsglat02.app.eiffage.loc/psc/PPOPSGL1/EMPLOYEE/ERP/c/"
        "E1_DFO_APPR_MNU.E1_DFO_APPROBATION.GBL"
    )
    params = {
        "Page": "E1_DFO_APPROB_PGE",
        "Action": "U",
        "BUSINESS_UNIT": business_unit,
        "VOUCHER_ID": voucher_id,
    }
    cookies = {
        "PPOPSGL1-PORTAL-PSJSESSIONID": jsessionid,
        "PS_TOKEN": ps_token,
    }
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.6",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": "Mozilla/5.0",
        "Referer": "http://ppopsglat02.app.eiffage.loc/psp/PPOPSGL1/EMPLOYEE/ERP/w/WORKLIST",
    }

    async with httpx.AsyncClient(follow_redirects=True, timeout=30, verify=False) as client:
        r = await client.get(base_url, params=params, headers=headers, cookies=cookies)
        r.raise_for_status()

        html = r.text
        final_url = str(r.url)
        status = r.status_code

        if not parse:
            return {"status_code": status, "url": final_url, "body": html}

        info = extract_approbation_details(html)
        info["business_unit"] = business_unit
        info["voucher_id"] = voucher_id
        return {"status_code": status, "url": final_url, "details": info}

# ---------------------------------------
# (Optionnel) /login brut pour débogage cookie — aussi en Query
# ---------------------------------------
@app.get("/login")
async def login(
    userid: str = Query(..., description="Identifiant PeopleSoft"),
    pwd: str = Query(..., description="Mot de passe PeopleSoft"),
    parse: bool = Query(
        False,
        description="True = cookies seulement; False = réponse HTTP complète.",
    ),
):
    url = "http://ppopsglat02.app.eiffage.loc/psp/PPOPSGL1/EMPLOYEE/ERP/?&cmd=login&languageCd=FRA"
    payload = {
        "timezoneOffset": "-120",
        "ptmode": "f",
        "ptlangcd": "FRA",
        "ptinstalledlang": "ENG,FRA",
        "userid": userid,
        "pwd": pwd,
        "ptlangsel": "FRA",
    }
    headers = {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "content-type": "application/x-www-form-urlencoded",
        "upgrade-insecure-requests": "1",
    }
    async with httpx.AsyncClient(follow_redirects=False, verify=False) as client:
        resp = await client.post(url, data=payload, headers=headers)

    cookies = {k: v for k, v in resp.cookies.items()}
    if parse:
        return {"status_code": resp.status_code, "cookies": cookies}
    return {
        "status_code": resp.status_code,
        "headers": dict(resp.headers),
        "cookies": cookies,
        "body": resp.text,
    }
