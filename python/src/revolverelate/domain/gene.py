"""Pineoblastoma gene sample: public FASTA + literature abstracts + KPI fact rows."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from revolverelate.domain.fasta import NCBI_EFETCH, baked_records, parse_fasta

# Public literature cues (DICER1 syndrome, RB1 / trilateral retinoblastoma, DROSHA/DGCR8 microprocessor).
# Abstracts use because/therefore/caused so chunk_causal can bind; not a discovery claim.
_GENES = [
    (
        1,
        "DICER1",
        "23405",
        "Q9UPY3",
        "NP_803187.1",
        "DICER1 encodes the RNase III that cleaves pre-miRNA. Germline DICER1 mutation causes DICER1 syndrome. Therefore pineoblastoma risk rises when miRNA processing fails.",
    ),
    (
        2,
        "RB1",
        "5925",
        "P06400",
        "NP_000312.2",
        "RB1 encodes the retinoblastoma pocket protein. Biallelic RB1 loss causes pineoblastoma in heritable retinoblastoma because both copies are inactivated.",
    ),
    (
        3,
        "DROSHA",
        "29102",
        "Q9NRR4",
        "NP_037367.3",
        "DROSHA is the nuclear RNase III of the microprocessor. Somatic DROSHA mutation causes pineoblastoma because pri-miRNA cleavage fails; therefore let-7/miR-98-5p declines.",
    ),
    (
        4,
        "DGCR8",
        "54487",
        "Q8WYQ5",
        "NP_073557.3",
        "DGCR8 partners with DROSHA. DGCR8 mutation causes pineoblastoma because the microprocessor complex is disrupted.",
    ),
]

_DISEASES = [
    (
        1,
        "Pineoblastoma",
        "pinealblastoma pineal blastoma",
        "Pineoblastoma is a pineal parenchymal tumor. It is caused by germline DICER1 mutation or biallelic RB1 loss. DROSHA and DGCR8 mutations also cause the disease because miRNA biogenesis is disrupted and PLAGL2/CCND2 are derepressed. Therefore cases cluster in DICER1 syndrome and heritable retinoblastoma.",
    ),
    (
        2,
        "DICER1 syndrome",
        "dicer1-related tumor predisposition",
        "DICER1 syndrome is caused by germline DICER1 loss-of-function. Therefore several rare tumors, including pineoblastoma, appear in carriers.",
    ),
]

_LINKS = [
    # LinkId, GeneId, DiseaseId, Role, Evidence, AssociationScore, Cases, LoFCount
    (
        1,
        1,
        1,
        "germline",
        "Pineoblastoma is caused by germline DICER1 mutation. Therefore association score is high in DICER1 syndrome pedigrees.",
        0.92,
        28.0,
        11.0,
    ),
    (
        2,
        2,
        1,
        "biallelic-loss",
        "RB1 loss causes pineoblastoma because both alleles are inactivated in trilateral retinoblastoma.",
        0.81,
        17.0,
        9.0,
    ),
    (
        3,
        3,
        1,
        "somatic",
        "DROSHA mutation causes pineoblastoma because microprocessor cleavage fails; therefore miRNA maturation drops.",
        0.74,
        12.0,
        6.0,
    ),
    (
        4,
        4,
        1,
        "somatic",
        "DGCR8 mutation causes pineoblastoma because DROSHA cannot bind substrate. Therefore cases co-occur with DROSHA hits.",
        0.69,
        8.0,
        4.0,
    ),
    (
        5,
        1,
        2,
        "germline",
        "DICER1 syndrome is caused by germline DICER1 LoF. Therefore pineoblastoma is one of the associated tumors.",
        0.95,
        40.0,
        14.0,
    ),
]

_ACC_TO_GENE = {
    "NP_803187.1": 1,
    "NP_000312.2": 2,
    "NP_037367.3": 3,
    "NP_073557.3": 4,
}


def write_gene_pineal(path: str | Path, *, fasta_text: str | None = None) -> Path:
    """Write a tiny public gene/FASTA sqlite the agent can rr_boot and ask."""
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    records = parse_fasta(fasta_text) if fasta_text else baked_records()
    conn = sqlite3.connect(str(dest))
    conn.executescript(
        """
        CREATE TABLE Gene (
            GeneId INTEGER PRIMARY KEY,
            Symbol TEXT NOT NULL,
            NcbiGeneId TEXT,
            Uniprot TEXT,
            Accession TEXT,
            Summary TEXT
        );
        CREATE TABLE Fasta (
            FastaId INTEGER PRIMARY KEY,
            GeneId INTEGER NOT NULL,
            Accession TEXT,
            Header TEXT,
            Sequence TEXT,
            Length INTEGER,
            SourceUrl TEXT,
            FOREIGN KEY (GeneId) REFERENCES Gene(GeneId)
        );
        CREATE TABLE Disease (
            DiseaseId INTEGER PRIMARY KEY,
            DiseaseName TEXT,
            Alias TEXT,
            Abstract TEXT
        );
        CREATE TABLE GeneDisease (
            LinkId INTEGER PRIMARY KEY,
            GeneId INTEGER NOT NULL,
            DiseaseId INTEGER NOT NULL,
            Role TEXT,
            Evidence TEXT,
            AssociationScore REAL,
            Cases REAL,
            LoFCount REAL,
            FOREIGN KEY (GeneId) REFERENCES Gene(GeneId),
            FOREIGN KEY (DiseaseId) REFERENCES Disease(DiseaseId)
        );
        """
    )
    conn.executemany("INSERT INTO Gene VALUES (?,?,?,?,?,?)", _GENES)
    conn.executemany("INSERT INTO Disease VALUES (?,?,?,?)", _DISEASES)
    conn.executemany("INSERT INTO GeneDisease VALUES (?,?,?,?,?,?,?,?)", _LINKS)
    fasta_rows = []
    for i, rec in enumerate(records, start=1):
        header = str(rec.get("header") or "")
        acc = header.split()[0] if header else ""
        gene_id = _ACC_TO_GENE.get(acc)
        if gene_id is None:
            for key, gid in _ACC_TO_GENE.items():
                if key in header:
                    gene_id = gid
                    acc = key
                    break
        if gene_id is None:
            continue
        seq = str(rec.get("sequence") or "")
        fasta_rows.append(
            (
                i,
                gene_id,
                acc,
                header,
                seq,
                int(rec.get("length") or len(seq)),
                NCBI_EFETCH.format(accession=acc),
            )
        )
    conn.executemany("INSERT INTO Fasta VALUES (?,?,?,?,?,?,?)", fasta_rows)
    conn.commit()
    conn.close()
    return dest


def list_symbols(conn) -> set[str]:
    try:
        rows = conn.execute("SELECT Symbol FROM Gene").fetchall()
    except Exception:
        return set()
    return {str(r[0]) for r in rows if r and r[0]}


def append_follow_on(conn, records: list[dict]) -> list[str]:
    """Insert catalogued follow-on genes into an existing gene sqlite. No invented FASTA."""
    added: list[str] = []
    have = list_symbols(conn)
    disease_rows = {str(r[1]): int(r[0]) for r in conn.execute("SELECT DiseaseId, DiseaseName FROM Disease")}
    next_gene = int(conn.execute("SELECT COALESCE(MAX(GeneId), 0) FROM Gene").fetchone()[0]) + 1
    next_link = int(conn.execute("SELECT COALESCE(MAX(LinkId), 0) FROM GeneDisease").fetchone()[0]) + 1
    next_fasta = int(conn.execute("SELECT COALESCE(MAX(FastaId), 0) FROM Fasta").fetchone()[0]) + 1
    for rec in records or []:
        symbol = str(rec.get("symbol") or "").strip()
        if not symbol or symbol in have:
            continue
        conn.execute(
            "INSERT INTO Gene VALUES (?,?,?,?,?,?)",
            (
                next_gene,
                symbol,
                str(rec.get("ncbiGeneId") or ""),
                str(rec.get("uniprot") or ""),
                str(rec.get("protein") or ""),
                str(rec.get("summary") or ""),
            ),
        )
        disease_name = str(rec.get("disease") or "Pineoblastoma")
        disease_id = disease_rows.get(disease_name)
        if disease_id is None:
            new_id = int(conn.execute("SELECT COALESCE(MAX(DiseaseId), 0) FROM Disease").fetchone()[0]) + 1
            conn.execute(
                "INSERT INTO Disease VALUES (?,?,?,?)",
                (new_id, disease_name, "", str(rec.get("evidence") or rec.get("summary") or "")),
            )
            disease_id = new_id
            disease_rows[disease_name] = disease_id
        conn.execute(
            "INSERT INTO GeneDisease VALUES (?,?,?,?,?,?,?,?)",
            (
                next_link,
                next_gene,
                disease_id,
                str(rec.get("role") or "downstream"),
                str(rec.get("evidence") or rec.get("summary") or ""),
                float(rec.get("associationScore") or 0),
                float(rec.get("cases") or 0),
                float(rec.get("lofCount") or 0),
            ),
        )
        acc = str(rec.get("protein") or "")
        if acc:
            conn.execute(
                "INSERT INTO Fasta VALUES (?,?,?,?,?,?,?)",
                (
                    next_fasta,
                    next_gene,
                    acc,
                    f"{acc} {symbol} [Homo sapiens] (header-only; fetch NCBI for sequence)",
                    "",
                    0,
                    NCBI_EFETCH.format(accession=acc),
                ),
            )
            next_fasta += 1
        have.add(symbol)
        added.append(symbol)
        next_gene += 1
        next_link += 1
    conn.commit()
    return added
