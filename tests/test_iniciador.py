"""
Testes do iniciador da versão instalada (distribuicao/iniciar_carteira.py).

Cobrem as decisões que protegem os dados do usuário: segredo próprio por
instalação, carga demonstrativa nunca sobre um banco existente, servidor
restrito a 127.0.0.1 e instância única.
"""
from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "distribuicao"))

import iniciar_carteira as ini


@pytest.fixture
def pastas(tmp_path):
    p = ini.Pastas(tmp_path / "usuario")
    p.criar()
    return p


class TestSegredos:
    def test_gera_na_primeira_execucao_e_reutiliza(self, pastas):
        primeiro = ini.carregar_segredos(pastas.segredos)
        segundo = ini.carregar_segredos(pastas.segredos)
        assert len(primeiro["jwt_secret"]) >= 48
        assert primeiro == segundo

    def test_instalacoes_diferentes_tem_segredos_diferentes(self, tmp_path):
        a = ini.carregar_segredos(tmp_path / "a.json")
        b = ini.carregar_segredos(tmp_path / "b.json")
        assert a["jwt_secret"] != b["jwt_secret"]

    def test_arquivo_corrompido_e_regenerado(self, pastas):
        pastas.segredos.write_text("{nao é json", encoding="utf-8")
        assert ini.carregar_segredos(pastas.segredos)["jwt_secret"]

    def test_ambiente_do_servidor_nao_usa_chave_de_desenvolvimento(self, pastas):
        segredos = ini.carregar_segredos(pastas.segredos)
        env = ini.ambiente_servidor(pastas, segredos, 8501)
        assert env["CARTEIRA_JWT_SECRET"] == segredos["jwt_secret"]
        assert env["CARTEIRA_DATA_DIR"] == str(pastas.dados)
        assert env["CARTEIRA_DB"] == str(pastas.banco)
        assert env["CARTEIRA_URL"] == "http://127.0.0.1:8501"


class TestCargaDemonstrativa:
    def test_sem_marca_nao_carrega(self, pastas):
        assert not ini.precisa_carga_demo(pastas)

    def test_com_marca_e_sem_banco_carrega(self, pastas):
        pastas.marca_demo.touch()
        assert ini.precisa_carga_demo(pastas)

    def test_nunca_carrega_sobre_banco_existente(self, pastas):
        pastas.marca_demo.touch()
        pastas.banco.write_bytes(b"dados reais")
        assert not ini.precisa_carga_demo(pastas)


class TestServidor:
    def test_escuta_somente_em_localhost(self):
        comando = ini.comando_servidor(8600, interpretador="python")
        assert "--server.address=127.0.0.1" in comando
        assert "--server.port=8600" in comando
        assert "--browser.gatherUsageStats=false" in comando
        assert "--client.toolbarMode=minimal" in comando
        assert comando[-1].startswith("--")
        assert Path(comando[4]).name == "app.py"

    def test_porta_ocupada_escolhe_outra(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as ocupada:
            ocupada.bind(("127.0.0.1", 0))
            ocupada.listen()
            porta = ocupada.getsockname()[1]
            alternativa = ini.porta_livre(porta)
        assert alternativa != porta
        assert 0 < alternativa < 65536

    @pytest.mark.skipif(not socket.has_ipv6, reason="sem IPv6")
    def test_porta_ocupada_so_em_ipv6_escolhe_outra(self):
        """Outro servidor em [::] na mesma porta: 'localhost' poderia abrir o servidor errado."""
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as ocupada:
            ocupada.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            ocupada.bind(("::", 0))
            ocupada.listen()
            porta = ocupada.getsockname()[1]
            alternativa = ini.porta_livre(porta)
        assert alternativa != porta

    def test_url_aberta_no_navegador_e_ipv4_explicito(self):
        assert ini.url_local(8501) == "http://127.0.0.1:8501"

    def test_servidor_inexistente_nao_responde(self):
        assert not ini.servidor_responde(ini.porta_livre(0), timeout=0.5)


class TestInstancia:
    def test_pid_vivo(self):
        assert ini.pid_vivo(os.getpid())
        assert not ini.pid_vivo(0)

    def test_registro_de_processo_morto_e_ignorado(self, pastas):
        pastas.instancia.write_text(json.dumps({"pid": 2_000_000_000, "porta": 8501}))
        assert ini.ler_instancia(pastas) is None

    def test_registro_proprio_e_lido_e_apagado(self, pastas):
        ini.gravar_instancia(pastas, 8555)
        assert ini.ler_instancia(pastas) == {"pid": os.getpid(), "porta": 8555}
        ini.apagar_instancia(pastas)
        assert not pastas.instancia.exists()

    def test_trava_impede_segunda_instancia(self, pastas):
        primeira, segunda = ini.TravaInstancia(pastas.trava), ini.TravaInstancia(pastas.trava)
        assert primeira.adquirir()
        try:
            assert not segunda.adquirir()
        finally:
            primeira.liberar()
        assert segunda.adquirir()
        segunda.liberar()

    def test_encerrar_sem_instancia_nao_falha(self, pastas):
        assert ini.encerrar_instancia(pastas) is False
