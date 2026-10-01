# Entrega — ERP-FISCAL-6 · Descuentos declarados (con cierre del POS BETA)

Fecha: 2026-09-29 · Rama: `erp/sales-fiscal-ui` · Ambiente fiscal: **SUNAT BETA únicamente**.

Este informe es autosuficiente: cada afirmación cita el fichero, la función, el
test o la regla oficial que la sostiene. Lo que no se pudo verificar se dice.

## 1. Resumen ejecutivo

Una venta con descuento se emite como factura (01) o boleta (03). El descuento se
declara con `cac:AllowanceCharge` donde nació: cupón y descuento manual como un
descuento global con código `02` del Catálogo N.º 53 vigente; promoción automática
en cada línea rebajada con código `00`, a partir de la atribución por componente que
el motor comercial congela en la venta (ADR-42). Los totales siguen las reglas
oficiales de validación de SUNAT (ADR-43): `PayableAmount` es lo cobrado, la base
imponible es la del snapshot y `AllowanceTotalAmount` se omite para no restar la
rebaja dos veces. Un descuento que el snapshot no explica falla cerrado sin gastar
correlativo.

La caja ofrece nota interna, boleta y factura con un contrato que explica por qué una
opción no está disponible a quien puede emitir y no revela nada a quien no puede;
`seed_demo_users --fiscal-beta` prepara las series DEMO; la venta completada muestra
el comprobante real. Dos bugs latentes corregidos por el camino (§36).

Tres desviaciones del borrador de la fase se resolvieron siguiendo la fuente de mayor
autoridad (archivo oficial de reglas de validación, 21.04.2025): código `02` para el
descuento global (no `00`), «Total valor de venta» = base imponible (no el valor
previo al descuento) y `AllowanceTotalAmount` omitido (no «línea + global»). §3.

## 2. Git inicial

- Rama `erp/sales-fiscal-ui`; HEAD local y remoto `441ecfd` al comenzar; árbol limpio.
- La capa comercial se commiteó primero como `97043d6` (§41). El resto de la fase va
  en tres commits (§41). `origin/master` = `2dca0a3` (delta de 2 commits, sólo
  `docs/internal-parity-matrix.md`, SAFE TO INTEGRATE; no se integra en esta rama).

## 3. Decisión SUNAT (fuentes y contradicciones resueltas)

Fuentes oficiales consultadas (descargadas con `curl` desde `sunat.gob.pe` /
`cpe.sunat.gob.pe`; copias sólo en el scratchpad de la sesión, no en el repo):

| Fuente | Qué aporta |
|---|---|
| `AjustesValidacionesCPEv20250421.xlsx` — hojas `Factura2_0`, `Boleta2_0`, `Catálogos` | Reglas aritméticas y Catálogo N.º 53 vigente. **Autoridad de esta fase.** |
| Guía de elaboración XML UBL 2.1 — Factura (102 págs.) y Boleta (66 págs.) | Estructura, ejemplos (§21 descuentos globales, §38 precio de venta unitario, §40 descuentos por ítem). Usa el catálogo de 2017. |
| Anexos N.º 8 de las R.S. 117-2017, 245-2017 y 318-2017 | Catálogo 53 de 2017: `00 OTROS DESCUENTOS`, `50 OTROS CARGOS`. Superado. |
| Anexo VII R.S. 114-2019 (Anexo 9-A, estructura UBL 2.1) | Confirma «Sumatoria otros descuentos (que no afectan la base)» = `AllowanceTotalAmount`. |

Catálogo N.º 53 vigente (hoja `Catálogos`): `00` Descuentos que afectan la base
imponible del IGV/IVAP (ítem) · `01` … que no afectan (ítem) · `02` Descuentos
globales que afectan la base (global) · `03` … que no afectan (global) · `04`
globales por anticipos · `47`–`50` cargos.

Contradicciones entre el borrador de la fase y la fuente, y cómo se resolvieron:

| Borrador | Fuente oficial (hoja `Factura2_0`) | Impacto si se hubiera seguido el borrador | Decisión |
|---|---|---|---|
| Descuento global con código `00` | 4291 (OBSERV): `00`/`01`/`47`/`48` «no es válido a nivel global»; 3277/3278/3279/3291 sólo restan `02`/`04` | SUNAT recalcula totales SIN el descuento → ERROR 3277/3291/3279 | Global = `02`; línea = `00` |
| `LegalMonetaryTotal/LineExtensionAmount` = valor previo al descuento | 3278: Σ valor de venta por ítem − descuentos globales `02` + cargos `49` | ERROR 3278 y 3279 | = base imponible (`Order.taxable_amount`) |
| `AllowanceTotalAmount` = descuentos de línea + global | 3300: Σ descuentos que NO afectan la base (`01`, `03`, `63`); 3280 lo RESTA de `PayableAmount` | ERROR 3300 y 3280 (doble descuento) | Omitido |
| — (no previsto) | 3270: precio de venta unitario = (valor + tributos)/cantidad ±1 | ERROR 3270 en líneas con promoción (p. ej. 142,86 de diferencia) | `PricingReference` = precio pagado por unidad, n(12,10) |

Boleta: mismas reglas en `Boleta2_0` como observaciones 4287/4288/4290/4299/4307/4309/
4310/4312 y errores de catálogo/formato 3071/3072/3114/2968/3016/2065.

## 4. Transformación comercial → fiscal

`fiscal_services._order_to_invoice_data` (autoridad: el snapshot de `Order`):

    discount = money(Order.discount_amount)
    si discount > 0:
        subtotal      = Order.subtotal_amount            (guardas §19)
        pre_taxable   = breakdown_from_total(total=subtotal, tax_rate).taxable_amount
        net           = pre_taxable − Order.taxable_amount        (> 0 obligatorio)
        COUPON/MANUAL → Allowance(net, base=pre_taxable, '02') global; líneas SIN rebajar
                        reconciliadas contra pre_taxable (allocate_line_bases)
        PROMOTION     → rebaja bruta por línea desde AppliedPromotion (§22);
                        bases reconciliadas UNA vez contra Order.taxable_amount sobre
                        brutos ya rebajados; net repartido entre líneas en proporción
                        a su rebaja bruta (allocate_proportionally); base «de antes»
                        de cada línea = base + reparto; Allowance(reparto, base_antes, '00')
    sin descuento → camino de siempre, byte a byte.

Nunca `descuento / 1,18` por su cuenta; nunca la `Promotion` viva.

## 5. Cupón

`discount_source = coupon` → un `/Invoice/cac:AllowanceCharge`: `ChargeIndicator=false`,
`AllowanceChargeReasonCode=02`, `Amount = net`, `BaseAmount = pre_taxable`; sin
`MultiplierFactorNumeric` (el porcentaje del cupón no se congela en la venta; se
reconstruiría desde configuración mutable). Ejemplo verificado: 150,00 − 10 % →
`Amount 12.71`, `BaseAmount 127.12`, base 114,41, total 135,00
(`test_a_coupon_is_declared_as_one_global_allowance_on_a_factura`).

## 6. Manual

Misma representación, `02` global. Porcentaje manual 5 %: 7,50 bruto → `Amount 6.36`;
monto fijo 20,00 → `Amount 16.95` (`test_a_manual_percentage_…`, `test_a_manual_fixed_amount_…`).
No se inventa factor.

## 7. Promoción — regla comercial (ADR-42)

`promotion_services.allocate_component_discounts(regular_total, discount_total, components)`:
proporcional a `unit_price × quantity_used`, ROUND_DOWN a céntimos, céntimos restantes a
los mayores residuos, desempate `product_id` ASC, cierre exacto. Forma estricta
(`product_id`, `quantity_used`, `unit_price`); rechaza la forma de catálogo
(`quantity`/`price`/`available`), duplicados, negativos, `discount > regular`, Σ regular ≠
declarado, base cero con descuento. `evaluate()` enriquece; `freeze()` persiste como
texto. `frozen_component_discounts`: congelado = autoridad (valida, no recalcula);
legacy = reconstrucción en memoria sin escribir; a medias = fallo cerrado.

## 8. Promoción — representación fiscal

Por línea rebajada: `cac:AllowanceCharge` `00` entre `PricingReference` y `TaxTotal`;
`LineExtensionAmount` = valor DESPUÉS; `cac:Price/PriceAmount` = valor unitario SIN
rebajar; `PricingReference` = precio pagado por unidad. Sin allowance global.

## 9. Combos

Funda 100 + vidrio 50 a 100 (boleta): rebaja bruta 33,33 / 16,67; bases 56,50 / 28,25;
descuentos netos 28,24 / 14,13 (Σ 42,37 = 127,12 − 84,75); bases previas 84,74 / 42,38;
precios pagados 66,67 / 33,33. Percent 10 %: 8,47 / 4,24 (Σ 12,71). Dos aplicaciones de
(2 fundas + 1 vidrio) a 200: 67,80 / 16,95 (Σ 84,75). Tres líneas 3 150 → 3 000: Σ neto
127,12, atribución bruta 142,86 / 4,76 / 2,38.

## 10. Múltiples promociones

A (funda+vidrio a 100) y B (teléfono −10 %): 2 `AppliedPromotion`, 3 líneas con
allowance, Σ neto 296,61 (2 669,49 − 2 372,88), Σ bruto 350,00
(`test_multiple_promotions_each_explain_their_own_lines`).

## 11. Estructura UBL

Documento: `…PaymentTerms → AllowanceCharge → TaxTotal → LegalMonetaryTotal → InvoiceLine`.
Línea: `ID → InvoicedQuantity → LineExtensionAmount → PricingReference → AllowanceCharge →
TaxTotal → Item → Price`. `AllowanceChargeType`: `ChargeIndicator → ReasonCode →
[MultiplierFactorNumeric] → Amount → BaseAmount`. Verificado contra los XSD vendorizados
(`test_line_and_global_allowances_add_up_and_keep_the_xsd_order`).

## 12. Invariantes

`InvoiceData.check()` (exactas): `taxable + tax == total`; líneas no vacías; cada
descuento `0 < Amount ≤ BaseAmount` y `BaseAmount == valor antes de rebajar`;
`Σ líneas − Σ global == taxable`. `rules.validate`: código `00` sólo en línea, `02` sólo
en documento; factor (si existe) `base × factor == amount` ±0,01; tasa única (3462);
`tax == taxable × tasa` ±0,01 (3291); sin global, Σ impuesto de líneas == impuesto;
`cantidad × unitario − descuentos == valor de venta` ±0,01 (3271). Las tolerancias de
0,01 sólo absorben el valor unitario a 10 decimales (VEN-02A), nunca un descuadre.

## 13. Seguridad / multiempresa

`AppliedPromotion` se lee con `company_id=order.company_id`; cada componente debe
estar en las líneas de la venta y su producto pertenecer a la empresa; una promoción de
otra empresa se rechaza (`test_a_promotion_of_another_company_is_never_used`).
`receipt_options` no devuelve opciones fiscales a quien no tiene `sales.fiscal.issue`
(`test_without_the_fiscal_capability_no_fiscal_entry_is_returned`), y el diagnóstico
no contiene secretos (`test_with_fiscal_disabled_the_authorised_user_sees_a_safe_diagnosis`).
`has_capability` intacto. Sin llamadas de red en tests.

## 14. Migraciones

Nuevas: **0**. `makemigrations --check --dry-run`: «No changes detected».
`migrate --check` con la BD de desarrollo (SQLite): sin pendientes.

## 15. Archivos modificados

| Ruta | Cambio | Motivo |
|---|---|---|
| `backend/store/promotion_services.py` (97043d6) | allocator, `evaluate`, `frozen_component_discounts`, `_jsonable_components` | ADR-42 |
| `backend/store/fiscal_config.py` (97043d6) | `_DOCUMENT_NOUN`, mensajes de `resolve_series` | bug boleta/factura |
| `backend/store/pos_services.py` | `receipt_options` + `_series_diagnosis` (97043d6); `freeze` antes del comprobante | contrato POS; bug de orden |
| `backend/store/fiscal/data.py` | `Allowance`, constantes Catálogo 53, `Line.allowances`, `InvoiceData.allowances`, invariantes | ADR-43 |
| `backend/store/fiscal/builder.py` | `_allowance_charge`, `_factor`, `PricingReference` n(12,10), allowances en doc/línea | ADR-43 |
| `backend/store/fiscal/rules.py` | nivel de código, factor, tasa única, 3291, 3271 con descuentos | ADR-43 |
| `backend/store/fiscal/rounding.py` | `allocate_proportionally` | reparto neto por línea |
| `backend/store/fiscal_services.py` | `_order_to_invoice_data` con descuentos; guardas; `_promotion_line_discounts`; guarda antigua eliminada | ADR-43 |
| `backend/store/management/commands/seed_demo_users.py` | `--fiscal-beta` | bootstrap BETA |
| `backend/store/tests.py` | §41 promociones; `Fiscal6DiscountDeclarationTest`; `Fiscal6ReceiptOptionsTest`; `DemoUsersCommandTest`; `C22B`/`C22D` adaptados | cobertura |
| `frontend/app/admin/lib/internal-api.ts` | `PosReceiptOption` | contrato |
| `frontend/app/admin/sales/pos/PosReceiptSelector.tsx` | deshabilitadas con causa; suelta la selección inválida | §20 |
| `frontend/app/admin/sales/pos/page.tsx` | guarda `enabled`; panel fiscal real tras la venta; impresión sólo para nota | §28 |
| `frontend/__tests__/pos-receipt-selector.test.tsx` | 8 casos | §30 |
| `frontend/e2e/pos-receipt-options.spec.ts` | humo real dev_admin | §31 |
| `docs/adr-fiscal-c22a1.md` | ADR-42, ADR-43 | decisión |
| `docs/sunat-factura-ubl21-matriz.md` | sección «Descuentos declarados», fuente RV-2025 | §33 |
| `docs/sunat-cpe-requisitos.md` | Catálogo 53 vigente y reglas literales | §33 |
| `05_DECISIONES_TECNICAS.md` (nuevo), `02_ESTADO_ACTUAL.md`, `07_CHANGELOG.md`, `CHANGELOG.md` | estado y decisiones | §34 |
| `backend/.env` (ignorado por git) | `FISCAL_ENABLED=True`, `FISCAL_ENVIRONMENT=beta` | §25 |

## 16. Tests focalizados (comandos exactos y resultados)

Todos con `DATABASE_URL=postgres://postgres:postgres@localhost:5432/blackdog python3 manage.py test … --noinput`:

| Conjunto | Resultado |
|---|---|
| `C13PromotionEngineTest C13PromotionSaleTest` | 51 tests, 1 fallo de aserción del propio test corregido → verde en la regresión siguiente |
| `-k C22 -k Fiscal -k fiscal -k Ip1Pos -k C13Promotion` (regresión tras la capa fiscal) | **502 tests OK**, 100,6 s |
| `Fiscal6DiscountDeclarationTest Fiscal6ReceiptOptionsTest DemoUsersCommandTest C22BDiscountFailsClosedTest C22DBoletaTest C13PromotionSaleTest Ip1PosWebParityTest` | 123 tests, 2 errores de autoría corregidos (constraint `unique_order_line_per_product`; `only_caps` doble) |
| `Fiscal6DiscountDeclarationTest Fiscal6ReceiptOptionsTest` | **43 tests OK**, 14,2 s |

Humo puro sin BD (`store.fiscal` con `minimal_invoice`): global `02`, línea `00`, orden
XSD, y 5 negativos rechazados (código cruzado ×2, doble resta, base incorrecta, importe cero).

## 17. Suite final

Backend PostgreSQL (`DATABASE_URL=postgres://… python3 manage.py test store --noinput`):
**Ran 4489 tests in 1917.357s — OK (skipped=3) — exit 0**, sobre el árbol de trabajo
exactamente igual al código backend de estos commits (después de la suite sólo cambió
documentación). El proceso tardó 7,2 h de reloj por la contención con el build de
producción y los E2E que corrían a la vez; el tiempo de runner es el indicado.
`manage.py check` (BD de desarrollo): sin problemas. Con el PostgreSQL de humo `blackdog`
`check` avisa `store.W001` (135 migraciones sin aplicar): esa base de humo nunca se migró;
no es la base de test ni la de desarrollo.

Frontend: `npm run test:ci` **357 tests, 33 suites, OK** (+7 nuevos); `npx tsc --noEmit`
OK; `npm run lint` **0 errores, 33 advertencias** (las mismas 33 de la entrega anterior;
ninguna en los ficheros tocados); `npm run build` OK (44 páginas estáticas).

Playwright (servidores de desarrollo preexistentes, SQLite): `pos-ticket.spec.ts` **PASA**
(11,0 s); `pos-receipt-options.spec.ts` **PASA** (18,4 s) con payload real:
`sales_note`, `boleta`, `factura` — `enabled: true`, `branches: [1]`, sin `disabled_code`.

## 18. Bugs encontrados

| ID | Sev. | Módulo | Problema | Causa | Acción | Estado |
|---|---|---|---|---|---|---|
| BUG-F6-01 | Bloqueante | `fiscal_config` | `_DOCUMENT_NOUN` referenciado sin definir | dict ausente | definido, `.get()` con fallback | Corregido (97043d6) |
| BUG-F6-02 | Media | `fiscal_config` | «serie de factura» al faltar una boleta | mensajes hardcodeados | sustantivo por tipo en los 3 mensajes | Corregido (97043d6) |
| BUG-F6-03 | Media | `pos_services` | opción fiscal omitida en silencio | `receipt_options` saltaba errores | `enabled`/`disabled_code`/`disabled_reason` | Corregido (97043d6) |
| BUG-F6-04 | Alta | `promotion_services` | `Decimal` no serializable en `freeze()` | metadata JSON | texto en el borde de persistencia | Corregido (97043d6) |
| BUG-F6-05 | Alta | `pos_services` | venta con promoción + boleta/factura fallaba: «no conserva ninguna promoción aplicada» | `freeze()` después de preparar el comprobante | `freeze()` antes del documento, misma transacción | Corregido |
| BUG-F6-06 | Alta | especificación | `00` global, `LineExtensionAmount` previo, `AllowanceTotalAmount` = línea+global | catálogo de 2017 | fuente oficial 2025, ADR-43 | Resuelto |
| BUG-F6-07 | Baja | e2e | spec esperaba `/sales/pos/context/` y barra final | ruta real `/admin/pos/context` tras 308 | corregido | Corregido |
| FISCAL-PDF-01 | Media | PDF fiscal | representación impresa sin «Cargos y/o descuentos globales» (Anexo N.º 2, req. mínimo) | fuera del alcance de la fase | registrado como deuda | Pendiente |

## 19. Deuda pendiente

SUNAT producción · verificación directa en BETA de un comprobante con descuento ·
FISCAL-PDF-01 · Resumen Diario en la web · NC-BOL/ND-BOL · SMTP real · WhatsApp · SMS ·
E2E orden de servicio · UI-01 (conservar `canIssue`; «sin permiso» ≠ «falló el contexto») ·
33 advertencias de lint · SALES-SHIPPING-01 · SALES-NOTES-01 · CI-01 (sin CI de proyecto).

## 20. Documentación

Ver §15. La matriz ya no dice «AllowanceCharge: no hay descuentos».

## 21. Clasificación final

| Elemento | Estado |
|---|---|
| POS Nota interna | IMPLEMENTADO |
| POS Boleta (BETA) | IMPLEMENTADO |
| POS Factura (BETA) | IMPLEMENTADO |
| Diagnóstico fiscal POS | IMPLEMENTADO |
| Bootstrap BETA | IMPLEMENTADO |
| Allocator promociones | IMPLEMENTADO |
| Snapshot nuevo | IMPLEMENTADO |
| Legacy promotions | IMPLEMENTADO (reconstrucción en memoria) |
| Coupon UBL | IMPLEMENTADO (BETA; verificación directa PENDIENTE) |
| Manual UBL | IMPLEMENTADO (BETA; verificación directa PENDIENTE) |
| Promotion UBL | IMPLEMENTADO (BETA; verificación directa PENDIENTE) |
| Factura con descuento | IMPLEMENTADO (BETA) |
| Boleta con descuento | IMPLEMENTADO (BETA; envío por Resumen Diario) |
| Resumen Diario (web) | PENDIENTE |
| SUNAT producción | PENDIENTE |
| UI-01 | PENDIENTE (deuda UX) |
| PDF con descuentos globales | PENDIENTE (FISCAL-PDF-01) |

## 22. Commits

Organización real (el allocator ya estaba commiteado antes de empezar los tests §41,
así que no se forzó la partición sugerida; los hunks de `tests.py` se repartieron por
commit con parches de contexto verificados en seco):

| Commit | Contenido |
|---|---|
| `97043d6` feat(promotions): freeze deterministic component discount allocation | allocator, `evaluate()`, `frozen_component_discounts`, JSON, `_DOCUMENT_NOUN`, mensajes de `resolve_series`, contrato `receipt_options` |
| `c9e64b9` fix(pos): freeze the promotion snapshot before the receipt, and close the BETA till | orden del snapshot en `create_pos_sale`; `--fiscal-beta`; selector y pantalla post-venta; tests `receipt_options`, bootstrap, selector; E2E `pos-receipt-options` |
| `fabfa38` test(sales): close promotion and discount coverage | tests §41 del motor y del `freeze()` |
| HEAD de esta entrega — feat(fiscal): close ERP-FISCAL-6 discount attribution | `fiscal/data.py`, `builder.py`, `rules.py`, `rounding.py`, `fiscal_services.py`; `Fiscal6DiscountDeclarationTest`, C22B/C22D adaptados; ADR-42/43; matriz; Catálogo 53; `05_DECISIONES_TECNICAS.md`; 02_/07_/CHANGELOG; este dossier |

## 23. Siguiente fase recomendada (sólo recomendación)

Verificar en SUNAT BETA una factura sintética con descuento global y otra con
descuento de línea usando las credenciales públicas de prueba, y cerrar FISCAL-PDF-01.
Después, la auditoría integral ya autorizada sobre `audit/full-system-2026-09`.
