"""
CSRF-BODY-READ: the CSRF check must not be the thing that reads an upload.

Django looks for the token in the FORM before it looks at the header, and looking
at the form means parsing the whole body — a multipart upload goes to a temporary
file. On this API that happened right after the cookie's signature was checked and
before any permission: any signed-in account, a customer's included, could make
the server receive and store 111 MiB on a route it would then be refused on, and
hold a thread for as long as the body took to arrive.

The API takes its token from the `X-CSRFToken` header and nowhere else. No screen
of this application sends it as a form field.
"""
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from store.tests import _h411_cookie, _h411_csrf

NO_BODY = AssertionError('the request body was parsed before the request was authorised')


def _never_parse():
    return (
        mock.patch('django.http.multipartparser.MultiPartParser.parse', side_effect=NO_BODY),
        mock.patch('rest_framework.parsers.MultiPartParser.parse', side_effect=NO_BODY),
        mock.patch('rest_framework.parsers.FormParser.parse', side_effect=NO_BODY),
    )


class CsrfReadsNoBodyTest(TestCase):
    def setUp(self):
        self.customer = get_user_model().objects.create_user('comprador', 'c@example.com', 'x')
        self.client = _h411_cookie(self.customer)
        self.token = _h411_csrf(self.client)
        self.upload_url = reverse('admin-product-import-preview')

    def _upload(self, **headers):
        book = SimpleUploadedFile('productos.xlsx', b'PK' + b'\x00' * 4096)
        a, b, c = _never_parse()
        with a, b, c:
            return self.client.post(self.upload_url, {'file': book}, format='multipart', **headers)

    def test_an_account_without_permission_is_refused_without_its_upload_being_read(self):
        response = self._upload(HTTP_X_CSRFTOKEN=self.token)
        self.assertIn(response.status_code, (403, 404))

    def test_without_the_header_it_is_refused_without_its_upload_being_read(self):
        response = self._upload()
        self.assertEqual(response.status_code, 403)
        self.assertIn('CSRF', response.json()['detail'])

    def test_a_token_sent_as_a_form_field_is_not_a_token(self):
        a, b, c = _never_parse()
        with a, b, c:
            response = self.client.post(
                reverse('auth-logout'), {'csrfmiddlewaretoken': self.token}, format='multipart')
        self.assertEqual(response.status_code, 403)

    def test_the_header_still_works(self):
        response = self.client.post(reverse('auth-logout'), {}, format='json', HTTP_X_CSRFTOKEN=self.token)
        self.assertEqual(response.status_code, 200)

    def test_a_wrong_header_is_still_refused(self):
        response = self.client.post(reverse('auth-logout'), {}, format='json', HTTP_X_CSRFTOKEN='x' * 32)
        self.assertEqual(response.status_code, 403)

    def test_safe_methods_need_no_token(self):
        self.assertEqual(self.client.get('/api/auth/me/').status_code, 200)
