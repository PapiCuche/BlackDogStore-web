"""
Utilidades de prueba del dominio fiscal. NO se usan en producción.

Vive aquí y no en `tests.py` porque el certificado autofirmado hace falta en
varios sitios —tests unitarios, el envío manual a BETA— y duplicar su
construcción es cómo dos pruebas acaban firmando con perfiles distintos sin que
nadie lo note.

UN CERTIFICADO AUTOFIRMADO NO VALE PARA PRODUCCIÓN. SUNAT exige uno emitido por
una entidad acreditada ante INDECOPI a nombre del RUC. Éste sirve para el entorno
de pruebas y para comprobar que la firma se construye y se verifica.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from .data import InvoiceData, Line, Party


def self_signed_pem(ruc: str = '20100066603') -> tuple[bytes, bytes]:
    """Devuelve (clave PEM, certificado PEM). Sólo para pruebas."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, 'PE'),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, 'ENTORNO DE PRUEBAS'),
        x509.NameAttribute(NameOID.COMMON_NAME, ruc),
    ])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    return (
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
        cert.public_bytes(serialization.Encoding.PEM),
    )


def minimal_invoice(**overrides) -> InvoiceData:
    """
    El caso mínimo: venta interna gravada, una línea, 100 + 18 = 118.

    Las cifras son reconocibles a simple vista a propósito. Un test que falla
    diciendo «esperaba 100.00» se lee de un vistazo; uno que dice «esperaba
    4744.92» obliga a sacar la calculadora para saber si el error es del código
    o del test.
    """
    data = {
        'document_type': '01',
        'serie': 'F001',
        'correlativo': 1,
        'issue_date': dt.date(2026, 9, 6),
        'issue_time': dt.time(12, 0, 0),
        'currency': 'PEN',
        'supplier': Party(
            doc_type='6', doc_number='20100066603',
            legal_name='ENTORNO DE PRUEBAS SAC',
            address_line='AV PRUEBA 123', district_code='040101',
        ),
        'customer': Party(
            doc_type='6', doc_number='20000000001',
            legal_name='CLIENTE DE PRUEBA SAC',
        ),
        'lines': (
            Line(
                description='ARTICULO DE PRUEBA',
                quantity=Decimal('1'), unit_code='NIU',
                unit_price=Decimal('100.00'),
                unit_price_with_tax=Decimal('118.00'),
                line_amount=Decimal('100.00'),
                tax_amount=Decimal('18.00'),
                tax_percent=Decimal('18.00'),
            ),
        ),
        'taxable_amount': Decimal('100.00'),
        'tax_amount': Decimal('18.00'),
        'total': Decimal('118.00'),
        'amount_in_words': 'CIENTO DIECIOCHO CON 00/100 SOLES',
    }
    data.update(overrides)
    return InvoiceData(**data)
