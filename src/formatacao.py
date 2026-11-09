"""
Formatação numérica no padrão brasileiro (1.234,56), única para a interface,
o relatório PDF e as mensagens de validação.

`vazio` é o texto exibido quando o valor não existe: a interface usa "—" e
o PDF usa "n/d".
"""
from __future__ import annotations

import config


def numero(valor: float | None, casas: int = 2, vazio: str = "—") -> str:
    if valor is None:
        return vazio
    return f"{valor:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def moeda(valor: float | None, codigo: str = "BRL", casas: int = 2, vazio: str = "—") -> str:
    if valor is None:
        return vazio
    return f"{config.SIMBOLO_MOEDA.get(codigo, codigo)} {numero(valor, casas)}"


def percentual(valor: float | None, casas: int = 2, sinal: bool = False,
               vazio: str = "—") -> str:
    if valor is None:
        return vazio
    texto = numero(valor * 100, casas)
    return ("+" + texto if sinal and valor >= 0 else texto) + "%"


def quantidade(valor: float) -> str:
    """Inteiro sem casas; fracionário com até 4 casas, sem zeros à direita."""
    if abs(valor - round(valor)) < 1e-9:
        return numero(valor, 0)
    return numero(valor, 4).rstrip("0").rstrip(",")
