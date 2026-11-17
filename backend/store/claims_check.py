r"""
P2 · F5c — control de afirmaciones (Manual V4 §21, «Control de afirmaciones»).

POR QUÉ. El código de la tienda nunca escribe estas frases, pero el personal escribe texto libre que la tienda
publica: descripciones de productos y modelos, grados estéticos, notas de equipos usados, campañas, portada y
servicios. El manual veta o condiciona ciertas afirmaciones cuando no hay respaldo; esto las señala mientras se
escribe.

SÓLO ACONSEJA. `find()` nunca cambia el texto, nunca lanza una excepción por el texto y ningún guardado la llama:
una frase señalada no impide guardar. Quien escribe decide si tiene el respaldo.

CÓMO SE BUSCA. Sin distinguir mayúsculas ni tildes (`batería` = `bateria`) y con límites de palabra: una frase sólo
se señala si no está pegada a otra letra o cifra, así «original» dentro de «originalidad» no cuenta, y
«originales» en «cargadores originales» sí. El subrayado y los asteriscos del Markdown no pegan. Se devuelve la
frase tal como se escribió, una vez por frase y regla, en el orden del texto.

SIN RETROCESO CUADRÁTICO. Un texto llega hasta `MAX_TEXT_LENGTH` y lo escribe cualquiera con permiso, así que
ningún patrón pone dos cuantificadores que puedan comerse los mismos caracteres uno al lado del otro (`\s*:?\s*`
probaba cada reparto de un tramo de espacios: ~3 s con 20 000). Entre dos `\s` cuantificados va siempre un literal
que los separa, o se funden en una sola clase (`[\s:]*`, `[\s.]*`). `FindIsLinearTest` lo vigila.

LAS REGLAS VIVEN AQUÍ, en `RULES`, y en ningún otro sitio: el panel recibe el consejo ya escrito. Son palabras de
la plataforma, sin el nombre de ninguna marca ni de ninguna tienda.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

#: La referencia que acompaña a cada consejo.
MANUAL_REFERENCE = 'Manual V4 §21'

#: El texto más largo que se revisa (el mismo tope que una descripción, `description_text.DESCRIPTION_MAX_LENGTH`).
MAX_TEXT_LENGTH = 20000

RULE_OFFICIAL = 'official'
RULE_CONDITION = 'condition'
RULE_GUARANTEE = 'guarantee'
RULE_ORIGIN = 'origin'
RULE_WARRANTY_TERM = 'warranty_term'
RULE_ABSOLUTE = 'absolute'


@dataclass(frozen=True)
class Rule:
    code: str
    #: El nombre de la regla, para quien lee el código.
    title: str
    #: El consejo que ve el panel después de «Revisa: "<frase>"».
    advice: str
    #: Sobre el texto en minúsculas y sin tildes.
    pattern: re.Pattern


# Una «letra o cifra» pega; el subrayado y la puntuación no (el Markdown usa `_cursiva_`).
_B = r'(?<![^\W_])'
_E = r'(?![^\W_])'


def _rule(code, title, advice, *alternatives):
    body = '|'.join(f'(?:{a})' for a in alternatives)
    return Rule(code, title, f'{advice} ({MANUAL_REFERENCE})', re.compile(f'{_B}(?:{body}){_E}'))


_AUTHORITY = r'(?:distribuidor(?:es)?|tienda|servicio\s+tecnico|centro\s+de\s+servicio|revendedor(?:es)?|partner|proveedor)'
_NUMBER = r'(?:\d{1,3}|un|uno|una|dos|tres|cuatro|seis|doce|dieciocho|veinticuatro)'
_PERIOD = r'(?:anos?|mes(?:es)?)'

#: Manual V4 §21. El orden es el de la sección.
RULES: tuple[Rule, ...] = (
    _rule(
        RULE_OFFICIAL, '«Oficial» o «autorizado»',
        'sólo con una acreditación verificable del fabricante',
        rf'(?:{_AUTHORITY}\s+)?oficial(?:es|mente)?',
        rf'{_AUTHORITY}\s+autorizad[oa]s?(?:\s+por)?',
        r'autorizad[oa]s?\s+por',
    ),
    _rule(
        RULE_CONDITION, 'Estado del equipo',
        'sólo si el equipo inspeccionado lo respalda',
        r'bateria(?:\s+(?:al|de|en|del))?[\s:]*100\s*(?:%|por\s*ciento)',
        r'100\s*(?:%|por\s*ciento)\s+de\s+bateria',
        r'impecables?',
        r'como\s+nuev[oa]s?',
        r'sin\s+(?:ninguna\s+)?fall(?:as?|os?)',
        r'perfect[oa]s?',
    ),
    _rule(
        RULE_GUARANTEE, 'Resultado garantizado',
        'nunca antes del diagnóstico',
        r'(?:reparacion(?:es)?\s+)?garantizad[oa]s?',
        r'garantizamos',
    ),
    _rule(
        RULE_ORIGIN, 'Origen de las piezas',
        'sólo con prueba de origen',
        r'(?:(?:piezas?|repuestos?|partes?|componentes?|calidad)\s+)?original(?:es)?',
    ),
    _rule(
        RULE_WARRANTY_TERM, 'Plazo de garantía',
        'sólo si la política de garantía publicada dice lo mismo',
        rf'{_NUMBER}\s+{_PERIOD}\s+de\s+garantia',
        rf'garantia\s+(?:de\s+|por\s+)?{_NUMBER}\s+{_PERIOD}',
    ),
    _rule(
        RULE_ABSOLUTE, 'Absolutos',
        'evita los absolutos',
        r'siempre',
        r'tod[oa]s',
        r'numero\s+(?:uno|1)',
        r'n[\s.]*[º°]\s*1',
        r'\#\s*1',
    ),
)


def _fold(text: str) -> tuple[str, list[int]]:
    """
    `text` en minúsculas y sin tildes, y para cada carácter de la versión plegada, su posición en `text`: así una
    coincidencia se recorta del texto ORIGINAL, con sus tildes y mayúsculas.
    """
    folded: list[str] = []
    origin: list[int] = []
    for index, char in enumerate(text):
        for part in unicodedata.normalize('NFD', char):
            if unicodedata.combining(part):
                continue
            for lower in part.lower():
                if unicodedata.combining(lower):
                    continue
                folded.append(lower)
                origin.append(index)
    return ''.join(folded), origin


def find(text) -> list[dict]:
    """
    Las afirmaciones de `text` que el manual veta o condiciona: `[{phrase, rule, advice}]`, en el orden del texto.

    Nunca lanza por el texto: algo que no es texto, o vacío, no tiene avisos.
    """
    if not isinstance(text, str) or not text:
        return []
    folded, origin = _fold(text)

    spans = []
    for rule in RULES:
        for match in rule.pattern.finditer(folded):
            if match.end() > match.start():
                spans.append((match.start(), match.end(), rule))
    # Where two phrases overlap, the one that starts first (and then the longer) is the one shown.
    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))

    warnings: list[dict] = []
    seen: set[tuple[str, str]] = set()
    covered_until = -1
    for start, end, rule in spans:
        if start < covered_until:
            continue
        covered_until = end
        phrase = ' '.join(text[origin[start]:origin[end - 1] + 1].split())
        key = (' '.join(folded[start:end].split()), rule.code)
        if key in seen:
            continue
        seen.add(key)
        warnings.append({'phrase': phrase, 'rule': rule.code, 'advice': rule.advice})
    return warnings


def too_long_message(length: int) -> str:
    return (f'El texto tiene {length:,} caracteres y el control de afirmaciones revisa hasta '
            f'{MAX_TEXT_LENGTH:,}.').replace(',', ' ')
