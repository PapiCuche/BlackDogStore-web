"""
The access log of the production server, without what a URL may carry.

Selected in `Dockerfile.prod` with `--logger-class`. See `backend.log_redaction`.
"""
from gunicorn.glogging import Logger

from .log_redaction import redact_request_line, redact_uri


class RedactingLogger(Logger):
    def atoms(self, resp, req, environ, request_time):
        atoms = super().atoms(resp, req, environ, request_time)
        atoms['r'] = redact_request_line(str(atoms.get('r', '')))
        for key in ('U', 'f', '{referer}i'):
            if key in atoms and atoms[key]:
                atoms[key] = redact_uri(str(atoms[key]))
        if atoms.get('q'):
            atoms['q'] = redact_uri('?' + str(atoms['q']))[1:]
        return atoms
