"""
FIN-F — FINANCE-MANUAL-REMINDERS-01: manual collection / payment messages
(docs/adr-finance-obligations.md §7.3–§7.5, §11.2).

The server writes the text from the obligation's CURRENT figures; the person copies it. Nothing is sent
(no WhatsApp, Meta, SMS, e-mail, push, no outbound HTTP) and nothing economic is ever written.
"""
from contextlib import ExitStack
from datetime import date, timedelta
from unittest import mock

from django.core import mail
from django.test import TestCase

from store import finance_test_support as fx
from store.finance import messages, services
from store.models import AdminAuditLog, FinanceMessageTemplate, Obligation, RepairStatusCode

D = fx.D
MESSAGES = ('finance.messages.prepare',)
LIST = '/api/admin/finance/obligations/'
TEMPLATES = '/api/admin/finance/message-templates/'


def url(obligation, tail):
    return f'{LIST}{obligation.pk}/{tail}/'


def today_of(company):
    return services.company_today(company)


def manual(world, *, amount='300.00', due_in=10, currency=None, concept='Equipo prestado', customer=None,
           branch=None, **extra):
    """A receivable by hand, due `due_in` days from today (negative: already overdue)."""
    from store.finance import receivables

    today = today_of(world.company)
    due = today + timedelta(days=due_in)
    return receivables.create_receivable(
        world.company, actor=world.staff, customer=customer or world.customer, concept=concept, amount=amount,
        due_date=due, reason='El cliente no devolvió el equipo.', currency=currency, branch=branch,
        issue_date=min(today, due), **extra)


def pay(obligation, amount, actor):
    return services.record_payment(obligation, amount=amount, method='cash', actor=actor)


def fixed(company, actor, **over):
    """The first period of a fixed payment, already overdue (the rule started months ago)."""
    over.setdefault('payee_name', 'Inmobiliaria Sol')
    rule = fx.rule(company, actor, today=date(2026, 1, 1), **over)
    return Obligation.objects.filter(rule=rule).order_by('due_date').first()


class DefaultTextsTest(TestCase):
    """The owner's wording, verbatim (docs §7.5)."""

    def test_customer_templates_are_the_owners_literal_text(self):
        self.assertEqual(messages.DEFAULT_BODIES['receivable.friendly'], (
            'Hola, {{cliente}}. Te saludamos de {{empresa}}.\n\n'
            'Queremos recordarte que registramos un saldo pendiente de {{saldo}} correspondiente a '
            '{{referencia}}.\n\n'
            'Si ya realizaste el pago, por favor comunícanoslo para verificarlo y actualizar nuestros '
            'registros.\n\n'
            'Si necesitas alguna información adicional, estaremos encantados de ayudarte.\n\n'
            'Muchas gracias.'))
        self.assertEqual(messages.DEFAULT_BODIES['receivable.upcoming'], (
            'Hola, {{cliente}}.\n\n'
            'Te recordamos que el pago de {{saldo}}, correspondiente a {{referencia}}, tiene como fecha de '
            'vencimiento el {{vencimiento}}.\n\n'
            'Si ya realizaste el pago, puedes comunicárnoslo para verificarlo.\n\n'
            'Gracias por tu atención.\n\n'
            '{{empresa}}'))
        self.assertEqual(messages.DEFAULT_BODIES['receivable.overdue'], (
            'Hola, {{cliente}}. Buen día.\n\n'
            'Nos comunicamos de {{empresa}} para informarte que, según nuestros registros, permanece pendiente '
            'un saldo de {{saldo}} correspondiente a {{referencia}}, cuyo vencimiento fue el {{vencimiento}}.\n\n'
            'Agradeceremos que nos confirmes si ya efectuaste el pago o si necesitas coordinarlo con '
            'nosotros.\n\n'
            'Muchas gracias.'))
        self.assertEqual(messages.DEFAULT_BODIES['receivable.partial'], (
            'Hola, {{cliente}}.\n\n'
            'Hemos registrado un abono a tu cuenta correspondiente a {{referencia}}.\n\n'
            'Actualmente, el saldo pendiente es de {{saldo}}.\n\n'
            'Gracias por tu pago. Si ya completaste el importe restante, comunícanoslo para verificarlo.\n\n'
            '{{empresa}}'))

    def test_the_internal_fixed_payment_reminder_is_literal(self):
        self.assertEqual(messages.DEFAULT_BODIES['fixed.internal'], (
            'Recordatorio de pago\n\n'
            'Concepto: {{concepto}}\n'
            'Beneficiario: {{beneficiario}}\n'
            'Importe pendiente: {{saldo}}\n'
            'Vencimiento: {{vencimiento}}\n'
            'Estado: {{estado}}\n'
            'Empresa: {{empresa}}'))

    def test_supplier_texts_are_stored_but_not_offered_to_any_obligation_yet(self):
        self.assertIn('{{proveedor}}', messages.DEFAULT_BODIES['payable.statement'])
        self.assertIn('{{importe_pagado}}', messages.DEFAULT_BODIES['payable.paid'])
        offered = {key for keys in messages.AUDIENCES.values() for key in keys}
        self.assertFalse({'payable.statement', 'payable.coordinate', 'payable.paid'} & offered)
        self.assertNotIn(('payable', Obligation.ORIGIN_SUPPLIER_DOCUMENT), messages.AUDIENCES)

    def test_every_default_uses_only_the_closed_variable_list(self):
        for key, body in messages.DEFAULT_BODIES.items():
            self.assertEqual(messages.unknown_variables(body), [], key)
            self.assertLessEqual(len(body), messages.MAX_LENGTH, key)


class RenderTest(TestCase):
    """The pure renderer: closed variable list, no leftovers, no links, no injection."""

    FACTS = {'cliente': 'Ana Quispe', 'empresa': 'Taller', 'saldo': 'S/ 300.00', 'referencia': 'OB-000001'}

    def test_fills_the_variables(self):
        self.assertEqual(messages.render('Hola {{cliente}} de {{empresa}}: {{saldo}} ({{ referencia }})',
                                         self.FACTS),
                         'Hola Ana Quispe de Taller: S/ 300.00 (OB-000001)')

    def test_a_variable_outside_the_list_is_refused(self):
        for text in ('{{external_reference}}', '{{notes}}', '{{customer.dni}}', 'x {{saldo.__class__}}',
                     '{{__import__}}', '{{ }}'):
            with self.assertRaises(services.FinanceError, msg=text):
                messages.render(text, self.FACTS)

    def test_a_malformed_brace_is_refused_and_none_survives(self):
        for text in ('Hola {{cliente', 'Hola cliente}}', '{{{cliente}}}x {{'):
            with self.assertRaises(services.FinanceError, msg=text):
                messages.render(text, self.FACTS)

    def test_a_required_variable_without_data_blocks_with_the_reason(self):
        for name in ('saldo', 'referencia'):
            facts = {**self.FACTS, name: None}
            with self.assertRaises(services.FinanceError) as raised:
                messages.render('Debes {{saldo}} por {{referencia}}.', facts)
            self.assertIn(name, raised.exception.message)

    def test_an_optional_variable_without_data_drops_only_its_sentence(self):
        facts = {**self.FACTS, 'vencimiento': None}
        text = messages.render('Hola {{cliente}}. Vence el {{vencimiento}}. Saldo {{saldo}}.', facts)
        self.assertEqual(text, 'Hola Ana Quispe. Saldo S/ 300.00.')
        text = messages.render('Hola.\n\nVencimiento: {{vencimiento}}\nEmpresa: {{empresa}}', facts)
        self.assertEqual(text, 'Hola.\n\nEmpresa: Taller')

    def test_nothing_unresolved_ever_remains(self):
        facts = {name: None for name in messages.VARIABLES}
        facts.update(saldo='S/ 1.00', referencia='OB-1')
        for key, body in messages.DEFAULT_BODIES.items():
            if key == 'receivable.custom':
                continue  # every sentence needs data that is missing here: rightly empty (below)
            self.assertNotIn('{{', messages.render(body, facts), key)
        with self.assertRaises(services.FinanceError):
            messages.render(messages.DEFAULT_BODIES['receivable.custom'], facts)

    def test_a_value_cannot_smuggle_a_variable_or_a_line_break(self):
        facts = {**self.FACTS, 'cliente': 'Ana {{saldo}}\nOB-9 }}', 'empresa': 'A\r\nB'}
        text = messages.render('Hola {{cliente}} / {{empresa}}', facts)
        self.assertNotIn('{{', text)
        self.assertNotIn('}}', text)
        self.assertNotIn('\r', text)
        self.assertEqual(text.count('\n'), 0)

    def test_links_and_addresses_are_refused(self):
        for text in ('Paga en https://x.pe', 'http://evil', 'www.evil.com', 'wa.me/51999', 'escribe a a@b.pe',
                     'mailto:a@b.pe', 'WA.ME/1', 'ftp://x'):
            with self.assertRaises(services.FinanceError, msg=text):
                messages.render(text, self.FACTS)

    def test_length_and_emptiness(self):
        with self.assertRaises(services.FinanceError):
            messages.render('x' * (messages.MAX_LENGTH + 1), self.FACTS)
        messages.render('x' * messages.MAX_LENGTH, self.FACTS)
        for text in ('', '   \n ', None):
            with self.assertRaises(services.FinanceError):
                messages.render(text, self.FACTS)

    def test_formats(self):
        self.assertEqual(messages.money(D('1234.5'), 'PEN'), 'S/ 1,234.50')
        self.assertEqual(messages.money(D('0.5'), 'USD'), '$ 0.50')
        self.assertEqual(messages.money(D('10'), 'CLP'), 'CLP 10.00')
        self.assertEqual(messages.local_date(date(2026, 3, 1)), '01/03/2026')


class _World(TestCase):
    slug = 'fin-msg'

    def setUp(self):
        self.w = fx.RepairWorld(self.slug, 'Taller Mensajes')
        self.user = fx.member(self.w.company, f'{self.slug}_user', MESSAGES + ('finance.view',))
        self.api = fx.client_for(self.user)

    def available(self, obligation, api=None):
        response = (api or self.api).get(url(obligation, 'messages'))
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def keys(self, obligation):
        return [t['key'] for t in self.available(obligation)['templates']]

    def preview(self, obligation, template, text=None, api=None, **extra):
        data = {'template': template, **extra}
        if text is not None:
            data['text'] = text
        return (api or self.api).post(url(obligation, 'message-preview'), data, format='json')


class ApplicabilityTest(_World):
    def test_pending_receivable_offers_friendly_upcoming_and_custom(self):
        row = manual(self.w, due_in=20)
        out = self.available(row)
        self.assertEqual([t['key'] for t in out['templates']],
                         ['receivable.friendly', 'receivable.upcoming', 'receivable.custom'])
        self.assertEqual(out['suggested'], 'receivable.upcoming')
        self.assertIsNone(out['blocked'])

    def test_overdue_offers_the_overdue_template_and_suggests_it(self):
        row = manual(self.w, due_in=-5)
        out = self.available(row)
        self.assertEqual([t['key'] for t in out['templates']],
                         ['receivable.friendly', 'receivable.overdue', 'receivable.custom'])
        self.assertEqual(out['suggested'], 'receivable.overdue')

    def test_partial_payment_adds_the_partial_template_and_suggests_it(self):
        row = manual(self.w, due_in=-5)
        pay(row, '100.00', self.w.staff)
        out = self.available(row)
        self.assertIn('receivable.partial', [t['key'] for t in out['templates']])
        self.assertEqual(out['suggested'], 'receivable.partial')
        row = manual(self.w, due_in=5)
        self.assertNotIn('receivable.partial', self.keys(row))

    def test_a_receivable_without_a_due_date_gets_no_date_template(self):
        row = manual(self.w)
        Obligation.objects.filter(pk=row.pk).update(due_date=None)
        self.assertEqual(self.keys(row), ['receivable.friendly', 'receivable.custom'])

    def test_fully_paid_has_no_message(self):
        row = manual(self.w)
        pay(row, '300.00', self.w.staff)
        out = self.available(row)
        self.assertEqual(out['templates'], [])
        self.assertEqual(out['blocked'], 'Esta cuenta ya está pagada.')

    def test_voided_has_no_message(self):
        row = manual(self.w)
        services.void(row, reason='Error de captura', actor=self.w.staff)
        out = self.available(row)
        self.assertEqual((out['templates'], bool(out['blocked'])), ([], True))
        self.assertEqual(self.preview(row, 'receivable.friendly').status_code, 400)

    def test_disputed_receivable_has_no_collection_template_but_says_why(self):
        row = manual(self.w)
        services.mark_disputed(row, reason='El cliente dice que ya pagó', actor=self.w.staff)
        out = self.available(row)
        self.assertEqual(out['templates'], [])
        self.assertIn('disputa', out['blocked'])
        self.assertEqual(self.preview(row, 'receivable.friendly').status_code, 400)
        services.resolve_dispute(row, resolution='Se verificó el pago', actor=self.w.staff)
        self.assertIn('receivable.friendly', self.keys(row))

    def test_repair_with_balance_gets_the_customer_templates(self):
        order = self.w.order('500.00')
        row = Obligation.objects.get(repair_order=order)
        out = self.available(row)
        self.assertIn('receivable.friendly', [t['key'] for t in out['templates']])
        self.assertEqual(out['data']['saldo'], 'S/ 500.00')

    def test_repair_without_balance_says_it_is_paid(self):
        order = self.w.order('500.00')
        self.w.pay(order, '500.00')
        row = Obligation.objects.get(repair_order=order)
        out = self.available(row)
        self.assertEqual((out['templates'], out['blocked']), ([], 'Esta cuenta ya está pagada.'))

    def test_cancelled_repair_has_no_message(self):
        order = self.w.order('500.00')
        self.w.force_status(order, RepairStatusCode.CANCELLED)
        row = Obligation.objects.get(repair_order=order)
        out = self.available(row)
        self.assertEqual(out['templates'], [])
        self.assertTrue(out['blocked'])
        self.assertEqual(self.preview(row, 'receivable.friendly').status_code, 400)

    def test_repair_whose_quote_is_being_redone_has_no_collection_message(self):
        order = self.w.order('500.00')
        self.w.requote(order, '700.00')  # new revision sent, not approved: the balance is provisional
        row = Obligation.objects.get(repair_order=order)
        out = self.available(row)
        self.assertEqual(out['templates'], [])
        self.assertTrue(out['blocked'])

    def test_repair_without_an_approved_quote_is_not_a_receivable_at_all(self):
        order = self.w.order('500.00', approve=False)
        self.assertFalse(Obligation.objects.filter(repair_order=order).exists())

    def test_fixed_payment_offers_the_internal_reminder_and_the_provider_version(self):
        row = fixed(self.w.company, self.w.staff)
        self.assertEqual(self.keys(row), ['fixed.internal', 'fixed.coordinate'])

    def test_disputed_fixed_payment_keeps_only_the_internal_reminder(self):
        row = fixed(self.w.company, self.w.staff)
        services.mark_disputed(row, reason='Cobro duplicado', actor=self.w.staff)
        self.assertEqual(self.keys(row), ['fixed.internal'])

    def test_a_paid_fixed_period_has_no_reminder(self):
        row = fixed(self.w.company, self.w.staff)
        pay(row, '1500.00', self.w.staff)
        self.assertEqual(self.keys(row), [])

    def test_paused_period_is_not_owed_so_no_reminder(self):
        row = fixed(self.w.company, self.w.staff)
        rule = row.rule
        services.pause_rule(rule, reason='Local cerrado', actor=self.w.staff, today=row.due_date - timedelta(days=3))
        out = self.available(Obligation.objects.get(pk=row.pk))
        self.assertEqual(out['templates'], [])

    def test_payables_without_supplier_templates_yet_have_nothing_to_prepare(self):
        row = services.create_obligation(self.w.company, actor=self.w.staff, concept='Luz', amount='80.00',
                                         due_date=today_of(self.w.company) + timedelta(days=3))
        out = self.available(row)
        self.assertEqual(out['templates'], [])
        self.assertTrue(out['blocked'])
        self.assertEqual(self.preview(row, 'payable.statement').status_code, 400)
        self.assertEqual(self.preview(row, 'fixed.internal').status_code, 400)

    def test_a_template_of_another_audience_cannot_be_forced_through_preview(self):
        row = manual(self.w)
        for key in ('fixed.internal', 'payable.paid', 'nope', '', None, 7):
            self.assertEqual(self.preview(row, key).status_code, 400, key)


class PreviewTest(_World):
    def test_template_a_uses_the_current_balance_and_the_company_name(self):
        row = manual(self.w, amount='300.00')
        out = self.preview(row, 'receivable.friendly').json()
        self.assertIn('Hola, Ana Quispe. Te saludamos de Taller Mensajes.', out['text'])
        self.assertIn(f'saldo pendiente de S/ 300.00 correspondiente a {row.reference}.', out['text'])
        self.assertNotIn('{{', out['text'])

    def test_partial_template_d_has_the_remaining_balance(self):
        row = manual(self.w, amount='300.00')
        pay(row, '120.00', self.w.staff)
        text = self.preview(row, 'receivable.partial').json()['text']
        self.assertIn('el saldo pendiente es de S/ 180.00.', text)
        self.assertTrue(text.endswith('Taller Mensajes'))

    def test_the_text_follows_the_database_after_a_payment(self):
        row = manual(self.w, amount='300.00')
        before = self.preview(row, 'receivable.friendly').json()['text']
        pay(row, '50.00', self.w.staff)
        after = self.preview(row, 'receivable.friendly').json()['text']
        self.assertIn('S/ 300.00', before)
        self.assertIn('S/ 250.00', after)

    def test_overdue_template_c_has_the_local_date(self):
        row = manual(self.w, due_in=-5)
        due = today_of(self.w.company) - timedelta(days=5)
        text = self.preview(row, 'receivable.overdue').json()['text']
        self.assertIn(f'cuyo vencimiento fue el {due:%d/%m/%Y}.', text)

    def test_upcoming_template_b_has_the_due_date(self):
        row = manual(self.w, due_in=4)
        due = today_of(self.w.company) + timedelta(days=4)
        self.assertIn(f'tiene como fecha de vencimiento el {due:%d/%m/%Y}.',
                      self.preview(row, 'receivable.upcoming').json()['text'])

    def test_usd_keeps_its_symbol_and_never_mixes_with_soles(self):
        usd = manual(self.w, amount='250.00', currency='USD')
        pen = manual(self.w, amount='250.00')
        self.assertIn('$ 250.00', self.preview(usd, 'receivable.friendly').json()['text'])
        text = self.preview(pen, 'receivable.friendly').json()['text']
        self.assertIn('S/ 250.00', text)
        self.assertNotIn('$', text)

    def test_amounts_and_names_sent_by_the_client_are_ignored(self):
        row = manual(self.w, amount='300.00')
        out = self.preview(row, 'receivable.friendly', saldo='1.00', amount='1.00', balance='1.00',
                           cliente='Otro', empresa='Otra', facts={'saldo': '1.00'}).json()
        self.assertIn('S/ 300.00', out['text'])
        self.assertNotIn('1.00', out['text'].replace('300.00', ''))
        self.assertIn('Ana Quispe', out['text'])
        self.assertNotIn('Otro', out['text'])

    def test_edited_text_is_rerendered_by_the_server(self):
        row = manual(self.w, amount='300.00')
        out = self.preview(row, 'receivable.custom',
                           'Ana, quedan {{saldo}} de {{referencia}}. Gracias, {{empresa}}.').json()
        self.assertEqual(out['text'], f'Ana, quedan S/ 300.00 de {row.reference}. Gracias, Taller Mensajes.')

    def test_edited_text_cannot_ask_for_other_data(self):
        row = manual(self.w, external_reference='CONTRATO-77', notes='nota privada')
        for text in ('{{external_reference}}', '{{notes}}', '{{cliente.document_number}}', '{{imei}}'):
            self.assertEqual(self.preview(row, 'receivable.custom', text).status_code, 400, text)

    def test_edited_text_with_links_or_too_long_is_a_400(self):
        row = manual(self.w)
        for text in ('Paga aquí https://pago.pe', 'wa.me/51999999999', 'x' * 1001):
            self.assertEqual(self.preview(row, 'receivable.custom', text).status_code, 400, text)

    def test_text_that_is_not_text_is_a_400(self):
        row = manual(self.w)
        for text in (['a'], {'a': 1}, True, 1.5):
            response = self.api.post(url(row, 'message-preview'),
                                     {'template': 'receivable.custom', 'text': text}, format='json')
            self.assertEqual(response.status_code, 400, text)

    def test_malformed_bodies_are_400_never_500(self):
        row = manual(self.w)
        for payload in ('[1, 2]', '"x"', '7', 'null', '{"template": '):
            response = self.api.post(url(row, 'message-preview'), payload, content_type='application/json')
            self.assertEqual(response.status_code, 400, payload)
            response = self.api.post(url(row, 'message-copied'), payload, content_type='application/json')
            self.assertEqual(response.status_code, 400, payload)

    def test_a_disputed_obligation_is_flagged_in_the_data(self):
        row = fixed(self.w.company, self.w.staff)
        services.mark_disputed(row, reason='Cobro duplicado', actor=self.w.staff)
        out = self.preview(row, 'fixed.internal').json()
        self.assertTrue(out['data']['disputed'])
        self.assertTrue(out['warnings'])

    def test_the_window_carries_original_paid_and_balance(self):
        row = manual(self.w, amount='300.00')
        pay(row, '100.00', self.w.staff)
        data = self.preview(row, 'receivable.friendly').json()['data']
        self.assertEqual((data['original'], data['paid'], data['saldo']), ('S/ 300.00', 'S/ 100.00', 'S/ 200.00'))
        self.assertEqual((data['cliente'], data['empresa'], data['referencia']),
                         ('Ana Quispe', 'Taller Mensajes', row.reference))

    def test_orientation_notice_is_shown(self):
        row = manual(self.w)
        notice = self.available(row)['guidance']
        self.assertIn('07:00', notice)
        self.assertIn('20:00', notice)

    def test_no_private_data_reaches_the_text(self):
        from store.models import Customer

        Customer.objects.filter(pk=self.w.customer.pk).update(document_number='45678912')
        row = manual(self.w, external_reference='CONTRATO-77', notes='cuenta 191-123456-0-77 IMEI 356938035643809',
                     concept='Equipo prestado')
        for key in ('receivable.friendly', 'receivable.upcoming', 'receivable.custom'):
            text = self.preview(row, key).json()['text']
            for secret in ('45678912', 'CONTRATO-77', '191-123456', '356938035643809', 'Equipo prestado'):
                self.assertNotIn(secret, text, key)

    def test_the_repair_number_and_device_do_not_leak_into_a_repair_receivable_text(self):
        order = self.w.order('500.00')
        row = Obligation.objects.get(repair_order=order)
        text = self.preview(row, 'receivable.friendly').json()['text']
        self.assertIn(row.reference, text)
        self.assertNotIn(order.number, text)
        self.assertNotIn('No enciende', text)


class FixedPaymentTest(_World):
    def test_internal_reminder_for_an_overdue_fixed_payment(self):
        row = fixed(self.w.company, self.w.staff)
        text = self.preview(row, 'fixed.internal').json()['text']
        self.assertIn('Concepto: Alquiler del local', text)
        self.assertIn('Beneficiario: Inmobiliaria Sol', text)
        self.assertIn('Importe pendiente: S/ 1,500.00', text)
        self.assertIn(f'Vencimiento: {row.due_date:%d/%m/%Y}', text)
        self.assertIn('Estado: Vencido', text)
        self.assertIn('Empresa: Taller Mensajes', text)

    def test_missing_beneficiary_drops_its_line(self):
        row = fixed(self.w.company, self.w.staff, payee_name='')
        text = self.preview(row, 'fixed.internal').json()['text']
        self.assertNotIn('Beneficiario', text)
        self.assertNotIn('{{', text)

    def test_partial_payment_shows_the_remaining_balance_and_state(self):
        row = fixed(self.w.company, self.w.staff)
        pay(row, '500.00', self.w.staff)
        text = self.preview(row, 'fixed.internal').json()['text']
        self.assertIn('Importe pendiente: S/ 1,000.00', text)

    def test_provider_version_names_the_beneficiary_and_the_balance(self):
        row = fixed(self.w.company, self.w.staff)
        text = self.preview(row, 'fixed.coordinate').json()['text']
        self.assertIn('Buen día, Inmobiliaria Sol.', text)
        self.assertIn('coordinar el pago pendiente de S/ 1,500.00', text)
        self.assertIn(row.reference, text)
        self.assertIn('Taller Mensajes', text)

    def test_the_supplier_name_is_used_when_the_rule_names_a_supplier(self):
        from store.models import Supplier

        supplier = Supplier.objects.create(company=self.w.company, name='Luz del Sur SAC')
        row = fixed(self.w.company, self.w.staff, payee_name='', payee_supplier=supplier)
        Obligation.objects.filter(pk=row.pk).update(supplier=supplier)
        text = self.preview(row, 'fixed.internal').json()['text']
        self.assertIn('Beneficiario: Luz del Sur SAC', text)


class AccessTest(_World):
    def test_without_the_capability_everything_is_403(self):
        row = manual(self.w)
        api = fx.client_for(fx.member(self.w.company, 'fin_msg_noperm', fx.ALL_FINANCE))
        self.assertEqual(api.get(url(row, 'messages')).status_code, 403)
        self.assertEqual(self.preview(row, 'receivable.friendly', api=api).status_code, 403)
        self.assertEqual(api.post(url(row, 'message-copied'), {'template': 'receivable.friendly'},
                                  format='json').status_code, 403)

    def test_another_company_answers_404(self):
        row = manual(self.w)
        other = fx.company('fin-msg-b', 'Otra Empresa')
        stranger = fx.client_for(fx.member(other, 'fin_msg_stranger', MESSAGES + ('finance.view',)))
        self.assertEqual(stranger.get(url(row, 'messages')).status_code, 404)
        self.assertEqual(self.preview(row, 'receivable.friendly', api=stranger).status_code, 404)
        self.assertEqual(stranger.post(url(row, 'message-copied'), {'template': 'receivable.friendly'},
                                       format='json').status_code, 404)
        # ?company= selects, never authorises.
        self.assertEqual(stranger.get(url(row, 'messages') + f'?company={self.w.company.pk}').status_code, 404)

    def test_the_company_name_comes_from_each_company(self):
        other = fx.RepairWorld('fin-msg-c', 'Servicio Norte')
        api = fx.client_for(fx.member(other.company, 'fin_msg_north', MESSAGES))
        row = manual(other)
        text = self.preview(row, 'receivable.friendly', api=api).json()['text']
        self.assertIn('Servicio Norte', text)
        self.assertNotIn('Taller Mensajes', text)

    def test_a_branch_the_caller_cannot_operate_in_answers_404(self):
        other_branch = fx.branch(self.w.company, 'Sucursal Norte')
        restricted = fx.client_for(fx.member(self.w.company, 'fin_msg_branch', MESSAGES,
                                             branches=[self.w.branch]))
        inside = manual(self.w, branch=self.w.branch)
        outside = manual(self.w, branch=other_branch)
        self.assertEqual(restricted.get(url(inside, 'messages')).status_code, 200)
        for tail in ('messages',):
            self.assertEqual(restricted.get(url(outside, tail)).status_code, 404)
        self.assertEqual(self.preview(outside, 'receivable.friendly', api=restricted).status_code, 404)
        self.assertEqual(restricted.post(url(outside, 'message-copied'), {'template': 'receivable.friendly'},
                                         format='json').status_code, 404)

    def test_a_general_row_needs_company_wide_scope(self):
        restricted = fx.client_for(fx.member(self.w.company, 'fin_msg_scope', MESSAGES, branches=[self.w.branch]))
        general = manual(self.w, branch=None)
        self.assertEqual(restricted.get(url(general, 'messages')).status_code, 404)

    def test_anonymous_is_refused(self):
        row = manual(self.w)
        from rest_framework.test import APIClient

        self.assertIn(APIClient().get(url(row, 'messages')).status_code, (401, 403))


class CopyTest(_World):
    def audit_count(self):
        return AdminAuditLog.objects.filter(company=self.w.company).count()

    def snapshot(self, row):
        row = Obligation.objects.get(pk=row.pk)
        history = list(AdminAuditLog.objects.filter(
            company=self.w.company, target_type=services.AUDIT_OBLIGATION, target_id=str(row.pk)
        ).values_list('pk', flat=True))
        return (services.figures(row), row.lifecycle, row.disputed_at, history,
                row.payments.count(), row.adjustments.count())

    def test_copy_leaves_an_audit_record_without_the_text_and_changes_nothing_economic(self):
        row = manual(self.w, amount='300.00')
        pay(row, '50.00', self.w.staff)
        before = self.snapshot(row)
        response = self.api.post(url(row, 'message-copied'), {'template': 'receivable.friendly'}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.snapshot(row), before)
        entry = AdminAuditLog.objects.get(company=self.w.company, action='finance_message_copied')
        self.assertEqual((entry.target_type, entry.target_id, entry.actor_id),
                         ('finance_message', str(row.pk), self.user.pk))
        self.assertEqual(entry.metadata['template'], 'receivable.friendly')
        self.assertFalse(entry.metadata['edited'])
        self.assertNotIn('Ana Quispe', str(entry.metadata))
        self.assertNotIn('saldo', str(entry.metadata).replace('template', ''))
        out = response.json()
        self.assertEqual((out['copied'], out['sent']), (True, False))

    def test_an_edited_copy_is_marked_as_edited(self):
        row = manual(self.w)
        self.api.post(url(row, 'message-copied'), {'template': 'receivable.custom',
                                                   'text': 'Hola {{cliente}}, te escribimos por {{saldo}}.'},
                      format='json')
        entry = AdminAuditLog.objects.get(company=self.w.company, action='finance_message_copied')
        self.assertTrue(entry.metadata['edited'])

    def test_an_invalid_or_blocked_copy_leaves_no_record(self):
        row = manual(self.w)
        before = self.audit_count()
        self.assertEqual(self.api.post(url(row, 'message-copied'),
                                       {'template': 'receivable.custom', 'text': '{{imei}}'},
                                       format='json').status_code, 400)
        self.assertEqual(self.api.post(url(row, 'message-copied'), {'template': 'fixed.internal'},
                                       format='json').status_code, 400)
        paid = manual(self.w)
        pay(paid, '300.00', self.w.staff)
        before = self.audit_count()
        self.assertEqual(self.api.post(url(paid, 'message-copied'), {'template': 'receivable.friendly'},
                                       format='json').status_code, 400)
        self.assertEqual(self.audit_count(), before)

    def test_preview_writes_nothing_at_all(self):
        row = manual(self.w)
        before = self.audit_count()
        self.assertEqual(self.preview(row, 'receivable.friendly').status_code, 200)
        self.assertEqual(self.audit_count(), before)

    def test_nothing_is_sent_through_any_channel(self):
        row = manual(self.w, due_in=-3)
        fixed_row = fixed(self.w.company, self.w.staff)
        boom = AssertionError('a finance message must never be sent')
        with ExitStack() as stack:
            for target in ('store.messaging.provider_for',
                           'store.messaging.whatsapp_cloud.WhatsAppCloudProvider.send_template',
                           'store.messaging.fake.FakeProvider.send_template',
                           'urllib.request.urlopen', 'http.client.HTTPConnection.request',
                           'http.client.HTTPSConnection.request', 'requests.sessions.Session.request',
                           'django.core.mail.EmailMessage.send', 'django.core.mail.send_mail',
                           'socket.create_connection'):
                try:
                    stack.enter_context(mock.patch(target, side_effect=boom))
                except (AttributeError, ModuleNotFoundError):
                    continue
            for obligation, keys in ((row, ('receivable.friendly', 'receivable.overdue', 'receivable.custom')),
                                     (fixed_row, ('fixed.internal', 'fixed.coordinate'))):
                self.api.get(url(obligation, 'messages'))
                for key in keys:
                    self.assertEqual(self.preview(obligation, key).status_code, 200)
                    self.assertEqual(self.api.post(url(obligation, 'message-copied'), {'template': key},
                                                   format='json').status_code, 200)
        self.assertEqual(mail.outbox, [])

    def test_no_link_that_sends_the_text_elsewhere_is_ever_returned(self):
        row = manual(self.w)
        payload = str(self.available(row)) + str(self.preview(row, 'receivable.friendly').json())
        for token in ('wa.me', 'mailto:', 'sms:', 'whatsapp', 'http'):
            self.assertNotIn(token, payload)


class CompanyTemplateTest(_World):
    def setUp(self):
        super().setUp()
        self.manager = fx.client_for(fx.member(self.w.company, 'fin_msg_mgr', MESSAGES + ('finance.templates.manage',)))

    def put(self, key, body, api=None):
        return (api or self.manager).put(f'{TEMPLATES}{key}/', {'body': body}, format='json')

    def test_listing_shows_defaults_overrides_and_only_enabled_templates(self):
        out = self.manager.get(TEMPLATES).json()['results']
        keys = [t['key'] for t in out]
        self.assertEqual(keys, ['receivable.friendly', 'receivable.upcoming', 'receivable.overdue',
                                'receivable.partial', 'receivable.custom', 'fixed.internal', 'fixed.coordinate'])
        first = out[0]
        self.assertEqual(first['body'], messages.DEFAULT_BODIES['receivable.friendly'])
        self.assertFalse(first['customized'])
        self.assertIn('saldo', first['variables'])

    def test_listing_and_editing_need_templates_manage(self):
        self.assertEqual(self.api.get(TEMPLATES).status_code, 403)
        self.assertEqual(self.put('receivable.friendly', 'Hola {{cliente}}', api=self.api).status_code, 403)
        self.assertEqual(self.api.delete(f'{TEMPLATES}receivable.friendly/').status_code, 403)
        self.assertFalse(FinanceMessageTemplate.objects.exists())

    def test_a_company_template_replaces_the_default_and_can_be_restored(self):
        row = manual(self.w)
        body = 'Estimado {{cliente}}: tiene {{saldo}} pendientes ({{referencia}}). {{empresa}}'
        response = self.put('receivable.friendly', body)
        self.assertEqual(response.status_code, 200, response.content)
        out = self.preview(row, 'receivable.friendly').json()
        self.assertEqual(out['text'], f'Estimado Ana Quispe: tiene S/ 300.00 pendientes ({row.reference}). '
                                      'Taller Mensajes')
        self.assertTrue(out['company_template'])
        self.assertTrue(AdminAuditLog.objects.filter(company=self.w.company,
                                                     action='finance_message_template_saved').exists())
        self.assertEqual(self.manager.delete(f'{TEMPLATES}receivable.friendly/').status_code, 200)
        out = self.preview(row, 'receivable.friendly').json()
        self.assertFalse(out['company_template'])
        self.assertIn('Te saludamos de Taller Mensajes', out['text'])
        self.assertTrue(AdminAuditLog.objects.filter(company=self.w.company,
                                                     action='finance_message_template_restored').exists())

    def test_saving_twice_updates_one_row(self):
        self.put('receivable.friendly', 'Uno {{saldo}} {{referencia}}')
        self.put('receivable.friendly', 'Dos {{saldo}} {{referencia}}')
        self.assertEqual(FinanceMessageTemplate.objects.filter(company=self.w.company).count(), 1)
        self.assertEqual(FinanceMessageTemplate.objects.get().body, 'Dos {{saldo}} {{referencia}}')

    def test_invalid_company_templates_are_refused(self):
        for body in ('Hola {{imei}}', 'Mira https://x.pe {{saldo}}', 'x' * 1001, '', '   ', 'Hola {{cliente',
                     'escribe a a@b.pe {{saldo}}'):
            self.assertEqual(self.put('receivable.friendly', body).status_code, 400, body[:30])
        self.assertEqual(self.put('receivable.friendly', ['x']).status_code, 400)
        self.assertEqual(self.put('receivable.friendly', None).status_code, 400)
        self.assertFalse(FinanceMessageTemplate.objects.exists())

    def test_unknown_or_not_yet_enabled_template_keys_are_404(self):
        for key in ('nope', 'payable.statement', 'payable.paid'):
            self.assertEqual(self.put(key, 'Hola {{saldo}}').status_code, 404, key)
            self.assertEqual(self.manager.delete(f'{TEMPLATES}{key}/').status_code, 404, key)

    def test_malformed_body_is_400(self):
        response = self.manager.put(f'{TEMPLATES}receivable.friendly/', '[1]', content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_templates_are_per_company(self):
        self.put('receivable.friendly', 'Solo para mi {{saldo}} {{referencia}}')
        other = fx.RepairWorld('fin-msg-d', 'Otro Taller')
        api = fx.client_for(fx.member(other.company, 'fin_msg_other', MESSAGES + ('finance.templates.manage',)))
        row = manual(other)
        text = self.preview(row, 'receivable.friendly', api=api).json()['text']
        self.assertNotIn('Solo para mi', text)
        listing = api.get(TEMPLATES).json()['results'][0]
        self.assertFalse(listing['customized'])

    def test_the_stored_override_is_revalidated_when_used(self):
        row = manual(self.w)
        FinanceMessageTemplate.objects.create(company=self.w.company, key='receivable.friendly',
                                              body='Hola {{imei}} {{saldo}}')
        self.assertEqual(self.preview(row, 'receivable.friendly').status_code, 400)
