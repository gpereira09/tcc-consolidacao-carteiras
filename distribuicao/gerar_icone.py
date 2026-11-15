"""
Gera `carteira.ico` (atalhos, instalador e bandeja) com as cores da interface.

Uso:  python distribuicao/gerar_icone.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

TINTA = (23, 37, 63, 255)
PAPEL = (250, 250, 248, 255)
DESTINO = Path(__file__).resolve().parent / "carteira.ico"


def desenhar(lado: int = 256) -> Image.Image:
    imagem = Image.new("RGBA", (lado, lado), (0, 0, 0, 0))
    d = ImageDraw.Draw(imagem)
    d.rounded_rectangle((0, 0, lado - 1, lado - 1), radius=lado // 6, fill=TINTA)
    margem, base = lado * 0.2, lado * 0.78
    largura = lado * 0.14
    passo = (lado - 2 * margem - largura) / 2
    for i, fracao in enumerate((0.28, 0.44, 0.58)):
        x = margem + i * passo
        d.rectangle((x, base - lado * fracao, x + largura, base), fill=PAPEL)
    return imagem


def main() -> None:
    desenhar().save(DESTINO, sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                                    (64, 64), (128, 128), (256, 256)])
    print(DESTINO)


if __name__ == "__main__":
    main()
