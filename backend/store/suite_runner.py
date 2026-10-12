"""
The test suite's own runner: the ONE place that says the suite charges through
simulated gateways.

Hundreds of tests open a payment as an ordinary buyer with TEST keys, against a
fake gateway. On a published shop that is exactly what must not happen
(`PAYMENTS_PUBLIC_TEST` is off there, and TEST keys only charge for a master),
so the suite turns the switch on — here, once, instead of in every fixture.
A test about the switch itself sets it with `override_settings`.

(Named so that test discovery, which collects `test*.py`, does not take this
module for a file of tests.)

The environment variable is set too: a test process started afresh (a parallel
worker, a subprocess) reads the settings module again and finds the same answer.
"""
import os

from django.conf import settings
from django.test.runner import DiscoverRunner


class SimulatedGatewayRunner(DiscoverRunner):
    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        self._public_test = (settings.PAYMENTS_PUBLIC_TEST, os.environ.get('PAYMENTS_PUBLIC_TEST'))
        settings.PAYMENTS_PUBLIC_TEST = True
        os.environ['PAYMENTS_PUBLIC_TEST'] = '1'

    def teardown_test_environment(self, **kwargs):
        setting, variable = self._public_test
        settings.PAYMENTS_PUBLIC_TEST = setting
        if variable is None:
            os.environ.pop('PAYMENTS_PUBLIC_TEST', None)
        else:
            os.environ['PAYMENTS_PUBLIC_TEST'] = variable
        super().teardown_test_environment(**kwargs)
