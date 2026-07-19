#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
import json
import shutil
import hashlib
import unicodedata
from pathlib import Path
from datetime import datetime
from decimal import Decimal
from typing import List, Dict, Tuple, Optional

import pdfplumber


# ==========================================================
# CONFIG / MEMÓRIA DA ÚLTIMA PASTA
# ==========================================================

def _config_path() -> Path:
    return Path.home() / "pdf2ofx_config.json"

def load_last_dir() -> Optional[str]:
    p = _config_path()
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            d = data.get("last_dir")
            if d and Path(d).exists():
                return d
        except Exception:
            pass
    return None

def save_last_dir(folder: str) -> None:
    try:
        _config_path().write_text(
            json.dumps({"last_dir": folder}, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    except Exception:
        pass


# ==========================================================
# MUNICÍPIOS PERMITIDOS
# (Atualizado: ampliado + possibilidade de arquivo externo municipios_permitidos.txt)
# ==========================================================

APP_VERSION = " - v.2026-07-19"

DEFAULT_MUNICIPIOS_PERMITIDOS = {
    # já existentes
    "JURUTI",
    "BUJARU",
    "TRAIRAO",
    "RUROPOLIS",
    "PLACAS",
    "ALTAMIRA",
    "REDENCAO",  
}

def normalizar_sem_acentos(s: str) -> str:
    s = (s or "").upper()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent

def carregar_municipios_permitidos() -> set[str]:
    """
    Se existir 'municipios_permitidos.txt' na mesma pasta do script/exe,
    usa esse arquivo (1 município por linha). Senão, usa o DEFAULT.
    """
    try:
        base = _base_dir()
        arq = base / "municipios_permitidos.txt"
        if arq.exists():
            mun = set()
            for ln in arq.read_text(encoding="utf-8", errors="ignore").splitlines():
                ln = normalizar_sem_acentos(ln)
                if ln and not ln.startswith("#"):
                    mun.add(ln)
            if mun:
                return mun
    except Exception:
        pass
    return {normalizar_sem_acentos(m) for m in DEFAULT_MUNICIPIOS_PERMITIDOS}

MUNICIPIOS_PERMITIDOS = carregar_municipios_permitidos()

def detectar_municipio(texto: str) -> Optional[str]:
    t = normalizar_sem_acentos(texto)
    for m in MUNICIPIOS_PERMITIDOS:
        if re.search(rf"\b{re.escape(m)}\b", t):
            return m
    return None


# ==========================================================
# GUI
# ==========================================================

def pick_folder_gui(initial_dir: Optional[str]) -> str:
    import tkinter as tk
    from tkinter import filedialog, messagebox

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)

    messagebox.showinfo(
        title=f"PDF → OFX {APP_VERSION}",
        message="Escolha a pasta onde estão os PDFs que deseja converter."
    )

    folder = filedialog.askdirectory(
        title="Escolha a pasta onde estão os PDFs",
        initialdir=initial_dir or ""
    )

    root.destroy()

    if not folder:
        raise SystemExit("[CANCELADO] Nenhuma pasta selecionada.")

    return folder


def show_finish_message(qtd: int, removidos: int = 0):
    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)

    linha_duplicados = f"{removidos} arquivo(s) duplicado(s) removido(s) da pasta raiz\n\n" if removidos else ""

    messagebox.showinfo(
        title=f"PDF → OFX {APP_VERSION}",
        message=(
            f"Fim de Serviço!\n\n"
            f"{qtd} arquivo(s) convertido(s) com sucesso!\n\n"
            f"{linha_duplicados}"
            "====================================\n"
            "💼 Betha Sistemas – Módulo Contábil\n"
            "Gestão contábil pública completa e integrada\n\n"
            "✔️ MCASP | PCASP\n"
            "✔️ TCM-PA | SICONFI\n"
            "✔️ Orçamento, Contabilidade e Tesouraria integrados\n\n"
            "Solução utilizada por centenas de municípios\n"
            "===================================="
        )
    )

    root.destroy()


# ==========================================================
# HELPERS
# ==========================================================

def formatar_conta_sem_ponto(conta: str) -> str:
    return (conta or "").replace(".", "").replace("-", "").strip()

def normalizar_conta_ofx(conta: str) -> str:
    """
    Remove tudo que não é dígito e tira zeros à esquerda, pois é assim
    que costuma estar cadastrado o identificador de conciliação no SIENGE
    (ex: 'Máscara' = 1617087, sem os zeros de '000161708-7').
    """
    d = re.sub(r"\D", "", conta or "")
    d = d.lstrip("0")
    return d or "0"

def ofx_escape(s: str) -> str:
    s = (s or "").replace("\r", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", s).strip()

def format_ofx_date(dt: datetime) -> str:
    return dt.strftime("%Y%m%d")

def amount_to_ofx(v: Decimal) -> str:
    return f"{v.quantize(Decimal('0.01')):.2f}"

def tx_type_from_amount(desc: str, v: Decimal) -> str:
    if "TRANSF" in (desc or "").upper():
        return "XFER"
    return "DEBIT" if v < 0 else "DEP"

def fitid_from_date_amount(dt: datetime, amount: Decimal) -> str:
    cents = int((abs(amount).quantize(Decimal("0.01")) * 100))
    return f"{format_ofx_date(dt)}{cents}"

def extrair_conta_por_layout(text: str) -> Optional[str]:
    lines = (text or "").splitlines()
    for i, ln in enumerate(lines):
        if ln.strip().upper().startswith("CONTA"):
            if i + 1 < len(lines):
                cand = re.sub(r"\D", "", lines[i + 1])
                if 6 <= len(cand) <= 12:
                    return cand
            cand2 = re.sub(r"\D", "", ln)
            if 6 <= len(cand2) <= 12:
                return cand2
    return None


# ==========================================================
# REGEX
# ==========================================================

RE_ACCOUNT = re.compile(r"\b(\d{1,3}\.\d{3}-\d|\d{1,10}-\d|\d{6,12})\b")

RE_PERIODO = re.compile(
    r"PER[IÍ]ODO\s*:\s*(\d{2}/\d{2}/\d{4})\s*(?:A|AT[ÉE])\s*(\d{2}/\d{2}/\d{4})",
    re.IGNORECASE
)

RE_DATE_START = re.compile(r"^\s*(\d{2})/(\d{2})(?:/(\d{4}))?\b")

RE_MONEY = re.compile(r"(\(?[-\u2212\u2013\u2014]?\s*\d{1,3}(?:\.\d{3})*,\d{2}\)?-?)")

RE_SALDO_ANTERIOR = re.compile(r"SALDO\s+ANTERIOR\s+([\d\.,]+)", re.IGNORECASE)

# ==========================================================
# BANCO
# ==========================================================


BANK_PATTERNS: List[Tuple[List[str], str, str]] = [
    (["BANPARA", "BANCO DO ESTADO DO PARA"], "037", "BANPARA"),
    (["BASA", "BANCO DA AMAZONIA"], "003", "BASA"),
    (["BANCO DO BRASIL"], "001", "BB"),
    (["BRADESCO"], "237", "BRADESCO"),
    (["ITAU UNIBANCO", "BANCO ITAU"], "341", "ITAU"),
    (["CAIXA ECONOMICA FEDERAL"], "104", "CAIXA"),
    (["SANTANDER"], "033", "SANTANDER"),
    (["SICOOB"], "756", "SICOOB"),
    (["SICREDI"], "748", "SICREDI"),
    (["NU PAGAMENTOS", "NUBANK"], "260", "NUBANK"),
    (["BANCO INTER"], "077", "INTER"),
    (["BANRISUL"], "041", "BANRISUL"),
    (["BANCO SAFRA"], "422", "SAFRA"),
    (["BTG PACTUAL"], "208", "BTG"),
    (["C6 BANK", "BANCO C6"], "336", "C6"),
    (["PAGSEGURO", "PAGBANK"], "290", "PAGBANK"),
    (["MERCADO PAGO"], "323", "MERCADOPAGO"),
    (["BANCO ORIGINAL"], "212", "ORIGINAL"),
    (["BANCO VOTORANTIM", "BANCO BV"], "655", "BV"),
]

RE_BANCO_GUESS = re.compile(r"BANCO\s+[A-Z0-9 .\-]{2,40}")

def detect_bank(text: str) -> Tuple[str, str]:
    t = normalizar_sem_acentos(text or "")

    for palavras, bank_id, bank_name in BANK_PATTERNS:
        if any(p in t for p in palavras):
            return bank_id, bank_name

    # Banco não cadastrado na tabela: tenta extrair um nome plausível do
    # texto para exibição (o Sienge/Betha casa a conta por ACCTID+BANKID,
    # então um nome/código genérico ainda gera um OFX utilizável).
    m = RE_BANCO_GUESS.search(t)
    if m:
        return "000", ofx_escape(m.group(0))

    return "000", "BANCO"


def extract_periodo(text: str) -> Tuple[datetime, datetime]:
    m = RE_PERIODO.search(text or "")
    if not m:
        raise ValueError("Período não encontrado")
    return (
        datetime.strptime(m.group(1), "%d/%m/%Y"),
        datetime.strptime(m.group(2), "%d/%m/%Y"),
    )


def extract_saldo_anterior(text: str) -> Optional[Decimal]:
    m = RE_SALDO_ANTERIOR.search(text or "")
    if not m:
        return None
    try:
        return _parse_decimal_ptbr(m.group(1))
    except Exception:
        return None




# ==========================================================
# PDF
# ==========================================================

def extract_text_lines(pdf: Path) -> Tuple[str, List[str]]:
    all_text, lines = [], []

    with pdfplumber.open(str(pdf)) as doc:
        total = len(doc.pages) or 1
        for i, p in enumerate(doc.pages, 1):
            t = p.extract_text() or ""
            all_text.append(t)
            lines.extend(t.splitlines())
            if int(i / total * 100) in (50, 100):
                print(f"  [PROGRESSO] {int(i / total * 100)}%")

    return "\n".join(all_text), lines


# ==========================================================
# TRANSAÇÕES
# ==========================================================


def _parse_decimal_ptbr(s: str) -> Decimal:
    s = (s or "").strip().replace(" ", "")
    s = s.replace("−", "-").replace("–", "-").replace("—", "-")
    return Decimal(s.replace(".", "").replace(",", "."))


def _saldo_consistente(
    valores: List[Decimal],
    saldos: List[Optional[Decimal]],
    tolerancia: Decimal = Decimal("0.01"),
    minimo_ok: float = 0.8,
) -> bool:
    """
    Confere se saldo[i] ≈ saldo[i-1] + valor[i] para a maioria das linhas
    consecutivas que têm saldo. Usada para validar a suposição de que o
    último valor monetário de cada linha é um saldo acumulado (layout comum
    em extratos, mas não garantido em bancos não mapeados).
    """
    pares = [
        (valores[i], saldos[i], saldos[i - 1])
        for i in range(1, len(valores))
        if saldos[i] is not None and saldos[i - 1] is not None
    ]
    if not pares:
        return True

    acertos = sum(
        1 for valor, saldo, saldo_ant in pares
        if abs((saldo_ant + valor) - saldo) <= tolerancia
    )
    return (acertos / len(pares)) >= minimo_ok


def parse_transactions_com_saldo(lines: List[str], default_year: Optional[int] = None) -> List[Dict]:
    """
    Parser para extratos em formato tabela (data | histórico | valor | saldo).
    Quando há 2+ valores monetários na linha, assume que o último é o saldo
    acumulado e o penúltimo é o valor do movimento. Essa suposição é validada
    por _saldo_consistente; se não bater para a maioria das linhas, retorna
    lista vazia para que o chamador use o parser simples como fallback.
    """
    txs: List[Dict] = []
    saldos: List[Optional[Decimal]] = []

    for ln in lines:
        m = RE_DATE_START.match(ln)
        if not m:
            continue

        dd, mm, yyyy = m.groups()
        year = int(yyyy) if yyyy else (default_year or datetime.now().year)

        try:
            dt = datetime.strptime(f"{dd}/{mm}/{year}", "%d/%m/%Y")
        except Exception:
            continue

        money_matches = list(RE_MONEY.finditer(ln))
        if not money_matches:
            continue

        # Se tiver 2+ valores na linha, o último é candidato a SALDO e o
        # penúltimo é o VALOR do movimento; com só 1 valor, não há saldo.
        tem_saldo = len(money_matches) >= 2
        amt_match = money_matches[-2] if tem_saldo else money_matches[-1]

        amount = parse_money(amt_match.group(1))
        saldo = parse_money(money_matches[-1].group(1)) if tem_saldo else None

        after_date = ln[m.end():amt_match.start()].strip()
        after_date = after_date.rstrip("-").strip()

        desc = re.sub(r"\s+", " ", after_date).strip()
        if not desc:
            desc = "SEM HISTORICO"

        doc_candidates = re.findall(r"\b\d+\b", after_date)
        checknum = doc_candidates[-1] if doc_candidates else ""

        txs.append({"dt": dt, "amount": amount, "desc": desc, "checknum": checknum})
        saldos.append(saldo)

    if not _saldo_consistente([t["amount"] for t in txs], saldos):
        return []

    ordem = sorted(range(len(txs)), key=lambda i: txs[i]["dt"])
    return [txs[i] for i in ordem]


def parse_money(token: str) -> Decimal:
    t = (token or "").strip()

    # Detecta negativo por: parênteses, sinal no começo, ou sinal no fim (alguns PDFs usam 123,45-)
    neg = False
    if t.startswith("(") and t.endswith(")"):
        neg = True
        t = t[1:-1].strip()

    if t.endswith("-"):
        neg = True
        t = t[:-1].strip()

    if t.startswith("-") or t.startswith("−") or t.startswith("–") or t.startswith("—"):
        neg = True
        t = t.lstrip("-−–—").strip()

    # Converte pt-BR
    s = t.replace(".", "").replace(",", ".")
    v = Decimal(s)

    return -abs(v) if neg else abs(v)


def parse_transactions(lines: List[str], base_year: int, prefer_full_year: bool) -> List[Dict]:
    txs = []

    re_date = re.compile(r"^\s*(\d{2})/(\d{2})(?:/(\d{4}))?")
    re_money = re.compile(r"(\(?-?\d{1,3}(?:\.\d{3})*,\d{2}\)?-?)")

    for ln in lines:
        m = re_date.match(ln)
        if not m:
            continue

        dd, mm, yyyy = m.groups()
        year = int(yyyy) if (prefer_full_year and yyyy) else base_year

        try:
            dt = datetime.strptime(f"{dd}/{mm}/{year}", "%d/%m/%Y")
        except Exception:
            continue

        vals = re_money.findall(ln)
        if not vals:
            continue

        amount = parse_money(vals[-1])
        desc = re_money.sub("", ln)
        desc = re.sub(re_date, "", desc).strip()
        if not desc:
            desc = "SEM HISTORICO"

        txs.append({"dt": dt, "amount": amount, "desc": desc, "checknum": ""})

    txs.sort(key=lambda x: x["dt"])
    return txs


# ==========================================================
# OFX
# ==========================================================

def build_ofx(
    bank_id: str,
    bank_name: str,
    acct: str,
    ini: datetime,
    fim: datetime,
    txs: List[Dict],
    ledger_bal: Optional[Decimal] = None,
    ledger_dtasof: Optional[datetime] = None,
) -> str:
    dts = format_ofx_date(datetime.now())

    nomes_completos = {
        "BANPARA": "Banco do Estado do Para S/A",
        "BASA": "Banco da Amazonia S/A",
        "BB": "Banco do Brasil S/A",
        "BRADESCO": "Banco Bradesco S/A",
        "ITAU": "Itau Unibanco S/A",
        "CAIXA": "Caixa Economica Federal",
        "SANTANDER": "Banco Santander (Brasil) S/A",
        "SICOOB": "Banco Cooperativo Sicoob S/A",
        "SICREDI": "Banco Cooperativo Sicredi S/A",
        "NUBANK": "Nu Pagamentos S/A",
        "INTER": "Banco Inter S/A",
        "BANRISUL": "Banco do Estado do Rio Grande do Sul S/A",
        "SAFRA": "Banco Safra S/A",
        "BTG": "Banco BTG Pactual S/A",
        "C6": "Banco C6 S/A",
        "PAGBANK": "PagSeguro Internet S/A",
        "MERCADOPAGO": "Mercado Pago",
        "ORIGINAL": "Banco Original S/A",
        "BV": "Banco Votorantim S/A",
    }
    nomes_curtos = {
        "BANPARA": "Banpara",
        "BASA": "Basa",
        "BB": "BB",
        "BRADESCO": "Bradesco",
        "ITAU": "Itau",
        "CAIXA": "Caixa",
        "SANTANDER": "Santander",
        "SICOOB": "Sicoob",
        "SICREDI": "Sicredi",
        "NUBANK": "Nubank",
        "INTER": "Inter",
        "BANRISUL": "Banrisul",
        "SAFRA": "Safra",
        "BTG": "BTG Pactual",
        "C6": "C6 Bank",
        "PAGBANK": "PagBank",
        "MERCADOPAGO": "Mercado Pago",
        "ORIGINAL": "Original",
        "BV": "BV",
    }
    org = nomes_completos.get(bank_name, bank_name)
    mktginfo = nomes_curtos.get(bank_name, bank_name)

    out = [
        "OFXHEADER:100",
        "DATA:OFXSGML",
        "VERSION:102",
        "SECURITY:NONE",
        "ENCODING:USASCII",
        "CHARSET:1252",
        "COMPRESSION:NONE",
        "OLDFILEUID:NONE",
        "NEWFILEUID:NONE",
        "<OFX>",
        "<SIGNONMSGSRSV1>",
        " <SONRS>",
        "  <STATUS>",
        "   <CODE>0</CODE>",
        "   <SEVERITY>INFO</SEVERITY>",
        "  </STATUS>",
        f"  <DTSERVER>{dts}",
        "  <LANGUAGE>POR",
        f"  <DTACCTUP>{dts}",
        "  <FI>",
        f"   <ORG>{org}",
        f"   <FID>{bank_id}",
        "  </FI>",
        " </SONRS>",
        "</SIGNONMSGSRSV1>",
        "<BANKMSGSRSV1>",
        " <STMTTRNRS>",
        "  <TRNUID>0",
        "   <STATUS>",
        "    <CODE>0",
        "    <SEVERITY>INFO",
        "   </STATUS>",
        "   <STMTRS>",
        "    <CURDEF>BRL",
        "    <BANKACCTFROM>",
        f"     <BANKID>{bank_id}",
        f"     <ACCTID>{acct}",
        "     <ACCTTYPE>CHECKING",
        "    </BANKACCTFROM>",
        "    <BANKTRANLIST>",
        f"     <DTSTART>{format_ofx_date(ini)}",
        f"     <DTEND>{format_ofx_date(fim)}",
    ]

    fitids_usados: Dict[str, int] = {}

    for t in txs:
        base_fitid = fitid_from_date_amount(t["dt"], t["amount"])
        n = fitids_usados.get(base_fitid, 0)
        fitids_usados[base_fitid] = n + 1
        fitid = base_fitid if n == 0 else f"{base_fitid}{n:02d}"

        memo = ofx_escape(t["desc"])
        checknum = (t.get("checknum") or "").strip()

        out += [
            "     <STMTTRN>",
            f"      <TRNTYPE>{tx_type_from_amount(t['desc'], t['amount'])}",
            f"      <DTPOSTED>{format_ofx_date(t['dt'])}",
            f"      <TRNAMT>{amount_to_ofx(t['amount'])}",
            f"      <FITID>{fitid}",
        ]

        if checknum:
            out.append(f"      <CHECKNUM>{checknum}")

        out += [
            f"      <MEMO>{memo}",
            "     </STMTTRN>",
        ]

    out.append("    </BANKTRANLIST>")

    if ledger_bal is not None:
        out += [
            "    <LEDGERBAL>",
            f"     <BALAMT>{amount_to_ofx(ledger_bal)}",
            f"     <DTASOF>{format_ofx_date(ledger_dtasof or fim)}",
            "    </LEDGERBAL>",
            f"    <MKTGINFO>{mktginfo}",
        ]

    out += [
        "   </STMTRS>",
        " </STMTTRNRS>",
        "</BANKMSGSRSV1>",
        "</OFX>",
    ]

    return "\n".join(out)


# ==========================================================
# PROCESSAMENTO
# ==========================================================

def montar_nome_pasta_destino(bank_id: str, ini: datetime, fim: datetime) -> str:
    """
    Nome da pasta de destino: data de hoje + código do banco + período do extrato.
    Exemplo: 17-07-2026_037_01-01-2026_a_31-01-2026
    """
    hoje = datetime.now().strftime("%d-%m-%Y")
    return f"{hoje}_{bank_id}_{ini.strftime('%d-%m-%Y')}_a_{fim.strftime('%d-%m-%Y')}"


def process_pdf(pdf: Path, base_dir: Path):
    text, lines = extract_text_lines(pdf)
    bank_id, bank_name = detect_bank(text)

    acct_layout = extrair_conta_por_layout(text)
    if acct_layout:
        acct = acct_layout
    else:
        m_acct = RE_ACCOUNT.search(text + " " + pdf.name)
        acct = formatar_conta_sem_ponto(m_acct.group(1) if m_acct else "SEM_CONTA")

    # >>> ALTERAÇÃO: identificador de conciliação sem zeros à esquerda
    # (é assim que o SIENGE costuma ter cadastrado em "Máscara")
    acct = normalizar_conta_ofx(acct)

    # Tenta pegar o período do cabeçalho (funciona para qualquer banco que
    # imprima "PERÍODO: dd/mm/aaaa A dd/mm/aaaa"); se não encontrar, o
    # período é derivado das próprias transações mais abaixo.
    try:
        ini, fim = extract_periodo(text)
        ano_base = fim.year
    except Exception:
        ini = fim = None
        ano_base = datetime.now().year

    txs = parse_transactions_com_saldo(lines, ano_base)
    if not txs:
        txs = parse_transactions(lines, ano_base, True)
    if not txs:
        raise ValueError("Nenhuma transação encontrada")

    if ini is None or fim is None:
        ini, fim = txs[0]["dt"], txs[-1]["dt"]

    # >>> ALTERAÇÃO: saldo final = saldo anterior + soma das transações
    # (aplicado a qualquer banco cujo extrato imprima "SALDO ANTERIOR")
    saldo_anterior = extract_saldo_anterior(text)
    ledger_bal = (saldo_anterior + sum((t["amount"] for t in txs), Decimal("0"))) if saldo_anterior is not None else None

    # >>> ALTERAÇÃO: pasta de destino nomeada por data + banco + período (por PDF)
    nome_pasta = montar_nome_pasta_destino(bank_id, ini, fim)
    out_dir = base_dir / nome_pasta
    out_dir.mkdir(parents=True, exist_ok=True)

    ofx = build_ofx(bank_id, bank_name, acct, ini, fim, txs, ledger_bal=ledger_bal, ledger_dtasof=fim)

    out_path = out_dir / f"{pdf.stem}.ofx"
    out_path.write_text(ofx, encoding="cp1252", errors="replace")

    # >>> ALTERAÇÃO: só depois que o OFX foi gravado com sucesso é que o PDF
    # é MOVIDO para a pasta de destino — ele deixa de existir na pasta raiz.
    # Se algo falhar antes disso, o PDF permanece na raiz para nova tentativa.
    pdf_dest = out_dir / pdf.name
    if pdf_dest.exists():
        pdf_dest.unlink()
    shutil.move(str(pdf), str(pdf_dest))

    return out_path


def _hash_arquivo(path: Path) -> str:
    h = hashlib.md5()
    h.update(path.read_bytes())
    return h.hexdigest()


def pdfs_ja_convertidos(base_dir: Path) -> set[str]:
    """
    Varre as subpastas já geradas em execuções anteriores (padrão
    dd-mm-aaaa_<banco>_<inicio>_a_<fim>) e retorna o hash (MD5) de
    cada PDF já convertido, para permitir identificar duplicatas
    pelo CONTEÚDO do arquivo (não apenas pelo nome).
    """
    hashes = set()
    if not base_dir.is_dir():
        return hashes
    for sub in base_dir.iterdir():
        if not sub.is_dir():
            continue
        for f in sub.glob("*.pdf"):
            try:
                hashes.add(_hash_arquivo(f))
            except Exception:
                pass
    return hashes


# ==========================================================
# MAIN
# ==========================================================

def main():
    if len(sys.argv) < 2:
        last = load_last_dir()
        pasta = pick_folder_gui(last)
        save_last_dir(pasta)
        in_path = Path(pasta)
    else:
        in_path = Path(sys.argv[1])

    if not in_path.exists():
        print(f"[ERRO] Caminho inválido: {in_path}")
        sys.exit(2)

    if in_path.is_file():
        pdfs = [in_path]
        base_dir = in_path.parent
    else:
        pdfs = [p for p in in_path.iterdir() if p.suffix.lower() == ".pdf"]
        base_dir = in_path

    # >>> ALTERAÇÃO: identifica PDFs que já foram convertidos em execuções
    # anteriores (mesmo conteúdo já presente em alguma subpasta de saída)
    # e os remove da pasta raiz antes de processar, evitando retrabalho
    # e pastas de saída duplicadas.
    ja_convertidos = pdfs_ja_convertidos(base_dir)
    pdfs_restantes = []
    removidos = 0
    for pdf in pdfs:
        try:
            if _hash_arquivo(pdf) in ja_convertidos:
                pdf.unlink()
                removidos += 1
                print(f"[DUPLICADO] {pdf.name} já havia sido convertido antes — removido da pasta raiz.")
                continue
        except Exception as e:
            print(f"[AVISO] Não foi possível checar duplicidade de {pdf.name}: {e}")
        pdfs_restantes.append(pdf)
    pdfs = pdfs_restantes

    ok = 0

    for pdf in pdfs:
        try:
            process_pdf(pdf, base_dir)
            ok += 1
        except Exception as e:
            print(f"[ERRO] {pdf.name}: {e}")

    print(f"\n[TOTAL] {ok} arquivo(s) convertido(s)")
    if removidos:
        print(f"[TOTAL] {removidos} arquivo(s) duplicado(s) removido(s) da pasta raiz")
    print("💼 Betha Sistemas – Módulo Contábil | MCASP | PCASP | TCM-PA")

    show_finish_message(ok, removidos)

if __name__ == "__main__":
    main()