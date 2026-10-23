# Aceptación del propietario — lanzamiento R1

Esta guía sirve para aceptar R1 **una vez desplegado**. R1 es `master` tras #156 y abarca #141–#157 desde la producción `6daf9ae`.

Cada paso indica:
- la pantalla (ruta del panel);
- la acción;
- lo esperado;
- lo que **no** debe pasar.

Cada sección termina con cómo deshacer lo que se creó. Los textos entre «» son los del código de `master`. Los de la sección D marcados *(#156)* vienen de la rama de #156 y valen sólo si #156 entra en R1.

## Reglas de la prueba

- **Sólo datos de prueba.** Se usan:
  - el modelo «Galaxy Prueba R1»;
  - sus productos «Galaxy Prueba R1 128 GB Negro» y «Galaxy Prueba R1 256 GB Negro»;
  - el proveedor «Proveedor Prueba R1»;
  - los IMEI ficticios de [listas-semanales-prueba.md](acceptance-r1/listas-semanales-prueba.md).
- **No tocar nunca** los modelos reales «iPhone 17 Pro» y «Nuevos», ni pedidos, pagos o comprobantes reales.
- Pagos: sólo con Izipay TEST y en un entorno autorizado. SUNAT real queda fuera.
- Cuenta: la del propietario o la del rol `administrador`, con «Ver costos y márgenes». La sección G usa además dos cuentas de prueba.
- Material:
  - lista de WhatsApp: [whatsapp-proveedor-prueba.txt](acceptance-r1/whatsapp-proveedor-prueba.txt);
  - archivos semanales: [listas-semanales-prueba.md](acceptance-r1/listas-semanales-prueba.md).
- Orden sugerido: A → B → C → E → D → F → G → Limpieza. Cada sección parte de lo que dejó la anterior.
- Estados: `VERIFIED` · `FAILED` · `BLOCKED` · `PENDING OWNER ACCEPTANCE`.
  - Todo empieza en `PENDING OWNER ACCEPTANCE`.
  - Un fallo se anota con la hora y una captura, y la prueba se detiene en esa sección.

## Fuera de R1 (BLOCKED)

| # | Qué | Estado |
|---|---|---|
| X1 | Equipos seminuevos externos de proveedor (usados de terceros). R1 no los vende bajo pedido; el borrador los deja fuera: «Seminuevo de proveedor: no es una oferta ni stock propio. Se recibe por equipo cuando llega a la tienda.» | BLOCKED |
| X2 | Cobros reales con Izipay (producción) | BLOCKED |
| X3 | Envío real a SUNAT | BLOCKED |
| X4 | IA del inventario con proveedor real (`INVENTORY_AI_ENABLED` apagada y sin proveedor) | BLOCKED |

## A. Modelos

Pantalla: menú «Productos» › «Modelos y variantes» (`/admin/products/models`). Título «Modelos».

| # | Acción | Esperado | No debe pasar | Estado |
|---|---|---|---|---|
| A1 | Pulsa «+ Nuevo modelo» y deja «Nombre» vacío. Pulsa «Crear modelo». | Aparece «Escribe el nombre del modelo.» | Que se cree un modelo sin nombre. | PENDING OWNER ACCEPTANCE |
| A2 | «Nombre»: `Galaxy Prueba R1`. «Categoría»: «Sin categoría». Deja **sin marcar** «Publicado en la tienda en línea». Pulsa «Crear modelo». | El formulario se cierra y el modelo aparece en la lista con «Oculto» en la columna «Tienda». | Que cambie cualquier otro modelo, o que el nuevo se vea en la tienda. | PENDING OWNER ACCEPTANCE |
| A3 | En su fila pulsa «Editar modelo». En «Descripción» escribe `Modelo de prueba R1 — no vender`. Pulsa «Guardar cambios». | Aparece «Modelo guardado.» | «No se pudo guardar el modelo.» | PENDING OWNER ACCEPTANCE |
| A4 | Recarga la página (F5) y vuelve a abrir el modelo. | La descripción sigue guardada. | Que vuelva el valor anterior. | PENDING OWNER ACCEPTANCE |

**Deshacer:** no existe borrar un modelo; es intencional. Al final se desmarca «Publicado en la tienda en línea» y se guarda (ver Limpieza).

## B. Variantes

Pantallas:
- «Productos» › «Productos» (`/admin/products`), botón «Nuevo producto»;
- el detalle del modelo (`/admin/products/models/<id>`).

| # | Acción | Esperado | No debe pasar | Estado |
|---|---|---|---|---|
| B1 | «Nuevo producto»: «Nombre» `Galaxy Prueba R1 128 GB Negro`, «Precio (S/)» `1199`, «Stock inicial» `0`, «Destino» «Publicar en e-commerce». Pulsa «Crear producto». Repite con `Galaxy Prueba R1 256 GB Negro` y `1299`. | Se crean dos productos sin stock. | Stock distinto de 0. | PENDING OWNER ACCEPTANCE |
| B2 | En el modelo pulsa «+ Agregar variante». En «Producto» busca `Galaxy Prueba R1` y pulsa «Buscar». Elige el de 128 GB. Pon «Línea» nuevo y las opciones de capacidad «128 GB» y color «Negro». Pulsa «Guardar variante». Repite con el de 256 GB. | Aparece «Variante guardada.» y la tabla de variantes muestra las dos. | Que se usen opciones que no existen. Usa las opciones de capacidad y color que ya tiene la empresa. | PENDING OWNER ACCEPTANCE |
| B3 | En la de 128 GB pulsa «Editar variante». Aparece «Editando …». Cambia «Precio (S/)» a `1189` y desmarca «Publicado en la tienda». Pulsa «Guardar cambios». | Aparece «Variante actualizada.» y la columna «Tienda» dice «Oculta». | Que cambie el stock o el historial: el texto dice «Es el mismo producto: conserva su stock, sus equipos y su historial.» | PENDING OWNER ACCEPTANCE |
| B4 | Recarga (F5) y abre de nuevo «Editar variante». | Siguen `1189` y la variante oculta. | Que vuelvan los valores anteriores. | PENDING OWNER ACCEPTANCE |
| B5 | Vuelve a poner `1199`, marca «Publicado en la tienda» y guarda. Después pulsa «Guardar cambios» sin tocar nada. | Primero «Variante actualizada.» y «Publicada»; después «No hay cambios que guardar.» | — | PENDING OWNER ACCEPTANCE |
| B6 | «Editar modelo»: marca «Publicado en la tienda en línea» y pulsa «Guardar cambios». | Aparece «Modelo guardado.» y la columna «Tienda» dice «Publicado». | — | PENDING OWNER ACCEPTANCE |
| B7 | **Después de F.** Abre «Editar variante» en la de 256 GB, que ya tiene venta y equipo. | Aparece «Ya tiene … venta(s) y … equipo(s): cambiar modelo u opciones lo hace un administrador de la empresa, con motivo.» y el campo «Motivo del cambio (obligatorio)». Si escribes un motivo de menos de 5 caracteres: «Escribe el motivo del cambio (al menos 5 caracteres): quedará en la auditoría.» Pulsa «Cancelar» sin guardar. | Que permita cambiar modelo u opciones sin motivo. | PENDING OWNER ACCEPTANCE |

**Deshacer:** ver Limpieza:
- desmarca «Publicado en la tienda»;
- usa «Quitar del modelo» en las variantes sin historial;
- pulsa «Desactivar» en los productos de prueba de `/admin/products`.

## C. Proveedores y lista semanal

Pantallas:
- «Productos» › «Proveedores» (`/admin/products/suppliers`);
- «Productos» › «Ofertas de proveedores» (`/admin/products/suppliers/offers`), panel «Actualizar ofertas desde Excel».

| # | Acción | Esperado | No debe pasar | Estado |
|---|---|---|---|---|
| C1 | En «Proveedores» pulsa «+ Nuevo proveedor». «Nombre o razón social»: `Proveedor Prueba R1`. «Documento»: «Sin documento». Deja «Activo» marcado. Pulsa «Crear proveedor». | El proveedor aparece como «Activo». | Datos reales de contacto. | PENDING OWNER ACCEPTANCE |
| C2 | En «Ofertas de proveedores» pulsa «Descargar plantilla». | Se descarga `ofertas-de-proveedor.xlsx` con las columnas descritas en la guía de listas. | — | PENDING OWNER ACCEPTANCE |
| C3 | Lista **parcial**. Sube `prueba-r1-parcial-1.xlsx` en «Archivo de ofertas (.xlsx)». Deja **sin marcar** «Usar IA en esta importación» y «Es la lista completa de un proveedor». Pulsa «Previsualizar». | «Nuevas» 2, «Con error» 0. «Antes → después» dice «Oferta nueva». | Que se escriba algo antes de aplicar. | PENDING OWNER ACCEPTANCE |
| C4 | Pulsa «Aplicar cambios». | Aparece «Ofertas actualizadas: 2 nuevas, 0 modificadas, 0 sin cambios.» En la tabla, la de 256 GB dice «Sin confirmar» / «Disponibilidad no confirmada». | Que cambie una oferta de otro proveedor. | PENDING OWNER ACCEPTANCE |
| C5 | Sube `prueba-r1-parcial-2.xlsx` y pulsa «Previsualizar». | La de 128 GB es «Cambia» y la de 256 GB «Agotada». Aparece «El archivo cambia lo que paga el cliente» con «Acepto los precios nuevos de esta oferta». | — | PENDING OWNER ACCEPTANCE |
| C6 | Sin marcar la aceptación, pulsa «Aplicar sin cambiar precios». | El resultado incluye «Se mantuvo el precio actual de 1 ofertas.» (o la forma singular del texto). La de 128 GB queda en `1199` y la de 256 GB, agotada. | Que el precio pase a `1249`. | PENDING OWNER ACCEPTANCE |
| C7 | Vuelve a subir `prueba-r1-parcial-2.xlsx` y previsualiza. | Aparece «Este archivo ya se aplicó el …; aplicarlo otra vez vuelve a fijar lo disponible.» Pulsa «Descartar». | — | PENDING OWNER ACCEPTANCE |
| C8 | Lista **completa vacía**. Sube `prueba-r1-completa-vacia.xlsx`. Marca «Es la lista completa de un proveedor» y elige «Proveedor Prueba R1» en «Elige el proveedor…». Pulsa «Previsualizar». | Aparece «La lista completa está vacía: no se desactiva nada con un archivo sin filas.» | Que se desactive algo. | PENDING OWNER ACCEPTANCE |
| C9 | Lista **completa**. Sube `prueba-r1-completa.xlsx` con la misma casilla y el mismo proveedor. Pulsa «Previsualizar». | Aparece «Lista completa de Proveedor Prueba R1: se desactivarán 1 ofertas vigentes ausentes … Las programadas no cambian.» y dos casillas obligatorias: «La lista incluye todos los mensajes y archivos del proveedor» y «Confirmo desactivar 1 ofertas ausentes». | Que deje aplicar sin las dos casillas. | PENDING OWNER ACCEPTANCE |
| C10 | Marca las dos casillas y pulsa «Aplicar cambios». | La de 128 GB queda «Desactivada». La de 256 GB queda vigente con 1 disponible. | Que se desactive cualquier oferta de otro proveedor. | PENDING OWNER ACCEPTANCE |

**Deshacer:**
- En cada oferta de prueba pulsa «Desactivar». En «Motivo de la desactivación» escribe `prueba R1` y pulsa «Desactivar oferta».
- En «Proveedores», pulsa «Desactivar» en «Proveedor Prueba R1».

Las ofertas se conservan con su «Historial».

## D. Lista de WhatsApp → borrador → importador oficial

Pantalla: menú «Inventario» › «Gestión masiva de inventario» (`/admin/inventory/bulk`), tarjeta «Reconocer lista de WhatsApp · Vista previa local».

| # | Acción | Esperado | No debe pasar | Estado |
|---|---|---|---|---|
| D1 | Configura la lista: «Estos productos son» = «Nuevos» y «Origen de la lista» = «Proveedor (NO stock propio)». Pega `whatsapp-proveedor-prueba.txt` en «Texto de WhatsApp». Pulsa «Analizar localmente». | Aparece «3 registros candidatos · 4 líneas sin producto · 0 de 3 revisados». La tarjeta dice «El texto no sale del navegador ni va a ninguna IA. No crea ni modifica productos, costos o stock.» | Que el teléfono ficticio aparezca como producto o sin enmascarar. | PENDING OWNER ACCEPTANCE |
| D2 | Revisa la tabla. | Las dos líneas del Galaxy tienen «Capacidad» 128 y 256, «Color» negro, y «Costo proveedor» 999 y 1099 (sólo con «Ver costos y márgenes»). | — | PENDING OWNER ACCEPTANCE |
| D3 | Corrige: en «Precio comercial» escribe `1199` y `1299`. En «Cantidad» escribe `3` en la de 128 GB y deja vacía la de 256 GB. Marca «Revisado» en las tres. | Los valores quedan escritos. | — | PENDING OWNER ACCEPTANCE |
| D4 | Pulsa «Buscar en el catálogo». | Las dos del Galaxy encuentran su variante en «Variante del catálogo». La funda dice «Sin coincidencia». | Que se envíe el texto, un costo o un IMEI: la ayuda dice «Envía al servidor sólo modelo, capacidad, color, tipo y línea; nunca el texto, costos ni IMEI.» | PENDING OWNER ACCEPTANCE |
| D5 | En «Borrador de importación» elige «Proveedor» `Proveedor Prueba R1`. Pon las mismas «Vigente desde» y «Vigente hasta» que en C, y «Plazo (días)» `3`. Deja **sin marcar** la lista completa. Pulsa «Generar borrador». | Aparece «Borrador listo: no se escribió nada. Es lo que haría la importación oficial de ofertas con estas filas.» con sus cuentas. La funda va en «Necesitan revisión» con «Elige la variante del catálogo («Buscar en el catálogo»).». | Que cambie alguna oferta en «Ofertas de proveedores»: compruébalo en otra pestaña. | PENDING OWNER ACCEPTANCE |
| D6 | Pulsa «Descargar borrador (.xlsx)». | Se descarga `borrador-ofertas-whatsapp.xlsx`. | IMEI, series o teléfono dentro del archivo. | PENDING OWNER ACCEPTANCE |
| D7 *(#156)* | Pulsa «Continuar al importador oficial». | En la misma página se abre «Importador oficial de ofertas», con el archivo «Del borrador de WhatsApp: …» y su vista previa (columnas «Fila», «Acción», «Proveedor», «Producto», «Precio», «Disponible», «Vigencia», «Antes → después», «Observaciones»). | Que se aplique solo: el aviso dice «sólo «Aplicar» escribe, con sus confirmaciones». | PENDING OWNER ACCEPTANCE |
| D8 *(#156)* | Si aparece «El archivo cambia lo que paga el cliente», marca la aceptación. Pulsa «Aplicar cambios». | Aparece «Ofertas actualizadas: …». En «Ofertas de proveedores» la de 128 GB queda con 3 disponibles y la de 256 GB, sin cambio de cantidad (vacía = no confirma). | Que cambie la oferta de otro proveedor o un producto real. | PENDING OWNER ACCEPTANCE |
| D9 *(#156)* | Pulsa «Seguir corrigiendo». | El importador se cierra. Ya usado: «El borrador ya se usó. Para otro archivo, usa «Ofertas de proveedores».» | Aplicar dos veces el mismo borrador. | PENDING OWNER ACCEPTANCE |

**Deshacer:** igual que en C: «Desactivar» cada oferta de prueba con su motivo. El texto pegado no se guarda en ningún sitio.

## E. Tienda: agotado, bajo pedido y precio

Pantalla: la página del modelo en la tienda, `/modelo/<dirección del modelo>` (la dirección se ve en «Editar modelo»). Se revisa justo después de C10.

| # | Acción | Esperado | No debe pasar | Estado |
|---|---|---|---|---|
| E1 | Elige la variante de 128 GB: sin stock y con su oferta desactivada. | Aparece «Agotado» y el botón también dice «Agotado», desactivado. | Que se pueda agregar al carrito. | PENDING OWNER ACCEPTANCE |
| E2 | Elige la variante de 256 GB: sin stock, con oferta vigente y 1 confirmada. | Se ve:<br>• «Bajo pedido — entrega en 3 días»;<br>• la etiqueta «Precio bajo pedido» con «S/ 1,299.00»;<br>• la nota «No lo tenemos en tienda ahora: lo pedimos para ti al confirmar tu compra.»;<br>• el botón «Pedir bajo pedido». | El precio del producto en lugar del de la oferta, o el costo del proveedor. | PENDING OWNER ACCEPTANCE |
| E3 | En el listado de la tienda busca el modelo. | Su tarjeta dice «Desde S/ …». | — | PENDING OWNER ACCEPTANCE |
| E4 | Desactiva la oferta de 256 GB (C, deshacer) y recarga la página del modelo. Vuelve a activarla si vas a hacer F. | La variante pasa a «Agotado». | Que siga vendiéndose bajo pedido. | PENDING OWNER ACCEPTANCE |

**Deshacer:** nada propio; el modelo se oculta en Limpieza.

## F. Recepción del proveedor e IMEI

Requisito: un pedido de prueba de la variante de 256 GB, comprado en la tienda con «Pedir bajo pedido» y pagado con **Izipay TEST** en un entorno autorizado. Si ese entorno no está disponible, marca F1–F7 como `BLOCKED`.

Pantalla: «Ventas» › «Pedidos» (`/admin/orders`), detalle del pedido de prueba, panel «Abastecimiento bajo pedido».

| # | Acción | Esperado | No debe pasar | Estado |
|---|---|---|---|---|
| F1 | Abre el pedido de prueba. | La línea dice «Pedido al proveedor», o «Por confirmar con el proveedor»; en ese caso, primero «Confirmar con proveedor». | — | PENDING OWNER ACCEPTANCE |
| F2 | Pulsa «Recibir del proveedor». En «IMEI (equipo 1)» escribe los 14 dígitos `35842107316529`. | Aparece «Falta un dígito: ¿es 358421073165298?» con el botón «Completar». | Que reescriba un IMEI de 15 dígitos. | PENDING OWNER ACCEPTANCE |
| F3 | Escribe `358421073165290` y pulsa «Registrar recepción». | Aparece «Ese IMEI no es válido: revisa los dígitos.» y no se registra. | Que se registre. | PENDING OWNER ACCEPTANCE |
| F4 | Pulsa «Completar», o escribe `358421073165298`. Completa «Serie (equipo 1)» con `PRUEBAR1-0001`. Pulsa «Registrar recepción». | Aparece «Recepción registrada.» y la línea pasa a «Recibido del proveedor». | — | PENDING OWNER ACCEPTANCE |
| F5 | Pulsa «Recibir del proveedor» otra vez, si aparece. | Aparece «Esta línea ya estaba recibida: no se registró otra vez.» | Dos equipos por una línea. | PENDING OWNER ACCEPTANCE |
| F6 | «Inventario» › «Movimientos / Kardex» (`/admin/inventory/movements`). | Aparece la entrada del equipo de prueba. Con «Ver costos y márgenes» se ve el costo. | Movimientos de productos reales. | PENDING OWNER ACCEPTANCE |
| F7 | Con otro pedido de prueba, intenta recibir con el mismo IMEI `358421073165298`. | Aparece «Ese IMEI ya está registrado en otro equipo de la empresa.» | Que lo acepte. | PENDING OWNER ACCEPTANCE |

**Deshacer:** en el detalle del pedido de prueba, «Cancelar pedido» con «Motivo de la cancelación» `prueba R1`. Si estaba pagado con Izipay TEST, el reembolso queda pendiente de un pago TEST: no hay dinero real que devolver. El equipo de prueba queda en el Kardex como historial; no se borra.

## G. Seguridad

Cuentas de prueba:
- **G-ventas:** de la misma empresa, con «Ver productos» pero **sin** «Administrar productos» ni «Ver costos y márgenes».
- **G-otra:** de **otra empresa de prueba**. Si no existe ninguna otra empresa, marca G4–G5 como `BLOCKED`; lo cubren las pruebas automáticas.

| # | Acción | Esperado | No debe pasar | Estado |
|---|---|---|---|---|
| G1 | Con G-ventas abre «Modelos y variantes». | Ve la lista, sin «+ Nuevo modelo», sin «Editar modelo» y sin «Editar variante». | Poder editar. | PENDING OWNER ACCEPTANCE |
| G2 | Con G-ventas abre `/admin/inventory/costs` escribiendo la ruta. | La entrada «Costos y precios» no está en el menú. La página dice «Necesitas el permiso Ver costos y márgenes; pídeselo al administrador.» o «Tu cuenta no tiene permiso para esta sección en esta empresa.» con «Volver al panel». | Ver costos o márgenes. | PENDING OWNER ACCEPTANCE |
| G3 | Con G-ventas abre «Ofertas de proveedores». | Aparece «Puedes descargar la plantilla y exportar las ofertas. Subir cambios requiere permiso para gestionar productos.» y no hay columna de costo. | Poder aplicar un archivo. | PENDING OWNER ACCEPTANCE |
| G4 | Con G-otra abre «Modelos y variantes» y «Ofertas de proveedores». | Ni «Galaxy Prueba R1» ni «Proveedor Prueba R1» aparecen. | Ver cualquier dato de esta empresa. | PENDING OWNER ACCEPTANCE |
| G5 | Con G-otra pega la ruta del modelo de prueba (`/admin/products/models/<id>`) y la del pedido de F (`/admin/orders/<id>`). | Ninguna muestra datos: el servidor responde como si no existieran. | Ver nombre, precio, pedido o IMEI. | PENDING OWNER ACCEPTANCE |

**Deshacer:** desactiva las dos cuentas de prueba al terminar.

## Limpieza final

| # | Acción | Estado |
|---|---|---|
| L1 | «Editar modelo» de «Galaxy Prueba R1»: desmarca «Publicado en la tienda en línea», pon de «Nombre» `ZZ Prueba R1 (no usar)` y guarda. | PENDING OWNER ACCEPTANCE |
| L2 | Variantes sin historial: «Quitar del modelo». La de 256 GB, con venta, sólo se oculta (desmarcar «Publicado en la tienda»). | PENDING OWNER ACCEPTANCE |
| L3 | `/admin/products`: «Desactivar» en los dos productos de prueba. | PENDING OWNER ACCEPTANCE |
| L4 | Ofertas de prueba «Desactivadas» y «Proveedor Prueba R1» «Inactivo». | PENDING OWNER ACCEPTANCE |
| L5 | La tienda ya no muestra el modelo de prueba. «iPhone 17 Pro» y «Nuevos» siguen como estaban. | PENDING OWNER ACCEPTANCE |

## Registro

| Fecha | SHA desplegado | Quién | Resultado (VERIFIED / FAILED / BLOCKED por sección) |
|---|---|---|---|
| | | | |
