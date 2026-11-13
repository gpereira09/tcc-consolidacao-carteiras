"""
Gera um certificado TLS autoassinado para servir a aplicação em HTTPS (RNF-005).

    python scripts/gerar_certificado.py            # certs/servidor.crt e .key
    python scripts/gerar_certificado.py meu.host   # nome alternativo adicional

Adequado para uso local e demonstração: o navegador exibirá um aviso por não
reconhecer a autoridade emissora. Em produção, use um certificado emitido
por AC reconhecida (por exemplo, Let's Encrypt) ou termine o TLS em um proxy
reverso. O Python negocia TLS 1.2 ou superior por padrão.
"""
from __future__ import annotations

import ipaddress
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

RAIZ = Path(__file__).resolve().parents[1]
DESTINO = RAIZ / "certs"


def gerar(nomes: list[str]) -> tuple[Path, Path]:
    DESTINO.mkdir(exist_ok=True)
    chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    sujeito = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "BR"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Consolidacao de Carteiras (TCC)"),
        x509.NameAttribute(NameOID.COMMON_NAME, nomes[0]),
    ])
    alternativos = [x509.DNSName(n) for n in nomes] + [
        x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    agora = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(sujeito).issuer_name(sujeito)
        .public_key(chave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(agora - timedelta(minutes=5))
        .not_valid_after(agora + timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName(alternativos), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(chave, hashes.SHA256())
    )
    crt, key = DESTINO / "servidor.crt", DESTINO / "servidor.key"
    crt.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key.write_bytes(chave.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption()))
    key.chmod(0o600)
    return crt, key


if __name__ == "__main__":
    crt, key = gerar(["localhost", *sys.argv[1:]])
    print(f"Certificado: {crt}\nChave:       {key}")
