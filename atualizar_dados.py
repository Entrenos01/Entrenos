"""
Atualiza os dados oficiais do SINASC (Ministério da Saúde / DATASUS) usados no app.

O que faz:
  1. Entra no FTP público do DATASUS e procura os arquivos de nascidos vivos
     (DN<UF><ANO>.dbc), tanto os definitivos quanto os preliminares.
  2. Lê só a idade da mãe (IDADEMAE) de cada nascimento.
  3. Soma, por estado e ano: total de nascimentos, mães de 11 a 14 anos, mães de 15 a 19 anos
     e a contagem de cada idade de 11 a 19 anos.
  4. Salva em data/sinasc.json, com a fonte e a data da atualização.

Se o DATASUS estiver fora do ar ou algo der errado, o arquivo antigo NÃO é apagado:
o site continua mostrando os últimos dados bons.

Só usa dados agregados (contagens). Nenhum dado individual é guardado.
"""

import ftplib
import io
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone

import datasus_dbc
from dbfread import DBF

FTP_HOST = "ftp.datasus.gov.br"
# Pastas onde o DATASUS publica os arquivos. Há mais de uma possível para cada tipo,
# porque o DATASUS já reorganizou o FTP; as que não existirem são ignoradas.
PASTAS = {
    "preliminar": ["/dissemin/publicos/SINASC/PRELIM/DNRES"],
    "definitivo": ["/dissemin/publicos/SINASC/1996_/Dados/DNRES",
                   "/dissemin/publicos/SINASC/NOV/DNRES"],
}
ANO_INICIAL = int(os.environ.get("ANO_INICIAL", "2015"))

UFS = {
    "AC": "Acre", "AL": "Alagoas", "AP": "Amapá", "AM": "Amazonas", "BA": "Bahia",
    "CE": "Ceará", "DF": "Distrito Federal", "ES": "Espírito Santo", "GO": "Goiás",
    "MA": "Maranhão", "MT": "Mato Grosso", "MS": "Mato Grosso do Sul", "MG": "Minas Gerais",
    "PA": "Pará", "PB": "Paraíba", "PR": "Paraná", "PE": "Pernambuco", "PI": "Piauí",
    "RJ": "Rio de Janeiro", "RN": "Rio Grande do Norte", "RS": "Rio Grande do Sul",
    "RO": "Rondônia", "RR": "Roraima", "SC": "Santa Catarina", "SP": "São Paulo",
    "SE": "Sergipe", "TO": "Tocantins",
}

# o arquivo de números fica em data/sinasc.json, na raiz do site
SAIDA = os.path.join(os.getcwd(), "data", "sinasc.json")
PADRAO = re.compile(r"^DN([A-Z]{2})(\d{4})\.DBC$", re.IGNORECASE)


def listar_arquivos(ftp):
    """Devolve {(uf, ano): (pasta, nome, status)}; o definitivo tem prioridade sobre o preliminar."""
    achados = {}
    for status in ("preliminar", "definitivo"):  # definitivo por último, para sobrescrever
      for pasta in PASTAS[status]:
        try:
            nomes = ftp.nlst(pasta)
        except ftplib.all_errors as erro:
            print(f"Aviso: não consegui listar {pasta}: {erro}")
            continue
        for caminho in nomes:
            nome = caminho.rsplit("/", 1)[-1]
            m = PADRAO.match(nome)
            if not m:
                continue
            uf, ano = m.group(1).upper(), int(m.group(2))
            if uf in UFS and ano >= ANO_INICIAL:
                achados[(uf, ano)] = (pasta, nome, status)
    return achados


def contar(ftp, pasta, nome):
    """Baixa um arquivo .dbc e conta os nascimentos por faixa de idade da mãe."""
    buffer = io.BytesIO()
    ftp.retrbinary(f"RETR {pasta}/{nome}", buffer.write)
    with tempfile.TemporaryDirectory() as tmp:
        dbc = os.path.join(tmp, "a.dbc")
        dbf = os.path.join(tmp, "a.dbf")
        with open(dbc, "wb") as f:
            f.write(buffer.getvalue())
        datasus_dbc.decompress(dbc, dbf)

        total = 0
        por_idade = {str(i): 0 for i in range(11, 20)}
        for registro in DBF(dbf, encoding="latin-1", load=False):
            total += 1
            idade = str(registro.get("IDADEMAE") or "").strip()
            if idade.isdigit() and str(int(idade)) in por_idade:
                por_idade[str(int(idade))] += 1
    m11_14 = sum(por_idade[str(i)] for i in range(11, 15))
    m15_19 = sum(por_idade[str(i)] for i in range(15, 20))
    return {"total": total, "maes_11_14": m11_14, "maes_15_19": m15_19, "por_idade": por_idade}


def principal():
    try:
        ftp = ftplib.FTP(FTP_HOST, timeout=120)
        ftp.login()
    except ftplib.all_errors as erro:
        print(f"ERRO: não consegui conectar ao DATASUS ({erro}). Mantendo os dados atuais.")
        sys.exit(1)

    arquivos = listar_arquivos(ftp)
    if not arquivos:
        print("ERRO: nenhum arquivo do SINASC encontrado. O DATASUS pode ter mudado as pastas.")
        sys.exit(1)

    # Reaproveita o que já foi lido: anos definitivos não mudam, então só baixa
    # anos novos e anos ainda preliminares. Assim a atualização mensal é rápida.
    anterior = {}
    if os.path.exists(SAIDA):
        try:
            with open(SAIDA, encoding="utf-8") as f:
                anterior = json.load(f).get("estados", {})
        except Exception:
            anterior = {}

    estados = {}
    for (uf, ano), (pasta, nome, status) in sorted(arquivos.items()):
        velho = anterior.get(uf, {}).get("anos", {}).get(str(ano))
        if velho and velho.get("status") == "definitivo" and status == "definitivo" and "maes_11_14" in velho:
            estados.setdefault(uf, {"nome": UFS[uf], "anos": {}})["anos"][str(ano)] = velho
            continue
        print(f"Lendo {nome} ({status})...")
        try:
            dados = contar(ftp, pasta, nome)
        except Exception as erro:  # um arquivo com problema não derruba os outros
            print(f"Aviso: falhou em {nome}: {erro}")
            continue
        dados["status"] = status
        estados.setdefault(uf, {"nome": UFS[uf], "anos": {}})["anos"][str(ano)] = dados
    ftp.quit()

    if len(estados) < 27:
        print(f"ERRO: só {len(estados)} estados lidos. Mantendo os dados atuais.")
        sys.exit(1)

    # Brasil = soma dos estados
    brasil = {}
    for uf in estados.values():
        for ano, d in uf["anos"].items():
            b = brasil.setdefault(ano, {"total": 0, "maes_11_14": 0, "maes_15_19": 0,
                                        "por_idade": {str(i): 0 for i in range(11, 20)}, "status": "definitivo"})
            for chave in ("total", "maes_11_14", "maes_15_19"):
                b[chave] += d[chave]
            for idade, n in d["por_idade"].items():
                b["por_idade"][idade] += n
            if d["status"] == "preliminar":
                b["status"] = "preliminar"

    resultado = {
        "fonte": "Sistema de Informações sobre Nascidos Vivos (SINASC) – Ministério da Saúde / DATASUS",
        "link_fonte": "https://datasus.saude.gov.br/",
        "nota": "Dados por estado de residência da mãe, idades de 11 a 19 anos. Anos preliminares ainda podem mudar.",
        "atualizado_em": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "brasil": brasil,
        "estados": estados,
    }

    os.makedirs(os.path.dirname(SAIDA), exist_ok=True)
    temporario = SAIDA + ".tmp"
    with open(temporario, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(temporario, SAIDA)  # só troca o arquivo quando tudo deu certo
    print(f"Pronto: {len(estados)} estados salvos em data/sinasc.json")


if __name__ == "__main__":
    principal()
