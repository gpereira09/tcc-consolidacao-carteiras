"""
Envio de e-mails transacionais (UC-03).

Com SMTP configurado (variáveis CARTEIRA_SMTP_*), a mensagem é enviada via
STARTTLS. Sem SMTP — caso do MVP local —, a mensagem é gravada como arquivo
.eml em data/outbox/, que pode ser aberto em qualquer cliente de e-mail.
Isso mantém o fluxo completo demonstrável sem depender de serviço externo.
"""
from __future__ import annotations

import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

import config


def enviar_email(destinatario: str, assunto: str, corpo: str) -> Path | None:
    agora = datetime.now()
    msg = EmailMessage()
    msg["From"] = config.SMTP_REMETENTE
    msg["To"] = destinatario
    msg["Subject"] = assunto
    msg["Date"] = agora.strftime("%a, %d %b %Y %H:%M:%S")
    msg.set_content(corpo)

    if config.SMTP_HOST:
        contexto = ssl.create_default_context()
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORTA, timeout=config.HTTP_TIMEOUT) as smtp:
            smtp.starttls(context=contexto)
            if config.SMTP_USUARIO:
                smtp.login(config.SMTP_USUARIO, config.SMTP_SENHA)
            smtp.send_message(msg)
        return None

    nome = f"{agora:%Y%m%d-%H%M%S-%f}-{destinatario.replace('@', '_at_')}.eml"
    caminho = config.OUTBOX_DIR / nome
    caminho.write_bytes(bytes(msg))
    return caminho
