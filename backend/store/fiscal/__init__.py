"""
El dominio de comprobantes de pago electrónicos (CPE) de Perú.

POR QUÉ ES UN PAQUETE APARTE Y NO MÁS FUNCIONES EN `store/`
-----------------------------------------------------------
Lo de aquí dentro no conoce Django. Ni ORM, ni `settings`, ni red, ni la empresa
piloto. Recibe datos planos y devuelve bytes.

Eso no es purismo: es lo que permite probar el XML contra el esquema de SUNAT sin
levantar una base de datos, y lo que impide que el generador «arregle» un importe
consultando algo. El dinero ya lo decidió C2.1 y aquí sólo se transcribe.

LO QUE ESTE PAQUETE NO ES
-------------------------
No es `SalesNote`. La nota de venta interna sigue siendo interna, sigue
numerándose con `InternalSequence` y sigue diciendo que no es un comprobante
SUNAT. Son dos documentos distintos con dos autoridades de numeración distintas,
y mezclarlos sería dar validez fiscal a un papel que no la tiene.
"""
