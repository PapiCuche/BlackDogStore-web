"""
La firma XMLDSig del comprobante.

POR QUÉ NO SE IMPLEMENTA A MANO
-------------------------------
Firmar un XML no es «calcular un hash y cifrarlo». Lo que se firma es el
documento CANONICALIZADO, y la canonicalización es una especificación entera:
orden de atributos, declaraciones de espacios de nombres heredadas, normalización
de espacios en blanco. Una implementación propia que acierte el 99 % de los
documentos falla en el 1 % restante de forma indistinguible de una firma
falsificada.

Por eso se delega en `signxml`, que se apoya en `lxml` y `cryptography`.

POR QUÉ NO BASTA LA BIBLIOTECA ESTÁNDAR
---------------------------------------
`xml.etree.ElementTree.canonicalize` implementa **C14N 2.0**. SUNAT exige
`http://www.w3.org/TR/2001/REC-xml-c14n-20010315`, que es la **1.0**. No son
intercambiables en un documento con prefijos de espacio de nombres, que es
exactamente lo que es un UBL.

LOS ALGORITMOS SON CONFIGURACIÓN, NO CONSTANTES DEL CÓDIGO
----------------------------------------------------------
«RSA-SHA256 funciona criptográficamente» y «esta firma cumple el perfil de SUNAT»
son dos preguntas distintas, y sólo la segunda importa aquí. Los URI viven en
`SignatureProfile` para que ajustar el perfil sea cambiar un dato y no reescribir
la función — y para que un test pueda afirmar qué perfil se emitió.
"""

from __future__ import annotations

from dataclasses import dataclass

from lxml import etree

#: Donde SUNAT exige que viva la firma. La segunda extensión: la primera lleva
#: la información adicional del comprobante.
EXT_NS = 'urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2'
DS_NS = 'http://www.w3.org/2000/09/xmldsig#'

#: El identificador que se pone al bloque de firma y al que apunta el
#: `cac:DigitalSignatureAttachment` del documento.
SIGNATURE_ID = 'SignatureSP'


@dataclass(frozen=True)
class SignatureProfile:
    """
    Los URI exactos con los que se firma.

    Se declaran para poder AFIRMAR qué se emitió. Un test que comprueba que el
    XML lleva estos algoritmos vale más que la fe en el valor por defecto de una
    librería, que puede cambiar entre versiones sin avisar.
    """

    canonicalization: str
    signature_method: str
    digest_method: str

    #: Nombre corto que espera `signxml` para el digest.
    digest_name: str


#: El perfil de partida, tomado del Anexo N.º 6 (canonicalización) y de los
#: ejemplos de la Guía XML de Factura UBL 2.1. Ver `docs/sunat-cpe-requisitos.md`.
SUNAT_PROFILE = SignatureProfile(
    canonicalization='http://www.w3.org/TR/2001/REC-xml-c14n-20010315',
    signature_method='http://www.w3.org/2001/04/xmldsig-more#rsa-sha256',
    digest_method='http://www.w3.org/2001/04/xmlenc#sha256',
    digest_name='sha256',
)


class SigningError(Exception):
    """No se pudo firmar. Nunca lleva la clave ni la contraseña en el mensaje."""


def sign_invoice(
    root: etree._Element,
    *,
    key_pem: bytes,
    cert_pem: bytes,
    profile: SignatureProfile = SUNAT_PROFILE,
) -> etree._Element:
    """
    Firma el documento y coloca la firma donde SUNAT la busca.

    `signxml` deja la firma al final del elemento raíz. SUNAT la quiere dentro
    del último `ext:ExtensionContent`, así que se MUEVE después de firmar. Mover
    un nodo tras la firma no la invalida en este caso concreto porque la firma es
    de tipo *enveloped*: la transformación excluye el propio bloque de firma del
    cálculo, de modo que su posición no forma parte de lo firmado.

    Ni la clave ni la contraseña salen de aquí: los errores se re-lanzan sin el
    detalle de la librería, que podría incluir material sensible.
    """
    from signxml import CanonicalizationMethod, SignatureMethod, XMLSigner, methods

    signer = XMLSigner(
        method=methods.enveloped,
        signature_algorithm=SignatureMethod(profile.signature_method),
        digest_algorithm=profile.digest_name,
        c14n_algorithm=CanonicalizationMethod(profile.canonicalization),
    )
    try:
        signed = signer.sign(root, key=key_pem, cert=cert_pem)
    except Exception as exc:  # noqa: BLE001 — se reduce a propósito
        raise SigningError(f'No se pudo firmar el comprobante: {type(exc).__name__}') from None

    signature = signed.find(f'.//{{{DS_NS}}}Signature')
    if signature is None:
        raise SigningError('La librería no produjo bloque de firma.')
    signature.set('Id', SIGNATURE_ID)

    slots = signed.findall(f'.//{{{EXT_NS}}}ExtensionContent')
    if not slots:
        raise SigningError('El documento no tiene ext:ExtensionContent donde alojar la firma.')
    slots[-1].append(signature)
    return signed


def digest_value(signed: etree._Element) -> str:
    """
    El `DigestValue` del documento firmado.

    Es el «Valor Resumen» que exige el código QR. NO se recalcula un hash
    aparte: SUNAT dice literalmente que corresponde «al valor del elemento
    <ds:DigestValue> del documento», así que se lee de ahí o no se pone.
    """
    node = signed.find(f'.//{{{DS_NS}}}DigestValue')
    if node is None or not node.text:
        raise SigningError('El documento firmado no expone DigestValue.')
    return node.text


def verify(signed_xml: bytes, *, cert_pem: bytes) -> bool:
    """
    ¿Sigue siendo válida esta firma?

    Existe para que un test pueda demostrar lo que importa: que alterar un
    importe DESPUÉS de firmar rompe la firma. Sin esta comprobación, «el
    documento está firmado» sería una afirmación sin evidencia.
    """
    from signxml import XMLVerifier

    try:
        XMLVerifier().verify(signed_xml, x509_cert=cert_pem)
        return True
    except Exception:  # noqa: BLE001 — cualquier fallo es «no verifica»
        return False
