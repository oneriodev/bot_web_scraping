"""Pré-processamento de imagem para OCR.

O Tesseract foi feito para documentos escaneados: texto escuro, fundo claro, letras
grandes. Encartes são o oposto (cores, fundos estampados, cards pequenos). Estes passos
aproximam a imagem do que o OCR espera.
"""

from __future__ import annotations

from PIL import Image, ImageFilter, ImageOps


def prepare_for_ocr(image: Image.Image, scale: float = 2.0) -> Image.Image:
    # 1. Transparência vira fundo branco (senão, áreas transparentes viram preto).
    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGBA")
        background = Image.new("RGBA", image.size, "white")
        image = Image.alpha_composite(background, image)

    # 2. Tons de cinza: a cor não ajuda o OCR; só o contraste entre letra e fundo importa.
    gray = ImageOps.grayscale(image)

    # 3. Ampliar: o Tesseract acerta mais com letras grandes (cards de 350 px são pequenos).
    if scale != 1:
        width, height = gray.size
        gray = gray.resize((round(width * scale), round(height * scale)), Image.Resampling.LANCZOS)

    # 4. Contraste automático (estica o cinza mais escuro até preto e o mais claro até branco)
    #    e nitidez, para as bordas das letras ficarem definidas.
    gray = ImageOps.autocontrast(gray, cutoff=1)
    return gray.filter(ImageFilter.SHARPEN)
