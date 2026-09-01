"""Public FASTA mine: parse NCBI/UniProt records. Never invent SQL."""

from __future__ import annotations

from urllib.error import URLError
from urllib.request import Request, urlopen

NCBI_EFETCH = (
    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    "?db=protein&id={accession}&rettype=fasta&retmode=text"
)
UNIPROT_FASTA = "https://rest.uniprot.org/uniprotkb/{accession}.fasta"

# Truncated public NCBI protein FASTA (Homo sapiens), first ~180 aa. Full records via NCBI_EFETCH.
PUBLIC_PROTEIN_FASTA = """\
>NP_803187.1 endoribonuclease Dicer isoform 1 [Homo sapiens]
MKSPALQPLSMAGLQLMTPASSPMGPFFGLPWQQEAIHDNIYTPRKYQVELLEAALDHNTIVCLNTGSGK
TFIAVLLTKELSYQIRGDFSRNGKRTVFLVNSANQVAQQVSAVRTHSDLKVGEYSNLEVNASWTKERWNQ
EFTKHQVLIMTCYVALNVLKNGYLSLSDINLLVFDECHLAILDHPYREIMKLCENCPSCPRILGLTASIL
>NP_000312.2 retinoblastoma-associated protein isoform 1 [Homo sapiens]
MPPKTPRKTAATAAAAAAEPPAPPPPPPPEEDPEQDSGPEDLPLVRLEFEETEEPDFTALCQKLKIPDHV
RERAWLTWEKVSSVDGVLGGYIQKKKELWGICIFIAAVDLDEMSFTFTELQKNIEISVHKFFNLLKEIDT
STKVDNAMSRLLKKYDVLFALFSKLERTCELIYLTQPSSSISTEINSALVLKVSWITFLLAKGEVLQMED
>NP_037367.3 ribonuclease 3 isoform 1 [Homo sapiens]
MMQGNTCHRMSFHPGRGCPRGRGGHGARPSAPSFRPQNLRLLHPQQPPVQYQYEPPSAPSTTFSNSPAPN
FLPPRPDFVPFPPPMPPSAQGPLPPCPIRPPFPNHQMRHPFPVPPCFPPMPPPMPCPNNPPVPGAPPGQG
TFPFMMPPPSMPHPPPPPVMPQQVNYQYPPGYSHHNFPPPSFNSFQNNPSSFLPSANNSSSPHFRHLPPY
>NP_073557.3 microprocessor complex subunit DGCR8 isoform 1 [Homo sapiens]
METDESPSPLPCGPAGEAVMESRARPFQALPREQSPPPPLQTSSGAEVMDVGSGGDGQSELPAEDPFNFY
GASLLSKGSFSKGRLLIDPNCSGHSPRTARHAPAVRKFSPDLKLLKDVKISVSFTESCRSKDRKVLYTGA
ERDVRAECGLLLSPVSGDVHACPFGGSVGDGVGIGGESADKKDEENELDQEKRVEYAVLDELEDFTDNLE
"""


def parse_fasta(text: str) -> list[dict]:
    """Split FASTA text into header / sequence / length dicts."""
    records: list[dict] = []
    header: str | None = None
    chunks: list[str] = []
    for line in (text or "").splitlines():
        if line.startswith(">"):
            if header is not None:
                seq = "".join(chunks)
                records.append({"header": header, "sequence": seq, "length": len(seq)})
            header = line[1:].strip()
            chunks = []
        else:
            piece = "".join(ch for ch in line.strip() if not ch.isspace())
            if piece:
                chunks.append(piece)
    if header is not None:
        seq = "".join(chunks)
        records.append({"header": header, "sequence": seq, "length": len(seq)})
    return records


def truncate_seq(seq: str, n: int = 180) -> str:
    text = "".join((seq or "").split())
    return text[: max(int(n), 0)]


def fasta_url(accession: str, *, source: str = "ncbi") -> str:
    acc = (accession or "").strip()
    if source == "uniprot":
        return UNIPROT_FASTA.format(accession=acc)
    return NCBI_EFETCH.format(accession=acc)


def fetch_fasta(accession: str, *, timeout: float = 8.0, source: str = "ncbi") -> str:
    """Optional live fetch. Tests use PUBLIC_PROTEIN_FASTA so they do not need the network."""
    url = fasta_url(accession, source=source)
    req = Request(url, headers={"User-Agent": "RevolveRelate/gene-domain"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        raise OSError(f"FASTA fetch failed for {accession}: {exc}") from exc


def baked_records(*, max_aa: int = 180) -> list[dict]:
    rows = []
    for rec in parse_fasta(PUBLIC_PROTEIN_FASTA):
        seq = truncate_seq(rec["sequence"], max_aa)
        rows.append({**rec, "sequence": seq, "length": len(seq), "source": "ncbi", "baked": True})
    return rows
