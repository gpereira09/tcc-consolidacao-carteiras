"""
Iniciador da versão instalada para Windows (RNF-009).

Executado pelos atalhos criados pelo instalador, sem janela de console:

    pythonw iniciar_carteira.py                 abre o sistema no navegador
    pythonw iniciar_carteira.py --segundo-plano sobe o servidor sem abrir o navegador
                                                (atalho de inicialização do Windows)
    pythonw iniciar_carteira.py --encerrar      encerra a instância em execução

Responsabilidades:

  * manter uma única instância por usuário; um segundo clique só abre o navegador;
  * guardar banco, chaves, backups e registros em %LOCALAPPDATA%\\ConsolidacaoCarteiras,
    fora da pasta do programa, para que atualizar ou desinstalar não apague dados;
  * gerar na primeira execução um segredo JWT próprio da instalação, em vez da
    chave de desenvolvimento de `config.py`;
  * carregar a base demonstrativa somente se o instalador pediu e se ainda não
    existir banco (a carga recria o schema e nunca roda sobre dados existentes);
  * servir apenas em 127.0.0.1 e reiniciar o servidor se ele cair;
  * oferecer um ícone na bandeja com "Abrir", "Pasta de dados" e "Encerrar".

Este módulo não importa `config` nem `src`: o servidor e a carga demonstrativa
rodam em processos filhos que recebem o ambiente já preparado.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path

NOME_APP = "ConsolidacaoCarteiras"
TITULO_APP = "Consolidação de Carteiras"
RAIZ_APP = Path(__file__).resolve().parent.parent
ICONE = Path(__file__).resolve().parent / "carteira.ico"
PORTA_PREFERIDA = 8501
ESPERA_SERVIDOR_S = 120
MAX_REINICIOS = 3
JANELA_REINICIOS_S = 300

log = logging.getLogger("carteira.iniciador")


@dataclass(frozen=True)
class Pastas:
    """Locais por usuário usados pela versão instalada."""

    base: Path

    @property
    def dados(self) -> Path:
        return self.base / "data"

    @property
    def logs(self) -> Path:
        return self.base / "logs"

    @property
    def banco(self) -> Path:
        return self.dados / "carteira.db"

    @property
    def segredos(self) -> Path:
        return self.dados / ".segredos.json"

    @property
    def instancia(self) -> Path:
        return self.base / "instancia.json"

    @property
    def trava(self) -> Path:
        return self.base / "instancia.lock"

    @property
    def marca_demo(self) -> Path:
        return self.base / "carregar_demonstracao"

    def criar(self) -> None:
        for pasta in (self.base, self.dados, self.logs):
            pasta.mkdir(parents=True, exist_ok=True)


def pastas_padrao() -> Pastas:
    """%LOCALAPPDATA%\\ConsolidacaoCarteiras, ou CARTEIRA_PASTA_USUARIO se definida."""
    sobrescrita = os.getenv("CARTEIRA_PASTA_USUARIO")
    if sobrescrita:
        return Pastas(Path(sobrescrita))
    raiz = os.getenv("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Pastas(Path(raiz) / NOME_APP)


def configurar_log(pastas: Pastas) -> Path:
    arquivo = pastas.logs / "iniciador.log"
    manipulador = RotatingFileHandler(arquivo, maxBytes=1_000_000, backupCount=3,
                                      encoding="utf-8")
    manipulador.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    raiz = logging.getLogger()
    raiz.addHandler(manipulador)
    raiz.setLevel(logging.INFO)
    return arquivo


def carregar_segredos(arquivo: Path) -> dict[str, str]:
    """Lê os segredos da instalação, gerando-os na primeira execução."""
    if arquivo.exists():
        try:
            dados = json.loads(arquivo.read_text(encoding="utf-8"))
            if dados.get("jwt_secret"):
                return dados
        except (OSError, ValueError):
            log.warning("Arquivo de segredos ilegível; um novo será gerado.")
    dados = {"jwt_secret": secrets.token_urlsafe(48)}
    temporario = arquivo.with_suffix(".tmp")
    temporario.write_text(json.dumps(dados), encoding="utf-8")
    os.replace(temporario, arquivo)
    return dados


def _alguem_escuta(porta: int) -> bool:
    """Há servidor aceitando conexões na porta, em IPv4 ou IPv6 local?"""
    destinos = [(socket.AF_INET, ("127.0.0.1", porta))]
    if socket.has_ipv6:
        destinos.append((socket.AF_INET6, ("::1", porta)))
    for familia, endereco in destinos:
        with socket.socket(familia, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            try:
                s.connect(endereco)
            except OSError:
                continue
            return True
    return False


def porta_livre(preferida: int = PORTA_PREFERIDA) -> int:
    """
    A porta preferida, se nenhum servidor a usa em IPv4 nem em IPv6;
    senão, uma porta livre qualquer. Um servidor de desenvolvimento escutando
    só em [::] não impede o bind em 127.0.0.1, mas faria "localhost" abrir o
    servidor errado.
    """
    for candidata in (preferida, 0):
        if candidata and _alguem_escuta(candidata):
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", candidata))
            except OSError:
                continue
            return s.getsockname()[1]
    raise RuntimeError("Nenhuma porta TCP local disponível.")


def url_local(porta: int) -> str:
    return f"http://127.0.0.1:{porta}"


def servidor_responde(porta: int, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{porta}/_stcore/health",
                                    timeout=timeout) as resposta:
            return resposta.status == 200
    except OSError:
        return False


def pid_vivo(pid: int) -> bool:
    """Verifica se o processo existe sem enviar sinal (no Windows, os.kill encerraria)."""
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    import ctypes
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        codigo = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(codigo)):
            return False
        return codigo.value == 259
    finally:
        kernel32.CloseHandle(handle)


def ler_instancia(pastas: Pastas) -> dict | None:
    """Dados da instância em execução, se o processo ainda existir."""
    try:
        dados = json.loads(pastas.instancia.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not pid_vivo(int(dados.get("pid", 0))):
        return None
    return dados


def gravar_instancia(pastas: Pastas, porta: int) -> None:
    pastas.instancia.write_text(json.dumps({"pid": os.getpid(), "porta": porta}),
                                encoding="utf-8")


def apagar_instancia(pastas: Pastas) -> None:
    try:
        dados = json.loads(pastas.instancia.read_text(encoding="utf-8"))
        if int(dados.get("pid", 0)) == os.getpid():
            pastas.instancia.unlink()
    except (OSError, ValueError):
        pass


class TravaInstancia:
    """Trava exclusiva de arquivo, mantida aberta enquanto a instância vive."""

    def __init__(self, arquivo: Path):
        self.arquivo = arquivo
        self._fh = None

    def adquirir(self) -> bool:
        self._fh = open(self.arquivo, "a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._fh.close()
            self._fh = None
            return False
        return True

    def liberar(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None


def interpretador_console() -> str:
    """python.exe ao lado do pythonw.exe: os filhos precisam de stdout válido."""
    atual = Path(sys.executable)
    candidato = atual.with_name("python.exe" if os.name == "nt" else atual.name)
    return str(candidato if candidato.exists() else atual)


def ambiente_servidor(pastas: Pastas, segredos: dict[str, str], porta: int) -> dict[str, str]:
    env = dict(os.environ)
    env.update({
        "CARTEIRA_DATA_DIR": str(pastas.dados),
        "CARTEIRA_DB": str(pastas.banco),
        "CARTEIRA_JWT_SECRET": segredos["jwt_secret"],
        "CARTEIRA_URL": url_local(porta),
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    return env


def comando_servidor(porta: int, interpretador: str | None = None) -> list[str]:
    return [
        interpretador or interpretador_console(), "-m", "streamlit", "run",
        str(RAIZ_APP / "app.py"),
        "--server.address=127.0.0.1",
        f"--server.port={porta}",
        "--server.headless=true",
        "--server.fileWatcherType=none",
        "--server.runOnSave=false",
        "--browser.gatherUsageStats=false",
        "--client.toolbarMode=minimal",
        "--global.developmentMode=false",
    ]


def precisa_carga_demo(pastas: Pastas) -> bool:
    """Só carrega a demonstração se pedida no instalador e se não houver banco."""
    return pastas.marca_demo.exists() and not pastas.banco.exists()


def _flags_sem_janela() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def carregar_demonstracao(pastas: Pastas, env: dict[str, str]) -> bool:
    arquivo_log = pastas.logs / "carga_demonstracao.log"
    log.info("Carregando a base demonstrativa em %s", pastas.banco)
    with open(arquivo_log, "w", encoding="utf-8") as saida:
        processo = subprocess.run(
            [interpretador_console(), str(RAIZ_APP / "scripts" / "seed_demo.py")],
            cwd=RAIZ_APP, env=env, stdout=saida, stderr=subprocess.STDOUT,
            creationflags=_flags_sem_janela(),
        )
    if processo.returncode != 0:
        log.error("Carga demonstrativa falhou (código %s); ver %s",
                  processo.returncode, arquivo_log)
        for sufixo in ("", "-wal", "-shm"):
            Path(f"{pastas.banco}{sufixo}").unlink(missing_ok=True)
        return False
    pastas.marca_demo.unlink(missing_ok=True)
    return True


class _GrupoProcessos:
    """Job Object do Windows: os filhos morrem junto com o iniciador, mesmo se ele for morto."""

    def __init__(self):
        self.handle = None
        if os.name != "nt":
            return
        import ctypes
        from ctypes import wintypes

        class _Basico(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class _ContadoresES(ctypes.Structure):
            _fields_ = [(nome, ctypes.c_uint64) for nome in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class _Estendido(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _Basico),
                ("IoInfo", _ContadoresES),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        kernel32.SetInformationJobObject.argtypes = (
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            return
        info = _Estendido()
        info.BasicLimitInformation.LimitFlags = 0x2000
        if kernel32.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            self.handle = handle
            self._kernel32 = kernel32

    def adicionar(self, processo: subprocess.Popen) -> None:
        if self.handle is None:
            return
        if not self._kernel32.AssignProcessToJobObject(self.handle, int(processo._handle)):
            log.warning("Não foi possível vincular o servidor ao grupo de processos.")


class Supervisor:
    """Mantém o servidor Streamlit no ar, reiniciando-o se cair."""

    def __init__(self, pastas: Pastas, porta: int, env: dict[str, str]):
        self.pastas = pastas
        self.porta = porta
        self.env = env
        self.processo: subprocess.Popen | None = None
        self.encerrando = threading.Event()
        self._grupo = _GrupoProcessos()
        self._reinicios: list[float] = []

    def iniciar(self) -> None:
        saida = open(self.pastas.logs / "servidor.log", "a", encoding="utf-8")
        saida.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} porta {self.porta} =====\n")
        saida.flush()
        self.processo = subprocess.Popen(
            comando_servidor(self.porta), cwd=RAIZ_APP, env=self.env,
            stdout=saida, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            creationflags=_flags_sem_janela(),
        )
        saida.close()
        self._grupo.adicionar(self.processo)
        log.info("Servidor iniciado (pid %s, porta %s)", self.processo.pid, self.porta)

    def aguardar_pronto(self, limite_s: float = ESPERA_SERVIDOR_S) -> bool:
        fim = time.monotonic() + limite_s
        while time.monotonic() < fim and not self.encerrando.is_set():
            if self.processo is not None and self.processo.poll() is not None:
                return False
            if servidor_responde(self.porta):
                return True
            time.sleep(0.5)
        return False

    def vigiar(self, ao_falhar) -> None:
        """Bloqueia até o encerramento, reiniciando o servidor em quedas esporádicas."""
        while not self.encerrando.is_set():
            if self.processo is not None and self.processo.poll() is not None:
                codigo = self.processo.returncode
                agora = time.monotonic()
                self._reinicios = [t for t in self._reinicios if agora - t < JANELA_REINICIOS_S]
                if len(self._reinicios) >= MAX_REINICIOS:
                    log.error("Servidor caiu %s vezes em %s s; desistindo.",
                              MAX_REINICIOS, JANELA_REINICIOS_S)
                    ao_falhar()
                    return
                log.warning("Servidor encerrou com código %s; reiniciando.", codigo)
                self._reinicios.append(agora)
                self.iniciar()
            self.encerrando.wait(2)

    def parar(self) -> None:
        self.encerrando.set()
        if self.processo is not None and self.processo.poll() is None:
            self.processo.terminate()
            try:
                self.processo.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.processo.kill()
        log.info("Servidor encerrado.")


def mensagem(texto: str, erro: bool = False) -> None:
    """Caixa de diálogo nativa; o iniciador não tem console para mostrar erros."""
    if os.name == "nt":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, texto, TITULO_APP, 0x10 if erro else 0x40)
    else:
        print(texto, file=sys.stderr)


def abrir_pasta(pasta: Path) -> None:
    if os.name == "nt":
        os.startfile(pasta)


def encerrar_instancia(pastas: Pastas, espera_s: float = 15) -> bool:
    """Encerra a instância registrada (atalho "Encerrar" e desinstalador)."""
    dados = ler_instancia(pastas)
    if dados is None:
        return False
    pid = int(dados["pid"])
    if os.name == "nt":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x0001, False, pid)
        if handle:
            kernel32.TerminateProcess(handle, 0)
            kernel32.CloseHandle(handle)
    else:
        import signal
        os.kill(pid, signal.SIGTERM)
    fim = time.monotonic() + espera_s
    while time.monotonic() < fim and pid_vivo(pid):
        time.sleep(0.25)
    pastas.instancia.unlink(missing_ok=True)
    return not pid_vivo(pid)


def _aguardar_outra_instancia(pastas: Pastas, limite_s: float = ESPERA_SERVIDOR_S) -> int | None:
    fim = time.monotonic() + limite_s
    while time.monotonic() < fim:
        dados = ler_instancia(pastas)
        if dados and servidor_responde(int(dados["porta"])):
            return int(dados["porta"])
        time.sleep(0.5)
    return None


def _imagem_bandeja():
    from PIL import Image, ImageDraw
    if ICONE.exists():
        return Image.open(ICONE)
    imagem = Image.new("RGBA", (64, 64), (23, 37, 63, 255))
    desenho = ImageDraw.Draw(imagem)
    for i, altura in enumerate((20, 32, 44)):
        desenho.rectangle((12 + i * 15, 54 - altura, 22 + i * 15, 54), fill=(250, 250, 248, 255))
    return imagem


def executar(pastas: Pastas, abrir_navegador: bool) -> int:
    trava = TravaInstancia(pastas.trava)
    if not trava.adquirir():
        porta = _aguardar_outra_instancia(pastas)
        if porta is None:
            mensagem("O sistema já está sendo iniciado, mas não respondeu a tempo.\n"
                     f"Registros em: {pastas.logs}", erro=True)
            return 1
        if abrir_navegador:
            webbrowser.open(url_local(porta))
        return 0

    try:
        segredos = carregar_segredos(pastas.segredos)
        porta = porta_livre()
        env = ambiente_servidor(pastas, segredos, porta)
        supervisor = Supervisor(pastas, porta, env)
        return _executar_com_interface(pastas, supervisor, abrir_navegador)
    finally:
        apagar_instancia(pastas)
        trava.liberar()


def _subir(pastas: Pastas, supervisor: Supervisor, avisar) -> bool:
    if precisa_carga_demo(pastas):
        avisar("Preparando a base de demonstração. Isso leva alguns segundos.")
        if not carregar_demonstracao(pastas, supervisor.env):
            mensagem("Não foi possível carregar a base de demonstração.\n"
                     "O sistema abrirá com a base vazia.\n"
                     f"Registros em: {pastas.logs}", erro=True)
            pastas.marca_demo.unlink(missing_ok=True)
    supervisor.iniciar()
    if not supervisor.aguardar_pronto():
        supervisor.parar()
        mensagem("O servidor não iniciou.\n"
                 f"Consulte os registros em: {pastas.logs}", erro=True)
        return False
    gravar_instancia(pastas, supervisor.porta)
    return True


def _executar_com_interface(pastas: Pastas, supervisor: Supervisor, abrir_navegador: bool) -> int:
    url = url_local(supervisor.porta)
    try:
        import pystray
        imagem = _imagem_bandeja()
    except Exception:
        log.warning("Ícone de bandeja indisponível; executando sem ele.", exc_info=True)
        if not _subir(pastas, supervisor, lambda _texto: None):
            return 1
        if abrir_navegador:
            webbrowser.open(url)
        supervisor.vigiar(lambda: None)
        return 0

    resultado = {"codigo": 0}

    def encerrar(icone, _item=None):
        supervisor.parar()
        icone.stop()

    def falhou():
        resultado["codigo"] = 1
        mensagem("O servidor parou de responder repetidas vezes e foi encerrado.\n"
                 f"Registros em: {pastas.logs}", erro=True)
        icone.stop()

    def preparar(icone):
        icone.visible = True

        def avisar(texto):
            try:
                icone.notify(texto, TITULO_APP)
            except Exception:
                pass

        if not _subir(pastas, supervisor, avisar):
            resultado["codigo"] = 1
            icone.stop()
            return
        icone.title = f"{TITULO_APP} · {url}"
        if abrir_navegador:
            webbrowser.open(url)
        else:
            avisar("Em execução em segundo plano. Clique no ícone para abrir.")
        supervisor.vigiar(falhou)

    menu = pystray.Menu(
        pystray.MenuItem("Abrir", lambda *_: webbrowser.open(url), default=True),
        pystray.MenuItem("Pasta de dados", lambda *_: abrir_pasta(pastas.base)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Encerrar", encerrar),
    )
    icone = pystray.Icon(NOME_APP, imagem, f"{TITULO_APP} · iniciando…", menu)
    icone.run(setup=preparar)
    supervisor.parar()
    return resultado["codigo"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"Iniciador do {TITULO_APP}.")
    grupo = parser.add_mutually_exclusive_group()
    grupo.add_argument("--segundo-plano", action="store_true",
                       help="sobe o servidor sem abrir o navegador")
    grupo.add_argument("--encerrar", action="store_true",
                       help="encerra a instância em execução")
    args = parser.parse_args(argv)

    pastas = pastas_padrao()
    pastas.criar()
    configurar_log(pastas)

    if args.encerrar:
        encerrada = encerrar_instancia(pastas)
        log.info("Pedido de encerramento: %s", "concluído" if encerrada else "nada em execução")
        return 0

    try:
        return executar(pastas, abrir_navegador=not args.segundo_plano)
    except Exception:
        log.exception("Falha inesperada no iniciador")
        mensagem(f"Falha inesperada ao iniciar.\nRegistros em: {pastas.logs}", erro=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
