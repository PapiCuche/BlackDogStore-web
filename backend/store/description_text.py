"""
CATALOG-RICH-DESC-01 — the description of a product or a model.

Stored as Markdown text in the same TextField as always (no migration): the storefront renders a closed subset
of it as React elements and never as HTML (`frontend/app/lib/markdown-lite.ts`), so nothing here needs to strip
tags — a `<script>` in a description is text. What the server does guarantee is the size and that the text can
be stored at all.
"""

#: The longest description accepted (the editor counts against the same number).
DESCRIPTION_MAX_LENGTH = 20000


def too_long_message(length: int) -> str:
    return (f'La descripción tiene {length} caracteres y admite hasta {DESCRIPTION_MAX_LENGTH}. '
            'Acórtala antes de guardar.')


def normalize_description(value) -> str:
    """The description as it will be stored: line endings unified, NUL removed (PostgreSQL rejects it)."""
    text = str(value or '').replace('\r\n', '\n').replace('\r', '\n').replace('\x00', '')
    return text.strip()
