"""
Segurança e controle de acesso (seção 2.6.2).

Cobre: hash de senha com bcrypt; sessão JWT com access token de curta
duração e refresh token revogável; recuperação de senha por token de uso
único (UC-03); autenticação em dois fatores opcional (TOTP, RFC 6238);
criptografia de campos sensíveis em repouso com AES-256-GCM (RNF-004);
rate limiting por usuário (RNF-011); e o modelo RBAC de três perfis
(RN-013), incluindo o bloqueio por tentativas falhas (RN-001).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import struct
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import bcrypt
import jwt
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select, update
from sqlalchemy.orm import Session

import config
from src import audit
from src.models import Carteira, Cliente, TokenSeguranca, Usuario


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _naive(dt: datetime) -> datetime:
    """O SQLite devolve datetimes sem fuso; tudo é gravado em UTC."""
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


def _agora_naive() -> datetime:
    return _naive(_agora())


TOKEN_REFRESH = "REFRESH"
TOKEN_RESET_SENHA = "RESET_SENHA"


def hash_senha(senha: str) -> str:
    return bcrypt.hashpw(senha.encode(), bcrypt.gensalt(config.BCRYPT_ROUNDS)).decode()


def verificar_senha(senha: str, senha_hash: str) -> bool:
    try:
        return bcrypt.checkpw(senha.encode(), senha_hash.encode())
    except (ValueError, TypeError):
        return False


def validar_politica_senha(senha: str) -> list[str]:
    """RN-001: mínimo 8 caracteres, contendo letras e números."""
    erros: list[str] = []
    if len(senha) < config.SENHA_MIN_LEN:
        erros.append(f"A senha deve ter no mínimo {config.SENHA_MIN_LEN} caracteres.")
    if not re.search(r"[A-Za-z]", senha):
        erros.append("A senha deve conter ao menos uma letra.")
    if not re.search(r"\d", senha):
        erros.append("A senha deve conter ao menos um número.")
    return erros


def gerar_token(usuario: Usuario) -> str:
    """Access token JWT de curta duração (JWT_EXPIRACAO_MINUTOS)."""
    agora = _agora()
    payload = {
        "sub": usuario.id,
        "email": usuario.email,
        "perfil": usuario.perfil,
        "tipo": "access",
        "iat": agora,
        "exp": agora + timedelta(minutes=config.JWT_EXPIRACAO_MINUTOS),
    }
    return jwt.encode(payload, config.JWT_SECRET, algorithm=config.JWT_ALGORITHM)


def validar_token(token: str) -> dict | None:
    try:
        dados = jwt.decode(token, config.JWT_SECRET, algorithms=[config.JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None
    return dados if dados.get("tipo", "access") == "access" else None


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _emitir_token_persistido(sessao: Session, usuario_id: str, tipo: str,
                             validade: timedelta) -> str:
    token = secrets.token_urlsafe(32)
    sessao.add(TokenSeguranca(
        usuario_id=usuario_id, tipo=tipo, token_hash=_hash_token(token),
        expira_em=_naive(_agora() + validade),
    ))
    sessao.commit()
    return token


def _localizar_token(sessao: Session, token: str, tipo: str) -> TokenSeguranca | None:
    """Token válido: existe, é do tipo pedido, não foi usado e não expirou."""
    registro = sessao.scalar(
        select(TokenSeguranca).where(TokenSeguranca.token_hash == _hash_token(token or ""),
                                     TokenSeguranca.tipo == tipo)
    )
    if registro is None or registro.usado_em is not None:
        return None
    if registro.expira_em < _agora_naive():
        return None
    return registro


def emitir_refresh(sessao: Session, usuario: Usuario) -> str:
    """Refresh token opaco, persistido como hash e revogável (seção 2.6.2)."""
    return _emitir_token_persistido(
        sessao, usuario.id, TOKEN_REFRESH, timedelta(days=config.REFRESH_EXPIRACAO_DIAS)
    )


def renovar_sessao(sessao: Session, refresh: str) -> tuple[Usuario, str, str] | None:
    """
    Troca um refresh token válido por um novo par (access, refresh).
    O refresh usado é invalidado (rotação), o que limita a janela de uso de
    um token eventualmente interceptado.
    """
    registro = _localizar_token(sessao, refresh, TOKEN_REFRESH)
    if registro is None:
        return None
    registro.usado_em = _agora_naive()
    sessao.commit()
    usuario = sessao.get(Usuario, registro.usuario_id)
    if usuario is None or not usuario.ativo:
        return None
    return usuario, gerar_token(usuario), emitir_refresh(sessao, usuario)


def revogar_sessoes(sessao: Session, usuario_id: str) -> int:
    """Invalida todos os refresh tokens do usuário (logout, troca de senha, LGPD)."""
    resultado = sessao.execute(
        update(TokenSeguranca)
        .where(TokenSeguranca.usuario_id == usuario_id,
               TokenSeguranca.tipo == TOKEN_REFRESH,
               TokenSeguranca.usado_em.is_(None))
        .values(usado_em=_agora_naive())
    )
    sessao.commit()
    return resultado.rowcount or 0


_PREFIXO_AES = "aes256gcm:"


def _chave_aes() -> bytes:
    env = os.getenv("CARTEIRA_AES_KEY")
    if env:
        chave = base64.urlsafe_b64decode(env)
    else:
        caminho = config.AES_KEY_PATH
        if not caminho.exists():
            caminho.write_bytes(base64.urlsafe_b64encode(AESGCM.generate_key(bit_length=256)))
            caminho.chmod(0o600)
        chave = base64.urlsafe_b64decode(caminho.read_bytes())
    if len(chave) != 32:
        raise RuntimeError("A chave AES deve ter 256 bits (32 bytes).")
    return chave


def cifrar(texto: str | None) -> str | None:
    if not texto:
        return None
    nonce = os.urandom(12)
    cifrado = AESGCM(_chave_aes()).encrypt(nonce, texto.encode(), None)
    return _PREFIXO_AES + base64.urlsafe_b64encode(nonce + cifrado).decode()


def decifrar(cifrado: str | None) -> str | None:
    """Devolve o texto, ou None se o valor estiver ausente, adulterado ou ilegível."""
    if not cifrado or not cifrado.startswith(_PREFIXO_AES):
        return None
    try:
        bruto = base64.urlsafe_b64decode(cifrado[len(_PREFIXO_AES):])
        return AESGCM(_chave_aes()).decrypt(bruto[:12], bruto[12:], None).decode()
    except (InvalidTag, ValueError):
        return None


def mascarar_cpf(cpf: str | None) -> str | None:
    """Exibe só os dois últimos dígitos: ***.***.***-NN (seção 2.6)."""
    digitos = re.sub(r"\D", "", cpf or "")
    return f"***.***.***-{digitos[-2:]}" if len(digitos) == 11 else None


def normalizar_cpf(cpf: str | None) -> str | None:
    """
    CPF só com dígitos, conferindo os dois dígitos verificadores.
    Vazio devolve None (o CPF é opcional). ValueError se for inválido.
    """
    digitos = re.sub(r"\D", "", cpf or "")
    if not digitos:
        return None
    if len(digitos) != 11 or digitos == digitos[0] * 11:
        raise ValueError("CPF inválido.")
    for tamanho in (9, 10):
        soma = sum(int(d) * peso for d, peso in zip(digitos, range(tamanho + 1, 1, -1)))
        if int(digitos[tamanho]) != (soma * 10 % 11) % 10:
            raise ValueError("CPF inválido.")
    return digitos


def gerar_segredo_totp() -> str:
    return base64.b32encode(os.urandom(20)).decode().rstrip("=")


def _codigo_totp(segredo: str, passo: int) -> str:
    chave = base64.b32decode(segredo + "=" * (-len(segredo) % 8), casefold=True)
    mac = hmac.new(chave, struct.pack(">Q", passo), hashlib.sha1).digest()
    desloc = mac[-1] & 0x0F
    numero = struct.unpack(">I", mac[desloc:desloc + 4])[0] & 0x7FFFFFFF
    return f"{numero % 1_000_000:06d}"


def _passo_totp(instante: float | None = None) -> int:
    return int((time.time() if instante is None else instante) // 30)


def codigo_totp_atual(segredo: str, instante: float | None = None) -> str:
    return _codigo_totp(segredo, _passo_totp(instante))


def verificar_totp(segredo: str, codigo: str, instante: float | None = None) -> bool:
    codigo = (codigo or "").strip().replace(" ", "")
    if not codigo.isdigit() or len(codigo) != 6:
        return False
    passo = _passo_totp(instante)
    return any(
        hmac.compare_digest(_codigo_totp(segredo, passo + d), codigo)
        for d in range(-config.TOTP_JANELA, config.TOTP_JANELA + 1)
    )


def uri_totp(usuario: Usuario, segredo: str) -> str:
    """URI otpauth:// lida por Google Authenticator, Authy, 1Password etc."""
    emissor = quote(config.TOTP_EMISSOR)
    return (f"otpauth://totp/{emissor}:{quote(usuario.email)}"
            f"?secret={segredo}&issuer={emissor}&digits=6&period=30")


def verificar_segundo_fator(usuario: Usuario, codigo: str | None) -> bool:
    """Verdadeiro se o 2FA está desligado ou se o código confere."""
    if not usuario.totp_ativo:
        return True
    segredo = decifrar(usuario.totp_segredo_cifrado)
    return bool(segredo) and verificar_totp(segredo, codigo or "")


def ativar_2fa(sessao: Session, usuario: Usuario, segredo: str, codigo: str) -> None:
    if not verificar_totp(segredo, codigo):
        raise ValueError("Código inválido. Confira o horário do celular e tente novamente.")
    usuario.totp_segredo_cifrado = cifrar(segredo)
    usuario.totp_ativo = True
    _auditar_conta(sessao, usuario, audit.ATIVAR_2FA)
    sessao.commit()


def desativar_2fa(sessao: Session, usuario: Usuario, senha: str, codigo: str) -> None:
    if not reautenticar(usuario, senha, codigo):
        raise ValueError("Senha ou código de verificação inválidos.")
    usuario.totp_segredo_cifrado = None
    usuario.totp_ativo = False
    _auditar_conta(sessao, usuario, audit.DESATIVAR_2FA)
    sessao.commit()


def reautenticar(usuario: Usuario, senha: str, codigo: str | None = None) -> bool:
    """RN-014: senha e, se habilitado, segundo fator."""
    return verificar_senha(senha, usuario.senha_hash) and verificar_segundo_fator(usuario, codigo)


class LimitadorTaxa:
    """
    RNF-011 — janela deslizante em memória: no máximo `limite` requisições
    por `janela` segundos para cada chave (usuário ou e-mail de login).
    Adequado à instância única do MVP; numa implantação distribuída o mesmo
    contrato seria atendido por um contador compartilhado (ex.: Redis).
    """

    def __init__(self, limite: int, janela: float) -> None:
        self.limite = limite
        self.janela = janela
        self._eventos: dict[str, deque[float]] = defaultdict(deque)
        self._trava = threading.Lock()

    def permitir(self, chave: str, agora: float | None = None) -> bool:
        agora = time.monotonic() if agora is None else agora
        with self._trava:
            fila = self._eventos[chave]
            while fila and agora - fila[0] >= self.janela:
                fila.popleft()
            if len(fila) >= self.limite:
                return False
            fila.append(agora)
            return True

    def espera(self, chave: str, agora: float | None = None) -> float:
        agora = time.monotonic() if agora is None else agora
        fila = self._eventos.get(chave)
        if not fila or len(fila) < self.limite:
            return 0.0
        return max(0.0, self.janela - (agora - fila[0]))

    def limpar(self) -> None:
        with self._trava:
            self._eventos.clear()


limitador = LimitadorTaxa(config.RATE_LIMIT_REQUISICOES, config.RATE_LIMIT_JANELA_SEGUNDOS)


class ErroLimiteTaxa(Exception):
    pass


def aplicar_limite(chave: str) -> None:
    if not limitador.permitir(chave):
        segundos = int(limitador.espera(chave)) + 1
        raise ErroLimiteTaxa(
            f"Limite de {config.RATE_LIMIT_REQUISICOES} requisições por minuto atingido. "
            f"Aguarde {segundos} s e tente novamente."
        )


class ErroAutenticacao(Exception):
    pass


class ErroAutorizacao(Exception):
    pass


def carteiras_visiveis(sessao: Session, usuario: Usuario) -> list[Carteira]:
    """
    RN-013 — ADMIN vê tudo; COLABORADOR vê os clientes vinculados a ele;
    CLIENTE vê apenas a própria carteira.
    """
    return list(sessao.scalars(_consulta_carteiras_visiveis(usuario)).all())


def _consulta_carteiras_visiveis(usuario: Usuario):
    stmt = select(Carteira).join(Cliente)
    if usuario.perfil == "COLABORADOR":
        stmt = stmt.where(Cliente.colaborador_id == usuario.id)
    elif usuario.perfil != "ADMIN":
        stmt = stmt.where(Cliente.usuario_id == usuario.id)
    return stmt


def pode_acessar_cliente(usuario: Usuario, cliente: Cliente) -> bool:
    """RN-013 aplicada a um cliente: ADMIN, o colaborador vinculado ou o próprio titular."""
    return (
        usuario.perfil == "ADMIN"
        or cliente.usuario_id == usuario.id
        or (usuario.perfil == "COLABORADOR" and cliente.colaborador_id == usuario.id)
    )


def exigir_perfil(usuario: Usuario, *perfis: str) -> None:
    if usuario.perfil not in perfis:
        raise ErroAutorizacao(
            f"Perfil '{usuario.perfil}' não autorizado. Requer: {', '.join(perfis)}."
        )


def pode_acessar_carteira(sessao: Session, usuario: Usuario, carteira_id: str) -> bool:
    consulta = _consulta_carteiras_visiveis(usuario).where(Carteira.id == carteira_id)
    return sessao.scalar(consulta.limit(1)) is not None


class SegundoFatorNecessario(ErroAutenticacao):
    """A senha confere, mas a conta tem 2FA e o código não foi informado."""


def _usuario_por_email(sessao: Session, email: str) -> Usuario | None:
    return sessao.scalar(select(Usuario).where(Usuario.email == email))


def limpar_bloqueio(usuario: Usuario) -> None:
    """RN-001 — zera as tentativas falhas e remove o bloqueio temporário."""
    usuario.tentativas_falhas = 0
    usuario.bloqueado_ate = None


def _registrar_falha_login(usuario: Usuario, agora: datetime) -> bool:
    """RN-001 — conta a falha; devolve True se a conta acabou de ser bloqueada."""
    usuario.tentativas_falhas += 1
    if usuario.tentativas_falhas >= config.MAX_TENTATIVAS_LOGIN:
        usuario.bloqueado_ate = agora + timedelta(minutes=config.BLOQUEIO_MINUTOS)
        usuario.tentativas_falhas = 0
        return True
    return False


MSG_CREDENCIAIS = "E-mail ou senha inválidos."


def _auditar_conta(sessao: Session, usuario: Usuario, acao: str) -> None:
    """RF-026 — eventos de conta são auditados no serviço, não na tela."""
    audit.registrar(sessao, usuario_id=usuario.id, acao=acao, entidade="USUARIO",
                    entidade_id=usuario.id)


def autenticar(sessao: Session, email: str, senha: str,
               codigo_2fa: str | None = None) -> tuple[Usuario, str]:
    """
    Autentica e devolve (usuário, access token JWT).
    Aplica o bloqueio temporário de RN-001 após 5 tentativas falhas, o rate
    limiting de RNF-011 por e-mail e, se habilitado, o segundo fator (TOTP).
    Sucesso e falha ficam na trilha de auditoria (RF-026).
    """
    try:
        usuario = _autenticar(sessao, email, senha, codigo_2fa)
    except SegundoFatorNecessario:
        raise
    except (ErroAutenticacao, ErroLimiteTaxa):
        audit.registrar(sessao, usuario_id=None, acao=audit.LOGIN_FALHA, entidade="USUARIO",
                        dados_novos={"email": (email or "").strip().lower()})
        sessao.commit()
        raise
    _auditar_conta(sessao, usuario, audit.LOGIN)
    sessao.commit()
    return usuario, gerar_token(usuario)


def _autenticar(sessao: Session, email: str, senha: str, codigo_2fa: str | None) -> Usuario:
    email = email.strip().lower()
    aplicar_limite(f"login:{email}")
    usuario = _usuario_por_email(sessao, email)
    if usuario is None:
        raise ErroAutenticacao(MSG_CREDENCIAIS)

    agora = _agora()
    bloqueado_ate = usuario.bloqueado_ate.replace(tzinfo=timezone.utc) if usuario.bloqueado_ate else None
    if bloqueado_ate and bloqueado_ate > agora:
        minutos = max(1, int((bloqueado_ate - agora).total_seconds() // 60))
        raise ErroAutenticacao(f"Conta bloqueada. Tente novamente em {minutos} minuto(s).")

    if not verificar_senha(senha, usuario.senha_hash):
        if _registrar_falha_login(usuario, agora):
            sessao.commit()
            raise ErroAutenticacao(
                f"Conta bloqueada por {config.BLOQUEIO_MINUTOS} minutos após "
                f"{config.MAX_TENTATIVAS_LOGIN} tentativas falhas."
            )
        sessao.commit()
        raise ErroAutenticacao(MSG_CREDENCIAIS)
    if not usuario.ativo:
        raise ErroAutenticacao("Conta desativada.")

    if usuario.totp_ativo:
        if not codigo_2fa:
            raise SegundoFatorNecessario(
                "Informe o código de 6 dígitos do aplicativo autenticador.")
        if not verificar_segundo_fator(usuario, codigo_2fa):
            _registrar_falha_login(usuario, agora)
            sessao.commit()
            raise ErroAutenticacao("Código de verificação inválido.")

    limpar_bloqueio(usuario)
    usuario.ultimo_login = agora
    return usuario


def solicitar_redefinicao(sessao: Session, email: str) -> str | None:
    """
    UC-03 — gera o token de redefinição e envia o link por e-mail.
    Devolve o token (usado nos testes) ou None se o e-mail não existir; a
    interface mostra a mesma mensagem nos dois casos, para não revelar contas.
    """
    from src import notificacao

    email = email.strip().lower()
    aplicar_limite(f"reset:{email}")
    usuario = _usuario_por_email(sessao, email)
    if usuario is None or not usuario.ativo:
        return None
    token = _emitir_token_persistido(
        sessao, usuario.id, TOKEN_RESET_SENHA, timedelta(minutes=config.RESET_SENHA_MINUTOS)
    )
    notificacao.enviar_email(
        usuario.email,
        "Redefinição de senha — Consolidação de Carteiras",
        (
            "Recebemos uma solicitação para redefinir a senha da sua conta.\n\n"
            f"Acesse {config.URL_APLICACAO}/?redefinir={token}\n"
            f"ou informe o código abaixo na tela \"Esqueci minha senha\", "
            f"em até {config.RESET_SENHA_MINUTOS} minutos:\n\n{token}\n\n"
            "Se você não fez esta solicitação, ignore esta mensagem; "
            "sua senha continua a mesma."
        ),
    )
    return token


def redefinir_senha(sessao: Session, token: str, nova_senha: str) -> Usuario:
    """UC-03 — valida o token de uso único, troca a senha e encerra sessões antigas."""
    exigir_politica_senha(nova_senha)
    registro = _localizar_token(sessao, (token or "").strip(), TOKEN_RESET_SENHA)
    if registro is None:
        raise ValueError("Link inválido, expirado ou já utilizado. Solicite um novo.")
    usuario = sessao.get(Usuario, registro.usuario_id)
    usuario.senha_hash = hash_senha(nova_senha)
    limpar_bloqueio(usuario)
    registro.usado_em = _agora_naive()
    _auditar_conta(sessao, usuario, audit.REDEFINIR_SENHA)
    sessao.commit()
    revogar_sessoes(sessao, usuario.id)
    return usuario


def exigir_politica_senha(senha: str) -> None:
    erros = validar_politica_senha(senha)
    if erros:
        raise ValueError(" ".join(erros))


def validar_email_novo(sessao: Session, email: str) -> str:
    """Normaliza o e-mail e garante que é válido e ainda não cadastrado."""
    email = (email or "").strip().lower()
    if "@" not in email or "." not in email.split("@")[-1]:
        raise ValueError("E-mail inválido.")
    if _usuario_por_email(sessao, email):
        raise ValueError("Já existe uma conta com este e-mail.")
    return email


def criar_conta(
    sessao: Session,
    *,
    email: str,
    nome: str,
    senha: str,
    consentimento_lgpd: bool,
    perfil: str = "CLIENTE",
    cpf: str | None = None,
    moeda_base: str = config.MOEDA_BASE_PADRAO,
    perfil_risco: str = "MODERADO",
) -> Usuario:
    """
    RF-002 — cria USUARIO + CLIENTE + CARTEIRA padrão em uma transação.
    O consentimento LGPD é condição obrigatória para a criação da conta.
    """
    if not consentimento_lgpd:
        raise ValueError("É obrigatório aceitar o termo de consentimento LGPD.")
    email = validar_email_novo(sessao, email)
    exigir_politica_senha(senha)

    usuario = Usuario(email=email, senha_hash=hash_senha(senha), perfil=perfil)
    sessao.add(usuario)
    sessao.flush()

    cliente = Cliente(
        usuario_id=usuario.id,
        nome=nome.strip(),
        cpf_cifrado=cifrar(cpf),
        moeda_base=moeda_base,
        perfil_risco=perfil_risco,
        consentimento_lgpd=True,
        data_consentimento=_agora(),
    )
    sessao.add(cliente)
    sessao.flush()
    sessao.add(Carteira(cliente_id=cliente.id, nome=config.NOME_CARTEIRA_PADRAO))
    _auditar_conta(sessao, usuario, audit.CRIAR_CONTA)
    sessao.commit()
    return usuario


def registrar_consentimento(sessao: Session, cliente: Cliente) -> None:
    """Consentimento LGPD dado pelo titular de conta criada pelo administrador."""
    cliente.consentimento_lgpd = True
    cliente.data_consentimento = _agora()
    audit.registrar(sessao, usuario_id=cliente.usuario_id, acao=audit.CONSENTIMENTO_LGPD,
                    entidade="CLIENTE", entidade_id=cliente.id)
    sessao.commit()
