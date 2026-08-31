"""Suryaan Bank -- CAMT.053 (ISO 20022) bank statement export.

This partner sends a REAL, standardised format rather than a proprietary
flat file: camt.053.001.02, the ISO 20022 "BankToCustomerStatement"
message. That choice is deliberate -- RBI has been migrating RTGS/NEFT
reporting onto ISO 20022, so CAMT.053 is what a real Indian banking
partner is most likely to hand Razorpay, and it is what mature accounting
systems (Odoo, SAP, NetSuite) import. Northbridge deliberately stays on a
proprietary camelCase flat export, because real life is always a mix of
the two -- which is the entire reason a normalization boundary exists.

CAMT.053 is genuinely harder than CSV in ways that matter:
  - it is hierarchical, not tabular: one <Stmt> per account, each holding
    many <Ntry> entries, with the payment reference buried in
    Ntry/NtryDtls/TxDtls/Refs/EndToEndId
  - it is namespaced, so every element lookup is namespace-qualified
  - credit/debit is a separate <CdtDbtInd> element rather than a sign
  - a missing reference is the literal sentinel "NOTPROVIDED", not an
    empty string

Element -> canonical mapping:
    Ntry/NtryRef                              -> bank_txn_id
    Ntry/AcctSvcrRef                          -> settlement_posting_id
    Ntry/NtryDtls/TxDtls/Refs/EndToEndId      -> utr  ("NOTPROVIDED" -> None)
    Ntry/Amt                                  -> credit_amount_rupees
    Ntry/BookgDt/Dt                           -> credit_date
    Ntry/ValDt/Dt                             -> value_date
    Ntry/NtryDtls/TxDtls/RmtInf/Ustrd         -> narration
    Stmt/Acct/Id/Othr/Id                      -> bank_account_id
    Ntry/CdtDbtInd  (CRDT)                    -> transaction_type
"""

import os
import datetime as dt
import xml.etree.ElementTree as ET

import pandas as pd

from ..config import ingestion_rand_id
from .base import CANONICAL_COLUMNS

CAMT_NS = "urn:iso:std:iso:20022:tech:xsd:camt.053.001.02"
_NS = {"c": CAMT_NS}

# The flat intermediate shape, named after the CAMT elements it maps to, so
# to_raw()'s output is self-documenting and the round-trip stays inspectable
# as a DataFrame even though the on-disk form is XML.
RAW_COLUMNS = [
    "NtryRef", "AcctSvcrRef", "EndToEndId", "Amt",
    "ValDt", "BookgDt", "Ustrd", "Acct_Othr_Id", "CdtDbtInd",
]

# ISO 20022 sentinel for "no reference supplied" -- a real CAMT feed uses
# this rather than omitting the element or sending an empty string.
NOT_PROVIDED = "NOTPROVIDED"

_TYPE_TO_RAW = {"credit": "CRDT"}
_TYPE_FROM_RAW = {"CRDT": "credit"}

CURRENCY = "INR"


def to_raw(canonical_df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, r in canonical_df.iterrows():
        utr = r["utr"]
        rows.append({
            "NtryRef": ingestion_rand_id("srybnk", 12),
            "AcctSvcrRef": r["settlement_posting_id"],
            "EndToEndId": NOT_PROVIDED if pd.isna(utr) or utr == "" else utr,
            "Amt": f"{r['credit_amount_rupees']:.2f}",
            "ValDt": r["value_date"],
            "BookgDt": r["credit_date"],
            "Ustrd": r["narration"],
            "Acct_Othr_Id": r["bank_account_id"],
            "CdtDbtInd": _TYPE_TO_RAW.get(r["transaction_type"], r["transaction_type"]),
        })
    return pd.DataFrame(rows, columns=RAW_COLUMNS)


def write_raw(raw_df: pd.DataFrame, raw_dir: str, partner_name: str) -> str:
    """Serialize to a real camt.053.001.02 document. Entries are grouped
    into one <Stmt> per account, which is how an actual bank statement
    message is structured -- not one flat list."""
    doc = ET.Element(f"{{{CAMT_NS}}}Document")
    stmt_root = ET.SubElement(doc, f"{{{CAMT_NS}}}BkToCstmrStmt")

    hdr = ET.SubElement(stmt_root, f"{{{CAMT_NS}}}GrpHdr")
    ET.SubElement(hdr, f"{{{CAMT_NS}}}MsgId").text = ingestion_rand_id("msg", 10)
    ET.SubElement(hdr, f"{{{CAMT_NS}}}CreDtTm").text = dt.datetime(
        2026, 8, 1, 0, 0, 0).isoformat()

    for account_id, grp in raw_df.groupby("Acct_Othr_Id", sort=True):
        stmt = ET.SubElement(stmt_root, f"{{{CAMT_NS}}}Stmt")
        ET.SubElement(stmt, f"{{{CAMT_NS}}}Id").text = ingestion_rand_id("stmt", 10)
        acct = ET.SubElement(stmt, f"{{{CAMT_NS}}}Acct")
        acct_id = ET.SubElement(acct, f"{{{CAMT_NS}}}Id")
        othr = ET.SubElement(acct_id, f"{{{CAMT_NS}}}Othr")
        ET.SubElement(othr, f"{{{CAMT_NS}}}Id").text = str(account_id)

        for _, r in grp.iterrows():
            ntry = ET.SubElement(stmt, f"{{{CAMT_NS}}}Ntry")
            ET.SubElement(ntry, f"{{{CAMT_NS}}}NtryRef").text = str(r["NtryRef"])
            amt = ET.SubElement(ntry, f"{{{CAMT_NS}}}Amt")
            amt.set("Ccy", CURRENCY)
            amt.text = str(r["Amt"])
            ET.SubElement(ntry, f"{{{CAMT_NS}}}CdtDbtInd").text = str(r["CdtDbtInd"])
            bookg = ET.SubElement(ntry, f"{{{CAMT_NS}}}BookgDt")
            ET.SubElement(bookg, f"{{{CAMT_NS}}}Dt").text = str(r["BookgDt"])
            vald = ET.SubElement(ntry, f"{{{CAMT_NS}}}ValDt")
            ET.SubElement(vald, f"{{{CAMT_NS}}}Dt").text = str(r["ValDt"])
            ET.SubElement(ntry, f"{{{CAMT_NS}}}AcctSvcrRef").text = str(r["AcctSvcrRef"])

            dtls = ET.SubElement(ntry, f"{{{CAMT_NS}}}NtryDtls")
            tx = ET.SubElement(dtls, f"{{{CAMT_NS}}}TxDtls")
            refs = ET.SubElement(tx, f"{{{CAMT_NS}}}Refs")
            ET.SubElement(refs, f"{{{CAMT_NS}}}EndToEndId").text = str(r["EndToEndId"])
            rmt = ET.SubElement(tx, f"{{{CAMT_NS}}}RmtInf")
            ET.SubElement(rmt, f"{{{CAMT_NS}}}Ustrd").text = str(r["Ustrd"])

    path = os.path.join(raw_dir, f"{partner_name}.xml")
    ET.ElementTree(doc).write(path, encoding="utf-8", xml_declaration=True)
    return path


def read_raw(path: str) -> pd.DataFrame:
    """Parse a camt.053 document back into the flat RAW_COLUMNS shape.
    Account id is inherited from the enclosing <Stmt>, which is the part a
    naive flat-file reader would lose entirely."""
    tree = ET.parse(path)
    rows = []
    for stmt in tree.getroot().findall(".//c:Stmt", _NS):
        acct_el = stmt.find("./c:Acct/c:Id/c:Othr/c:Id", _NS)
        account_id = acct_el.text if acct_el is not None else None
        for ntry in stmt.findall("./c:Ntry", _NS):
            def _text(rel, default=""):
                el = ntry.find(rel, _NS)
                return el.text if el is not None and el.text is not None else default
            rows.append({
                "NtryRef": _text("./c:NtryRef"),
                "AcctSvcrRef": _text("./c:AcctSvcrRef"),
                "EndToEndId": _text("./c:NtryDtls/c:TxDtls/c:Refs/c:EndToEndId", NOT_PROVIDED),
                "Amt": _text("./c:Amt"),
                "ValDt": _text("./c:ValDt/c:Dt"),
                "BookgDt": _text("./c:BookgDt/c:Dt"),
                "Ustrd": _text("./c:NtryDtls/c:TxDtls/c:RmtInf/c:Ustrd"),
                "Acct_Othr_Id": account_id,
                "CdtDbtInd": _text("./c:CdtDbtInd"),
            })
    return pd.DataFrame(rows, columns=RAW_COLUMNS)


def normalize(raw_df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, r in raw_df.iterrows():
        if r["CdtDbtInd"] not in _TYPE_FROM_RAW:
            raise ValueError(
                f"Suryaan connector: unsupported CdtDbtInd {r['CdtDbtInd']!r} for "
                f"NtryRef={r['NtryRef']!r} -- known values: {sorted(_TYPE_FROM_RAW)}. "
                f"Fails loudly rather than silently passing an unrecognized value "
                f"through to canonical data. A DBIT entry on a settlement credit feed "
                f"would be a genuine upstream problem, not something to coerce."
            )
        e2e = r["EndToEndId"]
        rows.append({
            "bank_txn_id": r["NtryRef"],
            "settlement_posting_id": r["AcctSvcrRef"],
            # ISO 20022's explicit "no reference" sentinel maps back to a real
            # null, so downstream missing_bank_reference logic sees None the
            # same way it does from the CSV partner.
            "utr": None if (pd.isna(e2e) or e2e in ("", NOT_PROVIDED)) else e2e,
            "credit_amount_rupees": round(float(r["Amt"]), 2),
            "credit_date": r["BookgDt"],
            "value_date": r["ValDt"],
            "narration": r["Ustrd"],
            "bank_account_id": r["Acct_Othr_Id"],
            "transaction_type": _TYPE_FROM_RAW[r["CdtDbtInd"]],
        })
    return pd.DataFrame(rows, columns=CANONICAL_COLUMNS)
