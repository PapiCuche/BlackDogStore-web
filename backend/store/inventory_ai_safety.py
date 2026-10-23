"""
INV-AI-01 — what any AI provider has to go through, whichever it is.

OUT: `redact` FAILS SAFE. It does not try to recognise every way a person's
data can be written; it removes whole classes of text, in one linear pass
over the lines, before anything leaves the server:

- the text is normalised first (NFKC, every format and combining mark out,
  every decimal digit made ASCII, full-stops and dashes unified);
- a line that carries personal data — a word that introduces it («cliente»,
  «Sr.», «teléfono», «DNI», «correo»…) or an e-mail sign — is replaced
  whole, and so is the next line when the label stands alone («Cliente:»);
- after «IMEI» every digit of that stretch is taken out; after a serial label
  («NS», «S/N», «serie», «Serial Number»…) its value is taken out;
- any long number, also split by spaces, dots, dashes, slashes or brackets,
  is taken out when its length is that of a phone, RUC, DNI or IMEI.

What redaction cannot see (a name written with no word before it) is
the reason AI stays off per company and per import: redaction lowers the
risk, it does not make sending a customer's data acceptable.

Since the structured contract (ADR docs/adr-inv-ai-structured.md) no text is
sent at all: this module is only the LAST TRIPWIRE of `inventory_ai.propose` —
a structured request it would still change is never sent.

Every pattern here is linear (no nested repetition): 60 000 characters of
anything are read in well under a second.
"""
import re
import unicodedata

MAX_CHARS = 60_000
TIMEOUT_SECONDS = 15
_TEXT_MAX = 120

_FULL_STOPS = {'。': '.', '．': '.', '｡': '.'}
_DASH_CHARS = set('‐‑‒–—―−﹘﹣－')

#: Words that introduce a person's data. A line with one of them goes out whole.
_PERSONAL = re.compile(
    r'(?<![a-záéíóúñü])(?:cliente|clienta|contacto|nombre|comprador|compradora|vendedor|vendedora|atiende|'
    r'llamar|ll[aá]mame|escribir|escr[ií]beme|se[nñ]or|se[nñ]ora|sr|sra|srta|due[nñ]o|due[nñ]a|vendido a|'
    r'a nombre de|cel|celular|movil|m[oó]vil|tel|telf|tlf|tel[eé]fono|fono|whatsapp|wsp|wasap|wa\.me|'
    r'correo|e-?mail|mail|gmail|hotmail|outlook|dni|d\.n\.i|ruc|r\.u\.c|carn[eé]|c\.e|pasaporte|'
    r'direcci[oó]n|domicilio|calle|av\.|avenida|jr\.|jir[oó]n)(?![a-záéíóúñü])')
_EMAIL_SIGN = re.compile(r'@|\[at\]|\(at\)|\(arroba\)|\barroba\b|\bat\s{0,3}[a-z0-9-]{1,64}\s{0,3}(?:\.|\(dot\)|\[dot\]|\bdot\b|\bpunto\b)')
_IMEI_WORD = re.compile(r'imei')
_SERIAL_WORD = re.compile(
    r'(?<![a-z0-9])(?:n[uú]mero de serie|nro\.? de serie|serial number|serial no\.?|serial #|serial|serie|'
    r'n/s|s/n|n\.s\.|ns|sn)(?![a-z0-9])')
#: Shapes of split identifiers, as group lengths and what the digits start with:
#: phones (9 starting 9, or 51 + 9), landlines (01 + 7), RUC (11 from 10/15/16/17/20),
#: IMEI (the usual 2+6+6(+1), 8+6(+1), 4+4+4+3…). A list of prices («1200 1500 1800»)
#: has none of these shapes, so it is kept.
_SHAPES = (
    (re.compile(r'^9'), {(3, 3, 3), (3, 2, 2, 2), (1, 8), (3, 6), (6, 3), (5, 4), (4, 5)}),
    (re.compile(r'^519'), {(2, 9), (2, 3, 3, 3), (2, 3, 2, 2, 2), (3, 8)}),
    (re.compile(r'^01'), {(2, 3, 4), (2, 7), (3, 4), (2, 3, 2, 2)}),
    (re.compile(r'^(?:10|15|16|17|20)'), {(2, 9), (2, 3, 3, 3), (2, 8, 1)}),
    (re.compile(r'^'), {(2, 6, 6), (2, 6, 6, 1), (8, 6), (8, 6, 1), (2, 6, 7), (8, 7), (4, 4, 4, 3), (4, 4, 4, 2),
                        (3, 3, 3, 3, 3), (5, 5, 5)}),
)
_SOFT_GAP = set(' .-/()+,\t')


def normalize(text: str) -> str:
    """One way of writing: NFKC, format/combining marks out, decimal digits ASCII, full-stops and dashes unified."""
    out = []
    for char in unicodedata.normalize('NFKC', text):
        category = unicodedata.category(char)
        if category in ('Cf', 'Mn', 'Me'):
            continue
        if category == 'Nd':
            out.append(str(unicodedata.decimal(char)))
        elif char in _FULL_STOPS:
            out.append('.')
        elif char in _DASH_CHARS:
            out.append('-')
        else:
            out.append(char)
    return ''.join(out)


def _number_runs(line: str):
    """Groups of digits joined by short soft gaps («987 654 321», «(01) 234-5678»), as (start, end, digits, gaps)."""
    groups = [(m.start(), m.end()) for m in re.finditer(r'\d+', line)]
    runs, i = [], 0
    while i < len(groups):
        start, end = groups[i]
        digits, gaps = line[start:end], []
        j = i + 1
        while j < len(groups):
            gap = line[end:groups[j][0]]
            if not gap or len(gap) > 3 or any(ch not in _SOFT_GAP for ch in gap):
                break
            gaps.append(gap)
            end = groups[j][1]
            digits += line[groups[j][0]:end]
            j += 1
        runs.append((start, end, digits, gaps))
        i = j
    return runs


def _luhn(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char) * (2 if index % 2 else 1)
        total += value - 9 if value > 9 else value
    return total % 10 == 0


def _identifier_windows(groups: list) -> list:
    """Which consecutive groups of one run form a split identifier: (first, last) indexes, never overlapping."""
    found, i = [], 0
    while i < len(groups):
        hit = None
        for j in range(min(len(groups), i + 6) - 1, i, -1):  # the longest window first; at most 6 groups
            shape = tuple(len(group) for group in groups[i:j + 1])
            digits = ''.join(groups[i:j + 1])
            if any(start.match(digits) and shape in shapes for start, shapes in _SHAPES) \
                    or (len(digits) == 15 and _luhn(digits)):
                hit = j
                break
        if hit is None:
            i += 1
        else:
            found.append((i, hit))
            i = hit + 1
    return found


def _hide_numbers(line: str, keep) -> str:
    out, last = [], 0
    for start, end, _digits, gaps in _number_runs(line):
        spans = [(m.start() + start, m.end() + start) for m in re.finditer(r'\d+', line[start:end])]
        groups = [line[a:b] for a, b in spans]
        hidden = {}
        for first, final in _identifier_windows(groups):
            hidden[first] = final
        dotted_dni = len(groups) == 3 and [len(g) for g in groups] in ([2, 3, 3], [1, 3, 3]) and all(g == '.' for g in gaps)
        if dotted_dni:
            hidden = {0: 2}
        i = 0
        while i < len(groups):
            if i in hidden or len(groups[i]) >= 7:
                final = hidden.get(i, i)
                value = ''.join(groups[i:final + 1])
                out.append(line[last:spans[i][0]])
                out.append(keep('IMEI' if 14 <= len(value) <= 16 else 'NUM', value))
                last = spans[final][1]
                i = final + 1
            else:
                i += 1
    out.append(line[last:])
    return ''.join(out)


def _hide_after_imei(segment: str, keep) -> str:
    """«IMEI 1: 3534 5678 y 9012 345»: every digit after the word, with what lies between them, out."""
    digits = re.findall(r'\d+', segment)
    if not digits:
        return segment
    first = re.search(r'\d', segment).start()
    # «IMEI 1:» / «IMEI2» name the slot: its number stays.
    # The slot digit only when a separator follows it («IMEI 1: …»), never the first digit of the IMEI.
    head = re.match(r'imei\s{0,3}(?:[12](?=\s{0,3}[:#=-]|\s+\d)\s{0,3})?[:#=-]?\s{0,3}', segment, re.I)
    body_start = max(first, head.end() if head else 0)
    body = segment[body_start:]
    value = ''.join(re.findall(r'\d+', body))
    if not value:
        return segment
    return segment[:body_start] + keep('IMEI' if 14 <= len(value) <= 16 else 'NUM', value)


def _hide_after_serial(segment: str, label_end: int, keep) -> str:
    """The value after a serial label: its first token, and the next ones while they look like code."""
    rest = segment[label_end:]
    lead = re.match(r'\s{0,3}[:#=.]?\s{0,3}', rest)
    colon = bool(re.search(r'[:#=]', lead.group(0)))
    tokens = rest[lead.end():].split(' ')
    taken = []
    for token in tokens:
        if not token:
            break
        code = bool(re.search(r'\d', token)) or (token.isupper() and len(token) <= 10)
        if not taken and not (colon or re.search(r'\d', token)):
            return segment  # «Serie iPhone 15»: «serie» as a word, not a label.
        if taken and (not code or token.lower() in ('imei', 'ns', 'sn')):
            break
        taken.append(token)
    if not taken:
        return segment
    value = ' '.join(taken)
    return segment[:label_end] + lead.group(0) + keep('SERIE', value) + rest[lead.end() + len(value):]


def redact(text: str) -> tuple[str, dict]:
    """`(text without personal data or identifiers, {placeholder: original})`. Originals never leave the server."""
    secrets, counters = {}, {}

    def keep(kind, value):
        counters[kind] = counters.get(kind, 0) + 1
        placeholder = f'[{kind}_{counters[kind]}]'
        secrets[placeholder] = value
        return placeholder

    out, carry = [], None
    for line in normalize(text).split('\n'):
        lower = line.lower()
        stripped = lower.strip()
        if carry == 'personal':
            out.append('[DATO_PERSONAL]' if stripped else line)
            carry = None if stripped else carry
            continue
        # A personal line goes out whole FIRST — also right after a lone «IMEI:» or «S/N:» label.
        if _PERSONAL.search(lower) or _EMAIL_SIGN.search(lower):
            out.append('[DATO_PERSONAL]')
            carry = 'personal' if stripped.endswith(':') else None
            continue
        if carry in ('IMEI', 'SERIE') and stripped:
            out.append(keep(carry, line.strip()) if carry == 'SERIE' else _hide_after_imei('imei ' + line, keep)[5:])
            carry = None
            continue
        # Split the line at every IMEI or serial label, and treat each stretch on its own.
        cuts = sorted({m.start() for m in _IMEI_WORD.finditer(lower)} | {m.start() for m in _SERIAL_WORD.finditer(lower)})
        pieces = [line[a:b] for a, b in zip([0] + cuts, cuts + [len(line)])]
        rebuilt = []
        for piece in pieces:
            low = piece.lower()
            if low.startswith('imei'):
                if not re.search(r'\d', piece[4:].lstrip(' 12:#=-')) and piece.strip().endswith(':'):
                    carry = 'IMEI'
                rebuilt.append(_hide_after_imei(piece, keep))
                continue
            label = _SERIAL_WORD.match(low)
            if label:
                if not piece[label.end():].strip(' :#=.'):
                    carry = 'SERIE' if piece.strip().endswith(':') else carry
                    rebuilt.append(piece)
                else:
                    rebuilt.append(_hide_after_serial(piece, label.end(), keep))
                continue
            rebuilt.append(piece)
        out.append(_hide_numbers(''.join(rebuilt), keep))
    return '\n'.join(out), secrets
